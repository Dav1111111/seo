"""URL matcher with Yandex precedence rules.

Differences from the generic robots.txt spec we must honor:
  - Yandex picks the most specific UA group (e.g. `YandexBot`) over the
    generic `Yandex`, and `Yandex` over `*`.
  - Within the selected group: longest matching rule wins.
  - Tie-break: at equal length, Allow beats Disallow. This is the
    documented Yandex behaviour and is the OPPOSITE of how many people
    assume robots.txt works.
"""

from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urlsplit

from app.core_audit.yandex_robots.parser import ParsedDirective, ParsedRobots


def _normalize_ua(ua: str) -> str:
    return ua.strip().lower()


def select_group_for_yandex(
    groups: dict[str, list[ParsedDirective]],
    preferred_ua: str = "Yandex",
) -> tuple[str, list[ParsedDirective]]:
    """Select the directive list that governs Yandex traffic.

    Precedence:
      1. Exact match of `preferred_ua` (case-insensitive), e.g.
         "YandexBot" beats "Yandex" when caller asks for YandexBot.
      2. Generic "Yandex" group.
      3. "*" group.
      4. None — caller treats as default-allow.
    """
    by_lower = {_normalize_ua(k): k for k in groups}

    pref = _normalize_ua(preferred_ua)
    if pref in by_lower:
        key = by_lower[pref]
        return key, groups[key]

    if pref != "yandex" and "yandex" in by_lower:
        key = by_lower["yandex"]
        return key, groups[key]

    if "*" in by_lower:
        key = by_lower["*"]
        return key, groups[key]

    return "", []


def _pattern_to_regex(pattern: str) -> tuple[re.Pattern[str], bool]:
    """Compile a robots.txt path pattern into a regex.

    Returns (compiled_regex, is_anchored_at_end).

    Pattern semantics:
      - `*` matches any sequence (including empty).
      - `$` at end of pattern anchors end-of-path.
      - Otherwise the pattern matches as a prefix.
    """
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern

    out: list[str] = []
    for ch in body:
        if ch == "*":
            out.append(".*")
        else:
            out.append(re.escape(ch))
    regex_str = "^" + "".join(out)
    if anchored:
        regex_str += "$"

    return re.compile(regex_str), anchored


def _path_from_url(url: str) -> str:
    """Extract the path-with-query part used for matching.

    If `url` is already a path (no scheme), return it directly with a
    leading slash if missing.
    """
    if "://" in url:
        parts = urlsplit(url)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        return path

    if not url.startswith("/"):
        return "/" + url
    return url


def _rule_specificity(value: str) -> int:
    """Specificity = literal-character count of the pattern.

    `*` and `$` are treated as zero-width markers; everything else
    counts. This is the standard Yandex / Google "longest match" metric.
    """
    return sum(1 for ch in value if ch not in ("*", "$"))


def match_url(
    parsed: ParsedRobots,
    url: str,
    preferred_ua: str = "Yandex",
) -> tuple[bool, str, str]:
    """Decide whether `url` is allowed for the Yandex bot.

    Returns (allowed, matched_user_agent, matched_rule). If no rule in
    the selected group matched, allowed=True and matched_rule="".
    """
    group_ua, directives = select_group_for_yandex(parsed["groups"], preferred_ua)

    if not directives:
        return True, group_ua, ""

    path = _path_from_url(url)

    best: tuple[int, int, ParsedDirective] | None = None
    # (specificity, allow_priority, directive)
    # allow_priority=1 for Allow, 0 for Disallow — used as tie-break at
    # equal specificity per Yandex's "Allow beats Disallow" rule.

    for d in directives:
        name = d["name"]
        if name not in ("allow", "disallow"):
            continue
        pattern = d["value"]
        if pattern == "":
            # Empty Disallow means "allow all" in classic robots.txt.
            # Empty Allow is a no-op. Either way, no path match — skip.
            continue

        regex, _ = _pattern_to_regex(pattern)
        if not regex.match(path):
            continue

        specificity = _rule_specificity(pattern)
        allow_priority = 1 if name == "allow" else 0

        candidate = (specificity, allow_priority, d)
        if best is None:
            best = candidate
            continue

        if specificity > best[0]:
            best = candidate
        elif specificity == best[0] and allow_priority > best[1]:
            # Yandex differs from Google here: Allow beats Disallow at
            # equal length.
            best = candidate

    if best is None:
        return True, group_ua, ""

    _, allow_priority, decided = best
    allowed = allow_priority == 1
    return allowed, group_ua, decided["raw"]
