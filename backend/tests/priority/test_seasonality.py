"""Pre-season + winter/summer split tests for the priority scorer.

Owner brief: tourism is seasonal — by the peak month it's already too
late to ship a new landing. The scorer should surface seasonal recs
in the preparation window with a HIGHER boost than peak month.
"""

from datetime import date

from app.core_audit.priority.constants import (
    SEASONAL_BOOST,
    SEASONAL_PRE_SEASON_BOOST,
)
from app.core_audit.priority.scorer import (
    ScorerContext,
    _seasonal_classification,
)


def _ctx(*, query: str, today: date) -> ScorerContext:
    return ScorerContext(
        category="title",
        priority="high",
        user_status="pending",
        has_after_text=False,
        signal_type="title_length",
        signal_name=None,
        detector_confidence=0.9,
        reviewer_model="rules",
        total_impressions_14d=100,
        current_score=3.0,
        top_query=query,
        today=today,
    )


class TestSummerSeasonality:
    def test_pre_season_april_summer_query(self):
        ctx = _ctx(query="экскурсия 33 водопада", today=date(2026, 4, 1))
        kind, boost = _seasonal_classification(ctx)
        assert kind == "summer_pre_season"
        assert boost == SEASONAL_PRE_SEASON_BOOST

    def test_peak_july_summer_query(self):
        ctx = _ctx(query="летние туры Сочи", today=date(2026, 7, 15))
        kind, boost = _seasonal_classification(ctx)
        assert kind == "summer_peak"
        assert boost == SEASONAL_BOOST

    def test_pre_season_boost_higher_than_peak(self):
        # Sanity: prep window matters more than mid-season reaction.
        assert SEASONAL_PRE_SEASON_BOOST > SEASONAL_BOOST


class TestWinterSeasonality:
    def test_pre_season_october_winter_query(self):
        ctx = _ctx(query="горнолыжные туры Роза Хутор", today=date(2026, 10, 5))
        kind, boost = _seasonal_classification(ctx)
        assert kind == "winter_pre_season"
        assert boost == SEASONAL_PRE_SEASON_BOOST

    def test_peak_january_new_year_query(self):
        ctx = _ctx(query="новогодние туры в Сочи", today=date(2026, 1, 5))
        kind, boost = _seasonal_classification(ctx)
        assert kind == "winter_peak"
        assert boost == SEASONAL_BOOST

    def test_peak_december_ski_query(self):
        ctx = _ctx(query="катание на горных лыжах", today=date(2026, 12, 20))
        kind, boost = _seasonal_classification(ctx)
        assert kind == "winter_peak"
        assert boost == SEASONAL_BOOST


class TestNoSeasonMatch:
    def test_off_season_summer_query_winter_month(self):
        # Summer query in November — neither pre nor peak summer
        ctx = _ctx(query="пляжный отдых", today=date(2026, 11, 1))
        kind, boost = _seasonal_classification(ctx)
        assert kind is None
        assert boost == 0.0

    def test_non_seasonal_query(self):
        # Year-round commercial query, no seasonal keywords
        ctx = _ctx(query="купить квартиру в Сочи", today=date(2026, 6, 1))
        kind, boost = _seasonal_classification(ctx)
        assert kind is None
        assert boost == 0.0

    def test_empty_query(self):
        ctx = _ctx(query="", today=date(2026, 6, 1))
        kind, boost = _seasonal_classification(ctx)
        assert kind is None
        assert boost == 0.0
