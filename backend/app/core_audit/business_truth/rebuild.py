"""Orchestrator — rebuild BusinessTruth from 3 DB sources and persist.

Pure composition layer: pulls understanding + pages + Webmaster queries,
calls the 3 readers + reconciler, optionally writes the JSONB back on
sites.target_config.business_truth.

No Celery decorator here — that's in task.py. Keeping business logic
pure makes it unit-testable via the async `db` fixture without a
worker.
"""

from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.business_truth.auto_vocabulary import (
    derive_vocabulary_from_data,
)
from app.core_audit.business_truth.dto import BusinessTruth, DirectionKey
from app.core_audit.business_truth.page_intent import extract_page_intents
from app.core_audit.business_truth.reconciler import reconcile
from app.core_audit.business_truth.traffic_reader import (
    load_traffic_distribution,
)
from app.core_audit.business_truth.understanding_reader import (
    read_understanding,
)
from app.models.daily_metric import DailyMetric
from app.models.page import Page
from app.models.search_query import SearchQuery
from app.models.site import Site


def _flatten_vocab(cfg: dict) -> tuple[set[str], set[str]]:
    """Collect services/geos for the classifier from target_config."""
    services = set()
    for key in ("services", "secondary_products"):
        for v in (cfg or {}).get(key) or []:
            if v:
                services.add(str(v).strip().lower())
    geos = set()
    for key in ("geo_primary", "geo_secondary"):
        for v in (cfg or {}).get(key) or []:
            if v:
                geos.add(str(v).strip().lower())
    return services, geos


async def _build_content_map(
    db: AsyncSession,
    site_id: uuid.UUID,
    services: set[str],
    geos: set[str],
) -> dict[DirectionKey, list[str]]:
    """Classify every crawled page with a title into direction keys.

    Filter is loose (title not null) — `in_index` is Yandex's
    indexation confirmation, often False right after crawl; page_match
    and now business_truth both treat a title as sufficient signal.
    """
    stmt = select(
        Page.url, Page.path, Page.title, Page.h1,
        Page.meta_description, Page.content_text,
    ).where(Page.site_id == site_id, Page.title.is_not(None))
    rows = (await db.execute(stmt)).all()

    content_map: dict[DirectionKey, list[str]] = {}
    for r in rows:
        page_dict = {
            "url": r.url,
            "path": r.path,
            "title": r.title,
            "h1": r.h1,
            "meta_description": r.meta_description,
            "content_snippet": (r.content_text or "")[:500],
        }
        keys = extract_page_intents(page_dict, services, geos)
        for k in keys:
            content_map.setdefault(k, []).append(r.url)
    return content_map


