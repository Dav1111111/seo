"""Content gap analysis — queries we're losing.

For each probed query:
  - find the best position of any confirmed competitor (from
    sites.competitor_domains) in the fresh SERP
  - find the best position of the site itself
  - if competitor is in top-5 AND site is not in top-30 → gap

Output is a ranked list of gap-queries with one example competitor URL
per query, so the owner sees "topic X: competitor Y at #2, you're off
the radar — create a page about X".

Implementation reuses the same YandexSerpCollector the discovery agent
uses. To keep costs bounded, gap analysis shares its query pool with
discovery and does NOT fire new SERPs — instead the discovery task will
be extended to also cache per-query SERP docs in target_config, and the
gap analyzer reads that cache. This file describes the pure analysis
function.
"""

from __future__ import annotations

import dataclasses
from typing import Iterable, Sequence

from app.core_audit.competitors.discovery import _is_excluded, _norm_domain


def _doc_get(doc, key: str, default=None):
    """Access a SERP doc field regardless of whether it's a SerpDoc or a dict."""
    if isinstance(doc, dict):
        return doc.get(key, default)
    return getattr(doc, key, default)


# Thresholds
COMPETITOR_TOP_POSITION = 5     # competitor must be in top-N to count as "ahead"
SITE_MIN_GAP_POSITION = 30      # site must be below this (or absent) to count


@dataclasses.dataclass(frozen=True)
class GapRow:
    query: str
    site_position: int | None            # None = not in SERP at all
    competitor_domain: str
    competitor_position: int
    competitor_url: str
    competitor_title: str
    # All competitors ranking in top-10 for this query (for context).
    other_competitors: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["other_competitors"] = list(d["other_competitors"])
        return d


def _find_position(docs, predicate) -> tuple[int | None, object]:
    """Return (position, doc) of the first doc matching predicate, else (None, None)."""
    for d in docs:
        if predicate(d):
            return _doc_get(d, "position"), d
    return None, None


def analyze_gaps(
    *,
    own_domain: str,
    competitor_domains: Sequence[str],
    query_to_serp: dict,
    top_k_gaps: int = 20,
) -> list[GapRow]:
    """Pure function — given per-query SERP docs, return ranked gap rows.

    Ranking: competitor_position asc (better competitor position =
    bigger gap = higher priority). Ties broken by site_position desc
    (further back = more painful) then query length asc.
    """
    own = _norm_domain(own_domain)
    comp_set = {_norm_domain(d) for d in competitor_domains if d}

    rows: list[GapRow] = []

    for query, docs in query_to_serp.items():
        if not docs:
            continue

        own_pos, _ = _find_position(
            docs, lambda d: _norm_domain(_doc_get(d, "domain", "")) == own,
        )
        if own_pos is not None and own_pos <= SITE_MIN_GAP_POSITION:
            continue

        best_comp_pos: int | None = None
        best_comp_doc = None
        all_comps_top10: list[str] = []
        for d in docs:
            dom = _norm_domain(_doc_get(d, "domain", ""))
            pos = _doc_get(d, "position")
            if not dom or dom == own or _is_excluded(dom):
                continue
            if dom not in comp_set:
                continue
            if pos is not None and pos <= 10:
                all_comps_top10.append(dom)
            if best_comp_pos is None or (pos is not None and pos < best_comp_pos):
                best_comp_pos = pos
                best_comp_doc = d

        if (
            best_comp_pos is None
            or best_comp_pos > COMPETITOR_TOP_POSITION
            or best_comp_doc is None
        ):
            continue

        rows.append(GapRow(
            query=query,
            site_position=own_pos,
            competitor_domain=_norm_domain(_doc_get(best_comp_doc, "domain", "")),
            competitor_position=best_comp_pos,
            competitor_url=_doc_get(best_comp_doc, "url", ""),
            competitor_title=_doc_get(best_comp_doc, "title", ""),
            other_competitors=tuple(dict.fromkeys(all_comps_top10)),
        ))

    rows.sort(key=lambda r: (
        r.competitor_position,
        -(r.site_position or 100),
        len(r.query),
    ))
    return rows[:top_k_gaps]


__all__ = ["GapRow", "analyze_gaps", "COMPETITOR_TOP_POSITION", "SITE_MIN_GAP_POSITION"]
