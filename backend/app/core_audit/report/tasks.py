"""Celery tasks for Module 5 — weekly report generation."""

from __future__ import annotations

import asyncio
import logging
from datetime import date as dt_date
from uuid import UUID

from sqlalchemy import select

from app.core_audit.report.service import ReportService
from app.models.site import Site
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


@celery_app.task(name="report_build_site", bind=True, max_retries=1)
def report_build_site(self, site_id: str, week_end_iso: str | None = None):
    async def _inner():
        async with task_session() as db:
            end = dt_date.fromisoformat(week_end_iso) if week_end_iso else None
            row = await ReportService().build_and_save(db, UUID(site_id), week_end=end)
            return {
                "report_id": str(row.id),
                "site_id": str(row.site_id),
                "week_end": row.week_end.isoformat(),
                "status": row.status,
                "health_score": row.health_score,
                "llm_cost_usd": float(row.llm_cost_usd or 0.0),
                "generation_ms": row.generation_ms,
            }

    return _run(_inner())


@celery_app.task(name="report_build_all_weekly", bind=True, max_retries=1)
def report_build_all_weekly(self):
    async def _inner():
        async with task_session() as db:
            rows = await db.execute(
                select(Site.id).where(Site.is_active == True)  # noqa: E712
            )
            site_ids = [r[0] for r in rows]
        for sid in site_ids:
            report_build_site.delay(str(sid), None)
        return {"dispatched": [str(s) for s in site_ids]}

    return _run(_inner())
