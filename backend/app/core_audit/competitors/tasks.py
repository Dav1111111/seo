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

from app.core_audit.activity import log_event
from app.core_audit.competitors.discovery import (
    DEFAULT_MAX_QUERIES,
    DEFAULT_TOP_K,
    discover_competitors,
)
from app.core_audit.demand_map.models import TargetCluster
from app.models.daily_metric import DailyMetric
from app.models.page import Page
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
                        await log_event(
                            db, site_id, "competitor_discovery", "skipped",
                            "Нет запросов для разведки — сначала запусти сбор из Вебмастера.",
                        )
                        return {
                            "status": "skipped",
                            "reason": "no_queries_available",
                            "site_id": site_id,
                        }

                    await log_event(
                        db, site_id, "competitor_discovery", "started",
                        f"Ищу конкурентов в Яндекс-выдаче по {len(queries)} запросам…",
                        extra={"queries_count": len(queries)},
                    )

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

                    await log_event(
                        db, site_id, "competitor_discovery", "done",
                        (
                            f"Разведка готова: найдено {len(profile.competitors)} "
                            f"конкурентов по {profile.queries_probed} запросам."
                        ),
                        extra={
                            "competitors_found": len(profile.competitors),
                            "top3": [c.domain for c in profile.competitors[:3]],
                            "cost_usd": round(profile.cost_usd, 4),
                        },
                    )

                    # Chain: discovery done → fire deep-dive automatically
                    # so the comparison table and growth opportunities are
                    # ready without the user clicking a second button.
                    # Skip the chain if we found nothing — deep-dive would
                    # have nothing to crawl.
                    if profile.competitors:
                        try:
                            competitors_deep_dive_site_task.delay(site_id)
                        except Exception as exc:  # noqa: BLE001
                            log.warning(
                                "competitors.discovery.chain_dive_failed "
                                "site=%s err=%s", site_id, exc,
                            )

                    return {
                        "status": "ok",
                        "site_id": site_id,
                        "queries_probed": profile.queries_probed,
                        "queries_with_results": profile.queries_with_results,
                        "competitors_found": len(profile.competitors),
                        "top3": [c.domain for c in profile.competitors[:3]],
                        "cost_usd": round(profile.cost_usd, 4),
                        "errors": profile.errors,
                        "deep_dive_queued": bool(profile.competitors),
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
    from app.core_audit.competitors.content_gap import analyze_gaps
    from app.core_audit.competitors.deep_dive import (
        analyze_competitor_site,
        analyze_page,
    )
    from app.core_audit.competitors.opportunities import build_growth_opportunities

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

                await log_event(
                    db, site_id, "competitor_deep_dive", "started",
                    f"Глубокий анализ: читаю сайты {min(5, len(competitors))} конкурентов…",
                )

                # Top 5 competitors — crawl in parallel so one slow site
                # (findgid.ru observed at 10s while 4 others finished in 3s)
                # doesn't block the pipeline.
                from concurrent.futures import ThreadPoolExecutor, as_completed

                targets = [c for c in competitors[:5] if c.get("domain")]
                reports_by_domain: dict[str, dict] = {}

                def _run_one(c: dict) -> tuple[str, dict]:
                    rep = analyze_competitor_site(
                        domain=c["domain"],
                        urls=[c.get("example_url") or ""] if c.get("example_url") else [],
                        max_pages=2,
                    )
                    return c["domain"], rep.to_dict()

                with ThreadPoolExecutor(max_workers=5) as pool:
                    fut_map = {pool.submit(_run_one, c): c for c in targets}
                    # Also kick off the own-site crawl alongside the
                    # competitors — it's just one more HTTP fetch.
                    own_url = f"https://{site.domain.removeprefix('www.')}/"
                    own_future = pool.submit(
                        lambda: analyze_page(own_url).to_dict(),
                    )
                    done_count = 0
                    for fut in as_completed(fut_map):
                        domain, rep_dict = fut.result()
                        reports_by_domain[domain] = rep_dict
                        done_count += 1
                        await log_event(
                            db, site_id, "competitor_deep_dive", "progress",
                            f"Готов {domain} ({done_count}/{len(targets)})…",
                        )
                    own_page = own_future.result()

                # Preserve original ranking order when emitting reports.
                reports = [
                    reports_by_domain[c["domain"]]
                    for c in targets
                    if c["domain"] in reports_by_domain
                ]

                cfg["competitor_deep_dive"] = {
                    "competitors": reports,
                    "self": own_page,
                }

                # Build growth opportunities from (cached gaps + fresh deep-dive).
                # Gaps come from the per-query SERP cache captured by discovery.
                query_serps = profile.get("query_serps") or {}
                gap_dicts = []
                if query_serps:
                    gaps = analyze_gaps(
                        own_domain=site.domain,
                        competitor_domains=list(site.competitor_domains or []),
                        query_to_serp=query_serps,
                        top_k_gaps=25,
                    )
                    gap_dicts = [g.to_dict() for g in gaps]

                # Pull our own crawled pages so we can check whether the
                # site already has a page for each gap query. This turns
                # "create new page" into "strengthen existing page" when
                # we already cover the topic.
                # Filter is intentionally loose: in_index comes from Yandex
                # indexation polling and is often False for freshly crawled
                # pages. A title is the minimal signal we need for matching.
                page_stmt = select(
                    Page.url, Page.path, Page.title, Page.h1,
                    Page.meta_description, Page.content_text,
                ).where(Page.site_id == site.id, Page.title.is_not(None))
                page_rows = (await db.execute(page_stmt)).all()
                own_pages_dicts = [
                    {
                        "url": r.url,
                        "path": r.path,
                        "title": r.title,
                        "h1": r.h1,
                        "meta_description": r.meta_description,
                        "content_snippet": (r.content_text or "")[:600],
                    }
                    for r in page_rows
                ]

                opportunities = build_growth_opportunities(
                    content_gaps=gap_dicts,
                    deep_dive_self=own_page,
                    deep_dive_competitors=reports,
                    own_pages=own_pages_dicts,
                    max_items=15,
                )
                cfg["growth_opportunities"] = opportunities
                site.target_config = cfg
                await db.commit()

                await log_event(
                    db, site_id, "opportunities", "done",
                    (
                        f"Готово: {len(opportunities)} точек роста, "
                        f"проверено {len(own_pages_dicts)} твоих страниц."
                    ),
                    extra={
                        "opportunities": len(opportunities),
                        "own_pages": len(own_pages_dicts),
                        "competitors_crawled": len(reports),
                    },
                )

                return {
                    "status": "ok",
                    "site_id": site_id,
                    "competitors_crawled": len(reports),
                    "successful_pages": sum(
                        1 for r in reports
                        for p in r.get("pages", [])
                        if p.get("status") == "ok"
                    ),
                    "opportunities_generated": len(opportunities),
                    "own_pages_scanned": len(own_pages_dicts),
                }
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "competitors.deep_dive.task_failed site=%s err=%s",
                site_id, exc,
            )
            return {"status": "error", "site_id": site_id, "err": str(exc)}

    return _run(_inner())


@celery_app.task(name="competitors_discover_all_weekly", bind=True, max_retries=0)
def competitors_discover_all_weekly_task(self) -> dict:
    """Weekly refresh of competitor lists + deep-dive for every active site.

    Loops over active sites, queues discovery with a 3-minute gap between
    sites so the shared SERP + Haiku quotas don't spike. Discovery
    auto-chains deep-dive, so this one task refreshes both halves of the
    competitor picture for every site.
    """
    async def _inner() -> dict:
        try:
            async with task_session() as db:
                result = await db.execute(
                    select(Site.id, Site.domain).where(Site.is_active.is_(True)),
                )
                rows = result.all()

            queued: list[str] = []
            for i, row in enumerate(rows):
                competitors_discover_site_task.apply_async(
                    args=[str(row.id)],
                    countdown=i * 180,
                )
                queued.append(row.domain)
            return {"status": "ok", "queued": queued}
        except Exception as exc:  # noqa: BLE001
            log.warning("competitors.weekly_all.failed err=%s", exc)
            return {"status": "error", "err": str(exc)}

    return _run(_inner())


__all__ = [
    "competitors_discover_site_task",
    "competitors_deep_dive_site_task",
    "competitors_discover_all_weekly_task",
]
