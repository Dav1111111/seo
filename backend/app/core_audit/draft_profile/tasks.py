"""Celery task — Draft Profile build (Phase F).

Single task: `draft_profile_build_site(site_id)`. Triggered via the
admin API only (no beat schedule in Phase F). Phase G's wizard UI will
call the admin rebuild endpoint which enqueues this task.

Fail-open contract: the task never raises to Celery — it returns a
structured dict summary for Flower / alerting consumers.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

import app.profiles  # noqa: F401 — triggers profile registration
from app.core_audit.draft_profile.builder import build_draft_profile
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


@celery_app.task(name="draft_profile_build_site", bind=True, max_retries=1)
def draft_profile_build_site_task(self, site_id: str) -> dict:
    """Build + persist the draft profile for one site.

    Returns a summary dict. Never raises — on any internal error we
    log a warning and return `{"status": "error", ...}`.
    """
    async def _inner() -> dict:
        try:
            async with task_session() as db:
                profile = await build_draft_profile(db, UUID(site_id))
                await db.commit()
                return {
                    "status": "ok",
                    "site_id": site_id,
                    "overall_confidence": profile.overall_confidence,
                    **profile.signals,
                }
        except LookupError as exc:
            return {"status": "skipped", "reason": "site_not_found", "site_id": site_id, "err": str(exc)}
        except Exception as exc:  # noqa: BLE001 — fail-open
            log.warning("draft_profile.task_failed site=%s err=%s", site_id, exc)
            return {"status": "error", "site_id": site_id, "err": str(exc)}

    return _run(_inner())


__all__ = ["draft_profile_build_site_task"]
