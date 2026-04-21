"""Activity-feed helper — one-line events written from Celery tasks.

Called like:
    await log_event(db, site_id, "competitor_discovery", "started",
                    "Ищу конкурентов по 25 запросам…")

Commits asynchronously. Kept separate from task business logic so
writing an event never blocks the main flow on failure.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis_event import AnalysisEvent

log = logging.getLogger(__name__)


async def log_event(
    db: AsyncSession,
    site_id: UUID | str,
    stage: str,
    status: str,
    message: str,
    extra: dict | None = None,
) -> None:
    """Write one event row. Best-effort: swallows errors so task flow
    keeps going even if the event table is down."""
    try:
        ev = AnalysisEvent(
            site_id=UUID(str(site_id)),
            stage=stage,
            status=status,
            message=message[:500],
            extra=extra or {},
        )
        db.add(ev)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("activity.log_event_failed site=%s stage=%s err=%s",
                    site_id, stage, exc)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["log_event"]
