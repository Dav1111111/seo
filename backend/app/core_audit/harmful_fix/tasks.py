"""Celery wrapper around materialize_harmful_fixes.

Triggered:
  - manually after harmful_diagnoser.py finishes a batch of diagnoses,
  - on the regular pipeline after `intent_decide` completes (so the
    owner sees fix-rows next to the recently-classified harmful
    queries),
  - via beat-schedule once a day as a safety net.

Cost: zero LLM. The diagnosis already paid for the LLM call.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from app.core_audit.activity import log_event
from app.core_audit.harmful_fix import materialize_harmful_fixes
from app.workers.celery_app import celery_app
from app.workers.db_session import task_session

log = logging.getLogger(__name__)


@celery_app.task(name="harmful_fix_materialize", bind=True, max_retries=1)
def harmful_fix_materialize_task(self, site_id: str) -> dict:
    """Translate cached harmful_diagnosis fixes into PageReviewRecommendation rows.

    Returns the MaterializeResult fields as a flat dict for activity logging.
    """

    async def _inner() -> dict:
        async with task_session() as db:
            result = await materialize_harmful_fixes(db, UUID(site_id))
            await db.commit()

            await log_event(
                db, site_id, "harmful_fix", "done",
                (
                    f"Подготовил {result.recs_created} правок по "
                    f"{result.pages_touched} страницам "
                    f"(пропущено {result.queries_skipped} запросов без URL)."
                ),
                extra={
                    "queries_processed": result.queries_processed,
                    "queries_skipped": result.queries_skipped,
                    "pages_touched": result.pages_touched,
                    "recs_created": result.recs_created,
                    "recs_skipped_existing": result.recs_skipped_existing,
                },
            )
            await db.commit()

            return {
                "status": "ok",
                "site_id": site_id,
                "queries_processed": result.queries_processed,
                "queries_skipped": result.queries_skipped,
                "pages_touched": result.pages_touched,
                "recs_created": result.recs_created,
                "recs_skipped_existing": result.recs_skipped_existing,
            }

    try:
        return asyncio.run(_inner())
    except Exception as exc:  # noqa: BLE001
        log.warning("harmful_fix.task_failed site=%s err=%s", site_id, exc)
        return {"status": "error", "site_id": site_id, "err": str(exc)}


__all__ = ["harmful_fix_materialize_task"]
