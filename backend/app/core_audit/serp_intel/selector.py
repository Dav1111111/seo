"""Pure selector: pick the SearchQuery rows worth a SERP-API call.

The Yandex Cloud Search API quota is shared across the project, so we
can't probe every query the site has. This selector ranks the candidate
queries by `wordstat_volume × layer_weight` (layer weights mirror
`core_audit/priority/scorer.py` so the SERP probe and the priority
queue agree on what «important» means) and returns the top N.

Pure function: no DB, no I/O — easy to unit-test against in-memory
SearchQuery rows.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


# Minimum monthly Wordstat volume to bother spending an API call.
# Wordstat itself often returns a rounded 0 below ~10/мес; below this
# floor a SERP probe gives no actionable signal.
MIN_VOLUME_TO_PROBE = 10

# Layer weights — mirror priority/scorer logic so «what's worth a
# probe» tracks «what's worth a recommendation». If you change these,
# check `core_audit/priority/scorer.py` too.
LAYER_WEIGHTS: dict[str, float] = {
    # Direct-money intent — always probe first.
    "direct_product": 1.0,
    "own": 1.0,
    # Warm funnel + adjacent — probe second.
    "funnel_warm": 0.7,
    "adjacent": 0.7,
    # Top-of-funnel — informational, lower priority but still useful.
    "funnel_top": 0.5,
    # Disputed = we don't agree it's ours. Still probe (we want to know
    # who else is on it) but with a lower weight.
    "disputed": 0.3,
    # Unclassified — probably hasn't been triaged yet. Low priority.
    "unclassified": 0.3,
}

# Relevance values we NEVER probe — they're either spam or out-of-market
# and burning quota on them helps no one.
SKIP_RELEVANCES: frozenset[str] = frozenset({"spam", "out_of_market"})


def _score(volume: int, relevance: str) -> float:
    weight = LAYER_WEIGHTS.get(relevance, 0.0)
    if weight <= 0:
        return 0.0
    return float(volume) * weight


def pick_queries_to_probe(
    queries: Iterable[Any],
    *,
    max_n: int = 30,
) -> list[Any]:
    """Return the up-to-N most worth-probing queries, sorted by score
    descending.

    Filters:
      * skip relevance in {spam, out_of_market} entirely
      * skip queries with `wordstat_volume IS NULL` or < MIN_VOLUME_TO_PROBE
      * skip relevances we don't have a weight for (defensive — unknown
        funnel layer = 0 weight = skipped)

    Scoring: `wordstat_volume × layer_weight`. Tie-break: alphabetic
    on `query_text` so the choice is deterministic and re-runs pick
    the same set.

    Returns the original objects (so callers can read `.id`, `.query_text`,
    `.relevance` etc.) — not dicts.
    """
    eligible: list[tuple[float, str, Any]] = []
    for q in queries:
        relevance = (getattr(q, "relevance", None) or "").strip()
        if not relevance or relevance in SKIP_RELEVANCES:
            continue
        volume = getattr(q, "wordstat_volume", None)
        if volume is None:
            continue
        try:
            volume_int = int(volume)
        except (TypeError, ValueError):
            continue
        if volume_int < MIN_VOLUME_TO_PROBE:
            continue
        score = _score(volume_int, relevance)
        if score <= 0:
            continue
        # Tie-break key: query_text alphabetic — so re-runs pick the
        # same set even when many queries land on the same score.
        eligible.append((score, (getattr(q, "query_text", "") or "").lower(), q))

    eligible.sort(key=lambda t: (-t[0], t[1]))
    return [q for _score_v, _text, q in eligible[: max(0, int(max_n))]]


__all__ = [
    "pick_queries_to_probe",
    "LAYER_WEIGHTS",
    "MIN_VOLUME_TO_PROBE",
    "SKIP_RELEVANCES",
]
