"""Tests for app.core_audit.relevance_llm — LLM half of the classifier.

Two surfaces pinned:

  _build_user_message — prompt-snapshot tests so prompt changes are
  intentional, not accidental. The Russian text is part of the contract
  with the model (Haiku is sensitive to prompt wording).

  classify_by_llm — mocks `call_with_tool` to verify result parsing,
  malformed-input coercion, idx out-of-range, duplicates, missing
  entries. No network, no DB.

Pattern matches `tests/core_audit/test_relevance_rules.py` — pure
functions, no fixtures, no asyncio.
"""

from __future__ import annotations

from unittest.mock import patch

from app.core_audit.relevance import ProfileSlice, RELEVANCE_VALUES
from app.core_audit.relevance_llm import (
    CLASSIFY_BATCH_SIZE,
    LLMClassificationResult,
    _build_user_message,
    classify_by_llm,
)


def _profile(**kw) -> ProfileSlice:
    return ProfileSlice(
        primary_product=kw.get("primary_product", "багги"),
        services=kw.get("services", ["багги", "экспедиции"]),
        secondary_products=kw.get("secondary_products", ["маршруты"]),
        geo_primary=kw.get("geo_primary", ["сочи", "абхазия"]),
        geo_secondary=kw.get("geo_secondary", []),
    )


# ── _build_user_message — prompt-snapshot ──────────────────────────


def test_user_message_has_all_profile_sections() -> None:
    """The prompt must surface all five profile dimensions + narrative.
    If a section is missing the model loses context and over-classifies
    as `disputed`."""
    msg = _build_user_message(
        _profile(),
        narrative_ru="Премиум багги-экспедиции в Сочи и Абхазии.",
        queries=["багги сочи", "джинсы багги"],
    )
    assert "ПРОФИЛЬ БИЗНЕСА:" in msg
    assert "основной продукт: багги" in msg
    assert "доп. продукты: маршруты" in msg
    assert "услуги: багги, экспедиции" in msg
    assert "основные регионы: сочи, абхазия" in msg
    assert "ОПИСАНИЕ БИЗНЕСА:" in msg
    assert "Премиум багги-экспедиции в Сочи и Абхазии." in msg
    assert "ЗАПРОСЫ" in msg


def test_user_message_indexes_queries_from_zero() -> None:
    """Index-based output protocol: queries MUST be numbered 0..N-1
    so the model returns matching `idx` values."""
    msg = _build_user_message(
        _profile(), narrative_ru="x",
        queries=["a", "b", "c"],
    )
    assert "  0. a" in msg
    assert "  1. b" in msg
    assert "  2. c" in msg
    # Off-by-one regression guard
    assert "  3. " not in msg


def test_user_message_em_dashes_for_empty_lists() -> None:
    """Empty lists should render as `—` so the model doesn't see
    raw `[]` and start hallucinating bracket syntax in reasons."""
    p = _profile(
        secondary_products=[],
        services=[],
        geo_secondary=[],
    )
    msg = _build_user_message(p, narrative_ru="", queries=["q"])
    assert "доп. продукты: —" in msg
    assert "услуги: —" in msg
    assert "доп. регионы: —" in msg
    # narrative also collapses to —
    assert "ОПИСАНИЕ БИЗНЕСА:\n—" in msg


def test_user_message_handles_empty_queries() -> None:
    """Empty queries list is a degenerate case — caller should never
    do this, but if they do the prompt still parses."""
    msg = _build_user_message(
        _profile(), narrative_ru="x", queries=[],
    )
    assert "ЗАПРОСЫ" in msg
    assert "Верни results" in msg


def test_user_message_unicode_safe() -> None:
    """Cyrillic + emoji in narrative shouldn't break the builder."""
    msg = _build_user_message(
        _profile(),
        narrative_ru="Премиум 🚙 багги в горах",
        queries=["багги 🌄"],
    )
    assert "🚙" in msg
    assert "  0. багги 🌄" in msg


# ── classify_by_llm — empty input ──────────────────────────────────


def test_classify_empty_returns_empty_no_llm_call() -> None:
    """No queries → no LLM call. Prevents the wasteful empty batch
    we'd otherwise pay for in classify_queries_site_task."""
    with patch("app.core_audit.relevance_llm.call_with_tool") as m:
        result = classify_by_llm([], _profile(), "narrative")
    assert isinstance(result, LLMClassificationResult)
    assert result.verdicts == {}
    assert result.cost_usd == 0.0
    assert result.input_tokens == 0
    m.assert_not_called()


# ── classify_by_llm — happy path parsing ───────────────────────────


def _mock_call(tool_input: dict, usage: dict | None = None):
    """Build a patch target for `call_with_tool` returning given value."""
    usage = usage or {
        "model": "claude-haiku-test",
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.0001,
    }
    return patch(
        "app.core_audit.relevance_llm.call_with_tool",
        return_value=(tool_input, usage),
    )


def test_classify_parses_well_formed_results() -> None:
    queries = ["багги сочи", "джинсы багги", "экскурсии сочи"]
    fake_tool_input = {
        "results": [
            {"idx": 0, "relevance": "own", "reason_ru": "Основной продукт + регион."},
            {"idx": 1, "relevance": "spam", "reason_ru": "Одежда, не транспорт."},
            {"idx": 2, "relevance": "adjacent", "reason_ru": "Тот же клиент."},
        ],
    }
    with _mock_call(fake_tool_input):
        result = classify_by_llm(queries, _profile(), "narrative")

    assert len(result.verdicts) == 3
    assert result.verdicts[0].relevance == "own"
    assert result.verdicts[0].set_by == "llm"
    assert "Основной" in result.verdicts[0].reason_ru
    assert result.verdicts[1].relevance == "spam"
    assert result.verdicts[2].relevance == "adjacent"
    assert result.cost_usd == 0.0001
    assert result.input_tokens == 100
    assert result.output_tokens == 50


