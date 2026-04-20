"""Tests for app.core_audit.draft_profile.service_extractor."""

from __future__ import annotations

from dataclasses import dataclass

from app.core_audit.draft_profile.service_extractor import (
    CANONICAL_SERVICES,
    extract_services,
)


@dataclass
class FakePage:
    title: str | None = None
    h1: str | None = None
    content_text: str | None = None


def test_empty_pages_returns_only_universal_services():
    out = extract_services([])
    names = [s.name for s in out]
    assert "туры" in names
    assert "экскурсии" in names
    # Only the two universals.
    assert len(out) == 2


def test_activity_in_title_and_h1_ranks_high():
    pages = [
        FakePage(title="Яхты в Сочи", h1="Яхты", content_text="аренда яхты на час"),
        FakePage(title="Яхты", h1="Морские прогулки", content_text="выход в море"),
        FakePage(title="Дайвинг", h1="Дайвинг в море", content_text="инструкторы и снаряжение"),
    ]
    out = extract_services(pages)
    names = [s.name for s in out]
    # "яхты" appears in multiple title/h1 so it must be extracted.
    assert "яхты" in names
    yachts = next(s for s in out if s.name == "яхты")
    assert yachts.occurrence_count >= 2
    assert yachts.pages_with >= 2
    assert 0.0 < yachts.confidence <= 1.0


def test_below_threshold_service_is_dropped():
    # Single body-only occurrence (weight 1) — below default min=2.
    pages = [FakePage(title="Другое", h1="Другое", content_text="однажды багги")]
    out = extract_services(pages)
    names = [s.name for s in out]
    assert "багги" not in names
    assert "туры" in names and "экскурсии" in names


def test_top_k_cap_honored():
    # Generate many services above threshold.
    vocab = {"яхты", "багги", "джиппинг", "рафтинг", "дайвинг", "сплав", "круизы"}
    pages = []
    for v in vocab:
        pages.append(FakePage(title=v, h1=v, content_text=v))
        pages.append(FakePage(title=v, h1=v, content_text=v))
    out = extract_services(pages, top_k=3)
    non_universal = [s for s in out if s.name not in {"туры", "экскурсии"}]
    assert len(non_universal) <= 3


def test_generic_nouns_are_not_counted_redundantly():
    pages = [
        FakePage(title="Туры и экскурсии", h1="Туры", content_text="туры туры"),
    ]
    out = extract_services(pages)
    names = [s.name for s in out]
    # Universals are always present exactly once.
    assert names.count("туры") == 1
    assert names.count("экскурсии") == 1


def test_confidence_is_bounded_0_1():
    # Huge repetition of yachts in a single page — confidence capped at 1.0.
    content = " ".join(["яхты"] * 200)
    pages = [FakePage(title="Яхты", h1="Яхты", content_text=content)]
    out = extract_services(pages)
    yachts = [s for s in out if s.name == "яхты"]
    assert yachts, "expected яхты to survive threshold"
    assert 0.0 <= yachts[0].confidence <= 1.0


def test_custom_vocabulary_overrides_canonical():
    pages = [FakePage(title="Foo Bar", h1="Foo", content_text="foo bar foo")]
    out = extract_services(pages, vocabulary={"foo"})
    names = [s.name for s in out]
    assert "foo" in names


def test_canonical_vocabulary_is_non_empty():
    # Safety check — if someone empties the vocab by mistake we want
    # the test suite to catch it.
    assert len(CANONICAL_SERVICES) >= 10
