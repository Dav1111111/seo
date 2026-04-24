"""Covers the chord callback that extends /pipeline/full past collection.

Focuses on:
- money-query counter (correct filter by business tokens),
- gate decision (skip vs queue competitor_discovery),
- skipped-terminal emission so the pipeline reconciler closes the wrapper.

The Celery task itself runs synchronous code under `_run`, so tests call
the async helpers directly and assert on their outputs.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select

from app.core_audit.pipeline.tasks import (
    MIN_MONEY_QUERIES,
    _count_money_queries,
    _skip_competitor_stages,
)
from app.models.analysis_event import AnalysisEvent
from app.models.daily_metric import DailyMetric
from app.models.search_query import SearchQuery
from app.models.site import Site


async def _seed_queries_with_impressions(
    db, site: Site, items: list[tuple[str, int]],
) -> None:
    today = date.today()
    for q, imp in items:
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


async def test_money_query_counter_zero_without_target_config(
    db, test_site: Site,
):
    """Empty services/geo → no biz tokens → count always 0 (no mis-signal)."""
    test_site.target_config = {}
    await _seed_queries_with_impressions(db, test_site, [
        ("багги абхазия", 100),
        ("маршруты сочи", 50),
    ])
    got = await _count_money_queries(db, test_site.id)
    assert got == 0


async def test_money_query_counter_counts_only_relevant(
    db, test_site: Site,
):
    test_site.target_config = {
        "services": ["багги"],
        "geo_primary": ["абхазия"],
    }
    await _seed_queries_with_impressions(db, test_site, [
        ("багги абхазия цена", 100),        # relevant — багги + абхазия
        ("маршруты в абхазия", 80),         # relevant — абхазия
        ("polaris slingshot цена", 5),      # NOT relevant
        ("салахаул", 3),                     # NOT relevant
        ("багги сочи аренда", 40),          # relevant — багги
    ])
    got = await _count_money_queries(db, test_site.id)
    assert got == 3


async def test_money_query_counter_excludes_zero_impressions(
    db, test_site: Site,
):
    test_site.target_config = {
        "services": ["багги"],
        "geo_primary": ["абхазия"],
    }
    # Two queries seeded — one with 0 impressions must NOT count.
    sq_ok = SearchQuery(
        id=uuid.uuid4(), site_id=test_site.id,
        query_text="багги абхазия", is_branded=False,
    )
    sq_zero = SearchQuery(
        id=uuid.uuid4(), site_id=test_site.id,
        query_text="абхазия туры", is_branded=False,
    )
    db.add_all([sq_ok, sq_zero])
    await db.flush()
    today = date.today()
    db.add(DailyMetric(
        site_id=test_site.id, date=today,
        metric_type="query_performance",
        dimension_id=sq_ok.id,
        impressions=100, clicks=0,
    ))
    db.add(DailyMetric(
        site_id=test_site.id, date=today,
        metric_type="query_performance",
        dimension_id=sq_zero.id,
        impressions=0, clicks=0,
    ))
    await db.flush()

    got = await _count_money_queries(db, test_site.id)
    assert got == 1  # only the first one with impressions > 0


async def test_money_query_counter_ignores_old_impressions(
    db, test_site: Site,
):
    """14-day window — older impressions must not count."""
    test_site.target_config = {
        "services": ["багги"], "geo_primary": ["абхазия"],
    }
    sq = SearchQuery(
        id=uuid.uuid4(), site_id=test_site.id,
        query_text="багги абхазия", is_branded=False,
    )
    db.add(sq)
    await db.flush()
    old = date.today() - timedelta(days=30)
    db.add(DailyMetric(
        site_id=test_site.id, date=old,
        metric_type="query_performance",
        dimension_id=sq.id,
        impressions=100, clicks=0,
    ))
    await db.flush()
    got = await _count_money_queries(db, test_site.id)
    assert got == 0


async def test_skip_competitor_stages_emits_both_terminals(
    db, test_site: Site,
):
    """Below-threshold case: both competitor stages get skipped events
    so the pipeline reconciler can close the wrapper cleanly."""
    run_id = uuid.uuid4()
    # Pre-open the pipeline wrapper to mimic an in-flight run.
    from app.core_audit.activity import log_event
    await log_event(
        db, test_site.id, "pipeline", "started",
        "trigger",
        extra={"queued": [
            "crawl", "webmaster", "demand_map",
            "business_truth",
            "competitor_discovery", "competitor_deep_dive",
        ]},
        run_id=run_id,
    )

    await _skip_competitor_stages(
        db, str(test_site.id), str(run_id), money_q=2,
    )

    rows = (await db.execute(
        select(AnalysisEvent)
        .where(
            AnalysisEvent.site_id == test_site.id,
            AnalysisEvent.stage.in_(
                ["competitor_discovery", "competitor_deep_dive"],
            ),
        )
        .order_by(AnalysisEvent.ts.asc())
    )).scalars().all()
    stages = [(e.stage, e.status) for e in rows]
    assert ("competitor_discovery", "skipped") in stages
    assert ("competitor_deep_dive", "skipped") in stages


def test_min_money_queries_is_documented_constant():
    """Guardrail: anyone lowering the threshold below 3 should think
    hard — SERP at that size is dominated by aggregators."""
    assert MIN_MONEY_QUERIES >= 3
