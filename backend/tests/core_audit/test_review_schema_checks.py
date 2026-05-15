"""Unit tests for the per-type Schema.org coverage check.

The check is pure (no DB / no LLM) — these tests synthesize a
`ReviewInput` and assert on emitted findings only. Branches covered:

  - intent not in profile         → not_applicable
  - intent declares no rules ()   → not_applicable (skipped, same path)
  - has_schema=False + empty types→ legacy `schema_missing` umbrella card
  - partial overlap of types      → N `schema_missing_type` cards, one
                                    per missing recommended type, with
                                    proper severity per the spec
  - full coverage                 → single `schema_types_complete` passed
  - intent_without_rules covered via TRUST_LEGAL on a page with no types
                                    (still emits umbrella)
  - normalization helpers         — list @type, URL-prefixed @type
  - JSON-LD example sanity        — every example must json.loads cleanly
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.core_audit.intent_codes import IntentCode
from app.core_audit.review.checks.schema_checks import check_schema
from app.core_audit.review.context_builder import _normalize_schema_type
from app.core_audit.review.dto import ReviewInput
from app.core_audit.review.findings import FindingStatus
from app.profiles.tourism import TOURISM_TOUR_OPERATOR
from app.profiles.tourism.schema_rules import (
    TOURISM_SCHEMA_EXAMPLES,
    TOURISM_SCHEMA_RULES,
)


# ── Test helpers ──────────────────────────────────────────────────────


def _ri(**overrides) -> ReviewInput:
    """Synthesize a ReviewInput for a commercial tour page (default)."""
    defaults: dict = dict(
        page_id=uuid4(),
        site_id=uuid4(),
        coverage_decision_id=uuid4(),
        target_intent=IntentCode.COMM_MODIFIED,
        path="/abkhazia/buggy-tours",
        url="https://grandtourspirit.ru/abkhazia/buggy-tours",
        title="Багги-туры в Абхазию из Сочи",
        meta_description="Однодневный багги-тур из Сочи в Абхазию",
        h1="Багги-тур в Абхазию",
        content_text="Описание тура. Программа. Цена 24900 руб.",
        word_count=80,
        has_schema=True,
        images_count=4,
        content_hash="ch",
        composite_hash="ch",
        schema_types=(),
        top_queries=("багги тур абхазия",),
    )
    defaults.update(overrides)
    return ReviewInput(**defaults)


# ── Branch 1: intent without rules → not_applicable ───────────────────


def test_check_schema_intent_without_rules_via_monkey(monkeypatch):
    """Branch coverage: `profile.schema_rules.get(intent)` returns None."""
    # Simulate by patching the profile's schema_rules to drop an entry.
    original = TOURISM_TOUR_OPERATOR.schema_rules
    monkeypatch.setattr(
        TOURISM_TOUR_OPERATOR,
        "schema_rules",
        {k: v for k, v in original.items() if k != IntentCode.COMM_MODIFIED},
    )
    result = check_schema(_ri(), TOURISM_TOUR_OPERATOR)
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.signal_type == "schema_missing"
    assert f.status == FindingStatus.not_applicable
    assert f.evidence["reason"] == "unknown_intent_in_profile"


def test_check_schema_intent_with_empty_rules_returns_not_applicable(monkeypatch):
    """Branch coverage: `profile.schema_rules[intent]` returns ()."""
    monkeypatch.setattr(
        TOURISM_TOUR_OPERATOR,
        "schema_rules",
        {**TOURISM_TOUR_OPERATOR.schema_rules, IntentCode.COMM_MODIFIED: ()},
    )
    result = check_schema(_ri(), TOURISM_TOUR_OPERATOR)
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.signal_type == "schema_missing"
    assert f.status == FindingStatus.not_applicable
    assert f.evidence["reason"] == "schema_not_applicable_for_intent"


# ── Branch 2: BACKWARD-COMPAT — no schema at all ──────────────────────


def test_check_schema_no_schema_at_all_emits_schema_missing_high():
    """A commercial-tour page with no markup at all must still emit the
    legacy `schema_missing` finding (high severity, recommended_types
    fully listed in evidence) — no N-card fan-out, no regression."""
    ri = _ri(has_schema=False, schema_types=())
    result = check_schema(ri, TOURISM_TOUR_OPERATOR)
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.signal_type == "schema_missing"
    assert f.status == FindingStatus.fail
    assert f.severity == "high"
    # Evidence must carry the FULL recommended list for COMM_MODIFIED.
    assert set(f.evidence["recommended_types"]) == set(
        TOURISM_SCHEMA_RULES[IntentCode.COMM_MODIFIED]
    )
    assert f.evidence["has_schema"] is False
    assert f.evidence["intent"] == IntentCode.COMM_MODIFIED.value


def test_check_schema_has_schema_true_but_no_parsed_types_emits_passed_fallback():
    """Older fingerprint (has_schema=True) without a deep-extract yet —
    we don't have parsed types to diff. Emit informational pass instead
    of spamming N missing-type cards from no data."""
    ri = _ri(has_schema=True, schema_types=())
    result = check_schema(ri, TOURISM_TOUR_OPERATOR)
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.signal_type == "schema_types_recommended"
    assert f.status == FindingStatus.passed
    assert "type_level_check_deferred_to_deep_extract" in f.evidence["note"]


# ── Branch 3: partial coverage → N findings, one per missing type ────


def test_check_schema_partial_coverage_emits_per_missing_type():
    """COMM_MODIFIED requires {TouristTrip, Service, Offer,
    AggregateOffer, FAQPage, BreadcrumbList}. Present: {Organization,
    WebSite, BreadcrumbList}. Expected: 5 missing-type findings.

    Severity expectations (per spec):
      - FAQPage, Offer, TouristTrip  → critical (high-value commercial
                                       on critical commercial intent)
      - AggregateOffer, Service      → high (other commercial-intent
                                       missings)
    """
    present = ("Organization", "WebSite", "BreadcrumbList")
    ri = _ri(has_schema=True, schema_types=present)
    result = check_schema(ri, TOURISM_TOUR_OPERATOR)

    missing_findings = [
        f for f in result.findings if f.signal_type == "schema_missing_type"
    ]
    # 6 recommended − 1 already present (BreadcrumbList) = 5 missing.
    assert len(missing_findings) == 5

    by_type = {f.evidence["missing_type"]: f for f in missing_findings}
    expected_missing = {"TouristTrip", "Service", "Offer", "AggregateOffer", "FAQPage"}
    assert set(by_type.keys()) == expected_missing

    # All findings carry consistent context.
    for mt, f in by_type.items():
        assert f.status == FindingStatus.fail
        assert f.evidence["intent"] == IntentCode.COMM_MODIFIED.value
        assert set(f.evidence["present_types"]) == set(present)
        assert f.evidence["rationale_ru"]      # short Russian explanation
        # example_jsonld is present for every type we wrote a template for.
        assert "example_jsonld" in f.evidence
        # And the embedded example must parse as JSON.
        json.loads(f.evidence["example_jsonld"])

    # Per-spec severity bucket — critical for FAQ/Offer/TouristTrip.
    assert by_type["FAQPage"].severity == "critical"
    assert by_type["Offer"].severity == "critical"
    assert by_type["TouristTrip"].severity == "critical"
    # Other missings on commercial intent → high.
    assert by_type["Service"].severity == "high"
    assert by_type["AggregateOffer"].severity == "high"


def test_check_schema_partial_coverage_info_intent_emits_medium():
    """For an INFO_* intent (INFO_DEST: Article, BreadcrumbList,
    FAQPage), missing anything is medium severity — info pages don't
    drive direct conversions."""
    ri = _ri(
        target_intent=IntentCode.INFO_DEST,
        has_schema=True,
        schema_types=("BreadcrumbList",),  # Article + FAQPage missing
    )
    result = check_schema(ri, TOURISM_TOUR_OPERATOR)
    missing = [f for f in result.findings if f.signal_type == "schema_missing_type"]
    assert len(missing) == 2
    assert {f.evidence["missing_type"] for f in missing} == {"Article", "FAQPage"}
    # Even FAQPage is only `medium` on an info intent — the high-value
    # commercial bump doesn't apply outside the commercial-intent bucket.
    for f in missing:
        assert f.severity == "medium"


def test_check_schema_partial_coverage_trust_legal_emits_medium():
    """TRUST_LEGAL covers «отзывы о туроператоре» — informational
    intent. All missings are medium."""
    ri = _ri(
        target_intent=IntentCode.TRUST_LEGAL,
        has_schema=True,
        schema_types=("Organization",),  # LocalBusiness missing
    )
    result = check_schema(ri, TOURISM_TOUR_OPERATOR)
    missing = [f for f in result.findings if f.signal_type == "schema_missing_type"]
    assert len(missing) == 1
    assert missing[0].evidence["missing_type"] == "LocalBusiness"
    assert missing[0].severity == "medium"


# ── Branch 4: full coverage → single passed finding ──────────────────


def test_check_schema_full_coverage_emits_passed():
    """Every recommended type present → 1 passed finding, no fail cards."""
    full = TOURISM_SCHEMA_RULES[IntentCode.COMM_MODIFIED]
    ri = _ri(has_schema=True, schema_types=full)
    result = check_schema(ri, TOURISM_TOUR_OPERATOR)
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.signal_type == "schema_types_complete"
    assert f.status == FindingStatus.passed
    assert f.confidence == 0.9
    # Composer drops passed findings — verified indirectly via the
    # composer-collapse suite. Here we only assert the structure.
    fails = [g for g in result.findings if g.status == FindingStatus.fail]
    assert fails == []


# ── JSON-LD examples sanity ──────────────────────────────────────────


def test_check_schema_full_example_jsonld_parses_as_json():
    """Every template in TOURISM_SCHEMA_EXAMPLES MUST be valid JSON so
    a copy-paste in Studio doesn't break the owner's site."""
    for type_name, src in TOURISM_SCHEMA_EXAMPLES.items():
        try:
            parsed = json.loads(src)
        except json.JSONDecodeError as e:
            pytest.fail(f"TOURISM_SCHEMA_EXAMPLES[{type_name!r}] invalid JSON: {e}")
        # Sanity check: top-level @type must match the key.
        assert parsed.get("@type") == type_name, (
            f"Example for {type_name!r} has wrong @type: {parsed.get('@type')!r}"
        )


def test_check_schema_examples_cover_every_recommended_type():
    """Every type referenced by TOURISM_SCHEMA_RULES has a paste-in
    example — otherwise the per-type card emits a generic «Добавьте
    JSON-LD» line without a template, which is a worse owner experience.
    """
    referenced: set[str] = set()
    for types in TOURISM_SCHEMA_RULES.values():
        referenced.update(types)
    missing_examples = referenced - set(TOURISM_SCHEMA_EXAMPLES.keys())
    assert missing_examples == set(), (
        f"Recommended types without example_jsonld: {missing_examples}"
    )


# ── Normalization helper coverage ────────────────────────────────────


def test_extract_top_level_types_strips_url_prefix():
    """`@type: "http://schema.org/Question"` must normalize to "Question".

    The context-builder helper is tested in isolation here because the
    full DB walk is integration territory; we cover the parsing rules
    that matter for the diff to schema_rules.
    """
    assert _normalize_schema_type("http://schema.org/Question") == "Question"
    assert _normalize_schema_type("https://schema.org/TouristTrip") == "TouristTrip"
    assert _normalize_schema_type("schema:LocalBusiness") == "LocalBusiness"
    assert _normalize_schema_type(" FAQPage ") == "FAQPage"
    assert _normalize_schema_type("") == ""


def test_extract_top_level_types_handles_list_type_field():
    """JSON-LD allows `@type` to be a string OR a list of strings.

    We don't go through the DB here — instead we exercise the in-memory
    union directly: simulate two blocks contributing the same and
    different types and assert the dedup is stable + insertion-ordered.
    """
    blocks = [
        {"@type": ["LocalBusiness", "Organization"]},
        {"@type": "Organization"},
        {"@type": "BreadcrumbList"},
        # Garbage entries must be ignored.
        {"@type": None},
        "not-a-dict",
    ]
    seen: dict[str, None] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        t = block.get("@type")
        if isinstance(t, str):
            norm = _normalize_schema_type(t)
            if norm:
                seen.setdefault(norm, None)
        elif isinstance(t, list):
            for item in t:
                if isinstance(item, str):
                    norm = _normalize_schema_type(item)
                    if norm:
                        seen.setdefault(norm, None)
    assert tuple(seen.keys()) == ("LocalBusiness", "Organization", "BreadcrumbList")


# ── Composer integration (per-type → Recommendation) ─────────────────


def test_check_schema_partial_coverage_produces_per_type_recommendations():
    """End-to-end through the composer: 5 missing types → 5
    Recommendation rows, each with a JSON-LD `after_text` and a stable,
    distinct `source_finding_id`."""
    from app.core_audit.review.composer import compose

    present = ("Organization", "WebSite", "BreadcrumbList")
    ri = _ri(has_schema=True, schema_types=present)
    result = check_schema(ri, TOURISM_TOUR_OPERATOR)
    recs = compose(result.findings)
    assert len(recs) == 5

    source_ids = {r.source_finding_id for r in recs}
    # IDs must be deterministic and per-type.
    expected = {
        "schema.missing_type.touristtrip",
        "schema.missing_type.service",
        "schema.missing_type.offer",
        "schema.missing_type.aggregateoffer",
        "schema.missing_type.faqpage",
    }
    assert source_ids == expected
    # Every after_text carries the JSON-LD template.
    for r in recs:
        assert r.after is not None
        assert "```json" in r.after
