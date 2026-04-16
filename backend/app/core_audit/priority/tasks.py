"""Celery tasks for Module 4 — rescore recommendations."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from app.core_audit.priority.service import PriorityService
from app.workers.celery_app import celery_app
from app.workers.db_session import task_session

logger = logging.getLogger(__name__)


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="priority_rescore_site", bind=True, max_retries=1)
def priority_rescore_site(self, site_id: str):
    """Rescore all latest-review recommendations for a site."""

    async def _inner():
        async with task_session() as db:
            return await PriorityService().rescore_site(db, UUID(site_id))

    return _run(_inner())
