"""Celery tasks for Module 5 — weekly report generation."""

from __future__ import annotations

import asyncio
import logging
from datetime import date as dt_date
from uuid import UUID

from sqlalchemy import select

from app.core_audit.activity import emit_terminal, log_event
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
def report_build_site(
    self,
    site_id: str,
    week_end_iso: str | None = None,
    run_id: str | None = None,
):
    async def _inner():
        async with task_session() as db:
            await log_event(
                db,
                site_id,
                "report",
                "started",
                "Собираю недельный отчёт и корневую проблему…",
                run_id=run_id,
            )
            end = dt_date.fromisoformat(week_end_iso) if week_end_iso else None
            try:
                row = await ReportService().build_and_save(
                    db,
                    UUID(site_id),
                    week_end=end,
                )
            except Exception as exc:  # noqa: BLE001
                await emit_terminal(
                    db,
                    site_id,
                    "report",
                    "failed",
                    f"Отчёт не собрался: {str(exc)[:200]}",
                    run_id=run_id,
                )
                raise

            result = {
                "report_id": str(row.id),
                "site_id": str(row.site_id),
                "week_end": row.week_end.isoformat(),
                "status": row.status,
                "health_score": row.health_score,
                "llm_cost_usd": float(row.llm_cost_usd or 0.0),
                "generation_ms": row.generation_ms,
            }
            await emit_terminal(
                db,
                site_id,
                "report",
                "done",
                f"Отчёт готов: Health {row.health_score}.",
                extra=result,
                run_id=run_id,
            )
            return result

    return _run(_inner())


@celery_app.task(name="report_build_all_weekly", bind=True, max_retries=1)
def report_build_all_weekly(self):
    async def _inner():
        from app.core_audit.onboarding.gate import onboarded_site_ids
        async with task_session() as db:
            site_ids = await onboarded_site_ids(db)
        for sid in site_ids:
            report_build_site.delay(str(sid), None)
        return {"dispatched": [str(s) for s in site_ids]}

    return _run(_inner())
