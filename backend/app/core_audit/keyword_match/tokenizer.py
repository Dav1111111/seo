"""Russian-aware tokenizer / lemmatizer for the keyword_match module.

Uses pymorphy3 (which is already a hard dependency for the wider project)
for morphological normalization. Cyrillic input gets lemmatized to its
nominative singular base form; Latin / numeric tokens pass through
lowercased.

Design choices worth keeping:

* `lemmatize()` is `lru_cache`-d. pymorphy3 parses are deterministic and
  this avoids re-walking the dictionary for the dozens of times we see
  "тур" / "сочи" per audit.
* Stopwords are a tiny, hand-picked set focused on Russian function
  words that never carry SEO meaning. We deliberately keep this list
  small — "цена", "купить", "недорого" stay IN, because they ARE the
  commercial intent we're matching on. We DO drop "тур"/"туры" — the
  word is so generic for a tourism site that scoring it as "missing"
  would generate noise on every page.
* If pymorphy3 fails to import (degraded local dev env, no Russian
  dict installed), the lemmatizer becomes the identity function — the
  module still runs, just with poorer recall on inflected forms.
"""

from __future__ import annotations

import re
from functools import lru_cache

try:
    import pymorphy3  # type: ignore[import-not-found]

    _MORPH = pymorphy3.MorphAnalyzer()
except ImportError:  # pragma: no cover — only fires in stripped envs
    _MORPH = None


# Split on whitespace AND hyphens AND punctuation. Critical: «багги-туры»
# must become {«багги», «тур»}, not «{багга-тура}». pymorphy3 lemmatizes
# whole hyphenated tokens to weird forms (e.g. «багги-туры» → «багга-тура»),
# so we explicitly break them. Underscores are also split — slug parts
# need separate lookup.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Russian function words + tourism-domain too-generic terms.
# "тур"/"туры" go here because a tourism site that's missing the word
# "тур" doesn't exist — flagging it is noise on every page.
_STOPWORDS_RU: frozenset[str] = frozenset({
    "и", "в", "во", "на", "с", "со", "по", "для", "из", "за", "от",
    "до", "не", "ни", "к", "ко", "у", "о", "об", "обо", "при",
    "что", "как", "это", "этот", "эта", "это", "тот", "та", "то",
    "а", "но", "или", "же", "ли", "бы", "тур", "туры",
})


@lru_cache(maxsize=10000)
def lemmatize(word: str) -> str:
    """Reduce a Russian word to its lemma.

    Returns "" for empty input or stopwords — call sites use a
    `… - {""}` filter to drop those cleanly.

    English / digit tokens pass through lowercased without morph
    analysis (pymorphy3 would just return them unchanged anyway).
    """
    word = word.lower().strip()
    if not word:
        return ""
    if word in _STOPWORDS_RU:
        return ""
    if _MORPH is None:
        return word
    parses = _MORPH.parse(word)
    if not parses:
        return word
    lemma = parses[0].normal_form
    # Re-check stopwords on the lemma — "тура" → "тур" should also be
    # dropped, and pymorphy3 sometimes maps function words via uncommon
    # spellings.
    if lemma in _STOPWORDS_RU:
        return ""
    return lemma


def tokenize_phrase(text: str | None) -> set[str]:
    """Split text into a set of lemmas, dropping stopwords + empty.

    A set is returned because duplicate-token "coverage" is binary: a
    page either has the lemma or it doesn't, count doesn't matter for
    the missing-tokens check.
    """
    if not text:
        return set()
    tokens = _WORD_RE.findall(text)
    return {lemmatize(t) for t in tokens} - {""}


def missing_lemmas(query_text: str, page_text: str | None) -> list[str]:
    """Lemmas from query NOT present in page text.

    Sorted for deterministic output (so test fixtures and DB rows
    stay stable run-to-run).
    """
    query_lemmas = tokenize_phrase(query_text)
    page_lemmas = tokenize_phrase(page_text)
    return sorted(query_lemmas - page_lemmas)


def has_synonym_coverage(
    query_text: str,
    page_text: str | None,
    synonyms: dict[str, list[str]],
) -> bool:
    """True if every query lemma is either present OR has a synonym present.

    `synonyms` maps lemma → list of synonym lemmas (already pre-lemmatized
    — the tourism vocabulary file stores them in canonical form).

    Used by the matcher to decide whether to set `has_synonym_in_title`
    True for a (query, page) pair even though some lemmas formally
    "miss" the title.
    """
    query_lemmas = tokenize_phrase(query_text)
    page_lemmas = tokenize_phrase(page_text)
    for ql in query_lemmas:
        if ql in page_lemmas:
            continue
        syns = synonyms.get(ql, [])
        if any(s in page_lemmas for s in syns):
            continue
        return False
    return True


def missing_lemmas_after_synonyms(
    query_text: str,
    page_text: str | None,
    synonyms: dict[str, list[str]],
) -> list[str]:
    """Same as `missing_lemmas` but a lemma that has a synonym present
    on the page is NOT considered missing.

    This is what the DTO's `missing_in_title_lemmas` actually wants — a
    page titled "джип-туры по Абхазии" should not be flagged as missing
    "джиппинг" when the synonym "джип" is right there.
    """
    query_lemmas = tokenize_phrase(query_text)
    page_lemmas = tokenize_phrase(page_text)
    missing: list[str] = []
    for ql in sorted(query_lemmas):
        if ql in page_lemmas:
            continue
        syns = synonyms.get(ql, [])
        if any(s in page_lemmas for s in syns):
            continue
        missing.append(ql)
    return missing
