"""Quota-based query selector — discovery uses BusinessTruth directions.

Instead of one flat "pick top N by impressions" pool, discovery now
allocates slots per direction proportional to the direction's
strength. Result: the Abkhazia skew becomes impossible unless the
owner explicitly said "Абхазия 100%".
"""

from __future__ import annotations

import pytest

from app.core_audit.business_truth.dto import (
    BusinessTruth, DirectionEvidence, DirectionKey,
)


def _dir(service, geo, u=0.0, c=0.0, t=0.0):
    return DirectionEvidence(
        key=DirectionKey.of(service, geo),
        strength_understanding=u, strength_content=c, strength_traffic=t,
    )


def test_single_direction_gets_full_budget():
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[_dir("багги", "абхазия", u=1.0, c=1.0, t=1.0)])
    quotas = allocate_quotas(truth, budget=30)
    assert quotas[DirectionKey.of("багги", "абхазия")] == 30


def test_even_split_when_strengths_equal():
    """3 equal directions, budget 30 → 10 each."""
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[
        _dir("багги", "абхазия", u=1/3),
        _dir("багги", "сочи",    u=1/3),
        _dir("багги", "крым",    u=1/3),
    ])
    quotas = allocate_quotas(truth, budget=30)
    assert sum(quotas.values()) == 30
    for v in quotas.values():
        assert v == 10


def test_uneven_weights_proportional():
    """Owner said 70/20/10 → 21/6/3 out of 30."""
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[
        _dir("багги", "абхазия", u=0.7),
        _dir("багги", "сочи",    u=0.2),
        _dir("багги", "крым",    u=0.1),
    ])
    quotas = allocate_quotas(truth, budget=30)
    assert quotas[DirectionKey.of("багги", "абхазия")] == 21
    assert quotas[DirectionKey.of("багги", "сочи")] == 6
    assert quotas[DirectionKey.of("багги", "крым")] == 3
    assert sum(quotas.values()) == 30


def test_minimum_slot_per_direction_guaranteed():
    """A direction with 1% weight still gets at least 1 slot (not zero).

    Protects small secondary directions from being squeezed out
    entirely by a dominant one — even 1 query per direction produces
    some SERP coverage for every confirmed corner of the business.
    """
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[
        _dir("багги", "абхазия", u=0.95),
        _dir("багги", "сочи",    u=0.03),
        _dir("багги", "крым",    u=0.02),
    ])
    quotas = allocate_quotas(truth, budget=30, min_slot=1)
    assert quotas[DirectionKey.of("багги", "сочи")] >= 1
    assert quotas[DirectionKey.of("багги", "крым")] >= 1
    assert sum(quotas.values()) == 30


def test_blind_spots_get_slots_even_if_no_traffic():
    """Key point for user: page exists + understanding says it, but
    zero traffic — direction STILL gets discovery slots, because
    the whole point is to find competitors in that space."""
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[
        _dir("багги", "абхазия", u=0.5, c=0.5, t=1.0),   # fully confirmed
        _dir("багги", "сочи",    u=0.5, c=0.5, t=0.0),   # blind spot
    ])
    quotas = allocate_quotas(truth, budget=20)
    sochi = quotas[DirectionKey.of("багги", "сочи")]
    abk = quotas[DirectionKey.of("багги", "абхазия")]
    # Sochi must NOT be starved — it gets a real share
    assert sochi >= 5, f"Sochi starved at {sochi} slots, got {quotas}"
    assert abk + sochi == 20


def test_empty_truth_returns_empty_quotas():
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[])
    assert allocate_quotas(truth, budget=30) == {}


def test_cap_50_percent_on_single_direction_by_default():
    """Even if owner said 95% Abkhazia, cap at 50% so secondary
    directions get real coverage. Mirrors the 'business_truth should
    detect bias' principle."""
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[
        _dir("багги", "абхазия", u=0.95),
        _dir("багги", "сочи",    u=0.05),
    ])
    # Without cap
    raw = allocate_quotas(truth, budget=20, cap_single_direction=None)
    assert raw[DirectionKey.of("багги", "абхазия")] >= 18

    # With cap
    capped = allocate_quotas(truth, budget=20, cap_single_direction=0.5)
    assert capped[DirectionKey.of("багги", "абхазия")] <= 10
    assert capped[DirectionKey.of("багги", "сочи")] >= 10


def test_cap_relaxes_if_owner_explicitly_dominant():
    """If understanding AND traffic both agree 95% Abkhazia, the cap
    shouldn't fight reality — allow up to 1.2× the understanding weight."""
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[
        _dir("багги", "абхазия", u=0.95, t=0.95),
        _dir("багги", "сочи",    u=0.05, t=0.05),
    ])
    quotas = allocate_quotas(
        truth, budget=20,
        cap_single_direction=0.5,
        cap_relax_on_matching_traffic=True,
    )
    # Abkhazia allowed past 50% because 3-way agreement
    assert quotas[DirectionKey.of("багги", "абхазия")] > 10


# ── Contract: sum(quotas.values()) == budget, always ─────────────────

def test_contract_sum_equals_budget_even_when_directions_exceed_budget():
    """10 directions on 5-slot budget: some get 0 or 1, but sum == 5.

    With min_slot=1 naively applied, this would sum to 10 (each
    direction demands its floor). Correct behaviour: drop weakest
    directions so total hits budget."""
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[
        _dir("s", f"geo{i}", u=1 - i * 0.05) for i in range(10)
    ])
    quotas = allocate_quotas(truth, budget=5, min_slot=1)
    assert sum(quotas.values()) == 5, (
        f"Contract broken: sum={sum(quotas.values())}, quotas={quotas}"
    )
    # Strongest directions should be the ones that survived
    allocated = {k: v for k, v in quotas.items() if v > 0}
    assert len(allocated) == 5


