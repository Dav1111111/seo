"""Celery task — competitor discovery run.

Fetches top money queries for a site, probes each via Yandex Cloud
Search API, aggregates domains, persists result into
`sites.competitor_domains` (plain list of domain strings for the wizard
UI) and `sites.target_config.competitor_profile` (full dict for drill-
down).

Idempotency: `pg_try_advisory_lock` on the site UUID — double-clicks
become no-ops.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy import desc, func, select, text

from app.core_audit.competitors.discovery import (
    DEFAULT_MAX_QUERIES,
    DEFAULT_TOP_K,
    discover_competitors,
)
from app.core_audit.demand_map.models import TargetCluster
from app.models.daily_metric import DailyMetric
from app.models.search_query import SearchQuery
from app.models.site import Site
from app.workers.celery_app import celery_app
from app.workers.db_session import task_session

log = logging.getLogger(__name__)


def _advisory_key(site_id: UUID) -> int:
    """Signed 64-bit int derived from UUID for pg_try_advisory_lock."""
    return int(site_id.hex[:16], 16) - (1 << 63)


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _pick_top_queries(db, site_id: UUID, limit: int) -> list[str]:
    """Best-effort source ranking for 'which queries to probe SERP for'.

    Priority:
      1. Observed queries (from Webmaster) that actually brought impressions
         in the last 14 days — these are guaranteed real demand, not our
         guesses.
      2. If that's empty or too small, fall back to top TargetClusters by
         business_relevance — these may include queries we target but don't
         yet rank on, which is useful for early-stage sites with no traffic.
    """
    from datetime import date, timedelta
    since = date.today() - timedelta(days=14)

    # 1) Observed queries with recent impressions.
    # DailyMetric.dimension_id = SearchQuery.id for metric_type='query_performance'
    # (see webmaster collector). Join on UUID, aggregate impressions, sort desc.
    stmt = (
        select(
            SearchQuery.query_text,
            func.coalesce(func.sum(DailyMetric.impressions), 0).label("imp_sum"),
        )
        .join(
            DailyMetric,
            (DailyMetric.site_id == SearchQuery.site_id)
            & (DailyMetric.dimension_id == SearchQuery.id)
            & (DailyMetric.metric_type == "query_performance")
            & (DailyMetric.date >= since),
        )
        .where(SearchQuery.site_id == site_id, SearchQuery.is_branded.is_(False))
        .group_by(SearchQuery.id, SearchQuery.query_text)
        .having(func.coalesce(func.sum(DailyMetric.impressions), 0) > 0)
        .order_by(desc("imp_sum"))
        .limit(limit)
    )
    try:
        rows = (await db.execute(stmt)).all()
    except Exception as exc:  # noqa: BLE001 — schema may lack join col
        log.warning("competitors.observed_query_pick_failed err=%s", exc)
        rows = []

    observed = [r.query_text for r in rows if r.query_text]

    if len(observed) >= max(5, limit // 2):
        return observed

    # 2) Fallback — top clusters by relevance. Use cluster name_ru as the
    # search string (it was already normalised by the expander).
    need = limit - len(observed)
    cl_stmt = (
        select(TargetCluster.name_ru)
        .where(
            TargetCluster.site_id == site_id,
            TargetCluster.quality_tier.in_(("core", "secondary")),
            TargetCluster.is_brand.is_(False),
            TargetCluster.is_competitor_brand.is_(False),
        )
        .order_by(desc(TargetCluster.business_relevance))
        .limit(need * 2)  # extra so we can de-dupe against observed
    )
    extras = [r[0] for r in await db.execute(cl_stmt)]
    merged: list[str] = list(observed)
    seen = {q.lower() for q in merged}
    for q in extras:
        lq = q.lower()
        if lq in seen:
            continue
        merged.append(q)
        seen.add(lq)
        if len(merged) >= limit:
            break
    return merged[:limit]


@celery_app.task(name="competitors_discover_site", bind=True, max_retries=0)
def competitors_discover_site_task(
    self,
    site_id: str,
    max_queries: int = DEFAULT_MAX_QUERIES,
    top_k: int = DEFAULT_TOP_K,
) -> dict:
    """Discover competitors for one site via SERP. Returns a summary dict."""

    async def _inner() -> dict:
        try:
            async with task_session() as db:
                lock_key = _advisory_key(UUID(site_id))
                locked = (await db.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key},
                )).scalar_one()
                if not locked:
                    return {
                        "status": "skipped",
                        "reason": "concurrent_run",
                        "site_id": site_id,
                    }
                try:
                    site = await db.get(Site, UUID(site_id))
                    if site is None:
                        return {"status": "skipped", "reason": "site_not_found"}

                    queries = await _pick_top_queries(db, site.id, max_queries)
                    if not queries:
                        return {
                            "status": "skipped",
                            "reason": "no_queries_available",
                            "site_id": site_id,
                        }

                    profile = discover_competitors(
                        own_domain=site.domain,
                        queries=queries,
                        max_queries=max_queries,
                        top_k=top_k,
                        site_id=site_id,
                    )

                    # Persist: plain list of domains for the UI + full profile
                    # under target_config for drill-down.
                    site.competitor_domains = [c.domain for c in profile.competitors]
                    cfg = dict(site.target_config or {})
                    cfg["competitor_profile"] = profile.to_jsonb()
                    # Clear LLM-hallucinated brand list — it's being superseded
                    # by the real SERP-derived list. Caller can re-add true
                    # known brands manually from the wizard.
                    cfg["competitor_brands"] = []
                    site.target_config = cfg

                    await db.commit()
                    return {
                        "status": "ok",
                        "site_id": site_id,
                        "queries_probed": profile.queries_probed,
                        "queries_with_results": profile.queries_with_results,
                        "competitors_found": len(profile.competitors),
                        "top3": [c.domain for c in profile.competitors[:3]],
                        "cost_usd": round(profile.cost_usd, 4),
                        "errors": profile.errors,
                    }
                finally:
                    await db.execute(
                        text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key},
                    )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "competitors.discover.task_failed site=%s err=%s",
                site_id, exc,
            )
            return {"status": "error", "site_id": site_id, "err": str(exc)}

    return _run(_inner())


__all__ = ["competitors_discover_site_task"]
