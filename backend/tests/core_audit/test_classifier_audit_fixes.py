"""Regression tests for the 2026-05-13 classifier/diagnosis audit.

Each test pins one of the cascade-bug fixes spelled out in the audit.
Keep them isolated — no DB, no network. Pattern mirrors
`test_relevance_llm.py` and `test_harmful_diagnoser.py`.
"""

from __future__ import annotations

from unittest.mock import patch

from app.collectors.yandex_serp import SerpDoc
from app.core_audit.competitors.tasks import (
    _business_tokens,
    _query_is_relevant,
)
from app.core_audit.harmful_diagnoser import find_matched_url
from app.core_audit.relevance import ProfileSlice
from app.core_audit.relevance_llm import classify_by_llm
from app.profiles.tourism.brand_tokens import TOURISM_BRAND_TOKENS


# ── Fix 1 — brand_tokens sanity ────────────────────────────────────


def test_brand_tokens_no_short_entries() -> None:
    """Tokens < 4 chars whole-word-match into legitimate queries
    (e.g. 'юк сочи', 'ук-фактор'); the fallback set must drop them."""
    for tok in TOURISM_BRAND_TOKENS:
        assert isinstance(tok, str)
        assert len(tok) >= 4, f"short fallback brand token: {tok!r}"


def test_brand_tokens_dropped_legacy_short_tokens() -> None:
    """Explicit guard: the two known-bad entries removed in the fix
    must stay out."""
    assert "юк" not in TOURISM_BRAND_TOKENS
    assert "ук" not in TOURISM_BRAND_TOKENS


# ── Fix 2 — _query_is_relevant word-boundary ───────────────────────


def test_query_is_relevant_word_boundary() -> None:
    """primary_product='тур' must NOT substring-match 'литература' /
    'структура' / 'турция'. The set-shape (legacy) path now uses
    token-set intersection."""
    biz = {"тур"}
    assert _query_is_relevant("литература", biz) is False
    assert _query_is_relevant("структура", biz) is False
    assert _query_is_relevant("турция", biz) is False
    # But true matches still pass.
    assert _query_is_relevant("тур в сочи", biz) is True
    assert _query_is_relevant("автобусный тур", biz) is True


def test_query_is_relevant_empty_set_admits_all() -> None:
    """Backward-compat: empty legacy set ⇒ no filter (relevance=True)."""
    assert _query_is_relevant("любой запрос", set()) is True


# ── Fix 3 — empty-after-filter widens to fail-closed ──────────────


def test_query_is_relevant_filtered_empty() -> None:
    """primary_product is in _GENERIC_QUERY_TOKENS — after filtering
    product_tokens becomes empty. We must fail closed, not fall through
    to region-only matching."""
    target_config = {
        "primary_product": "тур",  # in _GENERIC_QUERY_TOKENS
        "geo_primary": ["сочи"],
        # Intentionally no services / secondary_products
    }
    filt = _business_tokens(target_config)
    assert filt["fail_closed"] is True
    # Real-looking queries must be rejected outright now.
    assert _query_is_relevant("аренда жилья сочи", filt) is False
    assert _query_is_relevant("экскурсии сочи", filt) is False
    # …and even a query containing primary_product itself.
    assert _query_is_relevant("туры в сочи", filt) is False


def test_business_tokens_not_fail_closed_for_specific_product() -> None:
    """Sanity check the opposite case: a specific (non-generic)
    primary_product yields a normal filter, not fail-closed."""
    target_config = {
        "primary_product": "багги",
        "geo_primary": ["сочи"],
    }
    filt = _business_tokens(target_config)
    assert filt["fail_closed"] is False
    assert "багги" in filt["product_tokens"]
    assert _query_is_relevant("багги тур сочи", filt) is True


# ── Fix 4 — harmful_diagnoser two-char TLD reverse match ──────────


def _doc(position: int, url: str, domain: str) -> SerpDoc:
    return SerpDoc(
        position=position, url=url, domain=domain,
        title="t", headline="h",
    )


def test_harmful_diagnoser_rejects_two_char_tld_match() -> None:
    """SerpDoc.domain='ru' must NOT match our_domain='grandtourspirit.ru'
    just because our domain ends with '.ru'. The reverse-endswith branch
    was dropped in the audit fix."""
    docs = [_doc(1, "https://something.ru/page", "ru")]
    with patch(
        "app.core_audit.harmful_diagnoser.fetch_serp",
        return_value=(docs, None),
    ):
        m = find_matched_url("любой запрос", "grandtourspirit.ru")
    assert m is None