def test_contract_sum_equals_budget_with_all_caps_hit():
    """All directions hit the cap + still have leftover.

    budget=30, 2 equal directions, cap=0.3 (so max 9 each, total 18
    under cap). Remaining 12 must go somewhere — either bust the cap
    or redistribute. Either way, sum must equal budget."""
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[
        _dir("s", "a", u=0.5),
        _dir("s", "b", u=0.5),
    ])
    quotas = allocate_quotas(truth, budget=30, cap_single_direction=0.3)
    assert sum(quotas.values()) == 30, (
        f"Contract broken: sum={sum(quotas.values())}, quotas={quotas}"
    )


def test_contract_sum_equals_budget_on_zero_strength_truth():
    """All directions have strength 0 → equal split, but still sums exactly."""
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[
        _dir("s", "a"),  # all zeros
        _dir("s", "b"),
        _dir("s", "c"),
    ])
    quotas = allocate_quotas(truth, budget=10)
    assert sum(quotas.values()) == 10
    # Equal split: 4/3/3 or similar
    assert max(quotas.values()) - min(quotas.values()) <= 1


def test_contract_budget_smaller_than_min_slot_sum_drops_weak_directions():
    """budget=2, 3 directions with min_slot=1 can't all get slot.

    Correct: keep top-2, third direction gets 0. Sum == 2."""
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[
        _dir("s", "strong", u=0.8),
        _dir("s", "mid",    u=0.15),
        _dir("s", "weak",   u=0.05),
    ])
    quotas = allocate_quotas(truth, budget=2, min_slot=1)
    assert sum(quotas.values()) == 2
    assert quotas[DirectionKey.of("s", "strong")] >= 1
    # weakest gets dropped (0 allocation)
    assert quotas[DirectionKey.of("s", "weak")] == 0


def test_contract_preserves_order_stable_under_ties():
    """Two equal-strength directions + odd budget → deterministic
    winner, not random."""
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[
        _dir("s", "a", u=0.5),
        _dir("s", "b", u=0.5),
    ])
    r1 = allocate_quotas(truth, budget=5)
    r2 = allocate_quotas(truth, budget=5)
    assert r1 == r2  # deterministic
    assert sum(r1.values()) == 5


# ── Item 2: aspiration penalty policy layer ──────────────────────

def test_aspiration_penalty_shrinks_owner_only_directions():
    """aspiration_penalty=0.1 means a pure-aspiration direction (only
    u > 0) competes at 10% of its raw strength_understanding weight
    when allocating. Raw strength stays untouched in the truth."""
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[
        _dir("s", "real",  u=0.5, c=0.5, t=0.5),  # total 1.5
        _dir("s", "dream", u=0.5),                # total 0.5, aspiration
    ])
    # Without penalty: 1.5 vs 0.5 → 75/25 of 10 → 8/2
    raw = allocate_quotas(truth, budget=10)
    real_raw = raw[DirectionKey.of("s", "real")]
    dream_raw = raw[DirectionKey.of("s", "dream")]

    # With penalty: real = 1.5 vs dream = 0.05, ratio ~30:1,
    # dream should be further squeezed compared to no-penalty case.
    penalized = allocate_quotas(truth, budget=10, aspiration_penalty=0.1)
    real_pen = penalized[DirectionKey.of("s", "real")]
    dream_pen = penalized[DirectionKey.of("s", "dream")]

    # Penalty must reduce dream further and give more to real
    assert dream_pen < dream_raw, (
        f"Expected penalty to shrink dream; {dream_raw} → {dream_pen}"
    )
    assert real_pen >= real_raw
    assert sum(penalized.values()) == 10


def test_aspiration_penalty_does_not_touch_raw_truth():
    """Policy layer: raw DirectionEvidence strengths stay the same
    regardless of penalty. Only the ALLOCATED slot count differs."""
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[
        _dir("s", "dream", u=0.9),
    ])
    raw_u_before = truth.directions[0].strength_understanding
    allocate_quotas(truth, budget=10, aspiration_penalty=0.1)
    assert truth.directions[0].strength_understanding == raw_u_before


def test_aspiration_penalty_zero_keeps_evidenced_directions():
    """A direction with content/traffic isn't an aspiration, so
    aspiration_penalty doesn't reduce it."""
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[
        _dir("s", "a", u=0.3, t=0.7),  # evidenced by traffic
        _dir("s", "b", u=0.3),         # aspiration
    ])
    penalized = allocate_quotas(truth, budget=10, aspiration_penalty=0.1)
    # evidenced direction should dominate
    assert penalized[DirectionKey.of("s", "a")] > penalized[DirectionKey.of("s", "b")]


def test_aspiration_penalty_default_is_none_no_change():
    """When not passed, allocation unchanged — backwards compatible."""
    from app.core_audit.business_truth.query_selector import allocate_quotas
    truth = BusinessTruth(directions=[
        _dir("s", "a", u=0.5, c=0.5),
        _dir("s", "b", u=0.5),
    ])
    default = allocate_quotas(truth, budget=10)
    explicit_none = allocate_quotas(truth, budget=10, aspiration_penalty=None)
    assert default == explicit_none
