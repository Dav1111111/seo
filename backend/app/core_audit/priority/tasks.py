"""Celery tasks for Module 4 — rescore recommendations."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from app.core_audit.activity import emit_terminal, log_event
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


def _format_priority_rescore_message(result: dict) -> tuple[str, dict]:
    scored = int(result.get("scored", 0) or 0)
    dropped = int(result.get("dropped", 0) or 0)
    zeroed_older = int(result.get("zeroed_older", 0) or 0)
    return (
        (
            f"Приоритеты пересчитаны: {scored} рекомендаций scored, "
            f"{dropped} скрыто по confidence floor, "
            f"{zeroed_older} старых score обнулено."
        ),
        {
            "scored": scored,
            "dropped": dropped,
            "zeroed_older": zeroed_older,
        },
    )


@celery_app.task(name="priority_rescore_site", bind=True, max_retries=1)
def priority_rescore_site(self, site_id: str, run_id: str | None = None):
    """Rescore all latest-review recommendations for a site."""

    async def _inner():
        async with task_session() as db:
            await log_event(
                db,
                site_id,
                "priorities",
                "started",
                "Пересчитываю приоритеты страниц и рекомендаций…",
                run_id=run_id,
            )
            try:
                result = await PriorityService().rescore_site(db, UUID(site_id))
            except Exception as exc:  # noqa: BLE001
                await emit_terminal(
                    db,
                    site_id,
                    "priorities",
                    "failed",
                    f"Пересчёт приоритетов остановлен: {str(exc)[:200]}",
                    run_id=run_id,
                )
                raise

            message, extra = _format_priority_rescore_message(result)
            await emit_terminal(
                db,
                site_id,
                "priorities",
                "done",
                message,
                extra=extra,
                run_id=run_id,
            )
            return result

    return _run(_inner())
