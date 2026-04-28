"""Tests for app.core_audit.relevance — rules-only path.

The contract these tests pin down:
  - empty profile → no verdict (don't fabricate)
  - primary mentioned + geo mentioned → "own" with a reason that
    cites both
  - primary mentioned but no geo → None (defer to LLM)
  - primary NOT mentioned → None (defer to LLM)
  - whole-word match — «багги» matches but «штаны багги» does NOT
    fire as own (LLM has to decide if it's noise)

Why no LLM here: we PIN that rules NEVER classify spam/adjacent/disputed.
That's intentional — preserving these as «defer to LLM» is part of
the cost / accuracy trade-off documented in IMPLEMENTATION-V2.md
etap 4.
"""

from __future__ import annotations

from app.core_audit.relevance import (
    ProfileSlice,
    classify_by_rules,
)


def _profile(**kwargs) -> ProfileSlice:
    """Build a ProfileSlice for tests with sensible defaults."""
    return ProfileSlice(
        primary_product=kwargs.get("primary_product", "багги"),
        services=kwargs.get("services", ["багги", "экспедиции"]),
        secondary_products=kwargs.get("secondary_products", ["маршруты"]),
        geo_primary=kwargs.get("geo_primary", ["сочи", "абхазия"]),
        geo_secondary=kwargs.get("geo_secondary", []),
    )


# ── Empty / incomplete profile ─────────────────────────────────────

def test_empty_query_returns_none() -> None:
    assert classify_by_rules("", _profile()) is None


def test_profile_without_primary_returns_none() -> None:
    p = _profile(primary_product="")
    assert classify_by_rules("багги сочи", p) is None


# ── The main «own» path ────────────────────────────────────────────

def test_primary_plus_primary_geo_is_own() -> None:
    v = classify_by_rules("багги сочи", _profile())
    assert v is not None
    assert v.relevance == "own"
    assert v.set_by == "rules"
    assert "багги" in v.reason_ru.lower()
    assert "сочи" in v.reason_ru.lower()


def test_primary_plus_secondary_geo_is_own() -> None:
    p = _profile(geo_primary=["сочи"], geo_secondary=["абхазия"])
    v = classify_by_rules("багги абхазия", p)
    assert v is not None
    assert v.relevance == "own"


def test_phrase_with_extra_words_still_own() -> None:
    """Real-world: «прокат багги в сочи дёшево» — has both anchors."""
    v = classify_by_rules("прокат багги в сочи дёшево", _profile())
    assert v is not None
    assert v.relevance == "own"


# ── Cases that defer to LLM (return None) ──────────────────────────

def test_primary_without_geo_defers() -> None:
    """«багги тур» — has primary but no region we operate in.
    Could be us or a competitor — LLM decides."""
    v = classify_by_rules("багги тур", _profile())
    assert v is None


def test_no_primary_defers() -> None:
    """«экскурсии сочи» — adjacent in real life, but rules can't
    tell. LLM needs the narrative_ru context."""
    v = classify_by_rules("экскурсии сочи", _profile())
    assert v is None


def test_only_geo_no_primary_defers() -> None:
    v = classify_by_rules("отдых в сочи", _profile())
    assert v is None


# ── Whole-word match ───────────────────────────────────────────────

def test_partial_match_inside_word_does_not_fire() -> None:
    """«прокатился» contains «прокат» as a substring. We must NOT
    fire `own` for partial substring — that would generate false
    positives in real Russian text."""
    p = _profile(primary_product="прокат")
    # «прокатился по сочи» — primary appears as substring, but
    # whole-word match should reject, so verdict = None (defer).
    v = classify_by_rules("прокатился по сочи", p)
    assert v is None


def test_compound_word_with_dash_matches() -> None:
    """«багги-тур» — primary appears with dash. The whole-word
    boundary should accept the dash as a separator."""
    v = classify_by_rules("багги-тур абхазия", _profile())
    # «багги» followed by «-» — our regex treats `-` as non-word
    # for both cyr and ascii, so this should fire `own`.
    assert v is not None
    assert v.relevance == "own"


def test_capitalisation_does_not_matter() -> None:
    v = classify_by_rules("Багги Сочи", _profile())
    assert v is not None
    assert v.relevance == "own"


# ── Things rules deliberately do NOT label ─────────────────────────

def test_rules_never_emit_spam() -> None:
    """«джинсы багги» — homonym. LLM territory. Rules return None,
    not «spam», because rules cannot tell jeans from vehicles."""
    v = classify_by_rules("джинсы багги", _profile())
    assert v is None  # NOT 'spam' — that's the LLM's job


def test_rules_never_emit_adjacent() -> None:
    v = classify_by_rules("экскурсии в сочи", _profile())
    assert v is None  # NOT 'adjacent' — that's the LLM's job


# ── ProfileSlice.from_target_config ────────────────────────────────

def test_profile_slice_lowercases_and_strips() -> None:
    cfg = {
        "primary_product": "  Багги  ",
        "services": ["  Экспедиции  ", "", None, "Прокат"],
        "geo_primary": ["Сочи", "АБХАЗИЯ"],
        "secondary_products": ["маршруты"],
    }
    p = ProfileSlice.from_target_config(cfg)
    assert p.primary_product == "багги"
    assert p.services == ["экспедиции", "прокат"]
    assert p.geo_primary == ["сочи", "абхазия"]


def test_profile_slice_handles_missing_keys() -> None:
    p = ProfileSlice.from_target_config({})
    assert p.primary_product == ""
    assert p.services == []
    assert p.geo_primary == []


def test_profile_slice_handles_none() -> None:
    p = ProfileSlice.from_target_config(None)
    assert p.primary_product == ""
