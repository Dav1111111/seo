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


async def emit_terminal(
    db: AsyncSession,
    site_id: UUID | str,
    stage: str,
    status: str,
    message: str,
    extra: dict | None = None,
) -> None:
    """Emit a stage terminal event and, if a pipeline was started for
    this site recently, also close the pipeline with a matching
    terminal. Invariant enforced: every pipeline:started eventually
    gets a pipeline:<terminal> within 10 minutes of its start.

    Call this at every early exit of discovery/deep-dive/opportunities:
      - no queries available
      - site not found
      - no competitors yet
      - top-level exception
    Plus the natural done paths.

    Does NOT emit for statuses outside ('done'|'failed'|'skipped') —
    those are started/progress rows that log_event handles directly.
    """
    if status not in TERMINAL_STATUSES:
        raise ValueError(
            f"emit_terminal called with non-terminal status={status!r}",
        )

    await log_event(db, site_id, stage, status, message, extra)

    # Close the pipeline only if one is actually open (started and not
    # yet terminated). Standalone runs from the per-feature buttons
    # don't have an open pipeline, so no orphan terminal appears.
    cutoff = datetime.utcnow() - timedelta(minutes=PIPELINE_STARTED_LOOKBACK_MINUTES)
    # Find the newest pipeline event for this site within the window
    newest = (await db.execute(
        select(AnalysisEvent.status)
        .where(
            AnalysisEvent.site_id == UUID(str(site_id)),
            AnalysisEvent.stage == "pipeline",
            AnalysisEvent.ts >= cutoff,
        )
        .order_by(desc(AnalysisEvent.ts))
        .limit(1)
    )).scalar_one_or_none()

    if newest != "started":
        # No open pipeline — either never started, already terminated,
        # or too old to count. Don't write a duplicate close.
        return

    # Pipeline status mirrors the most severe stage outcome in this run.
    # Simpler for v1: mirror the stage status verbatim.
    pipeline_msg = {
        "done":    "Полный анализ завершён.",
        "failed":  "Полный анализ остановлен: ошибка на одном из этапов.",
        "skipped": "Полный анализ завершён без новых данных.",
    }[status]
    await log_event(
        db, site_id, "pipeline", status, pipeline_msg, extra,
    )


__all__ = ["log_event", "emit_terminal", "TERMINAL_STATUSES"]
