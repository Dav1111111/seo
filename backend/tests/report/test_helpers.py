"""Unit tests for diffs + health_score — pure math, no DB."""

from __future__ import annotations

from datetime import date

from app.core_audit.report.diffs import (
    clamp_pct,
    default_week_end,
    pct_diff,
    prev_week_range,
    week_range,
)
from app.core_audit.report.health_score import compute_health_score


def test_week_range():
    s, e = week_range(date(2026, 4, 19))   # Sunday
    assert s == date(2026, 4, 13)          # Monday
    assert e == date(2026, 4, 19)


def test_prev_week_range():
    s, e = prev_week_range(date(2026, 4, 19))
    assert s == date(2026, 4, 6)
    assert e == date(2026, 4, 12)


def test_default_week_end_from_monday():
    # Monday 2026-04-20 → last Sunday = 2026-04-19
    assert default_week_end(date(2026, 4, 20)) == date(2026, 4, 19)


def test_default_week_end_from_sunday():
    # Sunday 2026-04-19 → last Sunday is previous (2026-04-12) per weekday math
    # (weekday=6 → days_since_sunday = (6+1)%7 = 0 → fallback to 7)
    assert default_week_end(date(2026, 4, 19)) == date(2026, 4, 12)


def test_pct_diff_basic():
    assert pct_diff(150, 100) == 50.0
    assert pct_diff(50, 100) == -50.0


def test_pct_diff_zero_prev():
    assert pct_diff(100, 0) is None


def test_pct_diff_none_inputs():
    assert pct_diff(None, 100) is None
    assert pct_diff(100, None) is None


def test_clamp_pct():
    assert clamp_pct(2000) == 999.0
    assert clamp_pct(-2000) == -999.0
    assert clamp_pct(None) is None


def test_health_score_all_good():
    score = compute_health_score(
        coverage_strong_pct=1.0,
        critical_recs_count=0,
        indexation_rate=1.0,
        wow_impressions_pct=10.0,
    )
    assert score == 100


def test_health_score_all_bad():
    score = compute_health_score(
        coverage_strong_pct=0.0,
        critical_recs_count=100,
        indexation_rate=0.0,
        wow_impressions_pct=-20.0,
    )
    assert score == 0


def test_health_score_neutral_trend():
    score = compute_health_score(
        coverage_strong_pct=0.5,
        critical_recs_count=25,
        indexation_rate=0.5,
        wow_impressions_pct=None,   # no data → neutral 0.5
    )
    # 0.4*0.5 + 0.3*0.5 + 0.2*0.5 + 0.1*0.5 = 0.5
    assert score == 50
