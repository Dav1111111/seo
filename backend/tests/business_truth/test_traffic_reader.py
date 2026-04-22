"""traffic_reader: Webmaster queries → traffic share per direction.

Pure aggregation logic is unit-tested without DB; the DB-backed
`load_traffic_distribution` has one integration test that inserts
SearchQuery + DailyMetric rows and verifies aggregation.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.core_audit.business_truth.dto import DirectionKey


def test_aggregate_empty_input_returns_empty():
    from app.core_audit.business_truth.traffic_reader import aggregate_traffic
    out = aggregate_traffic([], services={"багги"}, geos={"абхазия"})
    assert out.direction_weights == {}
    assert out.total_impressions == 0
    assert out.unclassified_impressions == 0


def test_single_query_single_direction():
    from app.core_audit.business_truth.traffic_reader import aggregate_traffic
    out = aggregate_traffic(
        [("багги абхазия", 1000)],
        services={"багги"},
        geos={"абхазия"},
    )
    assert out.total_impressions == 1000
    assert out.unclassified_impressions == 0
    weights = out.direction_weights
    assert weights[DirectionKey.of("багги", "абхазия")] == pytest.approx(1.0)


def test_unclassified_query_goes_to_separate_bucket():
    """Query that doesn't contain any known service OR geo is unclassified."""
    from app.core_audit.business_truth.traffic_reader import aggregate_traffic
    out = aggregate_traffic(
        [
            ("багги абхазия", 800),
            ("случайный запрос про котиков", 200),
        ],
        services={"багги"},
        geos={"абхазия"},
    )
    assert out.total_impressions == 1000
    assert out.unclassified_impressions == 200
    # Direction weights renormalized over classified pool (800)
    weights = out.direction_weights
    assert weights[DirectionKey.of("багги", "абхазия")] == pytest.approx(1.0)


def test_multi_direction_traffic_distribution():
    from app.core_audit.business_truth.traffic_reader import aggregate_traffic
    out = aggregate_traffic(
        [
            ("багги абхазия",   7000),
            ("багги сочи",      2000),
            ("багги крым",      1000),
        ],
        services={"багги"},
        geos={"абхазия", "сочи", "крым"},
    )
    weights = {(k.service, k.geo): w for k, w in out.direction_weights.items()}
    assert weights[("багги", "абхазия")] == pytest.approx(0.7)
    assert weights[("багги", "сочи")]    == pytest.approx(0.2)
    assert weights[("багги", "крым")]    == pytest.approx(0.1)
    assert out.unclassified_impressions == 0


def test_query_matches_multiple_directions_splits_impressions():
    """Hub-style query 'багги абхазия сочи' counts for both pairs,
    each getting half the impressions."""
    from app.core_audit.business_truth.traffic_reader import aggregate_traffic
    out = aggregate_traffic(
        [("багги абхазия сочи", 1000)],
        services={"багги"},
        geos={"абхазия", "сочи"},
    )
    weights = {(k.service, k.geo): w for k, w in out.direction_weights.items()}
    assert weights[("багги", "абхазия")] == pytest.approx(0.5)
    assert weights[("багги", "сочи")]    == pytest.approx(0.5)


def test_classifier_uses_shared_matcher_tolerates_endings():
    """'абхазии' (locative case) should still match 'абхазия'."""
    from app.core_audit.business_truth.traffic_reader import aggregate_traffic
    out = aggregate_traffic(
        [("багги туры в абхазии цена", 500)],
        services={"багги"},
        geos={"абхазия"},
    )
    weights = out.direction_weights
    assert DirectionKey.of("багги", "абхазия") in weights


# ── DB-backed integration: load_traffic_distribution ──────────────────

async def test_load_traffic_distribution_pulls_from_webmaster(db, test_site):
    """Full integration: insert SearchQuery + DailyMetric, verify
    load_traffic_distribution aggregates correctly."""
    from datetime import datetime

    from app.core_audit.business_truth.traffic_reader import (
        load_traffic_distribution,
    )
    from app.models.daily_metric import DailyMetric
    from app.models.search_query import SearchQuery

    today = date.today()

    # Two queries with different impressions
    q1 = SearchQuery(
        id=uuid.uuid4(), site_id=test_site.id,
        query_text="багги абхазия цена", is_branded=False,
    )
    q2 = SearchQuery(
        id=uuid.uuid4(), site_id=test_site.id,
        query_text="багги сочи", is_branded=False,
    )
    db.add_all([q1, q2])
    await db.flush()

    # DailyMetric rows tying impressions to query_performance
    for q, imp in [(q1, 700), (q2, 300)]:
        db.add(DailyMetric(
            site_id=test_site.id,
            date=today,
            metric_type="query_performance",
            dimension_id=q.id,
            impressions=imp,
            clicks=0,
        ))
    await db.flush()

    out = await load_traffic_distribution(
        db, test_site.id,
        services={"багги"},
        geos={"абхазия", "сочи"},
        days_back=7,
    )
    assert out.total_impressions == 1000
    weights = {(k.service, k.geo): w for k, w in out.direction_weights.items()}
    assert weights[("багги", "абхазия")] == pytest.approx(0.7)
    assert weights[("багги", "сочи")]    == pytest.approx(0.3)