def test_harmful_diagnoser_still_matches_subdomain() -> None:
    """Regression guard: dropping the reverse branch must NOT break
    the legitimate subdomain → root domain match."""
    docs = [_doc(1, "https://shop.grandtourspirit.ru/p", "shop.grandtourspirit.ru")]
    with patch(
        "app.core_audit.harmful_diagnoser.fetch_serp",
        return_value=(docs, None),
    ):
        m = find_matched_url("any", "grandtourspirit.ru")
    assert m is not None
    assert m.url == "https://shop.grandtourspirit.ru/p"


# ── Fix 7 — thin-profile own-confidence floor ─────────────────────


def _thin_profile() -> ProfileSlice:
    """Bare profile: no narrative, very short services list."""
    return ProfileSlice(
        primary_product="багги",
        services=["багги"],  # < 2 entries
        secondary_products=[],
        geo_primary=["сочи"],
        geo_secondary=[],
    )


def _fat_profile() -> ProfileSlice:
    return ProfileSlice(
        primary_product="багги",
        services=["багги", "экспедиции", "трофи"],
        secondary_products=["туры"],
        geo_primary=["сочи", "абхазия"],
        geo_secondary=[],
    )


def test_relevance_llm_thin_profile_coerces_low_confidence_own_to_unclassified() -> None:
    """Low-confidence 'own' verdict against a thin profile → unclassified."""
    fake_tool_input = {
        "results": [
            {"idx": 0, "relevance": "own", "reason_ru": "вроде про багги", "confidence": 0.5},
        ],
    }
    fake_usage = {
        "model": "claude-haiku-test", "input_tokens": 10,
        "output_tokens": 5, "cost_usd": 0.0,
    }
    with patch(
        "app.core_audit.relevance_llm.call_with_tool",
        return_value=(fake_tool_input, fake_usage),
    ):
        result = classify_by_llm(["багги москва"], _thin_profile(), narrative_ru="")
    assert result.verdicts[0].relevance == "unclassified"
    # Reason gets a flag the owner will see in the UI.
    assert "ручн" in result.verdicts[0].reason_ru.lower() or "проверк" in result.verdicts[0].reason_ru.lower()


def test_relevance_llm_thin_profile_high_confidence_own_stays_own() -> None:
    """High-confidence (>= 0.7) 'own' must NOT be coerced, even on thin
    profile — owner trusts the model when it's sure."""
    fake_tool_input = {
        "results": [
            {"idx": 0, "relevance": "own", "reason_ru": "точно наше", "confidence": 0.9},
        ],
    }
    with patch(
        "app.core_audit.relevance_llm.call_with_tool",
        return_value=(
            fake_tool_input,
            {"model": "m", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0},
        ),
    ):
        result = classify_by_llm(["багги сочи"], _thin_profile(), narrative_ru="")
    assert result.verdicts[0].relevance == "own"


def test_relevance_llm_fat_profile_low_confidence_own_stays_own() -> None:
    """When the profile is rich (narrative + services), low confidence
    alone is NOT enough to coerce — only thin-AND-low triggers."""
    fake_tool_input = {
        "results": [
            {"idx": 0, "relevance": "own", "reason_ru": "ok", "confidence": 0.4},
        ],
    }
    with patch(
        "app.core_audit.relevance_llm.call_with_tool",
        return_value=(
            fake_tool_input,
            {"model": "m", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0},
        ),
    ):
        result = classify_by_llm(
            ["багги сочи"],
            _fat_profile(),
            narrative_ru="Премиум багги-туры в Сочи и Абхазии.",
        )
    assert result.verdicts[0].relevance == "own"


def test_relevance_llm_missing_confidence_treated_as_certain() -> None:
    """Backward-compat: old prompt outputs without a 'confidence' field
    must continue to be persisted as-is (treated as confidence=1.0)."""
    fake_tool_input = {
        "results": [
            {"idx": 0, "relevance": "own", "reason_ru": "ok"},
        ],
    }
    with patch(
        "app.core_audit.relevance_llm.call_with_tool",
        return_value=(
            fake_tool_input,
            {"model": "m", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0},
        ),
    ):
        result = classify_by_llm(["q"], _thin_profile(), narrative_ru="")
    assert result.verdicts[0].relevance == "own"
