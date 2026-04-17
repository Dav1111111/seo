"""
Site Crawler — fetches pages from user's website, extracts SEO-relevant content.

Reads sitemap.xml → crawls each page → extracts:
- title, meta description, H1
- Main content text (cleaned HTML)
- Internal links
- Images count
- Schema.org presence

Stores in Page model. Runs on demand or scheduled.
"""

import asyncio
import logging
import re
from datetime import datetime
from urllib.parse import urlparse, urljoin
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.page import Page
from app.models.site import Site

logger = logging.getLogger(__name__)

# Tags to strip for clean text extraction
_STRIP_TAGS_RE = re.compile(
    r'<(script|style|noscript|iframe|svg|nav|footer|header|aside)[^>]*>.*?</\1>',
    re.DOTALL | re.IGNORECASE,
)
_HTML_TAG_RE = re.compile(r'<[^>]+>')
_WHITESPACE_RE = re.compile(r'\s+')

# Extraction patterns
_TITLE_RE = re.compile(r'<title[^>]*>([^<]+)</title>', re.IGNORECASE)
_META_DESC_RE = re.compile(
    r'<meta\s+[^>]*name=[\"\']description[\"\'][^>]*content=[\"\']([^\"\']+)[\"\']',
    re.IGNORECASE,
)
_META_DESC_RE2 = re.compile(
    r'<meta\s+[^>]*content=[\"\']([^\"\']+)[\"\'][^>]*name=[\"\']description[\"\']',
    re.IGNORECASE,
)
_H1_RE = re.compile(r'<h1[^>]*>([\s\S]*?)</h1>', re.IGNORECASE)
_LINK_RE = re.compile(r'<a\s+[^>]*href=[\"\']([^\"\']+)[\"\']', re.IGNORECASE)
_IMG_RE = re.compile(r'<img\s', re.IGNORECASE)
_SCHEMA_RE = re.compile(r'application/ld\+json', re.IGNORECASE)
_SITEMAP_LOC_RE = re.compile(r'<loc>([^<]+)</loc>', re.IGNORECASE)


def _strip_html(html: str) -> str:
    """Extract clean text from HTML — removes scripts/styles/tags, collapses whitespace."""
    no_blocks = _STRIP_TAGS_RE.sub(' ', html)
    no_tags = _HTML_TAG_RE.sub(' ', no_blocks)
    clean = _WHITESPACE_RE.sub(' ', no_tags).strip()
    return clean


def _extract_text(pattern: re.Pattern, html: str) -> str | None:
    m = pattern.search(html)
    if not m:
        return None
    return _strip_html(m.group(1)).strip() or None


def _extract_meta_description(html: str) -> str | None:
    m = _META_DESC_RE.search(html) or _META_DESC_RE2.search(html)
    return m.group(1).strip() if m else None


