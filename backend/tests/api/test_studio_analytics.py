"""Studio /analytics endpoint — IMPLEMENTATION.md §3.3.

The merging logic across three metric_types is the highest-risk
piece of this module:

  - Webmaster `query_performance` (impressions/clicks/avg_position)
  - Webmaster `indexing` (pages_indexed in dedicated column)
  - Metrica `site_traffic` (visits/pageviews/bounce_rate/avg_duration
    in dedicated columns — NOT in `extra` JSONB)

If a future refactor reads `pages_indexed` from `impressions` again,
or chases visits through `extra`, these synthetic-row tests fail
loudly. That's the regression net for P0 #1 and P0 #2.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.studio import (
    ANALYTICS_DEFAULT_DAYS,
    ANALYTICS_MAX_DAYS,
    get_analytics,
)
from app.models.daily_metric import DailyMetric
from app.models.site import Site


pytestmark = pytest.mark.asyncio


async def test_get_analytics_empty_site(
    db: AsyncSession, test_site: Site,
) -> None:
    """No daily_metrics rows → empty series, zero totals, lag dates None."""
    resp = await get_analytics(site_id=test_site.id, days=90, db=db)
    assert resp.series == []
    assert resp.totals.impressions_sum == 0
    assert resp.totals.clicks_sum == 0
    assert resp.totals.visits_sum == 0
    assert resp.totals.indexed_latest is None
    assert resp.webmaster_latest_date is None
    assert resp.metrica_latest_date is None


async def test_get_analytics_404_for_unknown_site(db: AsyncSession) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await get_analytics(site_id=uuid.uuid4(), days=90, db=db)
    assert exc.value.status_code == 404


def test_analytics_days_bounds() -> None:
    """`days` is FastAPI-validated (ge=7, le=365). Pin the constants so
    a refactor that loosens those bounds without docs trips here."""
    assert ANALYTICS_DEFAULT_DAYS == 90
    assert ANALYTICS_MAX_DAYS == 365


async def test_get_analytics_reads_pages_indexed_from_dedicated_column(
    db: AsyncSession, test_site: Site,
) -> None:
    """Regression for P0 #1: the writer (collectors/webmaster.py:286-299)
    puts the count into `pages_indexed`, leaving `impressions=0`. Reading
    from `impressions` would surface 0 silently. We seed a row with
    impressions=0 and pages_indexed=42; result must show 42."""
    today = date.today()
    db.add(DailyMetric(
        site_id=test_site.id,
        date=today,
        metric_type="indexing",
        dimension_id=None,
        impressions=0,        # writer leaves this at default
        pages_indexed=42,     # this is the canonical source
    ))
    await db.flush()

    resp = await get_analytics(site_id=test_site.id, db=db, days=30)
    pts = [p for p in resp.series if p.pages_indexed is not None]
    assert len(pts) == 1
    assert pts[0].pages_indexed == 42
    assert resp.totals.indexed_latest == 42


async def test_get_analytics_reads_metrica_from_dedicated_columns(
    db: AsyncSession, test_site: Site,
) -> None:
    """Regression for P0 #2: MetricaCollector writes visits/pageviews/
    bounce_rate/avg_duration to dedicated columns (collectors/metrica.py:
    131-148). Reading via `extra` JSONB or falling back to
    `impressions` would silently zero out the chart."""
    today = date.today()
    db.add(DailyMetric(
        site_id=test_site.id,
        date=today,
        metric_type="site_traffic",
        dimension_id=None,
        impressions=0,         # NOT used by Metrica
        clicks=0,              # NOT used by Metrica
        visits=1234,
        pageviews=5678,
        bounce_rate=0.42,
        avg_duration=95.5,
        extra={},              # explicitly empty — must NOT be the source
    ))
    await db.flush()

    resp = await get_analytics(site_id=test_site.id, db=db, days=30)
    assert len(resp.series) == 1
    pt = resp.series[0]
    assert pt.visits == 1234
    assert pt.pageviews == 5678
    assert pt.bounce_rate == pytest.approx(0.42, rel=1e-3)
    assert pt.avg_duration_sec == pytest.approx(95.5, rel=1e-3)
    assert resp.totals.visits_sum == 1234
    assert resp.totals.pageviews_sum == 5678


async def test_get_analytics_merges_three_sources_by_date(
    db: AsyncSession, test_site: Site,
) -> None:
    """Three rows for the same date — one per metric_type — must merge
    into a single AnalyticsPoint with all four buckets populated."""
    today = date.today()
    db.add_all([
        # Webmaster query_performance — aggregated across queries.
        DailyMetric(
            site_id=test_site.id,
            date=today,
            metric_type="query_performance",
            dimension_id=uuid.uuid4(),
            impressions=100,
            clicks=10,
            avg_position=4.5,
        ),
        # Webmaster indexing.
        DailyMetric(
            site_id=test_site.id,
            date=today,
            metric_type="indexing",
            dimension_id=None,
            pages_indexed=50,
        ),
        # Metrica.
        DailyMetric(
            site_id=test_site.id,
            date=today,
            metric_type="site_traffic",
            dimension_id=None,
            visits=20,
            pageviews=80,
            bounce_rate=0.3,
            avg_duration=60.0,
        ),
    ])
    await db.flush()

    resp = await get_analytics(site_id=test_site.id, db=db, days=30)
    assert len(resp.series) == 1
    pt = resp.series[0]
    assert pt.impressions == 100
    assert pt.clicks == 10
    assert pt.avg_position == pytest.approx(4.5, rel=1e-3)
    assert pt.pages_indexed == 50
    assert pt.visits == 20
    assert pt.pageviews == 80
    # Both lag indicators reflect today.
    assert resp.webmaster_latest_date == today.isoformat()
    assert resp.metrica_latest_date == today.isoformat()


async def test_get_analytics_respects_days_window(
    db: AsyncSession, test_site: Site,
) -> None:
    """A row 100 days old must NOT appear when days=30."""
    old = date.today() - timedelta(days=100)
    db.add(DailyMetric(
        site_id=test_site.id,
        date=old,
        metric_type="indexing",
        dimension_id=None,
        pages_indexed=999,
    ))
    await db.flush()

    resp = await get_analytics(site_id=test_site.id, db=db, days=30)
    assert resp.series == []
    assert resp.totals.indexed_latest is None
