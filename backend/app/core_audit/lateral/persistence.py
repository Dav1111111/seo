"""UPSERT lateral query candidates.

Owner-decision protection rule (the only thing this file enforces):

    A row already in status ∈ {accepted, rejected, promoted} is
    *immutable* by the LLM. We never re-rank it back to 'new', and we
    never overwrite its confidence/rationale/relation. The LLM can
    only refresh rows still in 'new'.

That rule is why we cannot use a plain `ON CONFLICT DO UPDATE` — Postgres
doesn't know which status to guard on. We do a single SELECT to find
existing norms, partition the candidate list into (refresh / insert),
and issue two small statements.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.lateral.dto import LateralCandidate, normalize_query
from app.models.lateral_query import LateralQuery

logger = logging.getLogger(__name__)


async def upsert_lateral_candidates(
    db: AsyncSession,
    site_id: UUID,
    candidates: list[LateralCandidate],
    agent_run_id: UUID | None,
) -> dict[str, int]:
    """Persist the LLM output. Returns counters for the activity feed."""

    if not candidates:
        return {"inserted": 0, "refreshed": 0, "skipped_owner_locked": 0}

    # Dedup within this batch (safety net — llm_expansion already dedups).
    by_norm: dict[str, LateralCandidate] = {}
    for c in candidates:
        norm = normalize_query(c.query)
        if norm and norm not in by_norm:
            by_norm[norm] = c

    norms = list(by_norm.keys())

    stmt = select(LateralQuery).where(
        LateralQuery.site_id == site_id,
        LateralQuery.query_norm.in_(norms),
    )
    existing_rows = list((await db.execute(stmt)).scalars())
    by_norm_existing: dict[str, LateralQuery] = {
        row.query_norm: row for row in existing_rows
    }

    inserted = 0
    refreshed = 0
    skipped_owner_locked = 0

    for norm, cand in by_norm.items():
        existing = by_norm_existing.get(norm)
        if existing is None:
            db.add(
                LateralQuery(
                    site_id=site_id,
                    agent_run_id=agent_run_id,
                    query=cand.query,
                    query_norm=norm,
                    relation=cand.relation,
                    confidence=cand.confidence,
                    rationale=cand.rationale,
                    source_signal=cand.source_signal,
                    status="new",
                )
            )
            inserted += 1
            continue

        if existing.status != "new":
            # Owner has decided. Never overwrite.
            skipped_owner_locked += 1
            continue

        existing.relation = cand.relation
        existing.confidence = cand.confidence
        existing.rationale = cand.rationale
        existing.source_signal = cand.source_signal
        existing.agent_run_id = agent_run_id
        refreshed += 1

    await db.flush()
    stats = {
        "inserted": inserted,
        "refreshed": refreshed,
        "skipped_owner_locked": skipped_owner_locked,
    }
    logger.info("lateral.upsert site=%s %s", site_id, stats)
    return stats


async def set_status(
    db: AsyncSession,
    site_id: UUID,
    lateral_id: UUID,
    new_status: str,
) -> bool:
    """Owner action — accept / reject / promoted. Returns True if changed.

    Tenant scoping is enforced by `site_id` to avoid one site changing
    another site's row even if the lateral_id is leaked.
    """
    if new_status not in ("new", "accepted", "rejected", "promoted"):
        raise ValueError(f"Invalid status: {new_status}")

    from datetime import datetime, timezone

    accepted_at = (
        datetime.now(timezone.utc) if new_status == "accepted" else None
    )

    stmt = (
        update(LateralQuery)
        .where(
            LateralQuery.id == lateral_id,
            LateralQuery.site_id == site_id,
        )
        .values(status=new_status, accepted_at=accepted_at)
    )
    result = await db.execute(stmt)
    return (result.rowcount or 0) > 0
