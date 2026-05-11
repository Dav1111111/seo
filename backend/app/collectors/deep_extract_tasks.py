"""Celery tasks: run deep extract on a URL and persist to page_deep_extracts.

Two entry points by intent:
  - deep_extract_own_page_task(site_id, page_id) — looks up Page.url
  - deep_extract_competitor_url_task(site_id, url) — for any URL,
    is_competitor flag is set so UI can group competitor extracts

Both share the same extractor + persistence logic — only the metadata
(page_id, is_competitor, competitor_domain) differs.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select

from app.collectors.deep_crawler import deep_extract
from app.core_audit.activity import log_event
from app.models.page import Page
from app.models.page_deep_extract import PageDeepExtract
from app.models.site import Site
from app.workers.celery_app import celery_app
from app.workers.db_session import task_session

log = logging.getLogger(__name__)


def _domain_of(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        return host.lower().lstrip("www.")
    except Exception:  # noqa: BLE001
        return ""


async def _persist(
    *,
    site_id: UUID,
    page_id: UUID | None,
    url: str,
    is_competitor: bool,
    competitor_domain: str | None,
) -> dict:
    """Run extractor, write a row, return summary for the task return."""
    res = await deep_extract(url)
    async with task_session() as db:
        row = PageDeepExtract(
            site_id=site_id,
            page_id=page_id,
            url=url,
            is_competitor=is_competitor,
            competitor_domain=competitor_domain,
            status=res.status,
            error=res.error,
            extracted_at=datetime.now(timezone.utc),
            duration_ms=res.duration_ms,
            title=res.title,
            h1=res.h1,
            meta_description=res.meta_description,
            full_text=res.full_text,
            headings_tree=res.headings_tree or None,
            cta_inventory=res.cta_inventory or None,
            forms_inventory=res.forms_inventory or None,
            links_inventory=res.links_inventory or None,
            images_inventory=res.images_inventory or None,
            css_palette=res.css_palette or None,
            fonts=res.fonts or None,
            layout_meta=res.layout_meta or None,
            performance=res.performance or None,
            js_errors=res.js_errors or None,
            schema_blocks=res.schema_blocks or None,
            screenshot_desktop_path=res.screenshot_desktop_path,
            screenshot_mobile_path=res.screenshot_mobile_path,
        )
        db.add(row)
        await db.flush()
        extract_id = str(row.id)

        msg_what = "Конкурент" if is_competitor else "Своя страница"
        if res.status == "completed":
            cta_n = len(res.cta_inventory or [])
            forms_n = len(res.forms_inventory or [])
            imgs_n = len(res.images_inventory or [])
            stage_msg = (
                f"{msg_what}: глубокий разбор готов. "
                f"Кнопок: {cta_n}, форм: {forms_n}, картинок: {imgs_n}. "
                f"Заняло {res.duration_ms or 0} мс."
            )
        else:
            stage_msg = (
                f"{msg_what}: глубокий разбор не завершён "
                f"({res.status}: {res.error or '?'})."
            )

        await log_event(
            db, str(site_id),
            "deep_extract",
            "done" if res.status == "completed" else res.status,
            stage_msg,
            extra={"url": url, "extract_id": extract_id, "is_competitor": is_competitor},
        )
        await db.commit()
    return {
        "status": res.status,
        "extract_id": extract_id,
        "url": url,
        "duration_ms": res.duration_ms,
        "is_competitor": is_competitor,
    }


@celery_app.task(name="deep_extract_own_page", bind=True, max_retries=0)
def deep_extract_own_page_task(self, site_id: str, page_id: str) -> dict:
    """Deep-extract one of OUR pages. Resolves URL from Page row."""

    async def _run() -> dict:
        async with task_session() as db:
            page = await db.get(Page, UUID(page_id))
            if page is None:
                return {"status": "failed", "error": "page_not_found"}
            url = page.url
        # Run extractor + persist outside the read session.
        return await _persist(
            site_id=UUID(site_id),
            page_id=UUID(page_id),
            url=url,
            is_competitor=False,
            competitor_domain=None,
        )

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        log.warning("deep_extract.own_failed page=%s err=%s", page_id, exc)
        return {"status": "error", "err": str(exc)}


@celery_app.task(name="deep_extract_competitor_url", bind=True, max_retries=0)
def deep_extract_competitor_url_task(self, site_id: str, url: str) -> dict:
    """Deep-extract any URL — typically a competitor page.

    No page_id; competitor pages aren't in our pages table. The UI
    groups extracts by competitor_domain so the owner sees them under
    the right competitor.
    """

    async def _run() -> dict:
        return await _persist(
            site_id=UUID(site_id),
            page_id=None,
            url=url,
            is_competitor=True,
            competitor_domain=_domain_of(url),
        )

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        log.warning("deep_extract.competitor_failed url=%s err=%s", url, exc)
        return {"status": "error", "err": str(exc)}


__all__ = [
    "deep_extract_own_page_task",
    "deep_extract_competitor_url_task",
]
