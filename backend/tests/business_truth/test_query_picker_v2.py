"""Week 2: quota-driven query picker (shadow-mode component).

Takes a BusinessTruth + a budget, produces a list of queries **tagged
with the direction they came from**. Replaces the flat
_pick_top_queries() which had no direction awareness.

Pure logic tested here. Target-cluster fallback path has its own
integration test.
"""

from __future__ import annotations

import pytest

from app.core_audit.business_truth.dto import (
    BusinessTruth, DirectionEvidence, DirectionKey,
)


def _dir(service, geo, u=0.0, c=0.0, t=0.0, pages=(), queries=()):
    return DirectionEvidence(
        key=DirectionKey.of(service, geo),
        strength_understanding=u,
        strength_content=c,
        strength_traffic=t,
        pages=pages,
        queries=queries,
    )


def test_empty_truth_returns_empty_pick_result():
    from app.core_audit.business_truth.query_picker_v2 import (
        pick_queries_from_truth,
    )
    truth = BusinessTruth(directions=[])
    out = pick_queries_from_truth(truth, budget=30)
    assert out.queries == []
    assert out.direction_budget == {}


def test_single_direction_queries_drawn_from_truth():
    """Direction with 3 queries in its evidence, budget=2 → top 2 picked."""
    from app.core_audit.business_truth.query_picker_v2 import (
        pick_queries_from_truth,
    )
    truth = BusinessTruth(directions=[
        _dir("багги", "абхазия", u=0.5, c=0.5, t=1.0,
             queries=("багги абхазия", "багги абхазия цена", "багги абхазия 2025")),
    ])
    out = pick_queries_from_truth(truth, budget=2)
    # Only 2 queries requested, top of the direction's evidence
    assert len(out.queries) == 2
    # All tagged with the same direction
    for q in out.queries:
        assert q.direction == DirectionKey.of("багги", "абхазия")
        assert q.source == "business_truth"
    # direction_budget says this direction got 2 slots
    assert out.direction_budget[DirectionKey.of("багги", "абхазия")] == 2


def test_multiple_directions_share_budget_by_quotas():
    """3 equal directions, budget 6 → 2 each."""
    from app.core_audit.business_truth.query_picker_v2 import (
        pick_queries_from_truth,
    )
    truth = BusinessTruth(directions=[
        _dir("багги", "абхазия", u=0.33, t=0.33, queries=("q1", "q2", "q3")),
        _dir("багги", "сочи",    u=0.33, t=0.33, queries=("q4", "q5", "q6")),
        _dir("багги", "крым",    u=0.33, t=0.33, queries=("q7", "q8", "q9")),
    ])
    out = pick_queries_from_truth(truth, budget=6)
    assert len(out.queries) == 6
    counts = {}
    for q in out.queries:
        counts[q.direction] = counts.get(q.direction, 0) + 1
    assert counts[DirectionKey.of("багги", "абхазия")] == 2
    assert counts[DirectionKey.of("багги", "сочи")]    == 2
    assert counts[DirectionKey.of("багги", "крым")]    == 2


def test_direction_with_insufficient_evidence_reports_deficit():
    """Quota 5 but only 2 stored queries → pick returns 2 + deficit tracked."""
    from app.core_audit.business_truth.query_picker_v2 import (
        pick_queries_from_truth,
    )
    truth = BusinessTruth(directions=[
        _dir("багги", "абхазия", u=1.0, t=1.0, queries=("q1", "q2")),
    ])
    out = pick_queries_from_truth(truth, budget=5)
    assert len(out.queries) == 2
    assert out.direction_budget[DirectionKey.of("багги", "абхазия")] == 5
    assert out.deficit[DirectionKey.of("багги", "абхазия")] == 3


def test_aspiration_directions_capped_by_penalty():
    """Aspiration direction gets 10% weight in the picker (matches
    allocate_quotas default) so dream doesn't squeeze out real."""
    from app.core_audit.business_truth.query_picker_v2 import (
        pick_queries_from_truth,
    )
    truth = BusinessTruth(directions=[
        _dir("s", "real",  u=0.5, c=0.5, t=0.5, queries=("r1", "r2", "r3", "r4", "r5")),
        _dir("s", "dream", u=0.5, queries=()),
    ])
    out = pick_queries_from_truth(truth, budget=10, aspiration_penalty=0.1)
    real_slots = out.direction_budget[DirectionKey.of("s", "real")]
    dream_slots = out.direction_budget[DirectionKey.of("s", "dream")]
    assert real_slots >= dream_slots  # real wins


def test_queries_tagged_with_source():
    """Every picked query carries (direction, source) metadata.
    source='business_truth' when drawn from truth.queries."""
    from app.core_audit.business_truth.query_picker_v2 import (
        pick_queries_from_truth,
    )
    truth = BusinessTruth(directions=[
        _dir("s", "a", u=1.0, t=1.0, queries=("q1",)),
    ])
    out = pick_queries_from_truth(truth, budget=1)
    assert out.queries[0].query == "q1"
    assert out.queries[0].direction == DirectionKey.of("s", "a")
    assert out.queries[0].source == "business_truth"


def test_contract_total_queries_never_exceeds_budget():
    """Whatever the input, len(queries) ≤ budget."""
    from app.core_audit.business_truth.query_picker_v2 import (
        pick_queries_from_truth,
    )
    truth = BusinessTruth(directions=[
        _dir("s", "a", u=1.0, queries=tuple(f"q{i}" for i in range(20))),
    ])
    out = pick_queries_from_truth(truth, budget=5)
    assert len(out.queries) == 5
