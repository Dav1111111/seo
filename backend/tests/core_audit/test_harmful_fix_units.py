"""Unit tests for harmful_fix without a live DB.

Covers pure helpers: URL normalization, before-text extraction,
composite hash determinism, fix→category mapping.
"""

from types import SimpleNamespace

import pytest

from app.core_audit.harmful_fix import (
    _FIX_TO_CATEGORY,
    _before_text_for,
    _composite_hash_for_review,
    _normalize_url,
)


class TestNormalizeUrl:
    def test_strips_protocol_www_trailing_slash(self):
        assert _normalize_url("https://www.example.com/foo/") == "example.com/foo"

    def test_lowercases(self):
        assert _normalize_url("HTTPS://Example.COM/Path") == "example.com/path"

    def test_handles_http(self):
        assert _normalize_url("http://example.com") == "example.com"

    def test_empty_returns_empty(self):
        assert _normalize_url("") == ""
        assert _normalize_url(None) == ""

    def test_two_urls_match_modulo_canonicalization(self):
        assert (
            _normalize_url("https://www.example.com/page")
            == _normalize_url("http://example.com/page/")
        )


class TestBeforeTextFor:
    def _page(self, **kw):
        return SimpleNamespace(
            title=kw.get("title"),
            h1=kw.get("h1"),
            meta=kw.get("meta", {}),
        )

    def test_title_returns_page_title(self):
        page = self._page(title="Old title")
        assert _before_text_for("title", page) == "Old title"

    def test_h1_returns_page_h1(self):
        page = self._page(h1="Old H1")
        assert _before_text_for("h1_structure", page) == "Old H1"

    def test_meta_description_pulls_from_meta_dict(self):
        page = self._page(meta={"meta_description": "OD"})
        assert _before_text_for("meta_description", page) == "OD"

    def test_meta_description_fallback_to_description(self):
        page = self._page(meta={"description": "alt"})
        assert _before_text_for("meta_description", page) == "alt"

    def test_schema_concatenates_types(self):
        page = self._page(meta={"schema_types": ["Product", "FAQPage"]})
        assert _before_text_for("schema", page) == "Product, FAQPage"

    def test_over_optimization_returns_none(self):
        # Content tweak is described in the recommendation itself,
        # there's no single "before" field to display.
        page = self._page(title="x", h1="y")
        assert _before_text_for("over_optimization", page) is None

    def test_unknown_category_returns_none(self):
        assert _before_text_for("unknown", self._page()) is None


class TestCompositeHash:
    def test_deterministic_for_same_input(self):
        page = SimpleNamespace(id="abc", title="T", h1="H")
        assert _composite_hash_for_review(page) == _composite_hash_for_review(page)

    def test_changes_when_title_changes(self):
        a = SimpleNamespace(id="abc", title="A", h1="H")
        b = SimpleNamespace(id="abc", title="B", h1="H")
        assert _composite_hash_for_review(a) != _composite_hash_for_review(b)

    def test_returns_hex_string(self):
        page = SimpleNamespace(id="abc", title="T", h1="H")
        h = _composite_hash_for_review(page)
        assert len(h) == 64  # sha256
        int(h, 16)  # must be valid hex


class TestFixCategoryMapping:
    def test_all_categories_are_in_recommendation_enum(self):
        # Each mapped category must exist in RecCategory enum, otherwise
        # the recommendation won't render in UI.
        from app.core_audit.review.enums import RecCategory
        valid = {c.value for c in RecCategory}
        for fix_key, category in _FIX_TO_CATEGORY.items():
            assert category in valid, (
                f"fix_key {fix_key!r} maps to unknown category {category!r}"
            )

    def test_all_known_fix_keys_present(self):
        # If diagnoser starts emitting a new fix key, this test reminds
        # us to wire it through. Update both sides intentionally.
        expected_keys = {
            "title_change",
            "h1_change",
            "meta_description_change",
            "content_change_ru",
            "schema_recommendation",
        }
        assert set(_FIX_TO_CATEGORY.keys()) == expected_keys
