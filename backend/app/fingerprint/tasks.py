"""Celery tasks for fingerprinting."""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select

from app.fingerprint.dto import FingerprintInput
from app.fingerprint.enums import FingerprintStatus
from app.fingerprint.models import PageFingerprint
from app.fingerprint.service import FingerprintService
from app.models.page import Page
from app.models.site import Site
from app.workers.celery_app import celery_app
from app.workers.db_session import task_session, task_session_factory

logger = logging.getLogger(__name__)


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _fingerprint_site_async(site_id: UUID, force: bool = False) -> dict:
    """Fingerprint all pages of one site (incremental)."""
    t0 = time.monotonic()
    svc = FingerprintService()

    stats = {
        "site_id": str(site_id),
        "pages_total": 0,
        "pages_computed": 0,
        "pages_skipped_unchanged": 0,
        "pages_skipped_thin": 0,
        "pages_errored": 0,
        "db_errors": 0,
    }

    async with task_session_factory() as session_factory:
        async with session_factory() as db:
            pages = await db.execute(
                select(
                    Page.id, Page.site_id, Page.url, Page.title, Page.h1,
                    Page.content_text, Page.last_crawled_at,
                ).where(Page.site_id == site_id)
            )
            page_list = [dict(r._mapping) for r in pages]
            stats["pages_total"] = len(page_list)

        sem = asyncio.Semaphore(8)

        async def process(p: dict) -> None:
            async with sem:
                async with session_factory() as db:
                    try:
                        inp = FingerprintInput(
                            page_id=p["id"],
                            site_id=p["site_id"],
                            url=p["url"],
                            title=p.get("title"),
                            h1=p.get("h1"),
                            content_text=p.get("content_text"),
                            last_crawled_at=p.get("last_crawled_at") or datetime.now(timezone.utc),
                            force=force,
                        )
                        result = await svc.compute_one(db, inp)
                        await db.commit()

                        if result.status == FingerprintStatus.fingerprinted:
                            stats["pages_computed"] += 1
                        elif result.status == FingerprintStatus.skipped_unchanged:
                            stats["pages_skipped_unchanged"] += 1
                        elif result.status == FingerprintStatus.skipped_thin:
                            stats["pages_skipped_thin"] += 1
                        else:
                            stats["pages_errored"] += 1

                    except Exception as exc:
                        logger.warning("fingerprint failed page=%s: %s", p["id"], exc)
                        stats["pages_errored"] += 1
                        stats["db_errors"] += 1

        await asyncio.gather(*(process(p) for p in page_list), return_exceptions=True)

    stats["duration_ms"] = int((time.monotonic() - t0) * 1000)
    logger.info("fingerprint_site done: %s", stats)
    return stats


@celery_app.task(name="fingerprint_site", bind=True, max_retries=2)
def fingerprint_site(self, site_id: str, force: bool = False):
    """Fingerprint all pages of a single site."""
    return _run(_fingerprint_site_async(UUID(site_id), force=force))


@celery_app.task(name="fingerprint_site_force")
def fingerprint_site_force(site_id: str):
    """Force-rebuild all fingerprints for a site (ignores change detection)."""
    return _run(_fingerprint_site_async(UUID(site_id), force=True))


@celery_app.task(name="fingerprint_page")
def fingerprint_page(page_id: str, force: bool = False):
    """Fingerprint a single page by id."""

    async def _run_one() -> dict:
        svc = FingerprintService()
        async with task_session() as db:
            row = await db.execute(
                select(
                    Page.id, Page.site_id, Page.url, Page.title, Page.h1,
                    Page.content_text, Page.last_crawled_at,
                ).where(Page.id == UUID(page_id))
            )
            p = row.first()
            if not p:
                return {"error": "page_not_found"}
            p = dict(p._mapping)
            inp = FingerprintInput(
                page_id=p["id"],
                site_id=p["site_id"],
                url=p["url"],
                title=p.get("title"),
                h1=p.get("h1"),
                content_text=p.get("content_text"),
                last_crawled_at=p.get("last_crawled_at") or datetime.now(timezone.utc),
                force=force,
            )
            result = await svc.compute_one(db, inp)
            await db.commit()
            return {
                "page_id": str(result.page_id),
                "status": result.status.value,
                "reason": result.recompute_reason.value,
            }

    return _run(_run_one())


@celery_app.task(name="fingerprint_all_sites", bind=True, max_retries=1)
def fingerprint_all_sites(self):
    """Scheduled sweep — run fingerprinting for all active sites."""

    async def _run_all() -> dict:
        async with task_session() as db:
            rows = await db.execute(
                select(Site.id).where(Site.is_active == True)  # noqa: E712
            )
            site_ids = [r[0] for r in rows]

        results: dict[str, dict] = {}
        for sid in site_ids:
            try:
                results[str(sid)] = await _fingerprint_site_async(sid)
            except Exception as exc:
                logger.error("fingerprint_all_sites failed for %s: %s", sid, exc)
                results[str(sid)] = {"error": str(exc)}
        return results

    return _run(_run_all())


@celery_app.task(name="fingerprint_gc_stale")
def fingerprint_gc_stale():
    """Weekly GC — delete fingerprints of pages not seen in 90 days."""

    async def _gc() -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        async with task_session() as db:
            # Find pages with last_seen_at < cutoff
            rows = await db.execute(
                select(Page.id).where(Page.last_seen_at < cutoff)
            )
            stale_page_ids = [r[0] for r in rows]
            if not stale_page_ids:
                return {"deleted": 0}
            # Delete via cascade? No — Page stays, just drop fingerprint
            deleted = await db.execute(
                PageFingerprint.__table__.delete().where(
                    PageFingerprint.page_id.in_(stale_page_ids)
                )
            )
            await db.commit()
            return {"deleted": deleted.rowcount}

    return _run(_gc())
