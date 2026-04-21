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


def _business_tokens(target_config: dict) -> set[str]:
    """Union of service + geo tokens from target_config (lowercased).

    Used as a relevance gate for observed queries — a query must contain
    at least one of these tokens to count as "money". This drops noise
    like "polaris slingshot" that some user once typed and accidentally
    landed on the site.
    """
    tokens: set[str] = set()
    for key in ("services", "secondary_products", "geo_primary", "geo_secondary"):
        for v in (target_config or {}).get(key) or []:
            # split multi-word entries into tokens so "морские прогулки"
            # matches queries containing either "морские" or "прогулки"
            for t in str(v).lower().split():
                t = t.strip(".,!?«»\"'()[]{}")
                if len(t) >= 3:
                    tokens.add(t)
    return tokens


def _query_is_relevant(query: str, biz_tokens: set[str]) -> bool:
    """True if query contains at least one business token."""
    if not biz_tokens:
        return True  # no profile yet — accept everything
    q = query.lower()
    return any(tok in q for tok in biz_tokens)


async def _pick_top_queries(
    db, site_id: UUID, limit: int, *, biz_tokens: set[str] | None = None,
) -> list[str]:
    """Best-effort source ranking for 'which queries to probe SERP for'.

    Priority:
      1. Observed queries (from Webmaster) that actually brought impressions
         in the last 14 days AND contain at least one business token from
         target_config. The business-token filter drops random queries
         like 'polaris slingshot' that some visitor once typed and
         accidentally landed on the site.
      2. If that's empty or too small, fall back to top TargetClusters by
         business_relevance — these may include queries we target but
         don't yet rank on, useful for early-stage sites with no traffic.
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
        # pull a wide window then filter by business tokens in Python
        .limit(limit * 5)
    )
    try:
        rows = (await db.execute(stmt)).all()
    except Exception as exc:  # noqa: BLE001 — schema may lack join col
        log.warning("competitors.observed_query_pick_failed err=%s", exc)
        rows = []

    tokens = biz_tokens or set()
    observed = [
        r.query_text for r in rows
        if r.query_text and _query_is_relevant(r.query_text, tokens)
    ][:limit]

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

                    queries = await _pick_top_queries(
                        db, site.id, max_queries,
                        biz_tokens=_business_tokens(site.target_config or {}),
                    )
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


@celery_app.task(name="competitors_deep_dive_site", bind=True, max_retries=0)
def competitors_deep_dive_site_task(self, site_id: str) -> dict:
    """Crawl top competitor sites and write a structural comparison.

    Uses the persisted competitor_profile.competitors list — for each
    top competitor, visits the homepage + the example_url captured
    during discovery, then aggregates structural signals (price, CTA,
    reviews, schema types). Also analyzes the OWN site with the same
    extractor so the UI can show an apples-to-apples diff.

    Persists to sites.target_config.competitor_deep_dive (list of site
    reports, plus a 'self' entry).
    """
    from app.core_audit.competitors.deep_dive import (
        analyze_competitor_site,
        analyze_page,
    )

    async def _inner() -> dict:
        try:
            async with task_session() as db:
                site = await db.get(Site, UUID(site_id))
                if site is None:
                    return {"status": "skipped", "reason": "site_not_found"}

                cfg = dict(site.target_config or {})
                profile = cfg.get("competitor_profile") or {}
                competitors = profile.get("competitors") or []
                if not competitors:
                    return {
                        "status": "skipped",
                        "reason": "no_competitor_profile",
                        "site_id": site_id,
                    }

                # Top 5 competitors — crawl each (homepage + example URL)
                reports: list[dict] = []
                for c in competitors[:5]:
                    domain = c.get("domain")
                    example_url = c.get("example_url") or ""
                    if not domain:
                        continue
                    rep = analyze_competitor_site(
                        domain=domain,
                        urls=[example_url] if example_url else [],
                        max_pages=2,
                    )
                    reports.append(rep.to_dict())

                # Own site — homepage only, enough for feature-by-feature diff
                own_url = f"https://{site.domain.removeprefix('www.')}/"
                own_page = analyze_page(own_url).to_dict()

                cfg["competitor_deep_dive"] = {
                    "competitors": reports,
                    "self": own_page,
                }
                site.target_config = cfg
                await db.commit()

                return {
                    "status": "ok",
                    "site_id": site_id,
                    "competitors_crawled": len(reports),
                    "successful_pages": sum(
                        1 for r in reports
                        for p in r.get("pages", [])
                        if p.get("status") == "ok"
                    ),
                }
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "competitors.deep_dive.task_failed site=%s err=%s",
                site_id, exc,
            )
            return {"status": "error", "site_id": site_id, "err": str(exc)}

    return _run(_inner())


__all__ = [
    "competitors_discover_site_task",
    "competitors_deep_dive_site_task",
]
