"""Quota-based query budget allocator.

Discovery asks: "I have 30 SERP probes to spend; which direction
gets how many?" This module turns a BusinessTruth into a quota map
per direction.

Core rule: weights come from (strength_understanding + strength_content
+ strength_traffic), normalised. A direction that exists only in
strength_content (blind spot) still gets slots — that's the whole
point, discovery is supposed to find competitors for directions we
don't have traffic for yet.

Two safeguards (opt-in via params):
  • min_slot: every direction in the truth gets at least N slots.
    Protects tiny secondary directions from being rounded to zero.
  • cap_single_direction: no direction takes more than X% of the
    budget (unless cap_relax_on_matching_traffic + 3-source agree).
    Off by default — callers opt in when they want forced diversity.
"""

from __future__ import annotations

from app.core_audit.business_truth.dto import BusinessTruth, DirectionKey


DEFAULT_RELAX_FACTOR = 2.4   # cap × this if understanding + traffic both dominant
DEFAULT_MIN_SLOT = 1


def _total_strength(d) -> float:
    return (
        float(d.strength_understanding or 0.0)
        + float(d.strength_content or 0.0)
        + float(d.strength_traffic or 0.0)
    )


def allocate_quotas(
    truth: BusinessTruth,
    *,
    budget: int,
    min_slot: int = DEFAULT_MIN_SLOT,
    cap_single_direction: float | None = None,
    cap_relax_on_matching_traffic: bool = False,
) -> dict[DirectionKey, int]:
    """Turn direction strengths into integer slot counts summing to `budget`.

    Algorithm:
      1. Proportional raw share by total-strength across 3 sources.
      2. Apply min_slot floor — every direction ≥ min_slot.
      3. Apply cap_single_direction ceiling (if set).
      4. Largest-remainder rounding to integers summing to budget.
      5. Leftover distributed to directions that still have capacity
         (allocated < cap), sorted by largest remainder.
    """
    directions = list(truth.directions or [])
    if not directions or budget <= 0:
        return {}

    strengths = {d.key: _total_strength(d) for d in directions}
    total = sum(strengths.values())

    # Step 1: proportional allocation (equal split if all strengths zero)
    if total <= 0:
        per = budget / len(directions)
        raw = {d.key: per for d in directions}
    else:
        raw = {k: (v / total) * budget for k, v in strengths.items()}

    # Step 2: per-direction caps
    if cap_single_direction is None:
        caps = {d.key: float(budget) for d in directions}
    else:
        caps = {}
        for d in directions:
            c = cap_single_direction
            if (
                cap_relax_on_matching_traffic
                and d.strength_understanding > c
                and d.strength_traffic > c
            ):
                c = min(1.0, c * DEFAULT_RELAX_FACTOR)
            caps[d.key] = c * budget

    # Step 3: floor (min_slot) then ceiling (cap)
    allocated: dict[DirectionKey, float] = {}
    for d in directions:
        v = raw[d.key]
        v = max(v, float(min_slot))
        v = min(v, caps[d.key])
        allocated[d.key] = v

    # Step 4: round to ints with largest-remainder distribution
    rounded = {k: int(v) for k, v in allocated.items()}
    remainders = {k: (allocated[k] - rounded[k]) for k in allocated}
    sum_rounded = sum(rounded.values())
    leftover = budget - sum_rounded

    if leftover > 0:
        # Give to directions that still have capacity, ordered by remainder
        eligible = [k for k in rounded if rounded[k] < caps[k]]
        eligible.sort(key=lambda k: -remainders[k])
        i = 0
        while leftover > 0 and eligible:
            k = eligible[i % len(eligible)]
            if rounded[k] < caps[k]:
                rounded[k] += 1
                leftover -= 1
            # Re-sieve: drop keys that hit their cap
            eligible = [k for k in eligible if rounded[k] < caps[k]]
            if not eligible:
                break
            i += 1
        # If still leftover (all caps hit), fall back: distribute over
        # min_slot-protected directions regardless of cap. Budget-integrity
        # beats cap-precision.
        if leftover > 0:
            fallback = sorted(rounded, key=lambda k: -allocated[k])
            i = 0
            while leftover > 0:
                rounded[fallback[i % len(fallback)]] += 1
                leftover -= 1
                i += 1
    elif leftover < 0:
        # Over-budget — trim from highest, never below min_slot
        trim_order = sorted(rounded, key=lambda k: -rounded[k])
        i = 0
        while leftover < 0:
            k = trim_order[i % len(trim_order)]
            if rounded[k] > min_slot:
                rounded[k] -= 1
                leftover += 1
            i += 1
            if all(rounded[k] <= min_slot for k in rounded):
                # Would dip below min_slot. Stop — accept over-budget.
                break

    return rounded


__all__ = ["allocate_quotas", "DEFAULT_MIN_SLOT"]
