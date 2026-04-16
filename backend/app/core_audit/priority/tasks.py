"""Celery tasks for Module 4 — rescore recommendations."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core_audit.priority.service import PriorityService
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


@celery_app.task(name="priority_rescore_site", bind=True, max_retries=1)
def priority_rescore_site(self, site_id: str):
    """Rescore all latest-review recommendations for a site."""

    async def _inner():
        session_factory = _make_session()
        async with session_factory() as db:
            return await PriorityService().rescore_site(db, UUID(site_id))

    return _run(_inner())
