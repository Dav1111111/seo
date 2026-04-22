"""Pure unit tests for BusinessTruth dataclasses — no DB, no LLM, no I/O.

Fixes the contract: if any future change breaks the semantics of
is_blind_spot / is_traffic_only / divergence_ru, these tests will scream
before the code ships.
"""

from __future__ import annotations

from app.core_audit.business_truth.dto import (
    ALL_SOURCES, SOURCE_CONTENT, SOURCE_TRAFFIC, SOURCE_UNDERSTANDING,
    BusinessTruth, DirectionEvidence, DirectionKey,
)


def _mk(service, geo, u=0.0, c=0.0, t=0.0, pages=(), queries=()):
    return DirectionEvidence(
        key=DirectionKey.of(service, geo),
        strength_understanding=u,
        strength_content=c,
        strength_traffic=t,
        pages=pages,
        queries=queries,
    )


def test_key_normalizes_case_and_whitespace():
    a = DirectionKey.of("  Багги ", "АБХАЗИЯ")
    b = DirectionKey.of("багги", "абхазия")
    assert a == b
    assert a.label_ru() == "багги · абхазия"


def test_mentioned_in_tracks_non_zero_sources():
    d = _mk("багги", "абхазия", u=0.5, c=0.0, t=0.3)
    assert d.mentioned_in == {SOURCE_UNDERSTANDING, SOURCE_TRAFFIC}
    assert not d.is_confirmed or d.is_confirmed  # 2+ sources present

    d2 = _mk("багги", "абхазия", u=0.5)
    assert d2.mentioned_in == {SOURCE_UNDERSTANDING}
    assert not d2.is_confirmed


def test_blind_spot_owner_and_content_but_no_traffic():
    """The Sochi/Crimea case from user's complaint — page exists,
    owner claims it, but no traffic → needs SEO push."""
    d = _mk("багги", "сочи", u=0.3, c=0.3, t=0.0, pages=("/sochi/",))
    assert d.is_blind_spot is True
    assert d.is_content_only is False
    assert d.is_traffic_only is False
    msg = d.divergence_ru() or ""
    assert "страница" in msg.lower()
    assert "трафика" in msg.lower()


def test_content_only_page_exists_but_owner_silent_and_no_traffic():
    d = _mk("багги", "геленджик", c=0.1, pages=("/gelendzhik/",))
    assert d.is_content_only is True
    assert d.is_blind_spot is False
    assert d.is_traffic_only is False
    msg = d.divergence_ru() or ""
    assert "онбординге" in msg or "Уточни" in msg


def test_traffic_only_needs_landing_page():
    """People land on generic pages for a query → uncaptured demand."""
    d = _mk("багги", "адлер", t=0.1, queries=("багги адлер",))
    assert d.is_traffic_only is True
    assert d.is_content_only is False
    assert d.is_blind_spot is False
    msg = d.divergence_ru() or ""
    assert "посадочную" in msg.lower() or "страницы" in msg.lower()


def test_aspiration_only_owner_says_but_no_content_no_traffic():
    d = _mk("багги", "крым", u=0.2)
    assert d.is_blind_spot is False  # no content
    assert d.is_content_only is False
    assert d.is_traffic_only is False
    msg = d.divergence_ru() or ""
    assert "онбординге" in msg.lower()
    assert "страницы" in msg.lower()


def test_fully_aligned_direction_has_no_divergence():
    """Owner says 50%, content has it, traffic flows — everything matches."""
    d = _mk("багги", "абхазия", u=0.5, c=0.6, t=0.9)
    assert d.is_confirmed is True
    assert d.is_blind_spot is False
    assert d.divergence_ru() is None


def test_truth_confirmed_excludes_single_source_directions():
    truth = BusinessTruth(directions=[
        _mk("багги", "абхазия", u=0.5, c=0.6, t=0.9),   # all 3
        _mk("багги", "сочи",    u=0.3, c=0.3),          # only 2
        _mk("багги", "геленджик", c=0.1),               # only 1
    ])
    confirmed_keys = {(d.key.service, d.key.geo) for d in truth.confirmed()}
    assert ("багги", "абхазия") in confirmed_keys
    assert ("багги", "сочи") in confirmed_keys
    assert ("багги", "геленджик") not in confirmed_keys


def test_truth_divergences_returns_non_aligned_directions():
    truth = BusinessTruth(directions=[
        _mk("багги", "абхазия", u=0.5, c=0.6, t=0.9),   # aligned → no div
        _mk("багги", "сочи",    u=0.3, c=0.3),          # blind_spot
        _mk("багги", "адлер",            t=0.2),        # traffic_only
    ])
    divs = truth.divergences()
    assert len(divs) == 2
    services_with_divergence = {d.key.geo for d, _ in divs}
    assert services_with_divergence == {"сочи", "адлер"}


def test_jsonb_serialization_roundtrip_shape():
    truth = BusinessTruth(
        directions=[_mk("багги", "абхазия", u=0.5, c=0.6, t=0.9,
                        pages=("/a/",), queries=("багги абхазия",))],
        sources_used={"understanding": 1, "content": 3, "traffic": 2},
        built_at_iso="2026-04-22T20:00:00Z",
    )
    blob = truth.to_jsonb()
    assert blob["sources_used"] == {"understanding": 1, "content": 3, "traffic": 2}
    assert blob["built_at"] == "2026-04-22T20:00:00Z"
    assert len(blob["directions"]) == 1
    d = blob["directions"][0]
    assert d["service"] == "багги"
    assert d["geo"] == "абхазия"
    assert d["strength_understanding"] == 0.5
    assert d["is_confirmed"] is True
    assert d["divergence_ru"] is None


def test_jsonb_includes_unclassified_diagnostics():
    """Item 4: top_unclassified_queries and unclassified_share travel
    with the truth blob so UI can diagnose vocab narrowness."""
    truth = BusinessTruth(
        directions=[_mk("багги", "абхазия", u=0.5, c=0.6, t=0.9)],
        sources_used={"understanding": 1, "content": 3, "traffic": 2},
        built_at_iso="2026-04-22T20:00:00Z",
        top_unclassified_queries=[
            ("котики милые", 400),
            ("жучок паучок", 100),
        ],
        unclassified_share=0.35,
    )
    blob = truth.to_jsonb()
    assert blob["unclassified_share"] == 0.35
    assert blob["top_unclassified_queries"] == [
        {"query": "котики милые", "impressions": 400},
        {"query": "жучок паучок", "impressions": 100},
    ]


def test_jsonb_defaults_when_unclassified_empty():
    """Default empty: unclassified_share=0.0, queries=[]."""
    truth = BusinessTruth(
        directions=[_mk("багги", "абхазия", u=1.0)],
        sources_used={"understanding": 1, "content": 0, "traffic": 0},
        built_at_iso="2026-04-22T20:00:00Z",
    )
    blob = truth.to_jsonb()
    assert blob["unclassified_share"] == 0.0
    assert blob["top_unclassified_queries"] == []
