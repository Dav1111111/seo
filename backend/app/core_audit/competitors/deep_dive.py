"""Competitor deep-dive — crawl a handful of top competitor pages and
extract structural signals we can compare against our own site.

Signals extracted per page:
  - title, h1, meta_description
  - has_price (any pattern like '1500 ₽', 'от 1500 р.', '$1500')
  - has_booking_cta (keywords: бронь, заказать, book, request)
  - has_reviews (keywords: отзывы, rating, stars schema)
  - schema_types (set of @type from JSON-LD blocks)
  - word_count (plain text, rough)
  - phone, telegram, whatsapp presence (contact surface)

Output:
  CompetitorPageReport — per page
  CompetitorSiteReport — aggregated per domain
  DeepDiveResult — top-level for a site: list of reports + summary
    diff against our own site.

Design
------
- No LLM. Pure HTML parsing + regex — deterministic, free, fast.
- Fail-open: any fetch/parse error → status='error', continue to next.
- Target ≤ 3 seconds per page, ≤ 30 seconds per run for 5 competitors
  × 2 pages each.
- Uses stdlib urllib only — zero new deps.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Sequence
from urllib.parse import urljoin


log = logging.getLogger(__name__)


REQUEST_TIMEOUT = 5.0
MAX_HTML_BYTES = 2_000_000   # 2 MB hard cap
USER_AGENT = (
    "Mozilla/5.0 (compatible; YandexGrowthTowerCompetitorAudit/1.0; "
    "+https://grandtourspirit.ru)"
)

# Regex — deliberately broad, loose matches OK (false positives cheap).
PRICE_RE = re.compile(
    r"(?:\bот\s+)?\d[\d\s]{2,}\s*(?:₽|руб\.?|р\.|rub|\$|€|eur)",
    re.IGNORECASE,
)
BOOKING_CTA_RE = re.compile(
    r"\b(забронировать|бронь|оставить\s+заявк|заказать|"
    r"book(?:ing)?|request|reserve|request\s+a\s+quote)\b",
    re.IGNORECASE,
)
REVIEWS_RE = re.compile(
    r"\b(отзыв|рейтинг|звёзд|звезд|rating|review|stars?)\b",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"(?:\+7|8)[\s\-\(\)]*\d{3}[\s\-\(\)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")
TELEGRAM_RE = re.compile(r"(?:t\.me/|telegram\.me/|@\w+)", re.IGNORECASE)
WHATSAPP_RE = re.compile(r"(?:wa\.me|whatsapp\.com|api\.whatsapp)", re.IGNORECASE)

SCHEMA_JSONLD_RE = re.compile(
    r'<script\s+[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
SCHEMA_TYPE_RE = re.compile(r'"@type"\s*:\s*"([^"]+)"')


@dataclasses.dataclass(frozen=True)
class CompetitorPageReport:
    url: str
    status: str                 # 'ok' | 'error'
    error: str | None = None
    title: str = ""
    h1: str = ""
    meta_description: str = ""
    word_count: int = 0
    has_price: bool = False
    has_booking_cta: bool = False
    has_reviews: bool = False
    has_phone: bool = False
    has_telegram: bool = False
    has_whatsapp: bool = False
    schema_types: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["schema_types"] = list(d["schema_types"])
        return d


@dataclasses.dataclass
class CompetitorSiteReport:
    domain: str
    pages: list[CompetitorPageReport] = dataclasses.field(default_factory=list)

    # aggregated flags — any page of the domain has it
    has_price: bool = False
    has_booking_cta: bool = False
    has_reviews: bool = False
    has_phone: bool = False
    has_telegram: bool = False
    has_whatsapp: bool = False
    schema_types: list[str] = dataclasses.field(default_factory=list)

    def aggregate(self) -> None:
        schemas: set[str] = set()
        for p in self.pages:
            if p.status != "ok":
                continue
            self.has_price = self.has_price or p.has_price
            self.has_booking_cta = self.has_booking_cta or p.has_booking_cta
            self.has_reviews = self.has_reviews or p.has_reviews
            self.has_phone = self.has_phone or p.has_phone
            self.has_telegram = self.has_telegram or p.has_telegram
            self.has_whatsapp = self.has_whatsapp or p.has_whatsapp
            schemas.update(p.schema_types)
        self.schema_types = sorted(schemas)

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "pages": [p.to_dict() for p in self.pages],
            "has_price": self.has_price,
            "has_booking_cta": self.has_booking_cta,
            "has_reviews": self.has_reviews,
            "has_phone": self.has_phone,
            "has_telegram": self.has_telegram,
            "has_whatsapp": self.has_whatsapp,
            "schema_types": self.schema_types,
        }


# ── HTML parsing ─────────────────────────────────────────────────────

class _HTMLExtractor(HTMLParser):
    """Pulls out title, first H1, meta description and plain text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.h1 = ""
        self.meta_description = ""
        self._stack: list[str] = []
        self._text_parts: list[str] = []
        self._in_title = False
        self._in_h1 = False

    def handle_starttag(self, tag, attrs):
        self._stack.append(tag)
        if tag == "title":
            self._in_title = True
        elif tag == "h1" and not self.h1:
            self._in_h1 = True
        elif tag == "meta":
            a = dict(attrs)
            if (a.get("name") or "").lower() == "description":
                self.meta_description = (a.get("content") or "").strip()[:500]

    def handle_endtag(self, tag):
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_h1:
            self.h1 += data
        # skip script/style/noscript for plain text
        if self._stack and self._stack[-1] in ("script", "style", "noscript"):
            return
        self._text_parts.append(data)

    @property
    def plain_text(self) -> str:
        return " ".join("".join(self._text_parts).split())


