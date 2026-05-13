"""Unit tests for `verify.filter_hallucinated_cargo_cult`.

Bug context (2026-05-14): the review LLM was echoing the cargo-cult
deny-list from the prompt back as `detected_cargo_cult_schemas`, even when
none of those types appeared on the page. For `/stories/post-3` (a
BlogPosting), five false-positive recommendations were generated. The
filter compares against the actual JSON-LD blocks and is the Python-side
belt-and-braces guard.
"""

from __future__ import annotations

from app.core_audit.review.llm.verify import filter_hallucinated_cargo_cult


def test_filter_drops_types_not_in_schema_blocks():
    """LLM claims TouristTrip but page only has BlogPosting → drop."""
    result = filter_hallucinated_cargo_cult(
        detected=["TouristTrip"],
        schema_blocks=[{"@type": "BlogPosting", "@context": "https://schema.org"}],
    )
    assert result == []


def test_filter_keeps_real_types():
    """LLM claims TouristTrip and page actually carries TouristTrip → keep."""
    result = filter_hallucinated_cargo_cult(
        detected=["TouristTrip"],
        schema_blocks=[{"@type": "TouristTrip", "name": "Озеро Рица"}],
    )
    assert result == ["TouristTrip"]


def test_filter_handles_type_as_list():
    """schema.org `@type` may be a list of types — match if any element matches."""
    result = filter_hallucinated_cargo_cult(
        detected=["BlogPosting"],
        schema_blocks=[{"@type": ["Article", "BlogPosting"]}],
    )
    assert result == ["BlogPosting"]


def test_filter_fails_closed_when_schema_blocks_none():
    """No extraction available → drop everything (better silent than false-positive)."""
    result = filter_hallucinated_cargo_cult(
        detected=["TouristTrip", "Event"],
        schema_blocks=None,
    )
    assert result == []


def test_filter_case_insensitive():
    """LLM may send 'touristtrip' lowercase; page block uses 'TouristTrip'. Keep, preserve case."""
    result = filter_hallucinated_cargo_cult(
        detected=["touristtrip"],
        schema_blocks=[{"@type": "TouristTrip"}],
    )
    assert result == ["touristtrip"]


# ── Edge cases for robustness ─────────────────────────────────────────────


def test_filter_empty_detected_returns_empty():
    """Nothing to filter → empty result, regardless of schema_blocks."""
    assert filter_hallucinated_cargo_cult([], schema_blocks=[{"@type": "TouristTrip"}]) == []


def test_filter_empty_schema_blocks_drops_all():
    """No JSON-LD blocks at all → every detected type is hallucinated."""
    result = filter_hallucinated_cargo_cult(
        detected=["TouristTrip", "Event"],
        schema_blocks=[],
    )
    assert result == []


def test_filter_keeps_only_matching_subset():
    """Mixed list: keep the real one, drop the hallucinated ones."""
    result = filter_hallucinated_cargo_cult(
        detected=["TouristTrip", "Event", "TravelAction"],
        schema_blocks=[{"@type": "TouristTrip"}],
    )
    assert result == ["TouristTrip"]


def test_filter_tolerates_block_without_type():
    """Blocks missing `@type` are skipped, not crashed-on."""
    result = filter_hallucinated_cargo_cult(
        detected=["BlogPosting"],
        schema_blocks=[{"name": "no type field"}, {"@type": "BlogPosting"}],
    )
    assert result == ["BlogPosting"]


def test_filter_tolerates_non_dict_block():
    """Defensive: blocks that aren't dicts (corrupt data) are skipped."""
    result = filter_hallucinated_cargo_cult(
        detected=["BlogPosting"],
        schema_blocks=["garbage", None, {"@type": "BlogPosting"}],
    )
    assert result == ["BlogPosting"]


def test_filter_preserves_original_casing_in_returned_strings():
    """Case-insensitive compare, but returned values match the LLM input casing."""
    result = filter_hallucinated_cargo_cult(
        detected=["TOURISTTRIP", "blogposting"],
        schema_blocks=[{"@type": ["TouristTrip", "BlogPosting"]}],
    )
    assert result == ["TOURISTTRIP", "blogposting"]
