"""reconciler: merge 3 maps (understanding/content/traffic) → BusinessTruth.

Tests the merge logic only — no DB. Feeds the reconciler pre-built
maps and asserts the resulting BusinessTruth shape.
"""

from __future__ import annotations

import pytest

from app.core_audit.business_truth.dto import (
    BusinessTruth, DirectionEvidence, DirectionKey,
)


def _k(s, g):
    return DirectionKey.of(s, g)


def test_all_three_agree_single_direction():
    from app.core_audit.business_truth.reconciler import reconcile

    truth = reconcile(
        understanding_weights={_k("багги", "абхазия"): 1.0},
        content_pages={_k("багги", "абхазия"): ("/a/",)},
        traffic_weights={_k("багги", "абхазия"): 1.0},
        traffic_queries={_k("багги", "абхазия"): ("багги абхазия",)},
        sources_used={"understanding": 1, "content": 1, "traffic": 1},
    )
    assert len(truth.directions) == 1
    d = truth.directions[0]
    assert d.strength_understanding == 1.0
    assert d.strength_content == 1.0
    assert d.strength_traffic == 1.0
    assert d.is_confirmed
    assert d.is_blind_spot is False
    assert d.divergence_ru() is None


def test_blind_spot_page_and_understanding_but_no_traffic():
    """User's Sochi/Crimea case."""
    from app.core_audit.business_truth.reconciler import reconcile

    truth = reconcile(
        understanding_weights={
            _k("багги", "абхазия"): 0.7,
            _k("багги", "сочи"):    0.3,
        },
        content_pages={
            _k("багги", "абхазия"): ("/abkhazia/",),
            _k("багги", "сочи"):    ("/sochi/",),
        },
        traffic_weights={_k("багги", "абхазия"): 1.0},
        traffic_queries={_k("багги", "абхазия"): ("багги абхазия",)},
        sources_used={"understanding": 2, "content": 2, "traffic": 1},
    )
    blind = truth.blind_spots()
    keys = [(d.key.service, d.key.geo) for d in blind]
    assert ("багги", "сочи") in keys
    # Абхазия confirmed by all 3, not a blind spot
    assert ("багги", "абхазия") not in keys


def test_traffic_only_direction_needs_landing_page():
    """Traffic going to an unnamed (service, geo) with no page."""
    from app.core_audit.business_truth.reconciler import reconcile

    truth = reconcile(
        understanding_weights={_k("багги", "абхазия"): 1.0},
        content_pages={_k("багги", "абхазия"): ("/abkhazia/",)},
        traffic_weights={
            _k("багги", "абхазия"): 0.7,
            _k("багги", "адлер"):   0.3,
        },
        traffic_queries={_k("багги", "адлер"): ("багги адлер",)},
        sources_used={"understanding": 1, "content": 1, "traffic": 2},
    )
    traffic_only = truth.traffic_only()
    keys = [(d.key.service, d.key.geo) for d in traffic_only]
    assert ("багги", "адлер") in keys


def test_directions_ordered_by_total_strength():
    from app.core_audit.business_truth.reconciler import reconcile

    truth = reconcile(
        understanding_weights={
            _k("багги", "абхазия"): 0.5,
            _k("багги", "сочи"):    0.3,
            _k("багги", "крым"):    0.2,
        },
        content_pages={},
        traffic_weights={
            _k("багги", "абхазия"): 0.9,
            _k("багги", "сочи"):    0.1,
        },
        traffic_queries={},
        sources_used={"understanding": 3, "content": 0, "traffic": 2},
    )
    # Total strengths: абхазия=1.4, сочи=0.4, крым=0.2
    order = [(d.key.service, d.key.geo) for d in truth.directions]
    assert order[0] == ("багги", "абхазия")
    assert order[1] == ("багги", "сочи")
    assert order[2] == ("багги", "крым")


def test_union_of_keys_from_all_three_sources():
    """A direction is in the result if ANY source mentions it."""
    from app.core_audit.business_truth.reconciler import reconcile

    truth = reconcile(
        understanding_weights={_k("багги", "абхазия"): 1.0},
        content_pages={_k("экскурсии", "сочи"): ("/sochi-tours/",)},
        traffic_weights={_k("багги", "адлер"): 1.0},
        traffic_queries={_k("багги", "адлер"): ("багги адлер",)},
        sources_used={"understanding": 1, "content": 1, "traffic": 1},
    )
    keys = {(d.key.service, d.key.geo) for d in truth.directions}
    assert keys == {
        ("багги", "абхазия"),
        ("экскурсии", "сочи"),
        ("багги", "адлер"),
    }


def test_sources_used_passed_through():
    from app.core_audit.business_truth.reconciler import reconcile

    truth = reconcile(
        understanding_weights={},
        content_pages={_k("багги", "абхазия"): ("/a/",)},
        traffic_weights={},
        traffic_queries={},
        sources_used={"understanding": 0, "content": 15, "traffic": 0},
    )
    assert truth.sources_used["content"] == 15
