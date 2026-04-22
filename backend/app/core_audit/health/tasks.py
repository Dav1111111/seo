"""Platform health watchdogs — run frequently from Celery beat.

The only watchdog we currently run is the queue-depth sentinel: if
messages pile up in Redis without being consumed, we write a loud
event to `analysis_events` so the dashboard reflects "worker стоит,
очередь = N" rather than silently leaving the owner staring at a
spinning icon.

Scheduled every 2 minutes from `celery_app.conf.beat_schedule`.
"""

from __future__ import annotations

import logging
import asyncio
from uuid import UUID

import redis
from sqlalchemy import select

from app.config import settings
from app.core_audit.activity import log_event
from app.models.site import Site
from app.workers.celery_app import celery_app
from app.workers.db_session import task_session

log = logging.getLogger(__name__)

# If >= this many messages sit in Redis waiting to be picked up when we
# peek, we declare the queue stuck. Discovery + deep-dive + a couple of
# aux tasks rarely overlap by more than 3 items on a healthy worker.
STUCK_THRESHOLD = 5

# Default Celery queue name (we never set queue= anywhere → this is it)
DEFAULT_QUEUE = "celery"


@celery_app.task(name="queue_health_check", bind=True, max_retries=0)
def queue_health_check_task(self) -> dict:
    """Peek the Celery queue. If clogged, mark it in analysis_events."""

    async def _inner() -> dict:
        try:
            client = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=3)
            depth = client.llen(DEFAULT_QUEUE) or 0
        except Exception as exc:  # noqa: BLE001
            log.warning("queue_health.redis_failed err=%s", exc)
            return {"status": "error", "err": str(exc)}

        if depth < STUCK_THRESHOLD:
            return {"status": "ok", "depth": depth}

        # Fan the alert out to every active site so whichever dashboard
        # the owner is looking at shows the warning.
        async with task_session() as db:
            site_rows = (await db.execute(
                select(Site.id).where(Site.is_active.is_(True))
            )).all()
            for row in site_rows:
                await log_event(
                    db, row.id, "worker_health", "failed",
                    f"Очередь застряла ({depth} задач ждут). Worker не справляется "
                    "или упал — поднимем автоматически, минутку.",
                    extra={"queue_depth": depth, "threshold": STUCK_THRESHOLD},
                )
        return {"status": "stuck", "depth": depth}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_inner())
    finally:
        loop.close()


__all__ = ["queue_health_check_task"]