def _fetch_html(url: str) -> tuple[str | None, str | None]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read(MAX_HTML_BYTES)
    except urllib.error.HTTPError as exc:
        return None, f"http_{exc.code}"
    except urllib.error.URLError as exc:
        return None, f"url_error:{exc.reason}"
    except Exception as exc:  # noqa: BLE001
        log.info("deep_dive.fetch_failed url=%s err=%s", url, exc)
        return None, "fetch_exception"
    # best-effort decode
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1251"), None
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace"), None


def _extract_schema_types(html: str) -> list[str]:
    types: list[str] = []
    for match in SCHEMA_JSONLD_RE.finditer(html):
        block = match.group(1)
        for t in SCHEMA_TYPE_RE.finditer(block):
            v = t.group(1).strip()
            if v and v not in types:
                types.append(v)
    return types


def analyze_page(url: str) -> CompetitorPageReport:
    html, err = _fetch_html(url)
    if html is None:
        return CompetitorPageReport(url=url, status="error", error=err or "unknown")

    extractor = _HTMLExtractor()
    try:
        extractor.feed(html)
    except Exception as exc:  # noqa: BLE001
        return CompetitorPageReport(url=url, status="error", error=f"parse:{exc}")

    text = extractor.plain_text
    return CompetitorPageReport(
        url=url,
        status="ok",
        title=extractor.title.strip()[:400],
        h1=extractor.h1.strip()[:400],
        meta_description=extractor.meta_description[:400],
        word_count=len(text.split()),
        has_price=bool(PRICE_RE.search(text)),
        has_booking_cta=bool(BOOKING_CTA_RE.search(text)),
        has_reviews=bool(REVIEWS_RE.search(text)),
        has_phone=bool(PHONE_RE.search(text)),
        has_telegram=bool(TELEGRAM_RE.search(text)),
        has_whatsapp=bool(WHATSAPP_RE.search(text)),
        schema_types=tuple(_extract_schema_types(html)),
    )


def analyze_competitor_site(
    domain: str, urls: Sequence[str], max_pages: int = 2,
) -> CompetitorSiteReport:
    """Analyze up to `max_pages` URLs for a competitor. `urls` is pre-ranked."""
    rep = CompetitorSiteReport(domain=domain)
    seen: set[str] = set()
    # always include the homepage first if not already in urls
    ordered: list[str] = []
    home = f"https://{domain.removeprefix('www.')}/"
    ordered.append(home)
    for u in urls:
        if u and u not in seen and u != home:
            ordered.append(u)
            seen.add(u)
    for u in ordered[:max_pages]:
        rep.pages.append(analyze_page(u))
    rep.aggregate()
    return rep


__all__ = [
    "CompetitorPageReport",
    "CompetitorSiteReport",
    "analyze_page",
    "analyze_competitor_site",
]
