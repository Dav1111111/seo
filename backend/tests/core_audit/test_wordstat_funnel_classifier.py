"""Funnel-aware Wordstat classifier — exhaustive branch coverage.

Owner's tourism config (pilot grandtourspirit.ru):
    primary="багги", geo_primary=["сочи","абхазия"],
    geo_secondary=["крым","кавказ"], services=["прокат","экспедиции"]

Five-way verdict: direct_product / funnel_warm / funnel_top /
out_of_market / spam. `accepted=False` is only for spam.
"""

from __future__ import annotations

import pytest

from app.collectors.tasks import classify_wordstat_discovery_phrase
from app.collectors.backfill_funnel_relevance import _looks_like_url
from app.profiles.tourism.funnel_intents import (
    DISCOVERY_INTENT_PREFIXES,
    COMMERCIAL_INTENT_PREFIXES,
    TOURISM_INTENT_PREFIXES,
    detect_intent_layer,
)
from app.profiles.tourism.ru_cities import (
    RU_CITIES_AND_REGIONS,
    is_other_russian_geo,
)


CFG = {
    "primary_product": "багги",
    "geo_primary": ["сочи", "абхазия"],
    "geo_secondary": ["крым", "кавказ"],
    "services": ["прокат", "экспедиции"],
}


# ── Spam: empty / URL ───────────────────────────────────────────────


def test_empty_phrase_is_spam() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase("", CFG)
    assert accepted is False and rel == "spam"


def test_whitespace_only_is_spam() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase("   ", CFG)
    assert accepted is False and rel == "spam"


def test_url_shaped_phrase_is_spam() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "https://example.ru/page", CFG,
    )
    assert accepted is False and rel == "spam"


def test_bare_domain_is_spam() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase("example.ru", CFG)
    assert accepted is False and rel == "spam"


# ── Spam: homonyms ──────────────────────────────────────────────────


def test_transformers_is_spam() -> None:
    accepted, rel, reason = classify_wordstat_discovery_phrase(
        "трансформеры", CFG,
    )
    assert accepted is False
    assert rel == "spam"
    assert "омоним" in reason


def test_buggy_jeans_is_spam() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "джинсы багги", CFG,
    )
    assert accepted is False and rel == "spam"


def test_buggy_pants_is_spam() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "штаны багги мужские", CFG,
    )
    assert accepted is False and rel == "spam"


def test_software_bug_is_spam() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "баги в программе", CFG,
    )
    assert accepted is False and rel == "spam"


def test_autopart_is_spam() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "карбюратор тойота", CFG,
    )
    assert accepted is False and rel == "spam"


def test_autobrand_is_spam() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "запчасти лада", CFG,
    )
    assert accepted is False and rel == "spam"


def test_cartoon_character_is_spam() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "мультфильм багги", CFG,
    )
    assert accepted is False and rel == "spam"


def test_wikipedia_is_spam() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "википедия багги", CFG,
    )
    assert accepted is False and rel == "spam"


# ── Spam: transit ───────────────────────────────────────────────────


def test_bus_schedule_is_spam() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "расписание автобусов сочи", CFG,
    )
    assert accepted is False and rel == "spam"


# ── Out of market ───────────────────────────────────────────────────


def test_buggy_moscow_is_out_of_market() -> None:
    accepted, rel, reason = classify_wordstat_discovery_phrase(
        "прокат багги в москве", CFG,
    )
    assert accepted is True
    assert rel == "out_of_market"
    assert "москва" in reason.lower()


def test_buggy_spb_is_out_of_market() -> None:
    accepted, rel, reason = classify_wordstat_discovery_phrase(
        "багги санкт-петербург", CFG,
    )
    assert accepted is True
    assert rel == "out_of_market"


def test_buggy_arkhyz_is_out_of_market_when_arkhyz_not_in_geos() -> None:
    cfg = dict(CFG)
    cfg["geo_secondary"] = ["крым"]  # no Archyz
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "багги архыз", cfg,
    )
    assert accepted is True
    assert rel == "out_of_market"


def test_excursions_moscow_is_out_of_market() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "экскурсии в москве", CFG,
    )
    assert accepted is True
    assert rel == "out_of_market"


# ── Direct product (hot) ────────────────────────────────────────────


def test_buggy_sochi_price_is_direct_product() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "багги сочи цена", CFG,
    )
    assert accepted is True
    assert rel == "direct_product"


def test_buggy_abkhazia_is_direct_product() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "багги абхазия", CFG,
    )
    assert accepted is True
    assert rel == "direct_product"


def test_book_buggy_is_direct_product_even_without_geo() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "забронировать багги", CFG,
    )
    assert accepted is True
    assert rel == "direct_product"


def test_buggy_price_without_geo_is_direct_product() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "багги цена", CFG,
    )
    assert accepted is True
    assert rel == "direct_product"


# ── Funnel warm (mid) ───────────────────────────────────────────────


def test_excursions_sochi_is_funnel_warm() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "экскурсии в сочи", CFG,
    )
    assert accepted is True
    assert rel == "funnel_warm"


def test_tours_abkhazia_is_funnel_warm() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "туры абхазия", CFG,
    )
    assert accepted is True
    assert rel == "funnel_warm"


def test_active_rest_sochi_is_funnel_warm() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "активный отдых в сочи", CFG,
    )
    assert accepted is True
    assert rel == "funnel_warm"


def test_buggy_alone_is_funnel_warm() -> None:
    """Primary product, no geo, no commercial intent → warm, not hot."""
    accepted, rel, _ = classify_wordstat_discovery_phrase("багги", CFG)
    assert accepted is True
    assert rel == "funnel_warm"


