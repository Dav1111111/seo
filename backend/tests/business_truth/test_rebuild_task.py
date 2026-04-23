"""Integration: rebuild_business_truth orchestrator.

The task pulls all three sources from DB, reconciles, persists JSONB
on sites.target_config.business_truth. Covers both happy path and
several edge cases (no understanding, no pages, no traffic).
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models.daily_metric import DailyMetric
from app.models.page import Page
from app.models.search_query import SearchQuery
from app.models.site import Site


async def _seed_pages(db, site, urls_titles):
    for url, title in urls_titles:
        db.add(Page(
            site_id=site.id,
            url=url,
            path=url.replace("https://example.com", ""),
            title=title,
            h1=title,
            content_text="",
            in_index=True,
        ))
    await db.flush()


async def _seed_queries(db, site, query_imps):
    today = date.today()
    for q, imp in query_imps:
        sq = SearchQuery(
            id=uuid.uuid4(), site_id=site.id,
            query_text=q, is_branded=False,
        )
        db.add(sq)
        await db.flush()
        db.add(DailyMetric(
            site_id=site.id, date=today,
            metric_type="query_performance",
            dimension_id=sq.id,
            impressions=imp, clicks=0,
        ))
    await db.flush()


async def test_rebuild_happy_path_all_three_sources_align(db, test_site: Site):
    """Owner declares (багги, абхазия) + page exists + traffic flows →
    confirmed direction, no divergence."""
    from app.core_audit.business_truth.rebuild import rebuild_business_truth

    test_site.target_config = {
        "services": ["багги"],
        "geo_primary": ["абхазия"],
    }
    await _seed_pages(db, test_site, [
        ("https://example.com/abkhazia/", "Багги Абхазия туры"),
    ])
    await _seed_queries(db, test_site, [
        ("багги абхазия", 1000),
    ])

    truth = await rebuild_business_truth(db, test_site.id)

    assert len(truth.directions) == 1
    d = truth.directions[0]
    assert d.key.service == "багги"
    assert d.key.geo == "абхазия"
    assert d.is_confirmed
    assert d.divergence_ru() is None


async def test_rebuild_surfaces_blind_spot(db, test_site: Site):
    """User's Sochi case: page + understanding present, zero traffic."""
    from app.core_audit.business_truth.rebuild import rebuild_business_truth

    test_site.target_config = {
        "services": ["багги"],
        "geo_primary": ["абхазия", "сочи"],
    }
    await _seed_pages(db, test_site, [
        ("https://example.com/abkhazia/", "Багги Абхазия"),
        ("https://example.com/sochi/",    "Багги Сочи"),
    ])
    # Only Abkhazia gets traffic
    await _seed_queries(db, test_site, [
        ("багги абхазия", 1000),
    ])

    truth = await rebuild_business_truth(db, test_site.id)
    blind = truth.blind_spots()
    blind_keys = {(d.key.service, d.key.geo) for d in blind}
    assert ("багги", "сочи") in blind_keys


async def test_rebuild_surfaces_traffic_only(db, test_site: Site):
    """Traffic arrives on 'багги адлер' but no page and no declared geo."""
    from app.core_audit.business_truth.rebuild import rebuild_business_truth

    test_site.target_config = {
        "services": ["багги"],
        "geo_primary": ["абхазия"],
        "geo_secondary": ["адлер"],  # declared but no page
    }
    await _seed_pages(db, test_site, [
        ("https://example.com/abkhazia/", "Багги Абхазия"),
    ])
    await _seed_queries(db, test_site, [
        ("багги абхазия", 800),
        ("багги адлер",   200),
    ])

    truth = await rebuild_business_truth(db, test_site.id)
    traffic_only = truth.traffic_only()
    keys = {(d.key.service, d.key.geo) for d in traffic_only}
    assert ("багги", "адлер") in keys


async def test_rebuild_with_empty_config_returns_empty_truth(db, test_site: Site):
    """No target_config → empty directions, but sources_used still tracked."""
    from app.core_audit.business_truth.rebuild import rebuild_business_truth

    test_site.target_config = {}
    truth = await rebuild_business_truth(db, test_site.id)
    assert truth.directions == []
    assert truth.sources_used["understanding"] == 0


async def test_rebuild_persists_to_target_config_jsonb(db, test_site: Site):
    """Verify that after rebuild the site's target_config contains the
    serialized business_truth blob. Needs ≥2 pages AND ≥1 matching
    query — auto-vocab now requires query trace to avoid page-chrome
    tokens becoming fake services."""
    from app.core_audit.business_truth.rebuild import rebuild_business_truth

    test_site.target_config = {
        "services": ["багги"],
        "geo_primary": ["абхазия"],
    }
    await _seed_pages(db, test_site, [
        ("https://example.com/a/", "Багги Абхазия"),
        ("https://example.com/b/", "Багги туры Абхазия"),
    ])
    # Query trace required by new auto-vocab rule
    await _seed_queries(db, test_site, [("багги абхазия", 100)])

    await rebuild_business_truth(db, test_site.id, persist=True)
    await db.refresh(test_site)

    bt = (test_site.target_config or {}).get("business_truth")
    assert bt is not None
    assert "directions" in bt
    assert bt["sources_used"]["content"] >= 1
