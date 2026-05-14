"""Tests for the URL matcher with Yandex precedence rules.

Pins the documented behavior:
  - Most-specific User-agent group wins (YandexBot > Yandex > *).
  - Longest matching rule wins within a group.
  - At equal length, Allow beats Disallow (Yandex-specific; differs
    from Google).
  - Wildcards: `*` matches anything, `$` anchors end-of-path.
"""

from __future__ import annotations

from app.core_audit.yandex_robots.matcher import match_url
from app.core_audit.yandex_robots.parser import parse_robots


def test_matcher_yandex_overrides_star():
    text = (
        "User-agent: *\n"
        "Disallow: /\n"
        "\n"
        "User-agent: Yandex\n"
        "Allow: /\n"
    )
    parsed = parse_robots(text)

    allowed, ua, rule = match_url(parsed, "/anywhere", preferred_ua="Yandex")
    assert allowed is True
    assert ua == "Yandex"
    assert "Allow" in rule


def test_matcher_longest_match_wins():
    text = (
        "User-agent: Yandex\n"
        "Disallow: /\n"
        "Allow: /admin/\n"
    )
    parsed = parse_robots(text)

    # /admin/foo — /admin/ is longer than /, so Allow wins.
    allowed, _, rule = match_url(parsed, "/admin/foo", preferred_ua="Yandex")
    assert allowed is True
    assert "/admin/" in rule

    # /other — only / matches, so Disallow wins.
    allowed, _, rule = match_url(parsed, "/other", preferred_ua="Yandex")
    assert allowed is False
    assert rule.startswith("Disallow")


def test_matcher_allow_beats_disallow_same_length():
    text = (
        "User-agent: Yandex\n"
        "Disallow: /foo\n"
        "Allow: /foo\n"
    )
    parsed = parse_robots(text)

    allowed, _, rule = match_url(parsed, "/foo/bar", preferred_ua="Yandex")
    assert allowed is True
    assert rule.startswith("Allow")


def test_matcher_wildcard_and_dollar():
    text = (
        "User-agent: Yandex\n"
        "Disallow: *.pdf$\n"
    )
    parsed = parse_robots(text)

    blocked, _, rule = match_url(parsed, "/docs/file.pdf", preferred_ua="Yandex")
    assert blocked is False
    assert ".pdf$" in rule

    # .pdfx does NOT match the $-anchored .pdf$
    allowed, _, _ = match_url(parsed, "/docs/file.pdfx", preferred_ua="Yandex")
    assert allowed is True


def test_matcher_yandexbot_beats_generic_yandex():
    text = (
        "User-agent: Yandex\n"
        "Disallow: /\n"
        "\n"
        "User-agent: YandexBot\n"
        "Allow: /\n"
    )
    parsed = parse_robots(text)

    allowed, ua, _ = match_url(parsed, "/page", preferred_ua="YandexBot")
    assert allowed is True
    assert ua == "YandexBot"


def test_matcher_default_allow_when_no_group():
    text = (
        "User-agent: Googlebot\n"
        "Disallow: /\n"
    )
    parsed = parse_robots(text)

    allowed, ua, rule = match_url(parsed, "/anything", preferred_ua="Yandex")
    assert allowed is True
    assert ua == ""
    assert rule == ""


def test_matcher_handles_full_urls():
    text = (
        "User-agent: Yandex\n"
        "Disallow: /admin/\n"
    )
    parsed = parse_robots(text)

    allowed, _, rule = match_url(
        parsed, "https://example.com/admin/users", preferred_ua="Yandex"
    )
    assert allowed is False
    assert "/admin/" in rule