def test_classify_unexpected_value_coerced_to_disputed() -> None:
    """The model can hallucinate values outside the enum (despite the
    JSON schema). Anything unknown must land in `disputed` so we never
    silently optimistically assign `own`."""
    fake = {
        "results": [
            {"idx": 0, "relevance": "weird-value", "reason_ru": "x"},
            {"idx": 1, "relevance": "OWN", "reason_ru": "y"},  # case-coerced
        ],
    }
    with _mock_call(fake):
        result = classify_by_llm(["q1", "q2"], _profile(), "narrative")
    assert result.verdicts[0].relevance == "disputed"
    assert "неожиданный" in result.verdicts[0].reason_ru
    # uppercase enum coerced via .lower()
    assert result.verdicts[1].relevance == "own"


def test_classify_unclassified_value_coerced_to_disputed() -> None:
    """`unclassified` is not allowed from LLM (it's the default state)."""
    fake = {
        "results": [
            {"idx": 0, "relevance": "unclassified", "reason_ru": "хз"},
        ],
    }
    with _mock_call(fake):
        result = classify_by_llm(["q1"], _profile(), "narrative")
    assert result.verdicts[0].relevance == "disputed"


def test_classify_idx_out_of_range_dropped() -> None:
    fake = {
        "results": [
            {"idx": 0, "relevance": "own", "reason_ru": "ok"},
            {"idx": 99, "relevance": "spam", "reason_ru": "ghost"},
            {"idx": -1, "relevance": "spam", "reason_ru": "negative"},
        ],
    }
    with _mock_call(fake):
        result = classify_by_llm(["q1"], _profile(), "narrative")
    assert set(result.verdicts.keys()) == {0}


def test_classify_duplicate_idx_keeps_first() -> None:
    """If the model emits the same index twice we keep the first
    verdict — predictable, and prevents a later malformed entry from
    overwriting an earlier valid one."""
    fake = {
        "results": [
            {"idx": 0, "relevance": "own", "reason_ru": "first"},
            {"idx": 0, "relevance": "spam", "reason_ru": "second"},
        ],
    }
    with _mock_call(fake):
        result = classify_by_llm(["q1"], _profile(), "narrative")
    assert result.verdicts[0].relevance == "own"
    assert result.verdicts[0].reason_ru == "first"


def test_classify_missing_idx_skipped_silently() -> None:
    """Malformed entries (missing required keys, wrong types) are
    skipped — caller treats missing as «model didn't return», retried
    on next run."""
    fake = {
        "results": [
            {"relevance": "own", "reason_ru": "no idx"},
            {"idx": "not-int", "relevance": "own", "reason_ru": "x"},
            "not-a-dict",
            None,
            {"idx": 0, "relevance": "spam", "reason_ru": "valid"},
        ],
    }
    with _mock_call(fake):
        result = classify_by_llm(["q1"], _profile(), "narrative")
    assert len(result.verdicts) == 1
    assert result.verdicts[0].relevance == "spam"


def test_classify_empty_reason_replaced_with_dash() -> None:
    """Blank reason becomes «—» (UI fallback) so the column is never
    empty in the DB."""
    fake = {
        "results": [
            {"idx": 0, "relevance": "own", "reason_ru": ""},
            {"idx": 1, "relevance": "spam"},  # missing reason_ru
        ],
    }
    with _mock_call(fake):
        result = classify_by_llm(["q1", "q2"], _profile(), "narrative")
    assert result.verdicts[0].reason_ru == "—"
    assert result.verdicts[1].reason_ru == "—"


def test_classify_no_results_key_returns_empty() -> None:
    """If the model returned an empty payload (or call_with_tool gave
    us {}) we surface zero verdicts — caller leaves rows alone for
    next run."""
    with _mock_call({}):
        result = classify_by_llm(["q1", "q2"], _profile(), "narrative")
    assert result.verdicts == {}
    assert result.input_tokens == 100  # usage still recorded


def test_classify_results_not_list_returns_empty() -> None:
    """`results` may come back as something weird (None, dict). Don't
    crash — return empty verdicts."""
    with _mock_call({"results": None}):
        result = classify_by_llm(["q1"], _profile(), "narrative")
    assert result.verdicts == {}


def test_classify_partial_results_leaves_holes() -> None:
    """Model returned 1 of 3 — caller's job to leave the other 2 alone.
    We just don't fabricate verdicts for missing indexes."""
    fake = {
        "results": [
            {"idx": 1, "relevance": "own", "reason_ru": "ок"},
        ],
    }
    with _mock_call(fake):
        result = classify_by_llm(["q1", "q2", "q3"], _profile(), "narrative")
    assert set(result.verdicts.keys()) == {1}


# ── Pinned constants — config drift guard ──────────────────────────


def test_batch_size_is_30() -> None:
    """If this changes, cost & token math in module docstring is stale."""
    assert CLASSIFY_BATCH_SIZE == 30


def test_relevance_values_contract() -> None:
    """The migration's CHECK constraint hard-codes these. Drift here
    means a row insert blows up in prod."""
    assert RELEVANCE_VALUES == (
        "own", "adjacent", "disputed", "spam", "unclassified",
    )
