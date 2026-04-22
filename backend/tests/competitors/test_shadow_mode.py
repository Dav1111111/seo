"""Shadow-mode integration: _compute_shadow_picks rehydrates the
persisted BusinessTruth blob + compares new picks vs old queries.

Pure logic test — doesn't need DB or Celery. Verifies the diff shape
downstream analytics will consume from analysis_events.extra.
"""

from __future__ import annotations


def test_compute_shadow_returns_diff_shape():
    from app.core_audit.competitors.tasks import _compute_shadow_picks

    bt_blob = {
        "directions": [
            {
                "service": "багги",
                "geo": "абхазия",
                "strength_understanding": 0.5,
                "strength_content": 0.5,
                "strength_traffic": 1.0,
                "pages": ["/a/"],
                "queries_sample": ["багги абхазия", "багги абхазия цена"],
            },
            {
                "service": "багги",
                "geo": "сочи",
                "strength_understanding": 0.3,
                "strength_content": 0.3,
                "strength_traffic": 0.5,
                "pages": ["/sochi/"],
                "queries_sample": ["багги сочи"],
            },
        ],
    }
    old_queries = ["багги абхазия", "отдых абхазия", "туры в абхазию"]

    diff, picks = _compute_shadow_picks(bt_blob, budget=3, old_queries=old_queries)

    assert diff["old_count"] == 3
    assert 1 <= diff["new_count"] <= 3
    assert "direction_budget" in diff
    # Only "багги абхазия" overlaps between old and new
    assert diff["overlap_count"] == 1
    assert isinstance(picks, list)


def test_compute_shadow_empty_blob_returns_empty_picks():
    from app.core_audit.competitors.tasks import _compute_shadow_picks

    diff, picks = _compute_shadow_picks(
        {"directions": []}, budget=10, old_queries=["q1"],
    )
    assert diff["new_count"] == 0
    assert picks == []


def test_compute_shadow_tracks_deficit_when_evidence_thin():
    """Direction gets 5 slots but only has 1 query in evidence."""
    from app.core_audit.competitors.tasks import _compute_shadow_picks

    bt_blob = {
        "directions": [
            {
                "service": "s",
                "geo": "a",
                "strength_understanding": 1.0,
                "strength_content": 1.0,
                "strength_traffic": 1.0,
                "pages": [],
                "queries_sample": ["only one"],
            },
        ],
    }
    diff, picks = _compute_shadow_picks(bt_blob, budget=5, old_queries=[])
    assert diff["deficit"] is not None
    # All 5 slots wanted but only 1 delivered → deficit 4
    assert diff["deficit"]["s·a"] == 4
