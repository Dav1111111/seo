"""Russian lemmatization via pymorphy3 — cached analyzer, graceful degradation."""

import logging
import re

from app.fingerprint.ru_stopwords import STOPWORDS
from app.fingerprint.version import MAX_TOKENS

logger = logging.getLogger(__name__)

try:
    import pymorphy3
    _ANALYZER = pymorphy3.MorphAnalyzer()
    _MORPH_AVAILABLE = True
except Exception as exc:
    logger.warning("pymorphy3 unavailable, lemmatization degrades to lowercase: %s", exc)
    _ANALYZER = None
    _MORPH_AVAILABLE = False


_TOKEN_RE = re.compile(r"[а-яё]+|[a-z]+", re.IGNORECASE)


def is_morph_available() -> bool:
    return _MORPH_AVAILABLE


def tokenize(text: str) -> list[str]:
    """Lowercase + extract alphabetic tokens (Cyrillic + Latin)."""
    if not text:
        return []
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


def lemmatize_tokens(tokens: list[str], drop_stopwords: bool = True) -> list[str]:
    """Lemmatize + optionally drop stopwords. Graceful degrade if pymorphy3 missing."""
    if not tokens:
        return []
    # Cap for cost control
    if len(tokens) > MAX_TOKENS:
        tokens = tokens[:MAX_TOKENS]

    if not _MORPH_AVAILABLE:
        # Degrade: just lowercase + stopword filter
        return [t for t in tokens if not (drop_stopwords and t in STOPWORDS)]

    result = []
    for t in tokens:
        if drop_stopwords and t in STOPWORDS:
            continue
        # pymorphy3.parse is the expensive call
        parsed = _ANALYZER.parse(t)
        if not parsed:
            result.append(t)
            continue
        lemma = parsed[0].normal_form
        if drop_stopwords and lemma in STOPWORDS:
            continue
        result.append(lemma)
    return result


def normalize_heading(text: str | None) -> str | None:
    """Normalize a title/H1: lowercase, tokenize, lemmatize, join."""
    if not text:
        return None
    tokens = tokenize(text)
    lemmas = lemmatize_tokens(tokens, drop_stopwords=False)
    if not lemmas:
        return None
    return " ".join(lemmas)
