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
