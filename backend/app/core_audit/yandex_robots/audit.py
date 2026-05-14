"""Deterministic Yandex robots.txt auditor.

Pure function — no I/O, no LLM, no DB. Given the raw robots.txt text
(plus the fetch metadata and a list of URLs to test), returns a
structured `YandexRobotsAuditResult` with stable issue codes and
literal evidence.

Rule philosophy mirrors `schema_audit/`:
  - Stable string codes consumers may switch on.
  - Honest wording — no "robots blocks indexing" exaggeration; we say
    "Yandex may not crawl" / "проверьте".
  - `summary_ru` and `recommendations_ru` are templated, NOT
    paraphrased — fixed Russian strings concatenated from the issue
    list.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.core_audit.yandex_robots.dto import (
    YandexRobotsAuditResult,
    YandexRobotsIssue,
    YandexRobotsUrlCheck,
)
from app.core_audit.yandex_robots.matcher import (
    _path_from_url,
    match_url,
    select_group_for_yandex,
)
from app.core_audit.yandex_robots.parser import parse_robots

_MAX_BYTES = 500_000

# Markers in a URL path that suggest "admin / api / static / private"
# — disallowing these is normal and we should NOT nag about noindex.
_ADMIN_MARKERS = (
    "/admin",
    "/wp-admin",
    "/api",
    "/cgi-bin",
    "/cdn-cgi",
    "/static",
    "/assets",
    "/uploads",
    "/private",
    "/account",
    "/cart",
    "/checkout",
    "/login",
    "/register",
    "/search",
    "?",
    "*.css",
    "*.js",
    "*.json",
    "*.xml",
    ".pdf$",
    ".doc",
    ".xls",
    "/feed",
    "/rss",
    "/tag/",
    "/tags/",
    "/print",
    "/draft",
)


_TRACKING_PARAMS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "yclid",
    "gclid",
    "fbclid",
    "from",
    "ref",
)

_DUP_PARAMS = ("sort", "order", "page", "filter")


def _looks_like_admin_pattern(pattern: str) -> bool:
    p = pattern.lower()
    for marker in _ADMIN_MARKERS:
        if marker in p:
            return True
    return False


def _extract_query_params(url: str) -> list[str]:
    if "?" not in url:
        return []
    query = url.split("?", 1)[1]
    if "#" in query:
        query = query.split("#", 1)[0]
    keys: list[str] = []
    for part in query.split("&"):
        if "=" in part:
            k = part.split("=", 1)[0]
        else:
            k = part
        if k:
            keys.append(k)
    return keys


def _clean_param_keys(clean_params: list[str]) -> set[str]:
    """Parse `Clean-param:` raw values into a set of covered param names.

    The directive syntax is `Clean-param: p1&p2&... [/path-prefix]`. We
    only care about the parameter names for coverage purposes.
    """
    out: set[str] = set()
    for raw in clean_params:
        body = raw.split(None, 1)[0] if raw else ""
        for name in body.split("&"):
            name = name.strip()
            if name:
                out.add(name.lower())
    return out


def audit_yandex_robots(
    robots_txt: str | None,
    robots_url: str,
    http_status: int | None,
    important_urls: list[str],
    observed_urls: list[str],
) -> YandexRobotsAuditResult:
    """Run the deterministic Yandex robots.txt audit.

    Parameters
    ----------
    robots_txt:
        Raw text of the robots.txt file, or None if unreachable.
    robots_url:
        URL of the robots.txt (echoed back in the result).
    http_status:
        HTTP status code from the fetch, or None on connection failure.
    important_urls:
        URLs the owner cares about being crawlable. Each gets a
        `url_checks` entry; blocked ones also surface a critical issue.
    observed_urls:
        URLs collected from logs/sitemap that the audit may inspect for
        query-string patterns (Clean-param coverage).
    """
    result = YandexRobotsAuditResult(
        robots_url=robots_url,
        http_status=http_status,
    )

    size_bytes = len(robots_txt.encode("utf-8")) if robots_txt else 0
    result.size_bytes = size_bytes

    # ---- robots.unavailable -------------------------------------------------
    unavailable = (
        http_status is None
        or (http_status is not None and http_status >= 400)
        or robots_txt is None
        or robots_txt.strip() == ""
    )
    if unavailable:
        if http_status is None:
            status_str = "no response"
        else:
            status_str = f"HTTP {http_status}"
        result.is_accessible = False
        result.valid_for_yandex = False
        result.issues.append(
            YandexRobotsIssue(
                code="robots.unavailable",
                severity="critical",
                message_ru="Файл robots.txt недоступен — Яндекс не получит правил обхода.",
                evidence=f"{robots_url} → {status_str}",
                fix_ru="Опубликуйте корректный robots.txt по адресу /robots.txt с HTTP 200.",
            )
        )
        _finalize_summary(result)
        return result

    result.is_accessible = True

    # ---- robots.too_large ---------------------------------------------------
    if size_bytes > _MAX_BYTES:
        result.issues.append(
            YandexRobotsIssue(
                code="robots.too_large",
                severity="warning",
                message_ru="Размер robots.txt превышает лимит Яндекса (500 КБ).",
                evidence=f"{size_bytes} bytes",
                fix_ru="Сократите файл до 500 КБ: уберите дубли и слишком длинные списки.",
            )
        )

    parsed = parse_robots(robots_txt or "")

    # Did any Yandex-relevant group parse?
    group_keys_lower = {k.lower(): k for k in parsed["groups"]}
    yandex_groups = [
        orig for low, orig in group_keys_lower.items()
        if low == "yandex" or low.startswith("yandex")
    ]
    star_group = group_keys_lower.get("*")

    result.matched_groups = sorted(set(yandex_groups + ([star_group] if star_group else [])))
    result.valid_for_yandex = bool(yandex_groups) or bool(star_group)

    # ---- robots.cyrillic_found ---------------------------------------------
    if parsed["has_non_ascii"]:
        result.issues.append(
            YandexRobotsIssue(
                code="robots.cyrillic_found",
                severity="warning",
                message_ru="В robots.txt найдены не-ASCII символы (кириллица). Яндекс ожидает punycode/percent-encoding.",
                evidence=parsed["non_ascii_evidence"][:200],
                fix_ru="Перекодируйте кириллические домены в punycode, а пути — в percent-encoding (%xx).",
            )
        )

    result.sitemaps = list(parsed["sitemaps"])
    result.clean_params = list(parsed["clean_params"])

    # ---- robots.no_sitemap --------------------------------------------------
    if not parsed["sitemaps"]:
        result.issues.append(
            YandexRobotsIssue(
                code="robots.no_sitemap",
                severity="warning",
                message_ru="В robots.txt нет директивы Sitemap.",
                evidence="no Sitemap: directive",
                fix_ru="Добавьте строку Sitemap: https://example.com/sitemap.xml.",
            )
        )

    # ---- robots.root_disallowed_yandex -------------------------------------
    governing_ua, governing_directives = select_group_for_yandex(
        parsed["groups"], preferred_ua="Yandex"
    )
    if governing_directives:
        root_disallowed = False
        root_evidence = ""
        for d in governing_directives:
            if d["name"] == "disallow" and d["value"] == "/":
                root_disallowed = True
                root_evidence = d["raw"]
                break
        if root_disallowed:
            # Check whether some Allow overrides it for /.
            allowed_root, _, matched = match_url(parsed, "/", preferred_ua="Yandex")
            if not allowed_root:
                result.issues.append(
                    YandexRobotsIssue(
                        code="robots.root_disallowed_yandex",
                        severity="critical",
                        message_ru=(
                            f"Группа User-agent: {governing_ua} закрывает весь сайт "
                            "(Disallow: /). Яндекс не будет обходить страницы."
                        ),
                        evidence=root_evidence,
                        fix_ru="Уберите Disallow: / или добавьте Allow: для нужных разделов.",
                    )
                )

    # ---- URL checks + robots.important_url_blocked --------------------------
    blocked_important: list[str] = []
    for url in important_urls:
        allowed, matched_ua, matched_rule = match_url(parsed, url, preferred_ua="Yandex")
        path = _path_from_url(url)
        if allowed:
            risk = "ok"
            explanation = (
                f"Разрешено (правило: {matched_rule})"
                if matched_rule
                else "Разрешено (нет блокирующих правил)"
            )
        else:
            risk = "blocked"
            explanation = f"Заблокировано правилом: {matched_rule}"
            blocked_important.append(url)
        result.url_checks.append(
            YandexRobotsUrlCheck(
                url=url,
                path=path,
                user_agent="Yandex",
                allowed=allowed,
                matched_user_agent=matched_ua,
                matched_rule=matched_rule,
                risk=risk,
                explanation_ru=explanation,
            )
        )

    if blocked_important:
        result.issues.append(
            YandexRobotsIssue(
                code="robots.important_url_blocked",
                severity="critical",
                message_ru="Важные URL заблокированы для Яндекса.",
                evidence="; ".join(blocked_important[:5]),
                fix_ru="Снимите блокировку этих URL: добавьте Allow или удалите соответствующий Disallow.",
            )
        )

    # ---- robots.disallow_used_for_noindex ----------------------------------
    if governing_directives:
        for d in governing_directives:
            if d["name"] != "disallow":
                continue
            value = d["value"]
            if not value or value == "/":
                continue
            if not _looks_like_admin_pattern(value):
                result.issues.append(
                    YandexRobotsIssue(
                        code="robots.disallow_used_for_noindex",
                        severity="info",
                        message_ru=(
                            "Disallow закрывает обычный контентный URL — это запрещает обход, "
                            "но не индексацию. Для «не индексировать» Яндекс предпочитает meta noindex."
                        ),
                        evidence=d["raw"],
                        fix_ru=(
                            "Если цель — убрать страницу из выдачи, оставьте её доступной для "
                            "робота и добавьте <meta name=\"robots\" content=\"noindex\"> в HTML."
                        ),
                    )
                )

    # ---- robots.clean_param_(missing|present) ------------------------------
    observed_params: set[str] = set()
    for url in observed_urls:
        for p in _extract_query_params(url):
            observed_params.add(p.lower())

    covered = _clean_param_keys(parsed["clean_params"])

    needs_cleaning = {
        p for p in observed_params
        if p in _TRACKING_PARAMS or p in _DUP_PARAMS
    }
    uncovered = needs_cleaning - covered

    if parsed["clean_params"]:
        result.issues.append(
            YandexRobotsIssue(
                code="robots.clean_param_present",
                severity="info",
                message_ru="Настроена директива Clean-param — Яндекс корректно склеит дубли.",
                evidence="; ".join(parsed["clean_params"][:3]),
                fix_ru="Продолжайте поддерживать Clean-param при появлении новых параметров.",
            )
        )

    if uncovered:
        result.issues.append(
            YandexRobotsIssue(
                code="robots.clean_param_missing",
                severity="warning",
                message_ru=(
                    "В URL встречаются параметры (utm_*, ref, sort, page), но Clean-param "
                    "их не покрывает — Яндекс может посчитать страницы дублями."
                ),
                evidence="; ".join(sorted(uncovered)[:5]),
                fix_ru="Добавьте в robots.txt директиву Clean-param: utm_source&utm_medium&ref&page.",
            )
        )

    _finalize_summary(result)
    return result


def _finalize_summary(result: YandexRobotsAuditResult) -> None:
    """Build summary_ru and recommendations_ru deterministically.

    No paraphrasing — fixed Russian strings concatenated from the issue
    codes. This keeps the output stable for tests and snapshots.
    """
    critical = [i for i in result.issues if i.severity == "critical"]
    warning = [i for i in result.issues if i.severity == "warning"]

    if not critical and not warning:
        result.summary_ru = "Файл robots.txt в порядке для Яндекса."
    elif critical and warning:
        crit_codes = ", ".join(i.code for i in critical)
        warn_codes = ", ".join(i.code for i in warning)
        result.summary_ru = (
            f"Найдены критические проблемы: {crit_codes}. Исправьте их в первую очередь. "
            f"Также есть предупреждения: {warn_codes}."
        )
    elif critical:
        crit_codes = ", ".join(i.code for i in critical)
        result.summary_ru = (
            f"Найдены критические проблемы: {crit_codes}. Исправьте их в первую очередь."
        )
    else:
        warn_codes = ", ".join(i.code for i in warning)
        result.summary_ru = f"Есть предупреждения: {warn_codes}."

    recs: list[str] = []
    for issue in result.issues:
        if issue.severity == "info":
            continue
        recs.append(f"[{issue.severity}] {issue.code}: {issue.fix_ru}")
    result.recommendations_ru = recs
