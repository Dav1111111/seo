"""Celery task — rebuild BusinessTruth for one site.

Emits events under stage="business_truth" so the activity feed shows
the user "пересобираю понимание бизнеса..." while the task runs.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from app.core_audit.activity import emit_terminal, log_event
from app.core_audit.business_truth.rebuild import rebuild_business_truth
from app.workers.celery_app import celery_app
from app.workers.db_session import task_session

log = logging.getLogger(__name__)


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="business_truth_rebuild_site", bind=True, max_retries=0)
def business_truth_rebuild_site_task(
    self,
    site_id: str,
    run_id: str | None = None,
) -> dict:
    """Rebuild BusinessTruth for the site. Safe to fire whenever — it's
    idempotent (writes over the previous blob)."""

    async def _inner() -> dict:
        async with task_session() as db:
            await log_event(
                db, site_id, "business_truth", "started",
                "Собираю понимание бизнеса: онбординг + страницы + трафик…",
                run_id=run_id,
            )
            try:
                truth = await rebuild_business_truth(
                    db, UUID(site_id), persist=True,
                )
            except Exception as exc:  # noqa: BLE001
                await emit_terminal(
                    db, site_id, "business_truth", "failed",
                    f"Не удалось собрать понимание: {str(exc)[:200]}",
                    run_id=run_id,
                )
                raise

            confirmed = len(truth.confirmed())
            blind = len(truth.blind_spots())
            traffic_only = len(truth.traffic_only())

            await emit_terminal(
                db, site_id, "business_truth", "done",
                (
                    f"Понимание готово: {len(truth.directions)} направлений, "
                    f"{confirmed} подтверждено 3 источниками, "
                    f"{blind} слепых пятен, {traffic_only} незакрытого спроса."
                ),
                extra={
                    "directions": len(truth.directions),
                    "confirmed": confirmed,
                    "blind_spots": blind,
                    "traffic_only": traffic_only,
                },
                run_id=run_id,
            )
            return {
                "status": "ok",
                "site_id": site_id,
                "directions": len(truth.directions),
                "confirmed": confirmed,
                "blind_spots": blind,
                "traffic_only": traffic_only,
            }

    return _run(_inner())


__all__ = ["business_truth_rebuild_site_task"]
