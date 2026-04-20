"""Extract candidate services from crawled pages (Phase F).

Algorithm
---------
1. Build a canonical services vocabulary:
   - Union of literal words from tourism `seed_cluster_templates` slot
     usage, minus the universal nouns ("туры", "экскурсии") which are
     always added back unconditionally at the end.
   - Plus a hardcoded tourism-activity vocabulary: багги, яхты, ...
2. For each page, tokenize title + h1 (weighted 2x) + first 500 chars
   of content_text (weighted 1x). Lowercase only; no morphology because
   the vocabulary entries are already canonical lemma forms.
3. Tally occurrence per service. Rank by `occurrence × pages_mentioned`.
4. Keep top 10 services where occurrence_count >= 2 (hard threshold).
5. Always include "экскурсии" and "туры" (universal tourism nouns).

The extractor is pure — no DB, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from app.core_audit.draft_profile.dto import ExtractedService


# Universal tourism nouns — always included regardless of page content.
_UNIVERSAL_SERVICES: frozenset[str] = frozenset({"экскурсии", "туры"})

# Generic nouns that should NOT be counted as services on their own.
_GENERIC_STOPWORDS: frozenset[str] = frozenset({
    "туры", "тур", "экскурсии", "экскурсия",
    "отдых", "путешествия", "путешествие",
})

# Tourism-specific activity vocabulary (lemma form, lowercase).
_TOURISM_ACTIVITIES: frozenset[str] = frozenset({
    "багги", "яхты", "яхта", "вертолёт", "вертолет",
    "квадроциклы", "квадроцикл", "джиппинг", "джипы",
    "морские", "прогулки", "прогулка",
    "рафтинг", "каякинг", "сёрфинг", "серфинг",
    "дайвинг", "сноркелинг", "снорклинг",
    "горнолыжные", "сноуборд", "катание",
    "трекинг", "хайкинг", "походы", "поход",
    "конные", "верховая", "лошади",
    "рыбалка", "охота",
    "сплав", "сплавы",
    "парапланы", "параплан", "парапланеризм",
    "круизы", "круиз", "паром",
    "трансфер", "трансферы",
    "отели", "гостиницы", "апартаменты",
    "виза", "визы", "страховка",
})

# Canonical service vocabulary — built at import time.
# We expose the function so tests can rebuild from an arbitrary profile.
# Non-universal words from tourism seed templates are heuristically
# scanned here; we can't import the tourism profile at module load to
# avoid a circular import if draft_profile is ever imported from there.
_EXTRA_FROM_TEMPLATES: frozenset[str] = frozenset({
    # These strings appear literally in tourism seed templates
    # (pattern= strings) as non-slot tokens — they are real services.
    "туры", "экскурсии", "трансфер", "аренда",
    "бронирование", "отели",
    # Common Russian plural/sg variants seen in templates.
    "экскурсия",
})

CANONICAL_SERVICES: frozenset[str] = frozenset(
    _EXTRA_FROM_TEMPLATES | _TOURISM_ACTIVITIES
)


_TOKEN_RE = re.compile(r"[а-яёa-z0-9\-]+", re.IGNORECASE)


def _tokenize(text: str | None) -> list[str]:
    if not text:
        return []
    return [t.lower() for t in _TOKEN_RE.findall(text)]


@dataclass(frozen=True)
class _PageInput:
    """Duck-typed page-like object required by the extractor.

    The real caller passes `app.models.page.Page` rows; tests pass
    lightweight dataclasses with the same three attributes.
    """

    title: str | None
    h1: str | None
    content_text: str | None


def _coerce_page(obj: object) -> _PageInput:
    return _PageInput(
        title=getattr(obj, "title", None),
        h1=getattr(obj, "h1", None),
        content_text=getattr(obj, "content_text", None),
    )


def extract_services(
    pages: Sequence[object],
    *,
    vocabulary: Iterable[str] | None = None,
    top_k: int = 10,
    min_occurrences: int = 2,
    content_char_cap: int = 500,
) -> list[ExtractedService]:
    """Return up to `top_k` ExtractedService entries for the given pages.

    Parameters
    ----------
    pages:
        Sequence of page-like objects with `title`, `h1`, `content_text`.
    vocabulary:
        Override canonical vocabulary (tests). Defaults to
        `CANONICAL_SERVICES`.
    top_k:
        Maximum non-universal services to return.
    min_occurrences:
        Minimum total occurrences required for a service to survive.
    content_char_cap:
        First N chars of content_text scanned per page (cost control).
    """
    vocab: frozenset[str] = frozenset(
        (v.lower() for v in (vocabulary or CANONICAL_SERVICES))
    )
    # Remove generic stopwords from what we'll count, we re-inject the
    # universals at the end.
    vocab_filtered = vocab - _GENERIC_STOPWORDS

    occurrences: dict[str, int] = {}
    pages_with: dict[str, int] = {}
    total_pages = 0

    for raw in pages:
        page = _coerce_page(raw)
        total_pages += 1

        title_h1_tokens = _tokenize(page.title) + _tokenize(page.h1)
        body = (page.content_text or "")[:content_char_cap]
        body_tokens = _tokenize(body)

        per_page_seen: set[str] = set()

        # Title + h1 weight 2x.
        for tok in title_h1_tokens:
            if tok in vocab_filtered:
                occurrences[tok] = occurrences.get(tok, 0) + 2
                per_page_seen.add(tok)

        # Body weight 1x.
        for tok in body_tokens:
            if tok in vocab_filtered:
                occurrences[tok] = occurrences.get(tok, 0) + 1
                per_page_seen.add(tok)

        for tok in per_page_seen:
            pages_with[tok] = pages_with.get(tok, 0) + 1

    # Rank by (occurrence × pages_with). Break ties by name for
    # determinism.
    ranked = sorted(
        (
            (name, cnt, pages_with.get(name, 0))
            for name, cnt in occurrences.items()
            if cnt >= min_occurrences
        ),
        key=lambda x: (-(x[1] * x[2]), -x[1], x[0]),
    )[:top_k]

    pages_denom = max(total_pages * 0.3, 1.0)
    results: list[ExtractedService] = []
    seen_names: set[str] = set()

    for name, occ, pcount in ranked:
        conf = min(1.0, occ / pages_denom)
        results.append(
            ExtractedService(
                name=name,
                occurrence_count=int(occ),
                pages_with=int(pcount),
                confidence=float(conf),
            )
        )
        seen_names.add(name)

    # Always include universal services (not double-added).
    for universal in sorted(_UNIVERSAL_SERVICES):
        if universal in seen_names:
            continue
        results.append(
            ExtractedService(
                name=universal,
                occurrence_count=0,
                pages_with=0,
                confidence=1.0,  # universal is by-definition relevant
            )
        )
    return results


__all__ = [
    "CANONICAL_SERVICES",
    "extract_services",
]
