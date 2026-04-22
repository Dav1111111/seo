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

from app.core_audit.business_truth.dto import BusinessTruth, DirectionKey
from app.core_audit.business_truth.page_intent import extract_page_intents
from app.core_audit.business_truth.reconciler import reconcile
from app.core_audit.business_truth.traffic_reader import (
    load_traffic_distribution,
)
from app.core_audit.business_truth.understanding_reader import (
    read_understanding,
)
from app.models.page import Page
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
    services, geos = _flatten_vocab(cfg)

    # 1. Understanding — owner weights
    u_weights = dict(read_understanding(
        site.understanding or {}, cfg,
    ))  # list[(key, w)] → dict

    # 2. Content — classify crawled pages
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

    # Traffic queries per key — for evidence display we'd need to keep
    # a reverse map. TrafficDistribution only stores weights today;
    # leave queries empty and extend later if UI needs sample queries.
    traffic_queries: dict[DirectionKey, tuple[str, ...]] = {}

    sources_used = {
        "understanding": sum(1 for _ in u_weights),
        "content": sum(len(v) for v in content_pages.values()),
        "traffic": traffic.total_impressions,
    }

    truth = reconcile(
        understanding_weights=u_weights,
        content_pages=content_pages,
        traffic_weights=traffic.direction_weights,
        traffic_queries=traffic_queries,
        sources_used=sources_used,
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
