"""Unit tests for Phase D scorer extensions — target_demand_map fields.

Flag is not read by the pure scorer; it only consumes the Phase D
fields on ScorerContext. These tests exercise both paths (legacy =
fields None, Phase D = fields populated) and the post-composition
brand dampening.
"""

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
        today=date(2026, 4, 17),
    )
    defaults.update(overrides)
    return ScorerContext(**defaults)


# ─── 1. Flag OFF / legacy: Phase D fields default to None/False ──────


def test_phase_d_fields_default_to_none():
    """ScorerContext accepts no Phase D fields → same as legacy."""
    ctx = _ctx()
    assert ctx.target_cluster_relevance is None
    assert ctx.is_brand_cluster is False
    assert ctx.site_non_brand_coverage_ratio is None
    assert ctx.current_coverage_score is None


def test_legacy_path_unchanged_when_target_relevance_none():
    """Score must be identical whether the Phase D kwargs are passed as
    None/defaults or not passed at all — this is the parity contract."""
    base = score_recommendation(_ctx())
    explicit = score_recommendation(
        _ctx(
            target_cluster_relevance=None,
            is_brand_cluster=False,
            site_non_brand_coverage_ratio=None,
            current_coverage_score=None,
        )
    )
    assert base.priority_score == explicit.priority_score
    # No Phase D diagnostics in impact_parts on legacy path.
    assert "cluster_gap_weighted" not in base.impact_parts


# ─── 2. Phase D impact formula applied when relevance is set ─────────


def test_phase_d_impact_uses_cluster_gap_weighted():
    """When target_cluster_relevance is set, impact_parts must expose
    the new diagnostics and the formula must match the spec."""
    ctx = _ctx(
        target_cluster_relevance=0.9,
        current_coverage_score=0.2,
        total_impressions_14d=500,
    )
    result = score_recommendation(ctx)
    assert result is not None
    assert "cluster_gap_weighted" in result.impact_parts
    # (1 - 0.2) * 0.9 = 0.72
    assert result.impact_parts["cluster_gap_weighted"] == pytest.approx(0.72)
    # imp_component = 0.6 * 0.72 + 0.4 * imp_norm
    imp_norm = result.impact_parts["impressions_norm"]
    expected_imp_component = 0.6 * 0.72 + 0.4 * imp_norm
    assert result.impact_parts["imp_component"] == pytest.approx(
        round(expected_imp_component, 4)
    )


def test_phase_d_high_gap_increases_impact_over_legacy():
    """A cluster with huge gap × relevance should beat legacy impact."""
    legacy = score_recommendation(_ctx(total_impressions_14d=100))
    phase_d = score_recommendation(
        _ctx(
            total_impressions_14d=100,
            target_cluster_relevance=1.0,
            current_coverage_score=0.0,   # full gap
        )
    )
    assert phase_d.impact > legacy.impact


# ─── 3. Brand dampening fires ─────────────────────────────────────────


def test_brand_dampening_halves_score_when_nonbrand_weak():
    """Brand cluster rec with non_brand_ratio < 0.3 → score *= 0.5."""
    base = _ctx(
        target_cluster_relevance=0.5,
        current_coverage_score=0.3,
        is_brand_cluster=False,
        site_non_brand_coverage_ratio=0.1,
    )
    branded = _ctx(
        target_cluster_relevance=0.5,
        current_coverage_score=0.3,
        is_brand_cluster=True,
        site_non_brand_coverage_ratio=0.1,
    )
    s_base = score_recommendation(base)
    s_branded = score_recommendation(branded)
    # The only difference is is_brand_cluster → dampening applied.
    assert s_branded.priority_score == pytest.approx(
        round(s_base.priority_score * 0.5, 2)
    )
    assert "brand_deprioritized_until_nonbrand_foundation" in s_branded.notes
    assert "brand_deprioritized_until_nonbrand_foundation" not in s_base.notes


# ─── 4. Brand dampening does NOT fire when ratio >= 0.3 ──────────────


def test_brand_dampening_skipped_when_nonbrand_ratio_ok():
    ctx = _ctx(
        target_cluster_relevance=0.5,
        current_coverage_score=0.3,
        is_brand_cluster=True,
        site_non_brand_coverage_ratio=0.55,
    )
    s = score_recommendation(ctx)
    assert "brand_deprioritized_until_nonbrand_foundation" not in s.notes


def test_brand_dampening_skipped_when_ratio_unknown():
    """site_non_brand_coverage_ratio=None (no target map) → no dampening."""
    ctx = _ctx(
        target_cluster_relevance=0.5,
        current_coverage_score=0.3,
        is_brand_cluster=True,
        site_non_brand_coverage_ratio=None,
    )
    s = score_recommendation(ctx)
    assert "brand_deprioritized_until_nonbrand_foundation" not in s.notes


def test_brand_dampening_skipped_for_nonbrand_cluster():
    ctx = _ctx(
        target_cluster_relevance=0.5,
        current_coverage_score=0.3,
        is_brand_cluster=False,
        site_non_brand_coverage_ratio=0.05,
    )
    s = score_recommendation(ctx)
    assert "brand_deprioritized_until_nonbrand_foundation" not in s.notes


# ─── 5. ScorerContext accepts new optional fields ────────────────────


def test_scorer_context_validates_new_fields():
    ctx = ScorerContext(
        category="title",
        priority="high",
        user_status="pending",
        has_after_text=False,
        signal_type="title_length",
        signal_name=None,
        detector_confidence=0.9,
        reviewer_model="python-only",
        total_impressions_14d=200,
        current_score=3.0,
        top_query=None,
        today=date(2026, 4, 17),
        target_cluster_relevance=0.75,
        is_brand_cluster=True,
        site_non_brand_coverage_ratio=0.4,
        current_coverage_score=0.55,
    )
    assert ctx.target_cluster_relevance == 0.75
    assert ctx.is_brand_cluster is True
    result = score_recommendation(ctx)
    assert result is not None
    # Non-brand ratio is >= 0.3 so no dampening.
    assert "brand_deprioritized_until_nonbrand_foundation" not in result.notes
