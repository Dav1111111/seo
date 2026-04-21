"""Query ↔ page matching.

For each gap query, find the page on OUR site that best matches the
query's intent — using token overlap across title/h1/url/content.

Why this matters
----------------
Without this, the system tells the owner "create a new page about
багги абхазия" when /abkhazia/ already exists on their site. The
right action is "strengthen the existing page", not "create new".

Scoring
-------
Per page we build a searchable bag of tokens from:
  - title (weight 3)
  - h1 (weight 3)
  - url path segments (weight 2)
  - meta_description (weight 1)
  - first 300 chars of content_text (weight 1)

For a query's non-stop tokens, we count how many appear (weighted) in
that bag. Final score is weighted_hits / max_possible_weighted_hits,
clamped to [0, 1].

A score above STRONG_MATCH_THRESHOLD means "the page is about this"
→ emit `strengthen_existing_page` instead of `new_page`.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Sequence

# Same stop-tokens set as opportunities — don't reward matches on them.
_STOP = frozenset({
    "в", "во", "на", "у", "по", "из", "от", "до", "за", "для",
    "и", "а", "или", "но", "с", "со", "о", "об",
    "цена", "цены", "стоимость", "туры", "тур", "под", "ключ",
    "забронировать", "купить", "заказать", "заказ", "выбрать",
    "недорого", "дёшево", "недорогой",
    "2025", "2026", "2027",
    "the", "a", "an", "of", "to", "and", "or", "for",
})


STRONG_MATCH_THRESHOLD = 0.50   # score ≥ this → page is about the query
WEAK_MATCH_THRESHOLD = 0.25     # score ≥ this → page could be stretched


def _tokens(s: str | None) -> list[str]:
    if not s:
        return []
    s = s.lower()
    s = re.sub(r"[^a-zа-яё0-9\s-]", " ", s)
    return [t for t in s.split() if t and len(t) >= 3 and t not in _STOP]


def _path_tokens(url: str | None) -> list[str]:
    if not url:
        return []
    # Strip scheme + host, keep path segments
    path = re.sub(r"^https?://[^/]+", "", url)
    path = path.replace("-", " ").replace("_", " ").replace("/", " ")
    return _tokens(path)


@dataclasses.dataclass(frozen=True)
class PageMatch:
    url: str
    path: str
    title: str | None
    score: float              # 0..1
    matched_tokens: tuple[str, ...]
    missing_tokens: tuple[str, ...]

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["matched_tokens"] = list(self.matched_tokens)
        d["missing_tokens"] = list(self.missing_tokens)
        return d


def find_best_page(
    query: str,
    pages: Sequence[dict],
) -> PageMatch | None:
    """Return the best-matching page for `query`, or None if no page crawled.

    `pages` is a list of dicts with keys: url, path, title, h1,
    meta_description, content_snippet. Missing fields are tolerated.
    """
    q_tokens = _tokens(query)
    if not q_tokens or not pages:
        return None

    q_set = set(q_tokens)

    best: PageMatch | None = None

    for p in pages:
        title_toks = set(_tokens(p.get("title")))
        h1_toks = set(_tokens(p.get("h1")))
        path_toks = set(_path_tokens(p.get("url") or p.get("path")))
        meta_toks = set(_tokens(p.get("meta_description")))
        body_toks = set(_tokens((p.get("content_snippet") or "")[:600]))

        # Weighted hit count over query tokens
        hits = 0.0
        matched: list[str] = []
        for t in q_set:
            w = 0.0
            if t in title_toks:
                w = max(w, 3.0)
            if t in h1_toks:
                w = max(w, 3.0)
            if t in path_toks:
                w = max(w, 2.0)
            if t in meta_toks:
                w = max(w, 1.0)
            if t in body_toks:
                w = max(w, 1.0)
            if w > 0:
                hits += w
                matched.append(t)

        # Normalise: max possible per token = 3 (if it hit title/h1)
        max_possible = 3.0 * len(q_set)
        score = hits / max_possible if max_possible else 0.0

        if best is None or score > best.score:
            best = PageMatch(
                url=p.get("url", ""),
                path=p.get("path", "") or p.get("url", ""),
                title=p.get("title"),
                score=round(score, 3),
                matched_tokens=tuple(sorted(matched)),
                missing_tokens=tuple(sorted(q_set - set(matched))),
            )

    return best


__all__ = [
    "PageMatch",
    "find_best_page",
    "STRONG_MATCH_THRESHOLD",
    "WEAK_MATCH_THRESHOLD",
]
