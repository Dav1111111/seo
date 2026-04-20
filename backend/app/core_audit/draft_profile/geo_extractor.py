"""Extract candidate geos from crawled pages + observed queries (Phase F).

Algorithm
---------
1. Match token sequences in title/content_text against `CITIES_RU`.
   Multi-word cities ("красная поляна") are checked as substrings
   first, then single tokens are checked for exact matches.
2. Count frequency per city across all pages.
3. Classify:
     primary  : cities appearing in >= 30% of pages OR mentioned in
                the title/h1 of a "homepage/contacts" candidate page.
     secondary: cities mentioned but below the primary threshold.
4. Bump frequency for cities observed in any search query.

Pure — no DB, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from app.core_audit.draft_profile.cities_ru import CITIES_RU, MULTIWORD_CITIES
from app.core_audit.draft_profile.dto import ExtractedGeo


_TOKEN_RE = re.compile(r"[а-яёa-z0-9\-]+", re.IGNORECASE)


def _tokenize_lc(text: str | None) -> list[str]:
    if not text:
        return []
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _find_cities_in_text(text: str | None) -> set[str]:
    """Return the set of CITIES_RU tokens that appear in `text`."""
    if not text:
        return set()
    lc = text.lower()

    found: set[str] = set()

    # Multi-word first — substring match on lowered text.
    remaining = lc
    for mw in MULTIWORD_CITIES:
        if mw in remaining:
            found.add(mw)
            # Replace so tokens within the multi-word match don't
            # double-count as separate cities.
            remaining = remaining.replace(mw, " ")

    # Single-token matches on the remaining text.
    for tok in _TOKEN_RE.findall(remaining):
        tokl = tok.lower()
        if tokl in CITIES_RU:
            found.add(tokl)

    return found


@dataclass(frozen=True)
class _PageInput:
    title: str | None
    h1: str | None
    content_text: str | None
    url: str | None
    path: str | None


def _coerce_page(obj: object) -> _PageInput:
    return _PageInput(
        title=getattr(obj, "title", None),
        h1=getattr(obj, "h1", None),
        content_text=getattr(obj, "content_text", None),
        url=getattr(obj, "url", None),
        path=getattr(obj, "path", None),
    )


_HOME_OR_CONTACTS_RE = re.compile(
    r"(^/$|/contacts?/?$|/kontakty/?$|/about/?$|/o-nas/?$|/about-us/?$)",
    re.IGNORECASE,
)


def _is_homepage_or_contacts(page: _PageInput) -> bool:
    candidates = (page.path or "", page.url or "")
    for c in candidates:
        if not c:
            continue
        # Treat root or common contacts/about slugs as anchor pages.
        if _HOME_OR_CONTACTS_RE.search(c):
            return True
    return False


def extract_geos(
    pages: Sequence[object],
    observed_queries: Iterable[str] | None = None,
    *,
    primary_threshold: float = 0.30,
    content_char_cap: int = 2000,
) -> ExtractedGeo:
    """Return an ExtractedGeo classification for the site.

    Parameters
    ----------
    pages:
        Page-like sequence.
    observed_queries:
        Optional iterable of observed search query strings — each city
        present in a query bumps its frequency by +1.
    primary_threshold:
        Fraction of pages a city must appear in to be promoted primary.
    content_char_cap:
        Content text scanned per page (cost control — titles always
        scanned fully).
    """
    freq: dict[str, int] = {}
    pages_with: dict[str, int] = {}
    anchor_mentions: set[str] = set()  # cities in homepage/contacts titles
    total_pages = 0

    for raw in pages:
        page = _coerce_page(raw)
        total_pages += 1

        title_cities = _find_cities_in_text(page.title)
        h1_cities = _find_cities_in_text(page.h1)
        body_cities = _find_cities_in_text(
            (page.content_text or "")[:content_char_cap]
        )

        for c in title_cities | h1_cities:
            freq[c] = freq.get(c, 0) + 2
        for c in body_cities:
            freq[c] = freq.get(c, 0) + 1

        page_cities = title_cities | h1_cities | body_cities
        for c in page_cities:
            pages_with[c] = pages_with.get(c, 0) + 1

        if _is_homepage_or_contacts(page):
            anchor_mentions |= title_cities | h1_cities

    # Queries — add +1 per city match, and count them towards
    # `pages_with` proxy (so single-query cities still promote).
    if observed_queries:
        for q in observed_queries:
            cities = _find_cities_in_text(q)
            for c in cities:
                freq[c] = freq.get(c, 0) + 1

    primary: list[str] = []
    secondary: list[str] = []
    denom = max(total_pages, 1)

    # Classify, but only count cities that were actually seen somewhere
    # (in pages or queries).
    for city in sorted(freq.keys(), key=lambda c: (-freq[c], c)):
        pages_frac = pages_with.get(city, 0) / denom
        if pages_frac >= primary_threshold or city in anchor_mentions:
            primary.append(city)
        else:
            secondary.append(city)

    return ExtractedGeo(
        primary=primary,
        secondary=secondary,
        excluded=[],
        frequency_map=dict(freq),
    )


__all__ = ["extract_geos"]
