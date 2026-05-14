"""Pure-function parser for robots.txt.

Splits a robots.txt file into User-agent groups plus top-level
Sitemap/Clean-param lists. Tolerant of whitespace, comments, and mixed
casing of directive names; preserves the original line text so the
auditor can quote it as evidence.

No network, no DB, no LLM — input is the raw text, output is a
structured dict.
"""

from __future__ import annotations

from typing import TypedDict


class ParsedDirective(TypedDict):
    name: str           # canonical lowercase: "allow", "disallow", "crawl-delay", "host"
    value: str          # right-hand side after the colon, stripped
    raw: str            # original line (without trailing comment), e.g. "Disallow: /admin/"
    line_no: int        # 1-based


class ParsedRobots(TypedDict):
    groups: dict[str, list[ParsedDirective]]   # UA name (original case preserved) -> directives
    sitemaps: list[str]
    clean_params: list[str]
    raw_lines: list[str]
    has_non_ascii: bool
    non_ascii_evidence: str   # first offending line literal, or ""


_RECOGNIZED = {
    "user-agent",
    "allow",
    "disallow",
    "sitemap",
    "clean-param",
    "crawl-delay",
    "host",
}


def parse_robots(text: str) -> ParsedRobots:
    """Parse a robots.txt into structured groups + top-level lists.

    A group starts at a `User-agent:` line and continues until the next
    `User-agent:` (or EOF). Sitemap / Clean-param directives are
    collected globally because they apply across all bots regardless of
    their textual location.
    """
    groups: dict[str, list[ParsedDirective]] = {}
    sitemaps: list[str] = []
    clean_params: list[str] = []
    raw_lines: list[str] = []
    has_non_ascii = False
    non_ascii_evidence = ""

    current_uas: list[str] = []
    last_was_user_agent = False

    if not text:
        return ParsedRobots(
            groups=groups,
            sitemaps=sitemaps,
            clean_params=clean_params,
            raw_lines=raw_lines,
            has_non_ascii=has_non_ascii,
            non_ascii_evidence=non_ascii_evidence,
        )

    for idx, original in enumerate(text.splitlines(), start=1):
        raw_lines.append(original)

        # Strip comments (#...) but keep the rest.
        hash_pos = original.find("#")
        if hash_pos >= 0:
            line = original[:hash_pos]
        else:
            line = original
        line = line.strip()

        if not line:
            last_was_user_agent = False
            continue

        if ":" not in line:
            last_was_user_agent = False
            continue

        key, _, value = line.partition(":")
        key_norm = key.strip().lower()
        value = value.strip()

        if key_norm not in _RECOGNIZED:
            last_was_user_agent = False
            continue

        # Detect non-ASCII anywhere in directives (Yandex requires
        # punycode/percent-encoding).
        if not has_non_ascii:
            for ch in line:
                if ord(ch) > 0x7F:
                    has_non_ascii = True
                    non_ascii_evidence = line
                    break

        raw_directive = f"{key.strip()}: {value}" if value else f"{key.strip()}:"

        if key_norm == "user-agent":
            ua = value
            if not last_was_user_agent:
                current_uas = []
            current_uas.append(ua)
            groups.setdefault(ua, [])
            last_was_user_agent = True
            continue

        last_was_user_agent = False

        if key_norm == "sitemap":
            if value:
                sitemaps.append(value)
            continue

        if key_norm == "clean-param":
            if value:
                clean_params.append(value)
            continue

        directive: ParsedDirective = {
            "name": key_norm,
            "value": value,
            "raw": raw_directive,
            "line_no": idx,
        }

        if not current_uas:
            # Directive before any User-agent — orphaned, skip.
            continue

        for ua in current_uas:
            groups.setdefault(ua, []).append(directive)

    return ParsedRobots(
        groups=groups,
        sitemaps=sitemaps,
        clean_params=clean_params,
        raw_lines=raw_lines,
        has_non_ascii=has_non_ascii,
        non_ascii_evidence=non_ascii_evidence,
    )
