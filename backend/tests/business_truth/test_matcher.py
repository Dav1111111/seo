"""Covers _token_stems / matches_vocab edge cases.

History: 3-char stem prefixes caused cross-word collisions — "судно"
(vessel) matched gazetteer entry "судак" (Crimean city) via shared
"суд" stem. Minimum stem length is now 4.
"""

from __future__ import annotations

from app.core_audit.business_truth.matcher import (
    _token_stems,
    matches_vocab,
)


def test_stem_collision_sudno_vs_sudak():
    """"судно" and "судак" must not collide — they share only 3 chars."""
    page = "флот от 30 до 50 футов опытные капитаны и судно"
    assert matches_vocab(page, "судно") is True
    assert matches_vocab(page, "судак") is False


def test_russian_inflection_preserved():
    """4-char stem is enough to cover usual Russian suffix drops."""
    assert matches_vocab("экскурсии по абхазии", "абхазия") is True
    assert matches_vocab("тур в абхазию", "абхазия") is True
    assert matches_vocab("из красной поляны", "красная поляна") is True


def test_short_geo_exact_only():
    """4-char tokens don't get truncated, match only exactly."""
    assert matches_vocab("в сочи сегодня", "сочи") is True
    # False positive guard — "сочный" should not match "сочи"
    assert matches_vocab("сочный виноград", "сочи") is False


def test_min_stem_length_4():
    """A 5-char token produces a 4-char prefix, not a 3-char one."""
    stems = _token_stems("судно")
    assert "судн" in stems
    assert "суд" not in stems


def test_6_char_token_produces_4_char_stem():
    """6-char token gets both 5- and 4-char prefixes."""
    stems = _token_stems("абхазии")
    assert "абхази" in stems
    assert "абхаз" in stems
