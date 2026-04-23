"""query_picker_v2 — quota-driven query selection from BusinessTruth.

Replaces the old flat `_pick_top_queries()` with a picker that:
  1. Takes the BusinessTruth (direction map + queries_sample per dir)
  2. Runs allocate_quotas to get per-direction slot counts
  3. Fills each direction's slots from truth.queries in evidence order
  4. Returns each query tagged with its direction + source

The pure function here is BusinessTruth-only — no DB access. A sibling
helper adds target-cluster fallback for directions whose evidence
queries are too thin to fill the quota.
"""

from __future__ import annotations

import dataclasses

from app.core_audit.business_truth.dto import BusinessTruth, DirectionKey
from app.core_audit.business_truth.query_selector import allocate_quotas


@dataclasses.dataclass(frozen=True)
class QueryPick:
    """A query chosen for discovery, tagged with its origin direction.

    source:
      "business_truth"   — drawn from truth's queries_sample for direction
      "target_cluster"   — fallback when truth evidence was thin
      "unknown"          — legacy slot, no direction attribution
    """
    query: str
    direction: DirectionKey | None
    source: str


@dataclasses.dataclass
class PickResult:
    """Output of pick_queries_*.

    queries          — chosen queries in stable order (direction by
                       direction, each direction's queries in evidence
                       order)
    direction_budget — quota each direction was allocated
    deficit          — directions that wanted more slots than their
                       evidence could fill {direction: missing_count}
    """
    queries: list[QueryPick]
    direction_budget: dict[DirectionKey, int]
    deficit: dict[DirectionKey, int]


_FALLBACK_PATTERNS: tuple[str, ...] = (
    "{service} {geo}",
    "{service} {geo} цена",
    "{service} {geo} отзывы",
    "{service} туры {geo}",
    "{service} {geo} недорого",
    "{service} {geo} 2026",
    "{service} {geo} забронировать",
    "{service} в {geo}",
    "{service} аренда {geo}",
    "{service} {geo} на день",
)


def _synthesize_for_direction(key, want: int) -> list[str]:
    """Generate N queries for (service, geo) from templates.

    Covers typical commercial-intent Russian query shapes so SERP
    probes reach direction-relevant competitors even when our own
    site has no observed queries for that direction yet.
    """
    out: list[str] = []
    for tmpl in _FALLBACK_PATTERNS:
        if len(out) >= want:
            break
        out.append(tmpl.format(service=key.service, geo=key.geo))
    return out


def pick_queries_from_truth(
    truth: BusinessTruth,
    *,
    budget: int,
    aspiration_penalty: float | None = 0.1,
    min_slot: int = 1,
    cap_single_direction: float | None = None,
    synthesize_fallback: bool = False,
) -> PickResult:
    """Allocate budget across directions in truth, pick queries per slot.

    Defaults:
      aspiration_penalty=0.1 — owner-only "dream" directions compete at
      10% weight so real (evidenced) directions dominate.
      synthesize_fallback=False — when True, slots not fillable from
      truth.queries are supplemented by "{service} {geo} ..." templates.
      Useful for low-traffic sites where observed evidence is thin.

    Deficit tracking: if a direction got N slots but only M queries in
    evidence, (N - M) counted as deficit (unless synthesize_fallback
    filled the gap).
    """
    directions = list(truth.directions or [])
    if not directions or budget <= 0:
        return PickResult(queries=[], direction_budget={}, deficit={})

    quotas = allocate_quotas(
        truth,
        budget=budget,
        min_slot=min_slot,
        cap_single_direction=cap_single_direction,
        aspiration_penalty=aspiration_penalty,
    )

    out: list[QueryPick] = []
    deficit: dict[DirectionKey, int] = {}

    # Walk directions in truth order (already sorted by total strength)
    # so the strongest direction's queries lead the returned list.
    for d in directions:
        want = quotas.get(d.key, 0)
        if want == 0:
            continue
        available = list(d.queries)[:want]
        for q in available:
            out.append(QueryPick(
                query=q,
                direction=d.key,
                source="business_truth",
            ))
        missing = want - len(available)
        if missing > 0:
            if synthesize_fallback:
                # Don't double-count queries we already have
                existing_lower = {q.lower() for q in available}
                synth = [
                    q for q in _synthesize_for_direction(d.key, missing * 2)
                    if q.lower() not in existing_lower
                ][:missing]
                for q in synth:
                    out.append(QueryPick(
                        query=q,
                        direction=d.key,
                        source="synthesized",
                    ))
                # If synth couldn't cover fully (rare — >10 slots per dir
                # exhausts templates), record remaining as deficit.
                still_missing = missing - len(synth)
                if still_missing > 0:
                    deficit[d.key] = still_missing
            else:
                deficit[d.key] = missing

    return PickResult(
        queries=out,
        direction_budget={k: v for k, v in quotas.items() if v > 0},
        deficit=deficit,
    )


__all__ = ["QueryPick", "PickResult", "pick_queries_from_truth"]