async def rebuild_business_truth(
    db: AsyncSession,
    site_id: uuid.UUID,
    *,
    persist: bool = False,
    traffic_days_back: int = 30,
) -> BusinessTruth:
    """Pull 3 sources from DB → reconcile → (optionally) persist.

    Returns the BusinessTruth regardless of persist flag so callers
    can read without writing (useful for preview in UI).
    """
    site = await db.get(Site, site_id)
    if site is None:
        raise ValueError(f"site {site_id} not found")

    cfg = dict(site.target_config or {})

    # ── Auto-derive vocabulary from pages + queries, not from
    # target_config. Onboarding LLMs hallucinated "экскурсии" and
    # "туры" for Grand Tour that were never real services. We now
    # trust what's ACTUALLY on the site + what traffic ACTUALLY
    # searches. target_config.services stays as a secondary seed —
    # it contributes to understanding_weights only when it overlaps
    # with the auto-derived vocab.

    # 1a. Load pages for vocab derivation
    page_stmt = select(
        Page.url, Page.path, Page.title, Page.h1,
        Page.meta_description, Page.content_text,
    ).where(Page.site_id == site_id, Page.title.is_not(None))
    page_rows = (await db.execute(page_stmt)).all()
    page_dicts_for_vocab = [
        {
            "url": r.url, "path": r.path,
            "title": r.title, "h1": r.h1,
            "meta_description": r.meta_description,
            "content_snippet": (r.content_text or "")[:500],
        }
        for r in page_rows
    ]

    # 1b. Load queries for vocab derivation
    from datetime import date, timedelta
    from sqlalchemy import func
    since = date.today() - timedelta(days=traffic_days_back)
    q_stmt = (
        select(
            SearchQuery.query_text,
            func.coalesce(func.sum(DailyMetric.impressions), 0).label("imp"),
        )
        .join(
            DailyMetric,
            (DailyMetric.site_id == SearchQuery.site_id)
            & (DailyMetric.dimension_id == SearchQuery.id)
            & (DailyMetric.metric_type == "query_performance")
            & (DailyMetric.date >= since),
        )
        .where(
            SearchQuery.site_id == site_id,
            SearchQuery.is_branded.is_(False),
        )
        .group_by(SearchQuery.id, SearchQuery.query_text)
    )
    q_rows = (await db.execute(q_stmt)).all()
    queries_for_vocab = [(r.query_text, int(r.imp or 0)) for r in q_rows]

    # 1c. Derive vocabulary (site_domain passed so brand tokens like
    # "grand", "tour", "spirit", "gts" from "grandtourspirit.ru" get
    # filtered from the service candidates).
    auto_vocab = derive_vocabulary_from_data(
        page_dicts_for_vocab, queries_for_vocab,
        site_domain=site.domain,
    )
    services = auto_vocab["services"]
    geos = auto_vocab["geos"]

    # 2. Understanding — owner weights (only for services/geos that
    # the auto-derived vocab confirms — silent drops target_config
    # entries that aren't backed by site reality).
    cfg_filtered = dict(cfg)
    cfg_filtered["services"] = [s for s in (cfg.get("services") or []) if s and s.lower() in services]
    cfg_filtered["secondary_products"] = [s for s in (cfg.get("secondary_products") or []) if s and s.lower() in services]
    cfg_filtered["geo_primary"] = [g for g in (cfg.get("geo_primary") or []) if g and g.lower() in geos]
    cfg_filtered["geo_secondary"] = [g for g in (cfg.get("geo_secondary") or []) if g and g.lower() in geos]
    u_weights = dict(read_understanding(
        site.understanding or {}, cfg_filtered,
    ))

    # 2b. Content — classify crawled pages with the AUTO vocabulary
    if services and geos:
        content_map = await _build_content_map(db, site_id, services, geos)
    else:
        content_map = {}
    content_pages = {
        k: tuple(urls) for k, urls in content_map.items()
    }

    # 3. Traffic — classify Webmaster queries
    if services and geos:
        traffic = await load_traffic_distribution(
            db, site_id,
            services=services, geos=geos,
            days_back=traffic_days_back,
        )
    else:
        from app.core_audit.business_truth.traffic_reader import TrafficDistribution
        traffic = TrafficDistribution({}, 0, 0)

    # Item 3: TrafficDistribution now carries the reverse map. We keep
    # top 10 queries per direction to bound blob size.
    traffic_queries: dict[DirectionKey, tuple[str, ...]] = {
        k: tuple(qs[:10])
        for k, qs in traffic.queries_per_direction.items()
    }

    sources_used = {
        "understanding": sum(1 for _ in u_weights),
        "content": sum(len(v) for v in content_pages.values()),
        "traffic": traffic.total_impressions,
    }

    # Item 4: unclassified diagnostics travel with the truth.
    truth = reconcile(
        understanding_weights=u_weights,
        content_pages=content_pages,
        traffic_weights=traffic.direction_weights,
        traffic_queries=traffic_queries,
        sources_used=sources_used,
        top_unclassified_queries=list(traffic.unclassified_queries[:20]),
        unclassified_share=(
            1.0 - traffic.coverage_share
            if traffic.total_impressions > 0 else 0.0
        ),
    )

    if persist:
        cfg["business_truth"] = truth.to_jsonb()
        # Also remember unclassified-traffic share — this is a
        # diagnostic the UI renders as "X% of your search traffic
        # lies outside your declared services/geos".
        cfg["business_truth"]["traffic_coverage"] = {
            "total_impressions": traffic.total_impressions,
            "unclassified_impressions": traffic.unclassified_impressions,
            "coverage_share": round(traffic.coverage_share, 3),
        }
        site.target_config = cfg
        await db.commit()
        await db.refresh(site)

    return truth


__all__ = ["rebuild_business_truth"]
