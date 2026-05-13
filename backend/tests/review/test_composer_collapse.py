"""Composer-level tests for the audit 2026-05-14 fan-out collapse.

These tests start from synthetic `CheckFinding` rows (skipping the
checks entirely) so we can pin the composer contract:

  1. ONE aggregate EEAT finding → ONE Recommendation with a bulleted
     `after` text listing every missing item.
  2. Same for `commercial_factors_missing`.
  3. The schema-cargo-cult enricher collapses N hallucinated types into
     ONE Recommendation row.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from app.core_audit.intent_codes import IntentCode
from app.core_audit.review import LinkCandidate, ReviewInput, enrich_with_llm
from app.core_audit.review.composer import compose
from app.core_audit.review.dto import (
    PageLevelSummary,
    Recommendation,
    ReviewResult,
)
from app.core_audit.review.enums import RecCategory, RecPriority, ReviewStatus
from app.core_audit.review.findings import CheckFinding, FindingStatus


# ── EEAT composer ─────────────────────────────────────────────────────

def test_eeat_collapse_single_finding():
    """4 missing EEAT signals → exactly 1 Recommendation; `after` text
    must contain a human label for every slug."""
    finding = CheckFinding(
        signal_type="eeat_signals_missing",
        status=FindingStatus.fail,
        severity="critical",
        confidence=0.85,
        evidence={
            "missing_items": ["rto_number", "inn", "ogrn", "license_section"],
            "count": 4,
        },
    )
    recs = compose([finding])
    assert len(recs) == 1
    rec = recs[0]
    assert rec.category == RecCategory.eeat
    assert rec.priority == RecPriority.critical
    # All four human labels must appear in the bulleted after_text
    assert rec.after is not None
    for label in ("РТО", "ИНН", "ОГРН", "лицензии"):
        assert label in rec.after, f"missing '{label}' in after_text: {rec.after}"
    # Each label gets its own bullet line
    assert rec.after.count("\n- ") == 4
    # before_text is populated (so the UI's diff view has content)
    assert rec.before is not None
    assert "Не вижу на странице" in rec.before


def test_eeat_no_missing_emits_nothing():
    """Pass-status aggregate (no missing items) → composer drops it."""
    finding = CheckFinding(
        signal_type="eeat_signal_present",
        status=FindingStatus.passed,
        confidence=0.9,
        evidence={"signal_name": "rto_number"},
    )
    recs = compose([finding])
    assert recs == []


def test_commercial_collapse_single_finding():
    finding = CheckFinding(
        signal_type="commercial_factors_missing",
        status=FindingStatus.fail,
        severity="high",
        confidence=0.85,
        evidence={
            "missing_items": ["phone_in_header", "price_above_fold", "schedule_block"],
            "missing_descriptions": [
                "Телефон в формате +7 (XXX) в шапке сайта",
                "Цена видна на первом экране без скролла",
                "График работы указан",
            ],
            "count": 3,
        },
    )
    recs = compose([finding])
    assert len(recs) == 1
    rec = recs[0]
    assert rec.category == RecCategory.commercial
    assert rec.priority == RecPriority.high
    assert rec.after is not None
    # Descriptions, not slugs, must be rendered to the owner
    assert "Телефон в формате +7" in rec.after
    assert "Цена видна на первом экране" in rec.after
    assert "График работы" in rec.after
    assert rec.after.count("\n- ") == 3


def test_commercial_collapse_falls_back_to_slug_when_description_missing():
    """If a profile entry omits description_ru, the composer must still
    produce a non-broken card."""
    finding = CheckFinding(
        signal_type="commercial_factors_missing",
        status=FindingStatus.fail,
        severity="medium",
        confidence=0.85,
        evidence={
            "missing_items": ["mystery_factor"],
            "missing_descriptions": [""],   # empty description
            "count": 1,
        },
    )
    recs = compose([finding])
    assert len(recs) == 1
    assert "mystery_factor" in (recs[0].after or "")


# ── Cargo-cult enricher collapse ──────────────────────────────────────

def _ri() -> ReviewInput:
    return ReviewInput(
        page_id=uuid4(),
        site_id=uuid4(),
        coverage_decision_id=uuid4(),
        target_intent=IntentCode.COMM_MODIFIED,
        path="/tours/x",
        url="https://example.com/tours/x",
        title="Тур на Рицу",
        meta_description=None,
        h1="Тур",
        content_text="Текст. Тур на Рицу. Цена 2500.",
        word_count=10,
        has_schema=True,
        images_count=0,
        content_hash="h",
        composite_hash="h",
        top_queries=("тур на рицу",),
        link_candidates=(LinkCandidate(url="/x", anchor_hint="x", similarity=0.8),),
    )


def _seed_result() -> ReviewResult:
    """Bare ReviewResult that enrich_with_llm can mutate. We pass a
    single Recommendation so the enricher doesn't short-circuit; the
    actual content doesn't matter — we're asserting on appended cargo
    cult cards."""
    rec = Recommendation(
        category=RecCategory.title,
        priority=RecPriority.medium,
        reasoning_ru="seed",
        before="x",
        after="y",
        source_finding_id="title_length",
    )
    summary = PageLevelSummary(
        verdict_ru="seed",
        current_score=2.0,
        estimated_score_after=2.0,
    )
    return ReviewResult(
        page_id=uuid4(),
        site_id=uuid4(),
        target_intent=IntentCode.COMM_MODIFIED,
        composite_hash="h",
        status=ReviewStatus.completed,
        reviewer_model="python-only",
        reviewer_version="1.0.0",
        summary=summary,
        recommendations=[rec],
    )


def test_cargo_cult_collapse_into_single_rec():
    """Two hallucinated types → ONE recommendation, both names in `before`."""
    ri = _ri()
    seed_finding = CheckFinding(
        signal_type="title_length",
        status=FindingStatus.fail,
        severity="medium",
        confidence=1.0,
        evidence={"length": 90},
    )

    fake_tool_input = {
        "rewrites": [],
        "h2_drafts": [],
        "link_proposals": [],
        "detected_cargo_cult_schemas": ["TouristTrip", "Event"],
    }
    fake_usage = {
        "cost_usd": 0.002,
        "input_tokens": 100,
        "output_tokens": 20,
        "model": "claude-haiku-4-5",
    }

    # Bypass verify.filter_hallucinated_cargo_cult — we want the raw list
    # to flow through so we can assert composer behavior.
    with patch(
        "app.core_audit.review.llm.runner.call_with_tool",
        return_value=(fake_tool_input, fake_usage),
        create=True,
    ), patch(
        "app.core_audit.review.llm.runner.filter_hallucinated_cargo_cult",
        side_effect=lambda items, schema_blocks=None: list(items),
    ):
        result = _seed_result()
        enriched = enrich_with_llm(result, ri, [seed_finding])

    cargo_recs = [
        r for r in enriched.recommendations
        if r.source_finding_id and r.source_finding_id.startswith("schema_cargo_cult_present:")
    ]
    assert len(cargo_recs) == 1, f"expected 1 collapsed cargo rec, got {len(cargo_recs)}"
    rec = cargo_recs[0]
    # Both type names must be present in the `before` text (for the diff UI)
    assert "TouristTrip" in (rec.before or "")
    assert "Event" in (rec.before or "")
    # source_finding_id encodes both types but stays within 120-char column
    assert "TouristTrip" in rec.source_finding_id
    assert "Event" in rec.source_finding_id
    assert len(rec.source_finding_id) <= 120


def test_cargo_cult_empty_emits_no_rec():
    ri = _ri()
    seed_finding = CheckFinding(
        signal_type="title_length",
        status=FindingStatus.fail,
        severity="medium",
        confidence=1.0,
        evidence={"length": 90},
    )
    fake_tool_input = {
        "rewrites": [],
        "h2_drafts": [],
        "link_proposals": [],
        "detected_cargo_cult_schemas": [],
    }
    fake_usage = {"cost_usd": 0.001, "input_tokens": 10, "output_tokens": 5, "model": "haiku"}
    with patch(
        "app.core_audit.review.llm.runner.call_with_tool",
        return_value=(fake_tool_input, fake_usage),
        create=True,
    ):
        enriched = enrich_with_llm(_seed_result(), ri, [seed_finding])

    cargo_recs = [
        r for r in enriched.recommendations
        if r.source_finding_id and r.source_finding_id.startswith("schema_cargo_cult_present:")
    ]
    assert cargo_recs == []


def test_cargo_cult_source_finding_id_truncated_to_120():
    """Many type names → source_finding_id must fit the DB String(120) column.

    Uses every whitelisted cargo type so the verify step doesn't filter
    them out, then asserts the truncation logic holds even at the max
    real-world size."""
    from app.core_audit.review.llm.verify import CARGO_CULT_SCHEMA_TYPES

    ri = _ri()
    seed_finding = CheckFinding(
        signal_type="title_length",
        status=FindingStatus.fail,
        severity="medium",
        confidence=1.0,
        evidence={"length": 90},
    )
    # Each name repeated so the joined payload grows long enough to require
    # truncation. The composer-level truncate logic is exercised by the
    # joined-string length, not by the count of distinct names.
    many = list(CARGO_CULT_SCHEMA_TYPES) * 4
    fake_tool_input = {
        "rewrites": [],
        "h2_drafts": [],
        "link_proposals": [],
        "detected_cargo_cult_schemas": many,
    }
    fake_usage = {"cost_usd": 0.001, "input_tokens": 10, "output_tokens": 5, "model": "haiku"}
    with patch(
        "app.core_audit.review.llm.runner.call_with_tool",
        return_value=(fake_tool_input, fake_usage),
        create=True,
    ), patch(
        "app.core_audit.review.llm.runner.filter_hallucinated_cargo_cult",
        side_effect=lambda items, schema_blocks=None: list(items),
    ):
        enriched = enrich_with_llm(_seed_result(), ri, [seed_finding])

    cargo_recs = [
        r for r in enriched.recommendations
        if r.source_finding_id and r.source_finding_id.startswith("schema_cargo_cult_present:")
    ]
    assert len(cargo_recs) == 1
    assert len(cargo_recs[0].source_finding_id) <= 120
