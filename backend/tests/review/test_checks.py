"""Pytest coverage for Step 3 checks. Uses synthetic ReviewInput fixtures.

Goals:
  - Contract: every check returns CheckResult, signal_type in SIGNAL_TYPES
  - Severity mapping correctness (critical/high/medium/low)
  - Per-scope density separation
  - Critical vs recommended H2 tier split
  - Russian lemmatization path (when available)
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core_audit.intent_codes import IntentCode
from app.core_audit.review import (
    CheckFinding,
    CheckResult,
    FindingStatus,
    ReviewInput,
    run_python_checks,
)
from app.core_audit.review.checks import (
    check_commercial,
    check_density,
    check_eeat,
    check_h1,
    check_h2_completeness,
    check_schema,
    check_title,
)
from app.core_audit.review.checks.overoptimization import check_overoptimization
from app.profiles.tourism import TOURISM_TOUR_OPERATOR


def _ri(**overrides) -> ReviewInput:
    defaults = dict(
        page_id=uuid4(),
        site_id=uuid4(),
        coverage_decision_id=uuid4(),
        target_intent=IntentCode.COMM_MODIFIED,
        path="/tours/tur-na-ricu",
        url="https://example.com/tours/tur-na-ricu",
        title="Тур на Рицу — программа и цены",
        meta_description="Однодневный тур на озеро Рица из Сочи",
        h1="Экскурсия на Рицу",
        content_text="Программа тура. Забираем из отеля. Цена 2500 рублей. Отзывы клиентов.",
        word_count=30,
        has_schema=True,
        images_count=5,
        content_hash="abc123",
        composite_hash="hash_xyz",
        top_queries=("тур на рицу", "экскурсия на рицу"),
        current_score=2.5,
    )
    defaults.update(overrides)
    return ReviewInput(**defaults)


# ── title_checks ──────────────────────────────────────────────────────

def test_title_length_pass():
    r = check_title(_ri(title="Короткий title 40 символов"), TOURISM_TOUR_OPERATOR)
    statuses = [f.status for f in r.findings if f.signal_type == "title_length"]
    assert statuses == [FindingStatus.passed]


def test_title_length_warn_over_70():
    long_title = "Тур на озеро Рица на один полный день из Сочи с трансфером и обедом 2026 года"
    assert len(long_title) > 70
    r = check_title(_ri(title=long_title), TOURISM_TOUR_OPERATOR)
    lf = [f for f in r.findings if f.signal_type == "title_length"][0]
    assert lf.status == FindingStatus.warn
    assert lf.severity == "medium"


def test_title_missing_critical():
    r = check_title(_ri(title=""), TOURISM_TOUR_OPERATOR)
    mf = [f for f in r.findings if f.signal_type == "title_missing"][0]
    assert mf.status == FindingStatus.fail
    assert mf.severity == "critical"


def test_title_keyword_repetition_stuffing():
    # "тур" appears 3 times in title; target lemma is "тур"
    ri = _ri(
        title="Тур тур тур на Рицу",
        top_queries=("тур на рицу",),
    )
    r = check_title(ri, TOURISM_TOUR_OPERATOR)
    rf = [f for f in r.findings if f.signal_type == "title_keyword_repetition"][0]
    assert rf.status == FindingStatus.fail
    assert rf.severity == "high"
    assert rf.evidence["keyword_count"] >= 3


# ── h1_checks ─────────────────────────────────────────────────────────

def test_h1_equals_title_low():
    r = check_h1(_ri(title="X", h1="x"), TOURISM_TOUR_OPERATOR)
    f = [x for x in r.findings if x.signal_type == "h1_equals_title"][0]
    assert f.status == FindingStatus.warn
    assert f.severity == "low"


def test_h1_missing_high():
    r = check_h1(_ri(h1=""), TOURISM_TOUR_OPERATOR)
    f = [x for x in r.findings if x.signal_type == "h1_missing"][0]
    assert f.status == FindingStatus.fail
    assert f.severity == "high"


# ── density_checks ───────────────────────────────────────────────────

def test_density_non_russian_skipped():
    r = check_density(_ri(lang="en"), TOURISM_TOUR_OPERATOR)
    na = [f for f in r.findings if f.status == FindingStatus.not_applicable]
    assert len(na) == 3  # title, h1, body


def test_density_no_target_skipped():
    r = check_density(_ri(top_queries=()), TOURISM_TOUR_OPERATOR)
    na = [f for f in r.findings if f.status == FindingStatus.not_applicable]
    assert len(na) == 3


def test_density_three_scopes_emitted():
    r = check_density(_ri(), TOURISM_TOUR_OPERATOR)
    scopes = {f.signal_type for f in r.findings}
    assert scopes == {"density_title", "density_h1", "density_body"}


# ── h2_completeness ───────────────────────────────────────────────────

def test_h2_unavailable_emits_not_applicable():
    r = check_h2_completeness(_ri(h2_blocks=()), TOURISM_TOUR_OPERATOR)
    statuses = {f.status for f in r.findings}
    assert FindingStatus.not_applicable in statuses
    assert r.stats.get("h2_extraction") == "unavailable"


def test_h2_missing_critical_tier_emitted_as_high():
    # COMM_MODIFIED critical blocks: Цены, Точка сбора, Даты заездов
    ri = _ri(h2_blocks=("Цены", "Точка сбора / Как добраться"))  # missing "Даты заездов"
    r = check_h2_completeness(ri, TOURISM_TOUR_OPERATOR)
    fail_crit = [f for f in r.findings if f.signal_type == "missing_critical_h2" and f.status == FindingStatus.fail]
    assert len(fail_crit) == 1
    assert fail_crit[0].severity == "high"


def test_h2_missing_recommended_tier_emitted_as_medium():
    # All critical present, some recommended missing
    ri = _ri(h2_blocks=("Цены", "Точка сбора", "Даты заездов"))
    r = check_h2_completeness(ri, TOURISM_TOUR_OPERATOR)
    fail_rec = [f for f in r.findings if f.signal_type == "missing_recommended_h2" and f.status == FindingStatus.fail]
    assert all(f.severity == "medium" for f in fail_rec)


# ── schema_checks ─────────────────────────────────────────────────────

def test_schema_missing_emits_high_for_commercial():
    r = check_schema(_ri(has_schema=False), TOURISM_TOUR_OPERATOR)
    f = [x for x in r.findings if x.signal_type == "schema_missing"][0]
    assert f.status == FindingStatus.fail
    assert f.severity == "high"


def test_schema_present_emits_passed_with_llm_note():
    r = check_schema(_ri(has_schema=True), TOURISM_TOUR_OPERATOR)
    f = [x for x in r.findings if x.signal_type == "schema_types_recommended"][0]
    assert f.status == FindingStatus.passed


# ── eeat + commercial ─────────────────────────────────────────────────

def test_eeat_missing_rto_critical():
    # content_text without РТО → critical signal missing for tour_operator
    ri = _ri(content_text="Обычный текст без реквизитов")
    r = check_eeat(ri, TOURISM_TOUR_OPERATOR)
    fails = [f for f in r.findings if f.signal_type == "eeat_signal_missing" and f.evidence.get("signal_name") == "rto_number"]
    assert len(fails) == 1
    assert fails[0].severity == "critical"


def test_commercial_deferred_for_pattern_none():
    r = check_commercial(_ri(), TOURISM_TOUR_OPERATOR)
    deferred = [f for f in r.findings if f.signal_type == "commercial_factor_deferred_to_llm"]
    assert len(deferred) >= 1
    # price_above_fold has detection_pattern=None — must be deferred
    names = [f.evidence.get("factor_name") for f in deferred]
    assert "price_above_fold" in names


# ── overoptimization gate ─────────────────────────────────────────────

def test_overoptimization_body_and_title_critical():
    stats = {"density_body": 0.05, "title_keyword_count": 3}
    r = check_overoptimization(_ri(), TOURISM_TOUR_OPERATOR, prior_stats=stats)
    f = r.findings[0]
    assert f.status == FindingStatus.fail
    assert f.severity == "critical"


def test_overoptimization_clean_pass():
    stats = {"density_body": 0.02, "title_keyword_count": 1}
    r = check_overoptimization(_ri(), TOURISM_TOUR_OPERATOR, prior_stats=stats)
    f = r.findings[0]
    assert f.status == FindingStatus.passed


# ── aggregator ────────────────────────────────────────────────────────

def test_aggregator_returns_review_result():
    result = run_python_checks(_ri(), TOURISM_TOUR_OPERATOR)
    assert result.status.value == "completed"
    assert result.reviewer_model == "python-only"
    assert result.summary is not None
    assert result.cost_usd == 0.0
    # Aggregator must emit at least some recommendations for this synthetic page
    # (h2_blocks empty + no РТО → several fails)
    assert len(result.recommendations) >= 3


def test_aggregator_all_priorities_valid():
    result = run_python_checks(_ri(), TOURISM_TOUR_OPERATOR)
    priorities = {r.priority.value for r in result.recommendations}
    assert priorities.issubset({"critical", "high", "medium", "low"})


def test_aggregator_summary_has_counts():
    result = run_python_checks(_ri(), TOURISM_TOUR_OPERATOR)
    s = result.summary
    total = s.critical_count + s.high_count + s.medium_count + s.low_count
    assert total == len(result.recommendations)
