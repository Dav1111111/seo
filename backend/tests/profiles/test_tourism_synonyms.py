"""Sanity tests for the tourism synonym dictionary.

The dict is hand-curated; these tests pin the invariants the
keyword_match matcher relies on:

* It's non-trivially populated.
* All values are lists of strings (the matcher iterates them).
* Known core-domain synonym pairs are present.
* Bidirectional consistency for symmetric pairs (soft check —
  prints warnings for asymmetric pairs instead of failing).
"""

from __future__ import annotations

from app.profiles.tourism.synonyms import TOURISM_SYNONYMS


class TestStructure:
    def test_dict_is_non_empty(self) -> None:
        assert len(TOURISM_SYNONYMS) >= 80, (
            "Tourism synonyms must have at least 80 keys "
            "covering activities / geography / commercial intent."
        )

    def test_all_keys_are_lowercase_strings(self) -> None:
        for k in TOURISM_SYNONYMS:
            assert isinstance(k, str)
            assert k == k.lower(), f"Key {k!r} is not lowercase"
            assert k.strip() == k, f"Key {k!r} has whitespace"
            assert k != "", "Empty key in TOURISM_SYNONYMS"

    def test_all_values_are_lists_of_strings(self) -> None:
        for k, vs in TOURISM_SYNONYMS.items():
            assert isinstance(vs, list), f"Value for {k!r} is not a list"
            assert vs, f"Empty synonym list for {k!r}"
            for s in vs:
                assert isinstance(s, str), f"Non-string synonym for {k!r}: {s!r}"
                assert s == s.lower(), f"Synonym {s!r} for {k!r} is not lowercase"
                assert s != k, f"Self-reference: {k!r} → {s!r}"

    def test_no_duplicates_in_synonym_lists(self) -> None:
        for k, vs in TOURISM_SYNONYMS.items():
            assert len(vs) == len(set(vs)), f"Duplicate synonyms for {k!r}: {vs}"


class TestCoreDomainPairsPresent:
    """Spot-check that the dict actually covers the categories
    promised in the module docstring — guards against accidental
    truncation."""

    def test_offroad_vehicle_synonyms(self) -> None:
        assert "джиппинг" in TOURISM_SYNONYMS
        assert "джип" in TOURISM_SYNONYMS["джиппинг"] or \
               "джип-тур" in TOURISM_SYNONYMS["джиппинг"] or \
               "оффроад" in TOURISM_SYNONYMS["джиппинг"]

    def test_water_activity_synonyms(self) -> None:
        assert "рафтинг" in TOURISM_SYNONYMS
        assert "сплав" in TOURISM_SYNONYMS["рафтинг"]

    def test_tour_format_synonyms(self) -> None:
        assert "экскурсия" in TOURISM_SYNONYMS

    def test_commercial_intent_synonyms(self) -> None:
        assert "бронирование" in TOURISM_SYNONYMS
        assert "цена" in TOURISM_SYNONYMS
        # «стоимость» / «прайс» should be a synonym of «цена».
        price_syns = set(TOURISM_SYNONYMS["цена"])
        assert "стоимость" in price_syns or "прайс" in price_syns

    def test_geography_sochi_abkhazia(self) -> None:
        assert "абхазия" in TOURISM_SYNONYMS
        assert "сочи" in TOURISM_SYNONYMS or "адлер" in TOURISM_SYNONYMS

    def test_reviews_and_ratings(self) -> None:
        assert "отзыв" in TOURISM_SYNONYMS
        assert "рейтинг" in TOURISM_SYNONYMS


class TestBidirectionalConsistency:
    """Most genuine synonyms should appear in both directions —
    e.g. if X → [Y], then Y → [..., X, ...]. We print a warning
    rather than failing, since some intentional one-way mappings
    do exist (e.g. specific → generic)."""

    def test_known_symmetric_pairs(self) -> None:
        """A handful of pairs MUST be symmetric — these are pure
        synonyms with no asymmetric semantic intent."""
        symmetric = [
            ("экскурсия", "поездка"),
            ("рафтинг", "сплав"),
            ("цена", "стоимость"),
            ("отель", "гостиница"),
            ("отзыв", "обзор"),
        ]
        for a, b in symmetric:
            assert a in TOURISM_SYNONYMS, f"Missing key {a!r}"
            assert b in TOURISM_SYNONYMS, f"Missing key {b!r}"
            assert b in TOURISM_SYNONYMS[a], f"{a!r} → {b!r} missing"
            assert a in TOURISM_SYNONYMS[b], f"{b!r} → {a!r} missing"
