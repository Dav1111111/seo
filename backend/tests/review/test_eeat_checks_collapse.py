"""Unit tests for the audit 2026-05-14 EEAT/commercial check collapse.

Before: each missing EEAT signal / commercial factor produced its own
CheckFinding (and downstream its own Recommendation). On a tourism page
this surfaced 7+9 ≈ 16 near-identical cards. After: one aggregate
finding per category with `evidence.missing_items` listing the gaps.

These tests pin the new shape:
  - exactly one aggregate finding when ≥1 signal is missing
  - exactly zero aggregate findings when everything is present
  - evidence.missing_items contains the exact slugs that failed regex
  - severity inherits the strongest priority among missing items
"""

from __future__ import annotations

import re
from dataclasses import replace
from uuid import uuid4

from app.core_audit.intent_codes import IntentCode
from app.core_audit.profile_protocol import EEATSignal, ProfileData
from app.core_audit.review.checks.commercial_checks import check_commercial
from app.core_audit.review.checks.eeat_checks import check_eeat
from app.core_audit.review.dto import ReviewInput
from app.core_audit.review.findings import FindingStatus
from app.profiles.tourism import TOURISM_TOUR_OPERATOR


def _ri(**overrides) -> ReviewInput:
    defaults = dict(
        page_id=uuid4(),
        site_id=uuid4(),
        coverage_decision_id=uuid4(),
        target_intent=IntentCode.COMM_MODIFIED,
        path="/tours/x",
        url="https://example.com/tours/x",
        title="t",
        meta_description="m",
        h1="h",
        content_text="Обычный текст без реквизитов и без коммерческих факторов.",
        word_count=12,
        has_schema=False,
        images_count=0,
        content_hash="h",
        composite_hash="h",
    )
    defaults.update(overrides)
    return ReviewInput(**defaults)


# ── EEAT collapse ─────────────────────────────────────────────────────

def test_eeat_collapse_single_finding_for_multiple_missing():
    """Tourism profile has 7 EEAT signals; content with none of them
    must produce exactly ONE aggregate fail finding, not seven."""
    ri = _ri(content_text="Простой текст. Никаких реквизитов.")
    r = check_eeat(ri, TOURISM_TOUR_OPERATOR)

    aggregates = [f for f in r.findings if f.signal_type == "eeat_signals_missing"]
    assert len(aggregates) == 1
    agg = aggregates[0]
    assert agg.status == FindingStatus.fail
    # Every signal missing → 7 items
    assert agg.evidence["count"] == len(TOURISM_TOUR_OPERATOR.eeat_signals)
    assert set(agg.evidence["missing_items"]) == {s.name for s in TOURISM_TOUR_OPERATOR.eeat_signals}
    # Old per-signal fail findings must NOT be emitted any more.
    assert not any(f.signal_type == "eeat_signal_missing" and f.status == FindingStatus.fail for f in r.findings)


def test_eeat_no_missing_emits_no_aggregate_fail():
    """Synthetic profile with 3 trivial signals, all present in text →
    no `eeat_signals_missing` fail finding."""
    profile = _profile_with_eeat_signals(
        EEATSignal(name="alpha", priority="medium", weight=0.1, pattern=re.compile(r"alpha", re.IGNORECASE)),
        EEATSignal(name="beta", priority="high", weight=0.2, pattern=re.compile(r"beta", re.IGNORECASE)),
        EEATSignal(name="gamma", priority="low", weight=0.1, pattern=re.compile(r"gamma", re.IGNORECASE)),
    )
    ri = _ri(content_text="contains alpha beta gamma all three")
    r = check_eeat(ri, profile)
    assert not any(
        f.signal_type == "eeat_signals_missing" and f.status == FindingStatus.fail
        for f in r.findings
    )
    # 3 per-signal "present" findings should remain — they feed summary.
    present = [f for f in r.findings if f.signal_type == "eeat_signal_present"]
    assert len(present) == 3


def test_eeat_aggregate_inherits_strongest_severity():
    """Mixed severity → aggregate carries the strongest (critical)."""
    profile = _profile_with_eeat_signals(
        EEATSignal(name="x", priority="low", weight=0.1, pattern=re.compile(r"definitelynothere1")),
        EEATSignal(name="y", priority="critical", weight=0.4, pattern=re.compile(r"definitelynothere2")),
        EEATSignal(name="z", priority="medium", weight=0.2, pattern=re.compile(r"definitelynothere3")),
    )
    ri = _ri(content_text="empty content with no match")
    r = check_eeat(ri, profile)
    agg = next(f for f in r.findings if f.signal_type == "eeat_signals_missing")
    assert agg.severity == "critical"


def test_eeat_no_content_emits_single_na_aggregate():
    """Missing content_text → ONE not-applicable aggregate, not N copies."""
    ri = _ri(content_text=None)
    r = check_eeat(ri, TOURISM_TOUR_OPERATOR)
    aggregates = [f for f in r.findings if f.signal_type == "eeat_signals_missing"]
    assert len(aggregates) == 1
    assert aggregates[0].status == FindingStatus.not_applicable


# ── Commercial collapse ───────────────────────────────────────────────

def test_commercial_collapse_single_finding_for_multiple_missing():
    ri = _ri(content_text="Текст без телефона, цены, графика и оплаты.")
    r = check_commercial(ri, TOURISM_TOUR_OPERATOR)
    aggregates = [f for f in r.findings if f.signal_type == "commercial_factors_missing"]
    assert len(aggregates) == 1
    agg = aggregates[0]
    assert agg.status == FindingStatus.fail
    assert agg.evidence["count"] >= 1
    # Each missing item must come with a description for the composer
    assert len(agg.evidence["missing_descriptions"]) == agg.evidence["count"]
    # Old per-factor fail findings must be gone
    assert not any(
        f.signal_type == "commercial_factor_missing" and f.status == FindingStatus.fail
        for f in r.findings
    )


def test_commercial_deferred_still_per_factor():
    """`commercial_factor_deferred_to_llm` is NOT part of the collapse —
    those need per-factor identity so the LLM enricher can hit each one
    individually (e.g. price_above_fold uses DOM position)."""
    r = check_commercial(_ri(), TOURISM_TOUR_OPERATOR)
    deferred = [f for f in r.findings if f.signal_type == "commercial_factor_deferred_to_llm"]
    assert len(deferred) >= 1


# ── helpers ───────────────────────────────────────────────────────────

def _profile_with_eeat_signals(*signals: EEATSignal) -> ProfileData:
    """Clone the tourism profile but replace eeat_signals.

    `ProfileData` is frozen; `dataclasses.replace` is the safe way."""
    return replace(TOURISM_TOUR_OPERATOR, eeat_signals=tuple(signals))
