"""Orchestrator for the Draft Profile Builder (Phase F).

Pipeline
--------
  1. Load Site + Page (top 50) + observed SearchQuery rows (top 100).
  2. Extract services (service_extractor).
  3. Extract geos (geo_extractor).
  4. Propose competitor brands (LLM, fail-open).
  5. Compute per-field confidences.
  6. Assemble `draft_config` — same shape as `sites.target_config`.
  7. Write the DraftProfile to `sites.target_config_draft`.
  8. Return the DraftProfile.

Idempotent: running twice on the same site overwrites the previous
draft (last-write-wins). `sites.target_config` is never touched.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.draft_profile.competitor_detector import (
    propose_competitor_brands,
)
from app.core_audit.draft_profile.confidence import (
    competitor_brands_confidence,
    geo_primary_confidence,
    geo_secondary_confidence,
    overall_confidence,
    services_confidence,
)
from app.core_audit.draft_profile.dto import (
    DraftProfile,
    GENERATOR_VERSION,
)
from app.core_audit.draft_profile.geo_extractor import extract_geos
from app.core_audit.draft_profile.service_extractor import extract_services
from app.models.page import Page
from app.models.search_query import SearchQuery
from app.models.site import Site


log = logging.getLogger(__name__)


MAX_PAGES = 50
MAX_QUERIES = 100


async def _load_site(db: AsyncSession, site_id: UUID) -> Site | None:
    return await db.get(Site, site_id)


async def _load_pages(db: AsyncSession, site_id: UUID) -> list[Page]:
    """Load up to MAX_PAGES pages for the site.

    Selection heuristic: prefer pages with content_text populated
    (crawled), ordered by word_count desc as a proxy for traffic /
    fingerprint quality (we don't have a join-friendly metric here).
    """
    stmt = (
        select(Page)
        .where(Page.site_id == site_id)
        .order_by(Page.word_count.desc().nullslast())
        .limit(MAX_PAGES)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


async def _load_queries(db: AsyncSession, site_id: UUID) -> list[SearchQuery]:
    stmt = (
        select(SearchQuery)
        .where(SearchQuery.site_id == site_id)
        .limit(MAX_QUERIES)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


def _select_competitor_candidate_queries(
    queries: list[SearchQuery], site_name: str
) -> list[str]:
    """Pick short / branded queries to hand to the LLM detector.

    We feed the model:
      * Every query flagged is_branded=True that does NOT contain the
        site's own display_name.
      * Plus the top short (<= 4 words) queries as exploratory.
    """
    name_lc = (site_name or "").strip().lower()
    candidates: list[str] = []
    seen: set[str] = set()

    for q in queries:
        text = (q.query_text or "").strip().lower()
        if not text or text in seen:
            continue
        is_short = len(text.split()) <= 4
        is_branded = bool(getattr(q, "is_branded", False))
        own_brand_hit = bool(name_lc) and name_lc in text
        if (is_branded and not own_brand_hit) or is_short:
            candidates.append(text)
            seen.add(text)
    return candidates[:40]


def _assemble_draft_config(
    services: list[Any],
    geo: Any,
    competitor_brands: list[Any],
) -> dict[str, Any]:
    """Return a `target_config`-shaped dict ready for JSONB storage."""
    return {
        "services": [s.name for s in services],
        "excluded_services": [],
        "geo_primary": list(geo.primary),
        "geo_secondary": list(geo.secondary),
        "excluded_geo": list(geo.excluded),
        "competitor_brands": [b.name for b in competitor_brands],
        "months": [],
        "day_counts": [],
        "service_weights": {},
        "geo_weights": {},
    }


async def build_draft_profile(
    db: AsyncSession,
    site_id: UUID,
    *,
    competitor_caller: Any = None,
) -> DraftProfile:
    """Build a DraftProfile for the given site and persist it to
    `sites.target_config_draft`.

    Parameters
    ----------
    db:
        Async SQLAlchemy session.
    site_id:
        Target site UUID.
    competitor_caller:
        Optional injected `call_with_tool` callable for the LLM. If
        None, the real Anthropic caller is used in production and
        fail-open behaviour ensures test environments without the SDK
        still succeed.

    Returns
    -------
    DraftProfile
        The generated profile (also persisted to the row).

    Raises
    ------
    LookupError
        If the site_id does not exist.
    """
    t_start = time.monotonic()

    site = await _load_site(db, site_id)
    if site is None:
        raise LookupError(f"site not found: {site_id}")

    pages = await _load_pages(db, site_id)
    queries = await _load_queries(db, site_id)

    # 1. Services.
    services = extract_services(pages)

    # 2. Geos.
    observed_qstrings = [q.query_text for q in queries if q.query_text]
    geo = extract_geos(pages, observed_qstrings)

    # 3. Competitor brands (fail-open).
    candidate_queries = _select_competitor_candidate_queries(
        queries, site.display_name or ""
    )
    try:
        competitor_brands = propose_competitor_brands(
            site.display_name or "",
            site.domain or "",
            candidate_queries,
            caller=competitor_caller,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.warning("draft_profile.competitor.unexpected_err=%s", exc)
        competitor_brands = []

    # 4. Confidences.
    f_services = services_confidence(services)
    f_geo_p = geo_primary_confidence(geo)
    f_geo_s = geo_secondary_confidence(geo)
    f_comp = competitor_brands_confidence(competitor_brands)
    confidences = [f_services, f_geo_p, f_geo_s, f_comp]
    overall = overall_confidence(confidences)

    # 5. Assemble draft_config (target_config-shaped).
    draft_config = _assemble_draft_config(services, geo, competitor_brands)

    # 6. Build DraftProfile.
    gen_ms = int((time.monotonic() - t_start) * 1000)
    profile = DraftProfile(
        site_id=site_id,
        draft_config=draft_config,
        confidences=confidences,
        overall_confidence=overall,
        generated_at=datetime.now(tz=timezone.utc),
        generator_version=GENERATOR_VERSION,
        signals={
            "pages_analyzed": len(pages),
            "queries_analyzed": len(queries),
            "llm_cost_usd": 0.002 if competitor_brands else 0.0,
            "generation_ms": gen_ms,
            "services_count": len(services),
            "geo_primary_count": len(geo.primary),
            "geo_secondary_count": len(geo.secondary),
            "competitor_brands_count": len(competitor_brands),
        },
    )

    # 7. Persist to target_config_draft (never to target_config).
    await db.execute(
        update(Site)
        .where(Site.id == site_id)
        .values(target_config_draft=profile.to_jsonb())
    )
    await db.flush()

    return profile


__all__ = ["build_draft_profile", "MAX_PAGES", "MAX_QUERIES"]
