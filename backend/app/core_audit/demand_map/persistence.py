"""Idempotent persistence for the Target Demand Map.

Phase B DB writer. Strategy is intentionally simple and easy to reason
about:

  1. DELETE existing `target_clusters` for this site — the CASCADE rule
     on the FK drops orphaned `target_queries` automatically.
  2. Bulk INSERT the fresh clusters.
  3. For each TargetQueryDTO, look up the parent cluster by
     `cluster_key` and INSERT the query.
  4. Single transaction — commit/rollback is the caller's responsibility
     when wrapped in an existing session, OR we commit here if called
     stand-alone.

The expander is deterministic, so repeated runs converge on the same
set of rows; a rebuild is cheaper and safer than a row-level upsert
here because Phase B has no downstream reader yet (safe to nuke).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.demand_map.dto import (
    TargetClusterDTO,
    TargetQueryDTO,
)
from app.core_audit.demand_map.models import TargetCluster, TargetQuery

log = logging.getLogger(__name__)


async def persist_demand_map(
    db: AsyncSession,
    site_id: UUID,
    clusters: list[TargetClusterDTO],
    queries: list[TargetQueryDTO],
) -> dict[str, int]:
    """Idempotent write: delete-then-insert, one transaction.

    Returns a summary dict:
        {"clusters_written": int, "queries_written": int,
         "clusters_deleted": int}

    Orphan protection: `target_clusters.id` is the FK target for
    `target_queries.cluster_id` (ON DELETE CASCADE), so wiping the
    cluster set automatically removes stale queries — no manual
    `DELETE FROM target_queries` needed.
    """
    # 1. Wipe existing clusters for this site.
    del_stmt = delete(TargetCluster).where(TargetCluster.site_id == site_id)
    del_result = await db.execute(del_stmt)
    deleted = del_result.rowcount or 0

    # 2. Insert new clusters + remember the generated id per cluster_key.
    key_to_id: dict[str, UUID] = {}
    orm_clusters: list[TargetCluster] = []
    for dto in clusters:
        row = TargetCluster(
            site_id=site_id,
            cluster_key=dto.cluster_key,
            name_ru=dto.name_ru,
            intent_code=dto.intent_code.value,
            cluster_type=dto.cluster_type.value,
            quality_tier=dto.quality_tier.value,
            keywords=list(dto.keywords or ()),
            seed_slots=dict(dto.seed_slots or {}),
            is_brand=bool(dto.is_brand),
            is_competitor_brand=bool(dto.is_competitor_brand),
            expected_volume_tier=(dto.expected_volume_tier or "s") if isinstance(dto.expected_volume_tier, str) else dto.expected_volume_tier.value,
            business_relevance=Decimal(str(round(float(dto.business_relevance), 3))),
            source=dto.source.value,
        )
        orm_clusters.append(row)

    if orm_clusters:
        db.add_all(orm_clusters)
        # Flush to materialize the server-generated / default UUIDs so
        # we can map cluster_key -> cluster_id for query inserts below.
        await db.flush()
        for r in orm_clusters:
            key_to_id[r.cluster_key] = r.id

    # 3. Insert queries (skip any whose cluster_key is unknown — e.g. LLM
    #    hallucination that slipped past the validator).
    orm_queries: list[TargetQuery] = []
    skipped_queries = 0
    for q in queries or []:
        parent_id = key_to_id.get(q.cluster_key)
        if parent_id is None:
            skipped_queries += 1
            continue
        orm_queries.append(
            TargetQuery(
                cluster_id=parent_id,
                query_text=q.query_text,
                source=q.source.value,
                estimated_volume_tier=(
                    q.estimated_volume_tier.value
                    if hasattr(q.estimated_volume_tier, "value")
                    else (q.estimated_volume_tier or "s")
                ),
            )
        )
    if orm_queries:
        db.add_all(orm_queries)
        await db.flush()

    await db.commit()

    stats = {
        "clusters": len(orm_clusters),
        "queries": len(orm_queries),
        "clusters_written": len(orm_clusters),
        "queries_written": len(orm_queries),
        "clusters_deleted": int(deleted),
        "queries_skipped_unknown_key": skipped_queries,
    }
    log.info("demand_map.persist_done site=%s %s", site_id, stats)
    return stats


async def load_observed_queries(
    db: AsyncSession, site_id: UUID, *, limit: int = 500
) -> list[tuple[str, int]]:
    """Return recent observed queries for rescoring.

    Pulled from `search_queries` (Yandex Webmaster source). We use
    `wordstat_volume` if populated, otherwise fall back to 0 — the
    current rescoring rule only needs existence. Limit protects the
    rescoring hot path against pathologically large result sets.
    """
    from app.models.search_query import SearchQuery  # local import — avoid cycle

    stmt = (
        select(SearchQuery.query_text, SearchQuery.wordstat_volume)
        .where(SearchQuery.site_id == site_id)
        .order_by(SearchQuery.last_seen_at.desc().nullslast())
        .limit(limit)
    )
    rows = await db.execute(stmt)
    out: list[tuple[str, int]] = []
    for text, vol in rows.all():
        if not text:
            continue
        out.append((str(text), int(vol or 0)))
    return out


__all__ = ["persist_demand_map", "load_observed_queries"]
