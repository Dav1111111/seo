"""Tests for the robots.txt parser.

Pins the structural contract: groups keyed by User-agent (case-preserved
but resolved case-insensitively), Sitemap/Clean-param collected into
top-level lists, non-ASCII detection exposed as a flag.
"""

from __future__ import annotations

from app.core_audit.yandex_robots.parser import parse_robots


def test_parser_basic_groups():
    text = (
        "User-agent: Yandex\n"
        "Disallow: /admin/\n"
        "Allow: /admin/public/\n"
        "\n"
        "User-agent: *\n"
        "Disallow: /private/\n"
    )

    parsed = parse_robots(text)

    assert "Yandex" in parsed["groups"]
    assert "*" in parsed["groups"]

    yandex_directives = parsed["groups"]["Yandex"]
    yandex_kinds = [(d["name"], d["value"]) for d in yandex_directives]
    assert ("disallow", "/admin/") in yandex_kinds
    assert ("allow", "/admin/public/") in yandex_kinds

    star_directives = parsed["groups"]["*"]
    assert ("disallow", "/private/") in [(d["name"], d["value"]) for d in star_directives]


def test_parser_sitemap_and_clean_param():
    text = (
        "User-agent: *\n"
        "Disallow: /tmp/\n"
        "Sitemap: https://example.com/sitemap.xml\n"
        "Sitemap: https://example.com/sitemap-news.xml\n"
        "Clean-param: utm_source&utm_medium\n"
        "Clean-param: ref /catalog/\n"
    )

    parsed = parse_robots(text)

    assert parsed["sitemaps"] == [
        "https://example.com/sitemap.xml",
        "https://example.com/sitemap-news.xml",
    ]
    assert parsed["clean_params"] == [
        "utm_source&utm_medium",
        "ref /catalog/",
    ]
    # Sitemap/Clean-param should NOT pollute the group's directive list.
    star = parsed["groups"]["*"]
    names = {d["name"] for d in star}
    assert "sitemap" not in names
    assert "clean-param" not in names


def test_parser_cyrillic_detection():
    text = (
        "User-agent: Yandex\n"
        "Disallow: /экскурсии/\n"
    )

    parsed = parse_robots(text)

    assert parsed["has_non_ascii"] is True
    assert "/экскурсии/" in parsed["non_ascii_evidence"]


def test_parser_strips_comments_and_blank_lines():
    text = (
        "# top comment\n"
        "\n"
        "User-agent: Yandex   # the yandex bot\n"
        "Disallow: /admin/    # admin area\n"
    )

    parsed = parse_robots(text)
    yandex = parsed["groups"]["Yandex"]
    assert any(d["value"] == "/admin/" for d in yandex)
    # No comment text should leak into raw values.
    for d in yandex:
        assert "#" not in d["value"]


def test_parser_case_insensitive_directive_names():
    text = (
        "USER-AGENT: Yandex\n"
        "DISALLOW: /foo\n"
        "ALLOW: /foo/bar\n"
    )
    parsed = parse_robots(text)
    assert "Yandex" in parsed["groups"]
    kinds = {d["name"] for d in parsed["groups"]["Yandex"]}
    assert kinds == {"disallow", "allow"}
