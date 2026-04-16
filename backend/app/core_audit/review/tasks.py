"""Celery tasks for Page Review (Module 3).

Follows the `_run`/`_make_session` pattern from app.intent.tasks so each
task has its own async engine (prevents asyncpg session conflicts).
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core_audit.review.reviewer import DEFAULT_TOP_N, Reviewer
from app.models.site import Site
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_session() -> async_sessionmaker[AsyncSession]:
    eng = create_async_engine(settings.DATABASE_URL, pool_size=2, max_overflow=0)
    return async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


@celery_app.task(name="review_page", bind=True, max_retries=1)
def review_page_task(self, page_id: str, decision_id: str | None = None):
    async def _inner():
        session_factory = _make_session()
        async with session_factory() as db:
            result = await Reviewer().review_page(
                db,
                UUID(page_id),
                UUID(decision_id) if decision_id else None,
            )
            return {
                "page_id": str(result.page_id),
                "status": result.status.value,
                "reviewer_model": result.reviewer_model,
                "cost_usd": result.cost_usd,
                "skip_reason": result.skip_reason.value if result.skip_reason else None,
                "recommendations": len(result.recommendations),
            }

    return _run(_inner())


@celery_app.task(name="review_site_decisions", bind=True, max_retries=1)
def review_site_decisions_task(self, site_id: str, top_n: int = DEFAULT_TOP_N):
    """Review top-N strengthen decisions for a single site. Chains
    priority_rescore_site so fresh recs get scored immediately."""

    async def _inner():
        session_factory = _make_session()
        async with session_factory() as db:
            return await Reviewer().review_site(db, UUID(site_id), top_n=top_n)

    result = _run(_inner())
    # Chain rescore so priorities are fresh right after the review batch.
    try:
        from app.core_audit.priority.tasks import priority_rescore_site
        priority_rescore_site.delay(site_id)
    except Exception as exc:
        logger.warning("rescore chain dispatch failed site=%s: %s", site_id, exc)
    return result


@celery_app.task(name="review_all_nightly", bind=True, max_retries=1)
def review_all_nightly_task(self, top_n: int = DEFAULT_TOP_N):
    """Nightly: iterate active sites, fire one review_site_decisions per site."""

    async def _inner():
        session_factory = _make_session()
        async with session_factory() as db:
            rows = await db.execute(
                select(Site.id, Site.vertical).where(Site.is_active == True)  # noqa: E712
            )
            active = [(sid, vert) for sid, vert in rows]

        dispatched = []
        for sid, vert in active:
            if vert != "tourism":
                logger.warning("site %s vertical=%s — falls back to tourism profile", sid, vert)
            review_site_decisions_task.delay(str(sid), top_n)
            dispatched.append(str(sid))
        return {"dispatched": dispatched, "top_n": top_n}

    return _run(_inner())
