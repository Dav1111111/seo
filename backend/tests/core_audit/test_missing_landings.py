"""Tests for app.core_audit.missing_landings — Studio v2 etap 6.

The KEY guarantee of this module is the evidence-quote anti-hallucination
filter: anything LLM returns whose `evidence_quote` is NOT a substring of
the actual narrative gets dropped before reaching the owner. These tests
pin that guarantee plus the input-shaping helpers around it.

No network, no DB. Pure functions + a single mock for `call_with_tool`.
"""

from __future__ import annotations

from unittest.mock import patch

from app.core_audit.missing_landings import (
    MAX_ITEMS,
    build_business_signal,
    build_pages_block,
    evidence_in_signal,
    find_missing_landings,
)


# ── build_business_signal ────────────────────────────────────────────


def test_business_signal_includes_narrative_facts_and_target_config() -> None:
    """The LLM must see narrative + observed_facts + target_config in
    one string. If any layer is missing the detector loses context and
    drops valid gaps."""
    understanding = {
        "narrative_ru": "Премиум багги-экспедиции в Сочи и Абхазии.",
        "observed_facts": [
            {"fact": "Сезон 2026 — экспедиции в Крым.", "page_ref": "https://x.ru/"},
            {"fact": "Партнёры: яхты и вертолёты.", "page_ref": ""},
        ],
    }
    target_config = {
        "primary_product": "багги",
        "services": ["багги", "экспедиции"],
        "secondary_products": ["вертолёты"],
        "geo_primary": ["сочи", "абхазия"],
        "geo_secondary": ["крым"],
    }
    s = build_business_signal(
        understanding=understanding, target_config=target_config,
    )
    assert "Премиум багги-экспедиции в Сочи и Абхазии." in s
    assert "Сезон 2026 — экспедиции в Крым." in s
    assert "Партнёры: яхты и вертолёты." in s
    assert "основной продукт: багги" in s
    assert "услуги: багги, экспедиции" in s
    assert "дополнительные продукты: вертолёты" in s
    assert "основные регионы: сочи, абхазия" in s
    assert "второстепенные регионы: крым" in s


def test_business_signal_handles_empty_inputs() -> None:
    """No understanding + no target_config must not crash. The signal
    just degenerates to «—» placeholders — that's fine, the caller's
    skipped-task guard will catch it before this is called for real."""
    s = build_business_signal(understanding=None, target_config=None)
    assert "ОПИСАНИЕ БИЗНЕСА:" in s
    # Don't pin the «—» character literally — what we care about is no
    # exception and a stable shape.
    assert "СЛУЖЕБНЫЕ ПОЛЯ:" in s
    assert "НАБЛЮДАЕМЫЕ ФАКТЫ" in s


def test_business_signal_string_observed_facts_supported() -> None:
    """Older onboarding snapshots stored facts as plain strings — the
    builder must accept both `str` and `{fact, page_ref}` shapes so we
    don't crash on legacy data."""
    s = build_business_signal(
        understanding={"narrative_ru": "n", "observed_facts": ["plain string fact"]},
        target_config={},
    )
    assert "plain string fact" in s


# ── build_pages_block ────────────────────────────────────────────────


def test_pages_block_truncates_snippet_and_keeps_url() -> None:
    """Long content must be truncated; the URL must always appear so the
    LLM can ground `closest_existing_url` in real paths."""
    block = build_pages_block([
        {
            "path": "/abkhazia",
            "title": "Абхазия — багги",
            "h1": "Туры в Абхазию",
            "meta_description": "ежедневные багги-экспедиции",
            "content_snippet": "x" * 10_000,
        },
    ])
    assert "url: /abkhazia" in block
    assert "title: Абхазия — багги" in block
    assert "h1: Туры в Абхазию" in block
    # Snippet was cut to SNIPPET_CHARS (400) — never the full 10k.
    assert block.count("x") < 1_000


# ── evidence_in_signal — the anti-hallucination gate ─────────────────


def test_evidence_match_when_quote_is_real_substring() -> None:
    sig = "Премиум багги-экспедиции в Сочи и Абхазии. Сезон 2026 — Крым."
    assert evidence_in_signal("экспедиции в Сочи", sig)
    assert evidence_in_signal("Сезон 2026 — Крым", sig)


def test_evidence_match_is_punctuation_and_case_insensitive() -> None:
    """Different quote marks / case must not break the gate — that
    would create false rejections on perfectly valid LLM output."""
    sig = "Премиум багги — экспедиции в «Сочи»."
    # Different dash / quote chars in evidence vs source.
    assert evidence_in_signal("Премиум БАГГИ - экспедиции в \"Сочи\"", sig)


def test_evidence_rejected_when_fabricated() -> None:
    """The KEY guarantee: a quote that isn't in the signal must be
    dropped. Without this we'd surface LLM hallucinations to the owner
    as if they were real services."""
    sig = "Премиум багги-экспедиции в Сочи и Абхазии."
    # Plausible-sounding but absent from the signal.
    assert not evidence_in_signal("корпоративные туры для команд", sig)


def test_evidence_rejected_when_too_short() -> None:
    """Tiny fragments match anything by chance — we require ≥ 8 chars
    of real content. This stops «и», «1», «то» from accidentally
    validating fabricated services."""
    sig = "Премиум багги-экспедиции в Сочи и Абхазии."
    assert not evidence_in_signal("и", sig)
    assert not evidence_in_signal("Сочи", sig)  # 4 normalized chars


