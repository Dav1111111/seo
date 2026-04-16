"""Unit tests for the pure scorer — no DB."""

from __future__ import annotations

from datetime import date

import pytest

from app.core_audit.priority.scorer import ScorerContext, score_recommendation


def _ctx(**overrides) -> ScorerContext:
    defaults = dict(
        category="commercial",
        priority="critical",
        user_status="pending",
        has_after_text=True,
        signal_type="commercial_factor_missing",
        signal_name="rto_in_footer",
        detector_confidence=0.85,
        reviewer_model="python+claude-haiku-4-5",
        total_impressions_14d=500,
        current_score=2.5,
        top_query="туры в абхазию",
        today=date(2026, 4, 17),        # April → seasonal window
    )
    defaults.update(overrides)
    return ScorerContext(**defaults)


def test_score_in_range():
    s = score_recommendation(_ctx())
    assert s is not None
    assert 0 <= s.priority_score <= 100
    assert 0 <= s.impact <= 1
    assert 0 <= s.confidence <= 1
    assert 0 <= s.ease <= 1


def test_deterministic():
    s1 = score_recommendation(_ctx())
    s2 = score_recommendation(_ctx())
    assert s1.priority_score == s2.priority_score


def test_critical_beats_low():
    hi = score_recommendation(_ctx(priority="critical")).priority_score
    lo = score_recommendation(_ctx(priority="low")).priority_score
    assert hi > lo


def test_high_impressions_raise_impact():
    low = score_recommendation(_ctx(total_impressions_14d=50))
    high = score_recommendation(_ctx(total_impressions_14d=5000))
    assert high.impact > low.impact


def test_drafted_bonus_raises_ease():
    with_draft = score_recommendation(_ctx(has_after_text=True))
    without = score_recommendation(_ctx(has_after_text=False))
    assert with_draft.ease > without.ease


def test_rto_is_cheap_to_fix():
    # rto_in_footer override = 22 minutes (high ease)
    rto = score_recommendation(_ctx(signal_name="rto_in_footer"))
    # schema_missing = 45 minutes (lower ease)
    schema = score_recommendation(_ctx(
        category="schema",
        signal_type="schema_missing",
        signal_name=None,
        detector_confidence=0.9,
    ))
    assert rto.ease > schema.ease


def test_rto_number_eeat_fastest_override():
    # rto_number override = 5 minutes (display existing) — must be very easy
    fast = score_recommendation(_ctx(
        category="eeat", signal_type="eeat_signal_missing", signal_name="rto_number",
    ))
    # Generic eeat signal without override falls back to category=30min
    generic = score_recommendation(_ctx(
        category="eeat", signal_type="eeat_signal_missing", signal_name="unknown_one",
    ))
    assert fast.ease > generic.ease


def test_schema_below_confidence_floor_drops_rec():
    # Schema rec with very low confidence → None
    ctx = _ctx(
        category="schema",
        signal_type="schema_missing",
        detector_confidence=0.3,                 # below floor 0.7
        reviewer_model="python-only",
    )
    result = score_recommendation(ctx)
    assert result is None


def test_schema_above_floor_survives():
    ctx = _ctx(
        category="schema",
        signal_type="schema_missing",
        detector_confidence=0.95,
        reviewer_model="python+claude-haiku-4-5",
    )
    result = score_recommendation(ctx)
    assert result is not None


def test_seasonal_boost_in_april():
    # April + seasonal query → boost
    april = score_recommendation(_ctx(
        today=date(2026, 4, 17),
        top_query="пляжный отдых летом 2026",
    ))
    january = score_recommendation(_ctx(
        today=date(2026, 1, 17),
        top_query="пляжный отдых летом 2026",
    ))
    assert "seasonal_boost" in april.notes
    assert "seasonal_boost" not in january.notes
    assert april.priority_score > january.priority_score


def test_seasonal_skip_for_non_seasonal_query():
    # April but query not seasonal → no boost
    s = score_recommendation(_ctx(
        today=date(2026, 4, 17),
        top_query="виза в турцию",                # no seasonal keywords
    ))
    assert "seasonal_boost" not in s.notes


def test_deferred_halves_score():
    pending = score_recommendation(_ctx(user_status="pending"))
    deferred = score_recommendation(_ctx(user_status="deferred"))
    assert deferred.priority_score == pytest.approx(pending.priority_score * 0.5, rel=1e-3)
    assert "deferred_penalty" in deferred.notes


def test_missing_score_uses_default():
    # No current_score provided → falls back to DEFAULT (3.0)
    default = score_recommendation(_ctx(current_score=3.0))
    explicit = score_recommendation(_ctx(current_score=3.0))
    assert default.priority_score == explicit.priority_score


def test_zero_impressions_not_zero_score():
    # Long-tail page (zero impressions) should still have non-trivial impact
    s = score_recommendation(_ctx(total_impressions_14d=0))
    assert s.impact > 0.15  # impressions floor
    assert s.priority_score > 0


def test_model_boost_haiku_beats_python_only():
    with_llm = score_recommendation(_ctx(reviewer_model="python+claude-haiku-4-5"))
    no_llm = score_recommendation(_ctx(reviewer_model="python-only"))
    assert with_llm.confidence > no_llm.confidence
