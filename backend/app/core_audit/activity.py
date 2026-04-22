"""Activity-feed helper — one-line events written from Celery tasks.

Called like:
    await log_event(db, site_id, "competitor_discovery", "started",
                    "Ищу конкурентов по 25 запросам…")

Commits asynchronously. Kept separate from task business logic so
writing an event never blocks the main flow on failure.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis_event import AnalysisEvent

log = logging.getLogger(__name__)

TERMINAL_STATUSES = ("done", "failed", "skipped")
PIPELINE_STARTED_LOOKBACK_MINUTES = 10


async def log_event(
    db: AsyncSession,
    site_id: UUID | str,
    stage: str,
    status: str,
    message: str,
    extra: dict | None = None,
    run_id: UUID | str | None = None,
) -> None:
    """Write one event row. Best-effort: swallows errors so task flow
    keeps going even if the event table is down.

    `run_id` — pipelines pass their generated UUID through so the UI
    can group a run's events together. Standalone events (not part of
    a pipeline) leave it None, which is fine.
    """
    try:
        ev = AnalysisEvent(
            site_id=UUID(str(site_id)),
            stage=stage,
            status=status,
            message=message[:500],
            extra=extra or {},
            run_id=UUID(str(run_id)) if run_id else None,
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


async def emit_terminal(
    db: AsyncSession,
    site_id: UUID | str,
    stage: str,
    status: str,
    message: str,
    extra: dict | None = None,
    run_id: UUID | str | None = None,
) -> None:
    """Emit a stage terminal event and, if a pipeline was started for
    this site recently, also close the pipeline with a matching
    terminal. Invariant: every pipeline:started eventually gets a
    pipeline:<terminal> within 10 minutes of its start.

    If `run_id` is provided, pipeline lookup scopes to that run_id
    exactly — two concurrent pipelines won't close each other.
    If `run_id` is None, falls back to time-window lookup (legacy).

    Does NOT emit for statuses outside ('done'|'failed'|'skipped') —
    those are started/progress rows that log_event handles directly.
    """
    if status not in TERMINAL_STATUSES:
        raise ValueError(
            f"emit_terminal called with non-terminal status={status!r}",
        )

    await log_event(db, site_id, stage, status, message, extra, run_id=run_id)

    # Scope: match by run_id when supplied (precise), else time-window.
    stmt = (
        select(AnalysisEvent.status)
        .where(
            AnalysisEvent.site_id == UUID(str(site_id)),
            AnalysisEvent.stage == "pipeline",
        )
        .order_by(desc(AnalysisEvent.ts))
        .limit(1)
    )
    if run_id is not None:
        stmt = stmt.where(AnalysisEvent.run_id == UUID(str(run_id)))
    else:
        cutoff = datetime.utcnow() - timedelta(
            minutes=PIPELINE_STARTED_LOOKBACK_MINUTES,
        )
        stmt = stmt.where(AnalysisEvent.ts >= cutoff)

    newest = (await db.execute(stmt)).scalar_one_or_none()
    if newest != "started":
        return

    pipeline_msg = {
        "done":    "Полный анализ завершён.",
        "failed":  "Полный анализ остановлен: ошибка на одном из этапов.",
        "skipped": "Полный анализ завершён без новых данных.",
    }[status]
    await log_event(
        db, site_id, "pipeline", status, pipeline_msg, extra, run_id=run_id,
    )


__all__ = ["log_event", "emit_terminal", "TERMINAL_STATUSES"]