# ── find_missing_landings — end-to-end with mocked LLM ───────────────


def _stub_understanding() -> dict:
    return {
        "narrative_ru": (
            "Премиум багги-экспедиции в Сочи и Абхазии. На сезон 2026 "
            "открыт набор на экспедиции в Крым."
        ),
        "observed_facts": [
            {"fact": "Партнёры: яхты и вертолёты.", "page_ref": ""},
        ],
    }


def _stub_target_config() -> dict:
    return {
        "primary_product": "багги",
        "services": ["багги", "экспедиции"],
        "geo_primary": ["сочи", "абхазия"],
    }


def _stub_pages() -> list[dict]:
    return [
        {"path": "/", "title": "Главная", "h1": "GTS"},
        {"path": "/abkhazia", "title": "Абхазия — багги", "h1": "Туры"},
    ]


def test_full_run_keeps_only_items_with_real_evidence() -> None:
    """One LLM proposal grounded in narrative is kept; one fabricated
    is rejected. The shape of the kept item must be the JSONB contract
    the API serialises."""
    fake_tool_input = {
        "missing": [
            {
                "service_name": "Крым",
                # Real substring of narrative.
                "evidence_quote": "На сезон 2026 открыт набор на экспедиции в Крым",
                "closest_existing_url": "/abkhazia",
                "suggested_url_path": "/experiences/exp-crimea",
                "why_it_matters_ru": "Под Крым нет страницы — теряешь трафик.",
                "priority": "high",
            },
            {
                "service_name": "Корпоративные туры",
                # Plausible but absent — must be dropped.
                "evidence_quote": "корпоративные туры для команд",
                "closest_existing_url": None,
                "suggested_url_path": "/corporate",
                "why_it_matters_ru": "—",
                "priority": "medium",
            },
        ],
        "summary_ru": "Покрытие неполное.",
    }
    fake_usage = {"model": "claude-haiku-4-5", "cost_usd": 0.012}

    with patch(
        "app.core_audit.missing_landings.call_with_tool",
        return_value=(fake_tool_input, fake_usage),
    ):
        result = find_missing_landings(
            understanding=_stub_understanding(),
            target_config=_stub_target_config(),
            pages=_stub_pages(),
        )

    assert len(result["items"]) == 1
    assert result["items"][0]["service_name"] == "Крым"
    assert result["items"][0]["priority"] == "high"
    assert result["rejected_no_evidence"] == 1
    assert result["model"] == "claude-haiku-4-5"
    assert result["cost_usd"] == 0.012
    assert result["input_pages"] == 2
    assert result["summary_ru"] == "Покрытие неполное."
    assert "computed_at" in result


def test_full_run_drops_all_when_llm_fabricates_everything() -> None:
    """If the LLM goes off the rails entirely we surface zero items —
    not «one fake item that sort of sounded right». This is the
    behaviour the owner needs to trust the output."""
    fake_tool_input = {
        "missing": [
            {
                "service_name": "X",
                "evidence_quote": "выдумка",
                "closest_existing_url": None,
                "suggested_url_path": "/x",
                "why_it_matters_ru": "—",
                "priority": "high",
            },
        ],
        "summary_ru": "—",
    }
    with patch(
        "app.core_audit.missing_landings.call_with_tool",
        return_value=(fake_tool_input, {"model": "h", "cost_usd": 0.0}),
    ):
        result = find_missing_landings(
            understanding=_stub_understanding(),
            target_config=_stub_target_config(),
            pages=_stub_pages(),
        )
    assert result["items"] == []
    assert result["rejected_no_evidence"] == 1


def test_full_run_caps_at_max_items() -> None:
    """If LLM returns 50 valid items we still cap at MAX_ITEMS so the
    UI doesn't drown. Sort is high → medium → low."""
    real_quote = "Премиум багги-экспедиции в Сочи и Абхазии"
    fake_tool_input = {
        "missing": [
            {
                "service_name": f"svc-{i}",
                "evidence_quote": real_quote,
                "closest_existing_url": None,
                "suggested_url_path": f"/{i}",
                "why_it_matters_ru": "—",
                "priority": "low" if i % 2 else "high",
            }
            for i in range(MAX_ITEMS + 5)
        ],
        "summary_ru": "many",
    }
    with patch(
        "app.core_audit.missing_landings.call_with_tool",
        return_value=(fake_tool_input, {"model": "h", "cost_usd": 0.0}),
    ):
        result = find_missing_landings(
            understanding=_stub_understanding(),
            target_config=_stub_target_config(),
            pages=_stub_pages(),
        )
    assert len(result["items"]) == MAX_ITEMS
    # First items must be high-priority (sort guarantee).
    assert result["items"][0]["priority"] == "high"


def test_full_run_handles_empty_llm_response() -> None:
    """LLM returning an empty list is a valid «no gaps found» — must
    not crash, must return summary_ru so UI can show the verdict."""
    with patch(
        "app.core_audit.missing_landings.call_with_tool",
        return_value=(
            {"missing": [], "summary_ru": "Все покрыто."},
            {"model": "h", "cost_usd": 0.001},
        ),
    ):
        result = find_missing_landings(
            understanding=_stub_understanding(),
            target_config=_stub_target_config(),
            pages=_stub_pages(),
        )
    assert result["items"] == []
    assert result["summary_ru"] == "Все покрыто."
    assert result["rejected_no_evidence"] == 0
