"""Section 6 — Technical SEO Snapshot.

MVP technical audit:
- robots.txt and sitemap.xml availability
- redirects, canonical, noindex
- broken internal links
- duplicate title/H1/meta description
- schema.org type inventory
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import re
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.report.dto import TechnicalIssue, TechnicalSection
from app.fingerprint.models import PageFingerprint
from app.models.page import Page
from app.models.site import Site


STALE_DAYS = 30
FETCH_TIMEOUT = 6.0
HTTP_OK_MIN = 200
HTTP_ERROR_MIN = 400
HTML_SNIFF_CHARS = 500
MAX_FETCH_BODY = 1_000_000
MAX_EXAMPLES = 10
MAX_SITEMAP_CANDIDATES = 3
MAX_SCORE = 100
_SITEMAP_LOC_RE = re.compile(r"<loc>([^<]+)</loc>", re.IGNORECASE)
ISSUE_COPY_RU = {
    "robots_returns_html": (
        "robots.txt отдаёт HTML",
        "Поисковик ждёт обычный текстовый robots.txt, а получает страницу сайта.",
    ),
    "robots_unavailable": (
        "robots.txt недоступен или некорректен",
        "Это не всегда блокирует индексацию, но ломает понятные правила обхода.",
    ),
    "robots_disallow_all": (
        "robots.txt закрывает весь сайт",
        "Правило Disallow: / запрещает поисковику обходить сайт.",
    ),
    "sitemap_returns_html": (
        "sitemap.xml отдаёт HTML",
        "Поисковик ждёт XML-карту сайта, а получает обычную HTML-страницу.",
    ),
    "sitemap_invalid": (
        "Нет валидного sitemap.xml",
        "Без sitemap поисковику сложнее быстро найти все важные страницы.",
    ),
    "pages_not_in_sitemap": (
        "Есть страницы не из sitemap",
        "Важные страницы должны быть в sitemap, иначе поисковик может находить их медленнее.",
    ),
    "missing_title": (
        "Есть страницы без title",
        "Title нужен поисковику как название страницы в выдаче.",
    ),
    "missing_h1": (
        "Есть страницы без H1",
        "H1 помогает понять главный смысл страницы.",
    ),
    "missing_meta_description": (
        "Есть страницы без meta description",
        "Description влияет на сниппет и кликабельность страницы.",
    ),
    "duplicate_titles": (
        "Есть дубли title",
        "Разные страницы с одинаковым title конкурируют между собой.",
    ),
    "duplicate_h1": (
        "Есть дубли H1",
        "Одинаковые H1 затрудняют различение страниц по смыслу.",
    ),
    "noindex_pages": (
        "Есть страницы с noindex",
        "Эти страницы прямо запрещены к индексации.",
    ),
    "canonical_external": (
        "Canonical указывает на другой домен",
        "Так страница может передавать основной сигнал не себе, а внешнему сайту.",
    ),
    "canonical_mismatch": (
        "Canonical ведёт на другой URL",
        "Проверь, это намеренная склейка или ошибка шаблона.",
    ),
    "canonical_missing": (
        "Нет canonical на части страниц",
        "Это не всегда критично, но canonical помогает избежать дублей.",
    ),
    "redirect_chains": (
        "Есть цепочки редиректов",
        "Цепочки замедляют обход и могут съедать краулинговый бюджет.",
    ),
    "broken_internal_links": (
        "Есть битые внутренние ссылки",
        "Пользователь и поисковик переходят внутри сайта на ошибочные страницы.",
    ),
    "non_200_pages": (
        "Есть страницы с HTTP-ошибками",
        "Страницы с 4xx/5xx не должны быть в sitemap и важных внутренних ссылках.",
    ),
    "schema_missing_all": (
        "Schema.org не найдена",
        "Разметка помогает поисковику понять услуги, отзывы, FAQ и организацию.",
    ),
}


def _normalise_url(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower().removeprefix('www.')}{path}"


def _host(url: str | None) -> str:
    if not url:
        return ""
    return urlparse(url).netloc.lower().removeprefix("www.")


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").lower().split())


def _issue(
    code: str,
    severity: str,
    count: int,
    examples: list[str] | None = None,
) -> TechnicalIssue:
    title_ru, detail_ru = ISSUE_COPY_RU[code]
    return TechnicalIssue(
        code=code,
        severity=severity,
        title_ru=title_ru,
        detail_ru=detail_ru,
        count=count,
        examples=(examples or [])[:MAX_EXAMPLES],
    )


async def _fetch_text(client: httpx.AsyncClient, url: str) -> dict:
    try:
        r = await client.get(url, follow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "ok": False, "error": str(exc)}
    return {
        "url": url,
        "ok": HTTP_OK_MIN <= r.status_code < HTTP_ERROR_MIN,
        "status": r.status_code,
        "content_type": r.headers.get("content-type", ""),
        "body": r.text[:MAX_FETCH_BODY],
        "final_url": str(r.url),
        "redirect_count": len(r.history),
    }


def _parse_robots(payload: dict) -> dict:
    body = payload.get("body") or ""
    content_type = (payload.get("content_type") or "").lower()
    is_html = "<html" in body[:HTML_SNIFF_CHARS].lower() or "text/html" in content_type
    lines = [line.strip() for line in body.splitlines()]
    disallow_all = any(line.lower().replace(" ", "") == "disallow:/" for line in lines)
    sitemap_urls = [
        line.split(":", 1)[1].strip()
        for line in lines
        if line.lower().startswith("sitemap:") and ":" in line
    ]
    return {
        "url": payload.get("url"),
        "ok": bool(payload.get("ok")) and not is_html,
        "status": payload.get("status"),
        "error": payload.get("error"),
        "returns_html": is_html,
        "disallow_all": disallow_all,
        "sitemap_urls": sitemap_urls,
        "redirect_count": payload.get("redirect_count", 0),
    }


def _parse_sitemap(payload: dict) -> dict:
    body = payload.get("body") or ""
    content_type = (payload.get("content_type") or "").lower()
    is_html = "<html" in body[:HTML_SNIFF_CHARS].lower() or "text/html" in content_type
    locs = _SITEMAP_LOC_RE.findall(body) if not is_html else []
    return {
        "url": payload.get("url"),
        "ok": bool(payload.get("ok")) and not is_html and bool(locs),
        "status": payload.get("status"),
        "error": payload.get("error"),
        "returns_html": is_html,
        "valid_xml": bool(locs) and not is_html,
        "urls_declared": len(locs),
        "sample_urls": locs[:MAX_EXAMPLES],
        "redirect_count": payload.get("redirect_count", 0),
    }


async def fetch_robots_and_sitemap(domain: str) -> tuple[dict, dict]:
    base = f"https://{domain.strip().removeprefix('https://').removeprefix('http://').strip('/')}"
    headers = {"User-Agent": "GrowthTower TechnicalAudit/1.0"}
    async with httpx.AsyncClient(headers=headers, timeout=FETCH_TIMEOUT) as client:
        robots_payload = await _fetch_text(client, f"{base}/robots.txt")
        robots = _parse_robots(robots_payload)

        sitemap_candidates = robots.get("sitemap_urls") or [
            f"{base}/sitemap.xml",
            f"{base}/sitemap_index.xml",
        ]
        sitemap = {}
        for url in sitemap_candidates[:MAX_SITEMAP_CANDIDATES]:
            payload = await _fetch_text(client, url)
            sitemap = _parse_sitemap(payload)
            if sitemap.get("ok"):
                break
        return robots, sitemap


def _fresh_buckets() -> dict[str, list[str]]:
    return {
        "missing_title": [],
        "missing_h1": [],
        "missing_meta": [],
        "noindex_pages": [],
        "canonical_missing": [],
        "canonical_external": [],
        "canonical_mismatch": [],
        "redirect_pages": [],
        "redirect_chains": [],
        "broken_links": [],
        "uncrawled_internal_links": [],
        "non_200_pages": [],
    }


def _fresh_state() -> dict:
    return {
        "buckets": _fresh_buckets(),
        "titles": defaultdict(list),
        "h1s": defaultdict(list),
        "metas": defaultdict(list),
        "schema_counter": Counter(),
    }


def _is_http_error(status: int | None) -> bool:
    return int(status or 0) >= HTTP_ERROR_MIN


def _record_text_signals(
    page: Page,
    state: dict,
) -> None:
    fields = (
        (_clean_text(page.title), state["titles"], "missing_title"),
        (_clean_text(page.h1), state["h1s"], "missing_h1"),
        (_clean_text(page.meta_description), state["metas"], "missing_meta"),
    )
    for value, groups, missing_key in fields:
        if value:
            groups[value].append(page.url)
        else:
            state["buckets"][missing_key].append(page.url)


def _record_canonical_signal(page: Page, buckets: dict[str, list[str]]) -> None:
    canonical = (page.meta or {}).get("canonical_url")
    if not canonical:
        buckets["canonical_missing"].append(page.url)
        return
    if _host(canonical) and _host(canonical) != _host(page.url):
        buckets["canonical_external"].append(f"{page.url} -> {canonical}")
        return
    if _normalise_url(canonical) != _normalise_url(page.url):
        buckets["canonical_mismatch"].append(f"{page.url} -> {canonical}")


def _record_redirect_signal(page: Page, buckets: dict[str, list[str]]) -> None:
    redirect_count = int((page.meta or {}).get("redirect_count") or 0)
    if not redirect_count:
        return
    buckets["redirect_pages"].append(page.url)
    if redirect_count > 1:
        buckets["redirect_chains"].append(page.url)


def _record_link_signals(
    page: Page,
    urls_by_norm: dict[str, Page],
    buckets: dict[str, list[str]],
) -> None:
    for link in page.internal_links or []:
        norm = _normalise_url(str(link))
        if not norm:
            continue
        target = urls_by_norm.get(norm)
        if target is None:
            buckets["uncrawled_internal_links"].append(f"{page.url} -> {link}")
        elif _is_http_error(target.http_status):
            buckets["broken_links"].append(f"{page.url} -> {link}")


def _record_schema_signals(page: Page, state: dict) -> None:
    for schema_type in (page.meta or {}).get("schema_types") or []:
        if isinstance(schema_type, str) and schema_type:
            state["schema_counter"][schema_type] += 1


def _duplicate_groups(groups: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: urls for key, urls in groups.items() if len(urls) > 1}


def _duplicate_page_count(groups: dict[str, list[str]]) -> int:
    return sum(len(urls) - 1 for urls in groups.values())


def _examples_from_groups(groups: dict[str, list[str]]) -> list[str]:
    return [urls[0] for urls in groups.values()]


def _build_page_checks(
    pages: list[Page],
    state: dict,
    duplicate_titles: dict[str, list[str]],
    duplicate_h1: dict[str, list[str]],
    duplicate_meta: dict[str, list[str]],
) -> dict:
    buckets = state["buckets"]
    schema_counter = state["schema_counter"]
    return {
        "missing_title": len(buckets["missing_title"]),
        "missing_h1": len(buckets["missing_h1"]),
        "missing_meta_description": len(buckets["missing_meta"]),
        "duplicate_titles": _duplicate_page_count(duplicate_titles),
        "duplicate_h1": _duplicate_page_count(duplicate_h1),
        "duplicate_meta_description": _duplicate_page_count(duplicate_meta),
        "noindex_pages": len(buckets["noindex_pages"]),
        "canonical_missing": len(buckets["canonical_missing"]),
        "canonical_external": len(buckets["canonical_external"]),
        "canonical_mismatch": len(buckets["canonical_mismatch"]),
        "redirect_pages": len(buckets["redirect_pages"]),
        "redirect_chains": len(buckets["redirect_chains"]),
        "broken_internal_links": len(buckets["broken_links"]),
        "uncrawled_internal_links": len(buckets["uncrawled_internal_links"]),
        "schema_pages": sum(1 for p in pages if p.has_schema),
        "schema_types_found": sum(schema_counter.values()),
        "pages_non_200": len(buckets["non_200_pages"]),
    }


def _append_if_any(
    issues: list[TechnicalIssue],
    code: str,
    severity: str,
    examples: list[str],
    count: int | None = None,
) -> None:
    if examples:
        issues.append(_issue(code, severity, count or len(examples), examples))


def _build_page_issues(
    pages: list[Page],
    state: dict,
    checks: dict,
    duplicate_titles: dict[str, list[str]],
    duplicate_h1: dict[str, list[str]],
) -> list[TechnicalIssue]:
    buckets = state["buckets"]
    issues: list[TechnicalIssue] = []
    _append_if_any(issues, "missing_title", "high", buckets["missing_title"])
    _append_if_any(issues, "missing_h1", "medium", buckets["missing_h1"])
    _append_if_any(issues, "missing_meta_description", "medium", buckets["missing_meta"])
    _append_if_any(
        issues, "duplicate_titles", "medium",
        _examples_from_groups(duplicate_titles), checks["duplicate_titles"],
    )
    _append_if_any(
        issues, "duplicate_h1", "low",
        _examples_from_groups(duplicate_h1), checks["duplicate_h1"],
    )
    _append_if_any(issues, "noindex_pages", "critical", buckets["noindex_pages"])
    _append_if_any(issues, "canonical_external", "high", buckets["canonical_external"])
    _append_if_any(issues, "canonical_mismatch", "medium", buckets["canonical_mismatch"])
    _append_if_any(issues, "canonical_missing", "low", buckets["canonical_missing"])
    _append_if_any(issues, "redirect_chains", "medium", buckets["redirect_chains"])
    _append_if_any(issues, "broken_internal_links", "high", buckets["broken_links"])
    _append_if_any(issues, "non_200_pages", "high", buckets["non_200_pages"])
    if not state["schema_counter"] and pages:
        issues.append(_issue(
            "schema_missing_all", "medium",
            len(pages), [p.url for p in pages[:10]],
        ))
    return issues


def build_page_technical_checks(
    pages: list[Page],
) -> tuple[dict, dict[str, int], list[TechnicalIssue]]:
    urls_by_norm = {_normalise_url(p.url): p for p in pages}
    state = _fresh_state()

    for page in pages:
        if _is_http_error(page.http_status):
            state["buckets"]["non_200_pages"].append(page.url)
        if (page.meta or {}).get("noindex"):
            state["buckets"]["noindex_pages"].append(page.url)
        _record_text_signals(page, state)
        _record_canonical_signal(page, state["buckets"])
        _record_redirect_signal(page, state["buckets"])
        _record_schema_signals(page, state)
        _record_link_signals(page, urls_by_norm, state["buckets"])

    duplicate_titles = _duplicate_groups(state["titles"])
    duplicate_h1 = _duplicate_groups(state["h1s"])
    duplicate_meta = _duplicate_groups(state["metas"])
    checks = _build_page_checks(
        pages, state, duplicate_titles, duplicate_h1, duplicate_meta,
    )
    issues = _build_page_issues(
        pages, state, checks, duplicate_titles, duplicate_h1,
    )
    return checks, dict(state["schema_counter"]), issues


def build_external_technical_issues(
    robots: dict,
    sitemap: dict,
    pages_not_in_sitemap: list[str],
) -> list[TechnicalIssue]:
    issues: list[TechnicalIssue] = []
    if robots:
        if robots.get("returns_html"):
            issues.append(_issue("robots_returns_html", "high", 1, [str(robots.get("url"))]))
        elif robots.get("error") or not robots.get("ok"):
            issues.append(_issue("robots_unavailable", "medium", 1, [str(robots.get("url"))]))
        if robots.get("disallow_all"):
            issues.append(_issue("robots_disallow_all", "critical", 1, [str(robots.get("url"))]))

    if sitemap:
        if sitemap.get("returns_html"):
            issues.append(_issue("sitemap_returns_html", "high", 1, [str(sitemap.get("url"))]))
        elif not sitemap.get("valid_xml"):
            issues.append(_issue("sitemap_invalid", "high", 1, [str(sitemap.get("url"))]))

    if pages_not_in_sitemap:
        issues.append(_issue(
            "pages_not_in_sitemap", "medium",
            len(pages_not_in_sitemap), pages_not_in_sitemap,
        ))
    return issues


def technical_score(issues: list[TechnicalIssue]) -> int:
    penalties = {"critical": 25, "high": 12, "medium": 6, "low": 2}
    total = 0
    for issue in issues:
        total += min(penalties.get(issue.severity, 3) * max(issue.count, 1), 35)
    return max(0, MAX_SCORE - min(total, MAX_SCORE))


async def _load_pages(db: AsyncSession, site_id: UUID) -> list[Page]:
    page_rows = await db.execute(select(Page).where(Page.site_id == site_id))
    return list(page_rows.scalars())


async def _load_duplicate_fingerprints_count(db: AsyncSession, site_id: UUID) -> int:
    dup_row = await db.execute(
        select(PageFingerprint.content_hash, func.count().label("c"))
        .where(
            PageFingerprint.site_id == site_id,
            PageFingerprint.status == "fingerprinted",
        )
        .group_by(PageFingerprint.content_hash)
        .having(func.count() > 1)
    )
    return sum(int(c) - 1 for _, c in dup_row)


async def _load_stale_fingerprints_count(db: AsyncSession, site_id: UUID) -> int:
    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)
    stale_row = await db.execute(
        select(func.count())
        .select_from(PageFingerprint)
        .where(
            PageFingerprint.site_id == site_id,
            PageFingerprint.last_fingerprinted_at < stale_cutoff,
        )
    )
    return int(stale_row.scalar() or 0)


async def _fetch_external_audit(db: AsyncSession, site_id: UUID) -> tuple[dict, dict]:
    site = await db.get(Site, site_id)
    if site is None or not site.domain:
        return {}, {}
    return await fetch_robots_and_sitemap(site.domain)


def _pages_not_in_sitemap(pages: list[Page]) -> list[str]:
    return [
        p.url for p in pages
        if p.http_status is not None and not _is_http_error(p.http_status) and not p.in_sitemap
    ]


def _with_sitemap_page_stats(
    sitemap: dict, pages: list[Page], pages_not_in_sitemap: list[str],
) -> dict:
    if not sitemap:
        return sitemap
    return {
        **sitemap,
        "pages_in_sitemap": sum(1 for p in pages if p.in_sitemap),
        "pages_not_in_sitemap": len(pages_not_in_sitemap),
    }


def _technical_warning(pages_total: int) -> str | None:
    if pages_total == 0:
        return "Краулер не нашёл ни одной страницы. Запустите обход сайта."
    return None


async def build_technical(
    db: AsyncSession, site_id: UUID, week_end: date,
) -> TechnicalSection:
    pages = await _load_pages(db, site_id)
    pages_total = len(pages)
    pages_indexed = sum(1 for p in pages if p.in_index)
    pages_non_200 = sum(1 for p in pages if p.http_status is not None and _is_http_error(p.http_status))
    duplicates_suspected = await _load_duplicate_fingerprints_count(db, site_id)
    stale_count = await _load_stale_fingerprints_count(db, site_id)
    robots, sitemap = await _fetch_external_audit(db, site_id)
    pages_not_in_sitemap = _pages_not_in_sitemap(pages)
    sitemap = _with_sitemap_page_stats(sitemap, pages, pages_not_in_sitemap)

    checks, schema_types, page_issues = build_page_technical_checks(pages)
    issues = [
        *build_external_technical_issues(robots, sitemap, pages_not_in_sitemap),
        *page_issues,
    ]

    indexation_rate = (pages_indexed / pages_total) if pages_total else 0.0

    return TechnicalSection(
        pages_total=pages_total,
        pages_indexed=pages_indexed,
        pages_non_200=pages_non_200,
        indexation_rate=round(indexation_rate, 3),
        duplicates_suspected=duplicates_suspected,
        fingerprint_stale_count=stale_count,
        technical_score=technical_score(issues),
        robots=robots,
        sitemap=sitemap,
        checks=checks,
        schema_types=schema_types,
        issues=issues,
        warning_ru=_technical_warning(pages_total),
    )
