"""Quota-based query budget allocator.

Discovery asks: "I have 30 SERP probes to spend; which direction
gets how many?" This module turns a BusinessTruth into a quota map
per direction.

Core rule: weights come from (strength_understanding + strength_content
+ strength_traffic), normalised. A direction that exists only in
strength_content (blind spot) still gets slots — that's the whole
point, discovery is supposed to find competitors for directions we
don't have traffic for yet.

Two safeguards:
  • min_slot: every direction in the truth gets at least N slots
  • cap_single_direction: no direction takes more than X% of the budget
    (unless all 3 sources agree that one direction is dominant)
"""

from __future__ import annotations

from app.core_audit.business_truth.dto import BusinessTruth, DirectionKey


DEFAULT_CAP = 0.5           # no direction takes >50% of budget by default
DEFAULT_RELAX_FACTOR = 1.2  # cap × 2.4 if traffic + understanding both agree
DEFAULT_MIN_SLOT = 1        # every known direction gets at least 1 probe


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
    cap_single_direction: float | None = DEFAULT_CAP,
    cap_relax_on_matching_traffic: bool = False,
) -> dict[DirectionKey, int]:
    """Turn direction strengths into integer slot counts summing to `budget`.

    Rules:
      1. Shares proportional to total-strength across 3 sources.
      2. `cap_single_direction` caps any single direction's share.
      3. `cap_relax_on_matching_traffic`: if understanding and traffic
         both exceed the cap for the same direction, raise that
         direction's cap to `cap × DEFAULT_RELAX_FACTOR` so dominant
         businesses (90% Abkhazia) don't get forced into artificial
         diversity.
      4. `min_slot`: every direction in the truth gets at least N slots.
      5. Rounding: fractional remainders distributed by largest-remainder
         method so ints always sum to `budget`.
    """
    directions = list(truth.directions or [])
    if not directions or budget <= 0:
        return {}

    strengths = {d.key: _total_strength(d) for d in directions}
    total = sum(strengths.values())
    if total <= 0:
        # All strengths zero → equal split
        per = budget // len(directions)
        remainder = budget - per * len(directions)
        out = {d.key: per for d in directions}
        for i, d in enumerate(directions[:remainder]):
            out[d.key] += 1
        return out

    # Raw proportional shares
    raw = {k: (v / total) * budget for k, v in strengths.items()}

    # Apply per-direction cap
    if cap_single_direction is not None:
        for d in directions:
            cap = cap_single_direction
            if (
                cap_relax_on_matching_traffic
                and d.strength_understanding > cap
                and d.strength_traffic > cap
            ):
                cap = min(1.0, cap * DEFAULT_RELAX_FACTOR * 2)
            max_slots = cap * budget
            if raw[d.key] > max_slots:
                raw[d.key] = max_slots

    # Enforce minimum slot
    for d in directions:
        if raw[d.key] < min_slot:
            raw[d.key] = float(min_slot)

    # Normalise back to budget using largest-remainder rounding
    rounded = {k: int(v) for k, v in raw.items()}
    remainders = {k: (raw[k] - rounded[k]) for k in raw}
    leftover = budget - sum(rounded.values())
    # Distribute leftover to the directions with the largest fractional
    # remainders. If leftover is negative (cap + min_slot pulled us
    # over budget), trim from the highest-allocated directions.
    if leftover > 0:
        order = sorted(remainders, key=lambda k: -remainders[k])
        for k in order[:leftover]:
            rounded[k] += 1
    elif leftover < 0:
        # Over-allocated — trim from the highest raw shares that are
        # above min_slot. Protect the floor.
        order = sorted(rounded, key=lambda k: -rounded[k])
        for k in order:
            if leftover == 0:
                break
            if rounded[k] > min_slot:
                rounded[k] -= 1
                leftover += 1

    return rounded


__all__ = ["allocate_quotas", "DEFAULT_CAP", "DEFAULT_MIN_SLOT"]