class SiteCrawler:
    """Crawls a single site: reads sitemap, fetches pages, extracts content."""

    def __init__(self, domain: str, base_url: str | None = None, max_pages: int = 100):
        self.domain = domain
        # base_url used for fetching; handles punycode domains
        self.base_url = base_url or f"https://{domain}"
        self.max_pages = max_pages

    async def fetch_sitemap(self, client: httpx.AsyncClient) -> list[str]:
        """Fetch sitemap.xml and return list of URLs."""
        urls: list[str] = []
        for sitemap_path in ["/sitemap.xml", "/sitemap_index.xml"]:
            try:
                r = await client.get(f"{self.base_url}{sitemap_path}", follow_redirects=True)
                if r.status_code == 200 and 'xml' in r.headers.get('content-type', ''):
                    locs = _SITEMAP_LOC_RE.findall(r.text)
                    urls.extend(locs)
                    break
            except Exception as e:
                logger.warning(f"Sitemap fetch failed at {sitemap_path}: {e}")
        return urls[:self.max_pages]

    async def fetch_page(self, client: httpx.AsyncClient, url: str) -> dict | None:
        """Fetch single page, extract SEO data. Returns None on error."""
        try:
            r = await client.get(url, follow_redirects=True, timeout=15)
        except Exception as e:
            logger.warning(f"Fetch failed {url}: {e}")
            return None

        if r.status_code >= 400:
            return {
                "url": url,
                "http_status": r.status_code,
                "title": None,
                "meta_description": None,
                "h1": None,
                "content_text": None,
                "word_count": 0,
                "internal_links": [],
                "images_count": 0,
                "has_schema": False,
            }

        html = r.text
        domain_host = urlparse(self.base_url).netloc

        # Extract SEO fields
        title = _extract_text(_TITLE_RE, html)
        meta_desc = _extract_meta_description(html)
        h1 = _extract_text(_H1_RE, html)
        content_text = _strip_html(html)

        # Truncate content to 10k chars for storage
        if len(content_text) > 10000:
            content_text = content_text[:10000]

        word_count = len(content_text.split()) if content_text else 0

        # Internal links (same domain)
        all_links = _LINK_RE.findall(html)
        internal = []
        for link in all_links:
            abs_url = urljoin(url, link)
            parsed = urlparse(abs_url)
            if parsed.netloc == domain_host and not any(
                abs_url.endswith(ext) for ext in ['.jpg', '.png', '.svg', '.pdf', '.ico']
            ):
                # Normalize: drop fragment and query
                clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                if clean != url and clean not in internal:
                    internal.append(clean)
        internal = internal[:50]  # cap

        images_count = len(_IMG_RE.findall(html))
        has_schema = bool(_SCHEMA_RE.search(html))

        return {
            "url": url,
            "http_status": r.status_code,
            "title": title,
            "meta_description": meta_desc,
            "h1": h1,
            "content_text": content_text,
            "word_count": word_count,
            "internal_links": internal,
            "images_count": images_count,
            "has_schema": has_schema,
        }

    async def crawl_and_store(self, db: AsyncSession, site_id: UUID) -> dict:
        """Main entry — crawl sitemap + each page, persist to DB."""
        stats = {"sitemap_urls": 0, "pages_crawled": 0, "pages_failed": 0}

        headers = {
            "User-Agent": "GrowthTower SEO Crawler/1.0 (+https://growthtower.ru)",
            "Accept": "text/html,application/xhtml+xml",
        }

        async with httpx.AsyncClient(headers=headers, timeout=20) as client:
            # 1. Read sitemap
            urls = await self.fetch_sitemap(client)
            stats["sitemap_urls"] = len(urls)

            if not urls:
                # Fallback: just crawl the homepage and discovered links
                logger.info("No sitemap, crawling from homepage")
                urls = [self.base_url]

            logger.info(f"Crawling {len(urls)} URLs for {self.domain}")

            # 2. Fetch pages (with some concurrency)
            sem = asyncio.Semaphore(4)  # max 4 parallel
            visited: set[str] = set()

            async def fetch_one(url: str):
                if url in visited:
                    return None
                visited.add(url)
                async with sem:
                    return await self.fetch_page(client, url)

            results = await asyncio.gather(*(fetch_one(u) for u in urls), return_exceptions=True)

            for result in results:
                if isinstance(result, Exception) or result is None:
                    stats["pages_failed"] += 1
                    continue

                url = result["url"]
                parsed = urlparse(url)
                path = parsed.path or "/"

                stmt = pg_insert(Page).values(
                    site_id=site_id,
                    url=url,
                    path=path,
                    title=result["title"],
                    meta_description=result["meta_description"],
                    h1=result["h1"],
                    content_text=result["content_text"],
                    word_count=result["word_count"],
                    internal_links=result["internal_links"],
                    images_count=result["images_count"],
                    has_schema=result["has_schema"],
                    http_status=result["http_status"],
                    last_crawled_at=datetime.utcnow(),
                    last_seen_at=datetime.utcnow(),
                    in_sitemap=True,
                ).on_conflict_do_update(
                    index_elements=["site_id", "url"],
                    set_={
                        "title": result["title"],
                        "meta_description": result["meta_description"],
                        "h1": result["h1"],
                        "content_text": result["content_text"],
                        "word_count": result["word_count"],
                        "internal_links": result["internal_links"],
                        "images_count": result["images_count"],
                        "has_schema": result["has_schema"],
                        "http_status": result["http_status"],
                        "last_crawled_at": datetime.utcnow(),
                        "last_seen_at": datetime.utcnow(),
                    },
                )
                await db.execute(stmt)
                stats["pages_crawled"] += 1

            await db.commit()
            logger.info(f"Crawl done for {self.domain}: {stats}")

        return stats