def test_book_excursion_sochi_is_funnel_warm() -> None:
    """Commercial in your geo without primary product is still warm."""
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "забронировать экскурсию сочи", CFG,
    )
    assert accepted is True
    assert rel == "funnel_warm"


# ── Funnel top (cold but huge) ──────────────────────────────────────


def test_entertainment_sochi_is_funnel_top() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "развлечения сочи", CFG,
    )
    assert accepted is True
    assert rel == "funnel_top"


def test_what_to_see_adler_is_funnel_top() -> None:
    """Adler is part of Greater Sochi but the test cfg only lists
    «сочи» / «абхазия». «адлер» is in `_RU_TO_CASE` mapping, so
    _wordstat_geo_terms expands «сочи» — but «адлер» itself isn't.
    Add it to make this test stable across cfg shapes.
    """
    cfg = dict(CFG)
    cfg["geo_primary"] = ["сочи", "адлер", "абхазия"]
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "что посмотреть в адлере", cfg,
    )
    assert accepted is True
    assert rel == "funnel_top"


def test_attractions_abkhazia_is_funnel_top() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "достопримечательности абхазии", CFG,
    )
    assert accepted is True
    assert rel == "funnel_top"


def test_where_to_go_sochi_is_funnel_top() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "куда сходить в сочи", CFG,
    )
    assert accepted is True
    assert rel == "funnel_top"


# ── Inflections ─────────────────────────────────────────────────────


def test_inflected_geo_matches_locative() -> None:
    """`отдых в сочи` — but if geo terms include «сочи», all forms
    match because `_wordstat_geo_terms` expands inflections."""
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "отдых в сочи летом", CFG,
    )
    assert accepted is True
    assert rel == "funnel_warm"


def test_inflected_abkhazia_genitive() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "туры из абхазии", CFG,
    )
    assert accepted is True
    assert rel == "funnel_warm"


# ── Edge cases / fallback ───────────────────────────────────────────


def test_only_geo_no_intent_is_spam() -> None:
    """Bare «сочи» alone has neither intent nor product → spam."""
    accepted, rel, _ = classify_wordstat_discovery_phrase("сочи", CFG)
    assert accepted is False
    assert rel == "spam"


def test_unknown_token_is_spam() -> None:
    accepted, rel, _ = classify_wordstat_discovery_phrase(
        "флюрный кодор бамбини", CFG,
    )
    assert accepted is False
    assert rel == "spam"


# ── ru_cities module ────────────────────────────────────────────────


def test_cities_set_is_nontrivial() -> None:
    # Sanity check — list should be ~100 entries with inflections.
    assert len(RU_CITIES_AND_REGIONS) >= 100


def test_cities_set_does_not_contain_my_geos() -> None:
    """Sochi/Adler/Abkhazia must NOT be in the «other» list — those are
    the pilot's primary market."""
    for term in ("сочи", "адлер", "абхазия", "абхазии", "абхазию"):
        assert term not in RU_CITIES_AND_REGIONS


def test_is_other_russian_geo_detects_moscow() -> None:
    found, name = is_other_russian_geo(["прокат", "багги", "в", "москве"], set())
    assert found is True
    assert name == "москва"


def test_is_other_russian_geo_skips_my_geo() -> None:
    found, name = is_other_russian_geo(
        ["багги", "сочи"], {"сочи", "абхазия"},
    )
    assert found is False
    assert name is None


def test_is_other_russian_geo_multiword_combo() -> None:
    found, name = is_other_russian_geo(
        ["туры", "в", "нижний", "новгород"], set(),
    )
    assert found is True
    assert name == "нижний новгород"


def test_is_other_russian_geo_accepts_phrase_string() -> None:
    found, name = is_other_russian_geo("отдых в крыму", set())
    assert found is True


# ── funnel_intents module ───────────────────────────────────────────


def test_intent_commercial_beats_tourism() -> None:
    assert detect_intent_layer(["забронировать", "экскурсию"], "забронировать экскурсию") == "commercial"


def test_intent_tourism_beats_discovery() -> None:
    assert detect_intent_layer(["экскурсии", "достопримечательности"], "экскурсии достопримечательности") == "tourism"


def test_intent_discovery() -> None:
    assert detect_intent_layer(["развлечения", "сочи"], "развлечения сочи") == "discovery"


def test_intent_multiword_discovery() -> None:
    assert detect_intent_layer(["что", "делать", "в", "сочи"], "что делать в сочи") == "discovery"


def test_intent_none() -> None:
    assert detect_intent_layer(["сочи"], "сочи") == "none"


def test_intent_blacklist_blocks_tsenny() -> None:
    """«цен» prefix matches «ценный» textually — but blacklist blocks."""
    assert detect_intent_layer(["ценный", "совет"], "ценный совет") != "commercial"


def test_intent_tur_does_not_match_turkmenistan() -> None:
    assert detect_intent_layer(["туркменистан"], "туркменистан") != "tourism"


# ── _looks_like_url helper ──────────────────────────────────────────


def test_looks_like_url_http() -> None:
    assert _looks_like_url("http://foo.com") is True


def test_looks_like_url_www() -> None:
    assert _looks_like_url("www.example.ru") is True


def test_looks_like_url_bare_domain() -> None:
    assert _looks_like_url("example.ru") is True


def test_looks_like_url_negative() -> None:
    assert _looks_like_url("экскурсии в сочи") is False
    assert _looks_like_url("") is False


# ── Constants visible from the public lists ─────────────────────────


def test_discovery_prefixes_nonempty() -> None:
    assert len(DISCOVERY_INTENT_PREFIXES) >= 10


def test_tourism_prefixes_nonempty() -> None:
    assert len(TOURISM_INTENT_PREFIXES) >= 10


def test_commercial_prefixes_nonempty() -> None:
    assert len(COMMERCIAL_INTENT_PREFIXES) >= 5
