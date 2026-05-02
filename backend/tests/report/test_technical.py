"""Unit tests for technical audit summary logic."""

from __future__ import annotations

from types import SimpleNamespace

from app.core_audit.report.sections.technical import (
    build_external_technical_issues,
    build_page_technical_checks,
    technical_score,
)


def _page(
    url: str,
    *,
    title: str | None = "Title",
    h1: str | None = "H1",
    meta_description: str | None = "Description",
    status: int = 200,
    links: list[str] | None = None,
    meta: dict | None = None,
    has_schema: bool = False,
):
    return SimpleNamespace(
        url=url,
        title=title,
        h1=h1,
        meta_description=meta_description,
        http_status=status,
        internal_links=links or [],
        meta=meta or {},
        has_schema=has_schema,
    )


def test_page_checks_find_duplicates_noindex_canonical_and_broken_links():
    pages = [
        _page(
            "https://example.ru/a",
            title="Same title",
            h1="Same H1",
            links=["https://example.ru/b"],
            meta={
                "canonical_url": "https://example.ru/a",
                "schema_types": ["Organization"],
            },
            has_schema=True,
        ),
        _page(
            "https://example.ru/b",
            title="Same title",
            h1="Same H1",
            status=404,
            meta={
                "noindex": True,
                "canonical_url": "https://other.ru/b",
            },
        ),
        _page(
            "https://example.ru/c",
            title=None,
            h1=None,
            meta_description=None,
            meta={},
        ),
    ]

    checks, schema_types, issues = build_page_technical_checks(pages)
    codes = {issue.code for issue in issues}

    assert checks["duplicate_titles"] == 1
    assert checks["duplicate_h1"] == 1
    assert checks["missing_title"] == 1
    assert checks["missing_h1"] == 1
    assert checks["missing_meta_description"] == 1
    assert checks["noindex_pages"] == 1
    assert checks["canonical_external"] == 1
    assert checks["canonical_missing"] == 1
    assert checks["broken_internal_links"] == 1
    assert checks["pages_non_200"] == 1
    assert schema_types == {"Organization": 1}
    assert "noindex_pages" in codes
    assert "broken_internal_links" in codes
    assert "canonical_external" in codes


def test_external_checks_find_bad_robots_and_sitemap():
    issues = build_external_technical_issues(
        robots={
            "url": "https://example.ru/robots.txt",
            "ok": False,
            "returns_html": True,
            "disallow_all": True,
        },
        sitemap={
            "url": "https://example.ru/sitemap.xml",
            "valid_xml": False,
            "returns_html": True,
        },
        pages_not_in_sitemap=["https://example.ru/a"],
    )

    codes = {issue.code for issue in issues}
    assert "robots_returns_html" in codes
    assert "robots_disallow_all" in codes
    assert "sitemap_returns_html" in codes
    assert "pages_not_in_sitemap" in codes
    assert technical_score(issues) < 50


def test_clean_site_keeps_full_technical_score():
    checks, schema_types, issues = build_page_technical_checks([
        _page(
            "https://example.ru/",
            meta={
                "canonical_url": "https://example.ru/",
                "schema_types": ["Organization"],
            },
            has_schema=True,
        )
    ])
    assert checks["missing_title"] == 0
    assert schema_types == {"Organization": 1}
    assert issues == []
    assert technical_score(issues) == 100

