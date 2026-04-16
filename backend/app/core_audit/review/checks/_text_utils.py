"""Internal helpers for checks — lemmatization, normalization, density math.

Exposed only to check functions. External consumers should go through
app.fingerprint.lemmatize directly.
"""

from __future__ import annotations

from app.fingerprint.lemmatize import lemmatize_tokens, tokenize


def lemma_set(text: str | None, drop_stopwords: bool = True) -> frozenset[str]:
    """Tokenize + lemmatize + return unique lemmas as a set."""
    if not text:
        return frozenset()
    toks = tokenize(text)
    lemmas = lemmatize_tokens(toks, drop_stopwords=drop_stopwords)
    return frozenset(lemmas)


def lemma_list(text: str | None, drop_stopwords: bool = True) -> list[str]:
    """Tokenize + lemmatize + preserve order (for density denominator)."""
    if not text:
        return []
    return lemmatize_tokens(tokenize(text), drop_stopwords=drop_stopwords)


def density(
    target_lemmas: frozenset[str],
    content_lemmas: list[str],
) -> tuple[float, int]:
    """Return (density_ratio, match_count). Density = matches / len(content)."""
    if not content_lemmas or not target_lemmas:
        return 0.0, 0
    matches = sum(1 for t in content_lemmas if t in target_lemmas)
    return matches / len(content_lemmas), matches
