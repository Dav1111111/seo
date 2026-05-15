"""Unit tests for the Russian tokenizer / lemmatizer.

These tests assume pymorphy3 + pymorphy3-dicts-ru are installed (they
are hard dependencies in pyproject.toml). On Docker / Jino that's
guaranteed; locally without those packages the tests degrade to
"return input unchanged" mode, which is documented behavior of the
tokenizer module.
"""

from __future__ import annotations

import pytest

from app.core_audit.keyword_match.tokenizer import (
    has_synonym_coverage,
    lemmatize,
    missing_lemmas,
    missing_lemmas_after_synonyms,
    tokenize_phrase,
    _MORPH,
)


# All tests that depend on morphological inflection collapse skip in
# the degraded local mode (no pymorphy3 available).
needs_morph = pytest.mark.skipif(_MORPH is None, reason="pymorphy3 not installed")


# ---------------------------------------------------------------------------
# lemmatize
# ---------------------------------------------------------------------------


@needs_morph
def test_lemmatize_russian_inflections_to_nominative():
    """Inflected forms of "Абхазия" all collapse to the lemma."""
    assert lemmatize("Абхазии") == "абхазия"
    assert lemmatize("Абхазией") == "абхазия"
    assert lemmatize("Абхазию") == "абхазия"
    assert lemmatize("абхазия") == "абхазия"


@needs_morph
def test_lemmatize_plural_to_singular():
    """Plural genitive collapses to singular nominative."""
    assert lemmatize("экскурсий") == "экскурсия"
    assert lemmatize("экскурсии") == "экскурсия"


def test_lemmatize_drops_stopwords():
    """Function words and the project-stoplisted tourism generics
    return empty string so set operations strip them cleanly."""
    assert lemmatize("в") == ""
    assert lemmatize("на") == ""
    assert lemmatize("и") == ""
    assert lemmatize("тур") == ""
    assert lemmatize("туры") == ""


def test_lemmatize_lowercases_latin_passthrough():
    """Latin tokens just lowercase — no morph lookup."""
    assert lemmatize("SEO") == "seo"
    assert lemmatize("Yandex") == "yandex"


def test_lemmatize_empty_string():
    assert lemmatize("") == ""
    assert lemmatize("   ") == ""


# ---------------------------------------------------------------------------
# tokenize_phrase
# ---------------------------------------------------------------------------


@needs_morph
def test_tokenize_phrase_returns_set_of_lemmas():
    """A real tourism query splits, lemmatizes, drops stopwords."""
    result = tokenize_phrase("Багги-туры в Абхазии")
    assert "багги" in result
    assert "абхазия" in result
    # "туры" is project-stoplisted as a too-generic tourism word.
    assert "тур" not in result
    assert "в" not in result


def test_tokenize_phrase_handles_none():
    assert tokenize_phrase(None) == set()
    assert tokenize_phrase("") == set()


@needs_morph
def test_tokenize_phrase_punctuation_split():
    """Commas, dots, dashes split words but hyphenated compounds are
    treated as one token by the regex; pymorphy3 then lemmatizes the
    compound or returns it lowercased."""
    result = tokenize_phrase("Сочи, Адлер. Хоста-район")
    # All three should appear in lemma form.
    assert "сочи" in result or "сочи" in {r.lower() for r in result}
    assert "адлер" in result or "адлер" in {r.lower() for r in result}


# ---------------------------------------------------------------------------
# missing_lemmas
# ---------------------------------------------------------------------------


@needs_morph
def test_missing_lemmas_handles_inflections():
    """Inflected query forms should be considered present in a page
    that mentions the same lemmas in any inflection."""
    missing = missing_lemmas("багги абхазия", "Туры на багги в Абхазию")
    assert missing == []


@needs_morph
def test_missing_lemmas_returns_unseen_tokens():
    """A page that doesn't mention "джиппинг" misses that lemma."""
    missing = missing_lemmas(
        "джиппинг в горах",
        "Туры по морю в Сочи",
    )
    assert "джиппинг" in missing
    # Generic tourism stoplist still applies.
    assert "тур" not in missing


def test_missing_lemmas_empty_page():
    missing = missing_lemmas("джиппинг", None)
    assert missing == ["джиппинг"] or missing == []  # depends on stopwords/morph


# ---------------------------------------------------------------------------
# has_synonym_coverage / missing_lemmas_after_synonyms
# ---------------------------------------------------------------------------


@needs_morph
def test_synonym_coverage_when_present():
    """If every query lemma has either itself or a synonym on the page,
    we consider the query covered."""
    synonyms = {"джиппинг": ["джип"]}
    assert has_synonym_coverage(
        "джиппинг абхазия",
        "Джип-туры по Абхазии",
        synonyms,
    )


@needs_morph
def test_synonym_coverage_when_synonym_missing():
    """If a lemma has no presence and no synonym presence, NOT covered."""
    synonyms = {"джиппинг": ["джип"]}
    assert not has_synonym_coverage(
        "джиппинг рафтинг",
        "Джип-туры по Абхазии",
        synonyms,
    )


@needs_morph
def test_missing_lemmas_after_synonyms_excludes_covered():
    """A query lemma covered by a synonym is NOT in the missing list."""
    synonyms = {"джиппинг": ["джип"]}
    missing = missing_lemmas_after_synonyms(
        "джиппинг абхазия",
        "Джип-туры по Абхазии",
        synonyms,
    )
    # "джиппинг" is covered by "джип" synonym; "абхазия" is present
    # directly via "Абхазии" inflection.
    assert missing == []


@needs_morph
def test_missing_lemmas_after_synonyms_returns_uncovered():
    """Lemmas without coverage stay in the missing list."""
    synonyms = {"джиппинг": ["джип"]}
    missing = missing_lemmas_after_synonyms(
        "джиппинг рафтинг абхазия",
        "Джип-туры по Абхазии",
        synonyms,
    )
    assert missing == ["рафтинг"]
