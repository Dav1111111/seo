"""Tests for app.core_audit.strategic_focus — Studio v2 etap 7 Phase E.

Three contracts pinned:

  validate_and_normalise — owner UI form + LLM tool proposal both
  flow through this. If a future refactor accidentally drops a cap
  or weakens validation, the brain starts trusting bloat. These
  tests prevent that.

  from_target_config — round-trip via JSONB. The shape on disk must
  match what the API serialises.

  render_for_prompt — the prompt block that lands in the LLM. Two
  states (focus set / not set) — both must produce text that steers
  the model towards focus-aware answers and away from fabrication.
"""

from __future__ import annotations

import pytest

from app.core_audit.strategic_focus import (
    FocusValidationError,
    MAX_DEPRIORITISED,
    MAX_PRODUCTS,
    MAX_QUERY_SIGNALS,
    MAX_REGIONS,
    StrategicFocus,
    from_target_config,
    render_for_prompt,
    validate_and_normalise,
)


# ── validate_and_normalise ───────────────────────────────────────────


def test_validate_happy_path_normalises_and_assigns_active_since() -> None:
    payload = {
        "label": "  Багги-экспедиции в Абхазию  ",
        "products": ["Багги-Экспедиции", "  багги-Экспедиции  "],  # dupe + case
        "regions": ["Абхазия"],
        "query_signals": ["Багги Абхазия", "экскурсии абхазия"],
        "deprioritised": ["Яхты", "Вертолёты"],
        "exit_criterion": "  топ-10 по «экскурсии абхазия»  ",
        "owner_note": "Сначала это.",
    }
    f = validate_and_normalise(payload, set_by="owner_via_ui")

    assert f.label == "Багги-экспедиции в Абхазию"
    # Lowercased + deduped.
    assert f.products == ["багги-экспедиции"]
    assert f.regions == ["абхазия"]
    assert f.query_signals == ["багги абхазия", "экскурсии абхазия"]
    assert f.deprioritised == ["яхты", "вертолёты"]
    assert f.exit_criterion == "топ-10 по «экскурсии абхазия»"
    assert f.set_by == "owner_via_ui"
    # Server set active_since to a non-empty ISO timestamp.
    assert f.active_since
    assert "T" in f.active_since


def test_validate_rejects_missing_label() -> None:
    with pytest.raises(FocusValidationError):
        validate_and_normalise(
            {"products": ["x"]},
            set_by="owner_via_ui",
        )
    with pytest.raises(FocusValidationError):
        validate_and_normalise(
            {"label": "   "},
            set_by="owner_via_ui",
        )


def test_validate_rejects_no_signals() -> None:
    """At least one of products / regions / query_signals must be
    non-empty — otherwise downstream rules have nothing to match."""
    with pytest.raises(FocusValidationError):
        validate_and_normalise(
            {
                "label": "Что-то",
                "products": [],
                "regions": [],
                "query_signals": [],
            },
            set_by="owner_via_ui",
        )


def test_validate_caps_runaway_lists() -> None:
    """A bad LLM proposal could ship 200 products. Caps make sure that
    can't bloat target_config."""
    f = validate_and_normalise(
        {
            "label": "X",
            "products": [f"product-{i}" for i in range(MAX_PRODUCTS + 20)],
            "regions": [f"region-{i}" for i in range(MAX_REGIONS + 5)],
            "query_signals": [f"q-{i}" for i in range(MAX_QUERY_SIGNALS + 30)],
            "deprioritised": [f"d-{i}" for i in range(MAX_DEPRIORITISED + 50)],
        },
        set_by="owner_via_ui",
    )
    assert len(f.products) == MAX_PRODUCTS
    assert len(f.regions) == MAX_REGIONS
    assert len(f.query_signals) == MAX_QUERY_SIGNALS
    assert len(f.deprioritised) == MAX_DEPRIORITISED


def test_validate_rejects_invalid_set_by() -> None:
    with pytest.raises(FocusValidationError):
        validate_and_normalise(
            {"label": "X", "products": ["y"]},
            set_by="some_other_source",
        )


def test_validate_chat_set_by_tagged_correctly() -> None:
    f = validate_and_normalise(
        {"label": "X", "products": ["y"]},
        set_by="owner_via_chat",
    )
    assert f.set_by == "owner_via_chat"


# ── from_target_config — round-trip ─────────────────────────────────


def test_from_target_config_returns_none_for_empty() -> None:
    assert from_target_config(None) is None
    assert from_target_config({}) is None
    assert from_target_config({"strategic_focus": None}) is None
    assert from_target_config({"strategic_focus": {}}) is None
    # Even with a key but no label — treat as empty.
    assert from_target_config({"strategic_focus": {"label": "  "}}) is None


def test_round_trip_through_jsonb() -> None:
    """Serialise to JSONB, parse back — should be the same focus."""
    original = validate_and_normalise(
        {
            "label": "X-фокус",
            "products": ["a", "b"],
            "regions": ["r"],
            "query_signals": ["q1", "q2"],
            "deprioritised": ["d"],
            "exit_criterion": "когда",
            "owner_note": "n",
            "deadline": "2026-12-31",
        },
        set_by="owner_via_ui",
    )
    blob = {"strategic_focus": original.to_jsonb()}
    parsed = from_target_config(blob)
    assert parsed is not None
    assert parsed.label == original.label
    assert parsed.products == original.products
    assert parsed.regions == original.regions
    assert parsed.query_signals == original.query_signals
    assert parsed.deprioritised == original.deprioritised
    assert parsed.exit_criterion == original.exit_criterion
    assert parsed.owner_note == original.owner_note
    assert parsed.deadline == original.deadline
    assert parsed.set_by == "owner_via_ui"


# ── render_for_prompt ────────────────────────────────────────────────


def test_render_no_focus_says_so_and_invites_proposal() -> None:
    """When no focus is set, the LLM gets explicit «not set» context.
    Critical: this is what stops the model from inventing a focus
    out of thin air. It's the prompt-side half of «trust the data»."""
    block = render_for_prompt(None)
    assert "не задан" in block
    # Hints at the tool path so the model doesn't write a plain-text
    # «вот тебе фокус» — it must call propose_strategic_focus.
    assert "propose_strategic_focus" in block


def test_render_with_focus_includes_all_sections() -> None:
    f = StrategicFocus(
        label="L",
        active_since="2026-04-30T00:00:00+00:00",
        set_by="owner_via_ui",
        products=["p1"],
        regions=["r1"],
        query_signals=["q1"],
        deprioritised=["dep1"],
        exit_criterion="exit",
        owner_note="note",
        deadline="2026-12-31",
    )
    block = render_for_prompt(f)
    assert "ТЕКУЩИЙ ФОКУС" in block
    assert "главное: L" in block
    assert "p1" in block
    assert "r1" in block
    assert "q1" in block
    assert "dep1" in block
    assert "exit" in block
    assert "note" in block
    assert "2026-12-31" in block
    # The «if owner asks outside focus, gently redirect» rule must be
    # present — that's what makes focus actually shape replies.
    assert "вне фокуса" in block.lower() or "вне" in block.lower()
