"""understanding_reader: owner's onboarding data → weighted directions.

Input: sites.understanding JSONB + sites.target_config JSONB.
Output: list[(DirectionKey, weight)] where weights sum to ~1.0.

Tests cover each shape of incoming data and the weight arithmetic.
"""

from __future__ import annotations

import pytest

from app.core_audit.business_truth.dto import DirectionKey


def test_returns_empty_when_no_services_or_geos():
    from app.core_audit.business_truth.understanding_reader import (
        read_understanding,
    )
    assert read_understanding(None, None) == []
    assert read_understanding({}, {}) == []
    assert read_understanding({}, {"services": []}) == []
    assert read_understanding({}, {"services": ["багги"], "geo_primary": []}) == []


def test_single_service_single_geo_returns_one_direction_weight_1():
    from app.core_audit.business_truth.understanding_reader import (
        read_understanding,
    )
    out = read_understanding(
        {},
        {"services": ["багги"], "geo_primary": ["абхазия"]},
    )
    assert len(out) == 1
    key, weight = out[0]
    assert key == DirectionKey.of("багги", "абхазия")
    assert weight == pytest.approx(1.0)


def test_cartesian_without_weights_is_equal_share():
    """2 services × 3 geos = 6 pairs, each 1/6."""
    from app.core_audit.business_truth.understanding_reader import (
        read_understanding,
    )
    out = read_understanding(
        {},
        {
            "services": ["багги", "экскурсии"],
            "geo_primary": ["абхазия", "сочи"],
            "geo_secondary": ["крым"],
        },
    )
    assert len(out) == 6
    total = sum(w for _, w in out)
    assert total == pytest.approx(1.0)
    for _, w in out:
        assert w == pytest.approx(1 / 6)


def test_service_weights_honored():
    """service_weights = {"багги": 0.7, "экскурсии": 0.3}, one geo."""
    from app.core_audit.business_truth.understanding_reader import (
        read_understanding,
    )
    out = read_understanding(
        {},
        {
            "services": ["багги", "экскурсии"],
            "geo_primary": ["абхазия"],
            "service_weights": {"багги": 0.7, "экскурсии": 0.3},
        },
    )
    weights = {(k.service, k.geo): w for k, w in out}
    assert weights[("багги", "абхазия")] == pytest.approx(0.7)
    assert weights[("экскурсии", "абхазия")] == pytest.approx(0.3)


def test_geo_weights_honored():
    from app.core_audit.business_truth.understanding_reader import (
        read_understanding,
    )
    out = read_understanding(
        {},
        {
            "services": ["багги"],
            "geo_primary": ["абхазия"],
            "geo_secondary": ["сочи"],
            "geo_weights": {"абхазия": 0.8, "сочи": 0.2},
        },
    )
    weights = {(k.service, k.geo): w for k, w in out}
    assert weights[("багги", "абхазия")] == pytest.approx(0.8)
    assert weights[("багги", "сочи")] == pytest.approx(0.2)


def test_both_weights_multiply_and_normalize():
    """Service 70/30 × Geo 80/20 → 0.56/0.14/0.24/0.06 (normalized)."""
    from app.core_audit.business_truth.understanding_reader import (
        read_understanding,
    )
    out = read_understanding(
        {},
        {
            "services": ["багги", "экскурсии"],
            "geo_primary": ["абхазия"],
            "geo_secondary": ["сочи"],
            "service_weights": {"багги": 0.7, "экскурсии": 0.3},
            "geo_weights": {"абхазия": 0.8, "сочи": 0.2},
        },
    )
    weights = {(k.service, k.geo): w for k, w in out}
    assert weights[("багги", "абхазия")] == pytest.approx(0.56)
    assert weights[("багги", "сочи")] == pytest.approx(0.14)
    assert weights[("экскурсии", "абхазия")] == pytest.approx(0.24)
    assert weights[("экскурсии", "сочи")] == pytest.approx(0.06)
    total = sum(w for _, w in out)
    assert total == pytest.approx(1.0)


def test_unknown_weight_keys_ignored():
    """service_weights for service not in `services` must not leak in."""
    from app.core_audit.business_truth.understanding_reader import (
        read_understanding,
    )
    out = read_understanding(
        {},
        {
            "services": ["багги"],
            "geo_primary": ["абхазия"],
            "service_weights": {"багги": 0.6, "квадроциклы": 0.4},
        },
    )
    keys = [(k.service, k.geo) for k, _ in out]
    assert keys == [("багги", "абхазия")]
    assert out[0][1] == pytest.approx(1.0)  # renormalized


def test_missing_weights_fill_with_equal_share():
    """3 services, only 2 have weights → the third gets (1 - sum) / 1."""
    from app.core_audit.business_truth.understanding_reader import (
        read_understanding,
    )
    out = read_understanding(
        {},
        {
            "services": ["багги", "экскурсии", "трансфер"],
            "geo_primary": ["абхазия"],
            "service_weights": {"багги": 0.6, "экскурсии": 0.3},
            # трансфер missing — should get the remaining 0.1
        },
    )
    weights = {(k.service, k.geo): w for k, w in out}
    assert weights[("багги", "абхазия")] == pytest.approx(0.6)
    assert weights[("экскурсии", "абхазия")] == pytest.approx(0.3)
    assert weights[("трансфер", "абхазия")] == pytest.approx(0.1)


def test_secondary_products_merged_with_services():
    """Both `services` and `secondary_products` contribute to the service list."""
    from app.core_audit.business_truth.understanding_reader import (
        read_understanding,
    )
    out = read_understanding(
        {},
        {
            "services": ["багги"],
            "secondary_products": ["экскурсии"],
            "geo_primary": ["абхазия"],
        },
    )
    keys = {(k.service, k.geo) for k, _ in out}
    assert keys == {("багги", "абхазия"), ("экскурсии", "абхазия")}


def test_duplicate_entries_deduped():
    from app.core_audit.business_truth.understanding_reader import (
        read_understanding,
    )
    out = read_understanding(
        {},
        {
            "services": ["багги", "Багги ", "БАГГИ"],
            "geo_primary": ["абхазия"],
        },
    )
    assert len(out) == 1
    assert out[0][0].service == "багги"
