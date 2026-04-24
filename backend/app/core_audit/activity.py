"""Activity-feed helper — one-line events written from Celery tasks.

Called like:
    await log_event(db, site_id, "competitor_discovery", "started",
                    "Ищу конкурентов по 25 запросам…")

Commits asynchronously. Kept separate from task business logic so
writing an event never blocks the main flow on failure.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis_event import AnalysisEvent

log = logging.getLogger(__name__)

TERMINAL_STATUSES = ("done", "failed", "skipped")
PIPELINE_STARTED_LOOKBACK_MINUTES = 10
LEGACY_STAGE_ALIASES = {
    "crawl_site": "crawl",
    "collect_site_webmaster": "webmaster",
    "demand_map_build": "demand_map",
    "demand_map_build_site": "demand_map",
}


def _pipeline_terminal_status(statuses: list[str]) -> str:
    """Collapse queued-stage terminals into one pipeline terminal."""
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "skipped" for status in statuses):
        return "skipped"
    return "done"


def _pipeline_message(status: str) -> str:
    return {
        "done": "Полный анализ завершён.",
        "failed": "Полный анализ остановлен: ошибка на одном из этапов.",
        "skipped": "Полный анализ завершён без новых данных.",
    }[status]


def _normalize_stage_name(name: str) -> str:
    clean = str(name).strip()
    return LEGACY_STAGE_ALIASES.get(clean, clean)


def _should_close_pipeline(stage: str, status: str) -> bool:
    """Decide whether `stage:status` should close the wrapping pipeline.

    The pipeline is a batch of ~6 parallel tasks; first-to-finish must
    not close it prematurely. Rules:

    - opportunities:done|failed|skipped is the canonical end (it's the
      last task in the discovery→dive→opportunities chain), so always
      close.
    - competitor_discovery|competitor_deep_dive with failed|skipped
      status → prerequisite broken, pipeline can't reach opportunities,
      close with that status.
    - Everything else (crawl done, webmaster done, demand_map done/
      failed, plus happy-path discovery:done / deep_dive:done) → do
      NOT close; the pipeline keeps going until opportunities.
    """
    if stage == "opportunities":
        return True
    if stage in ("competitor_discovery", "competitor_deep_dive"):
        return status in ("failed", "skipped")
    return False


async def _active_pipeline_started(
    db: AsyncSession,
    site_id: UUID | str,
    run_id: UUID | str | None = None,
) -> AnalysisEvent | None:
    """Newest pipeline event if the run is still open (`started`)."""
    stmt = (
        select(AnalysisEvent)
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
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            minutes=PIPELINE_STARTED_LOOKBACK_MINUTES,
        )
        stmt = stmt.where(AnalysisEvent.ts >= cutoff)

    newest = (await db.execute(stmt)).scalar_one_or_none()
    if newest is None or newest.status != "started":
        return None
    return newest


async def _latest_stage_statuses(
    db: AsyncSession,
    site_id: UUID | str,
    stages: list[str],
    run_id: UUID | str,
) -> dict[str, str]:
    """Latest status per queued stage for one pipeline run."""
    stmt = (
        select(AnalysisEvent)
        .where(
            AnalysisEvent.site_id == UUID(str(site_id)),
            AnalysisEvent.run_id == UUID(str(run_id)),
            AnalysisEvent.stage.in_(stages),
        )
        .order_by(desc(AnalysisEvent.ts))
        .limit(max(50, len(stages) * 10))
    )
    rows = (await db.execute(stmt)).scalars().all()

    latest: dict[str, str] = {}
    for ev in rows:
        if ev.stage not in latest:
            latest[ev.stage] = ev.status
    return latest


async def _latest_stage_events(
    db: AsyncSession,
    site_id: UUID | str,
    stages: list[str],
    run_id: UUID | str,
) -> dict[str, AnalysisEvent]:
    stmt = (
        select(AnalysisEvent)
        .where(
            AnalysisEvent.site_id == UUID(str(site_id)),
            AnalysisEvent.run_id == UUID(str(run_id)),
            AnalysisEvent.stage.in_(stages),
        )
        .order_by(desc(AnalysisEvent.ts))
        .limit(max(50, len(stages) * 10))
    )
    rows = (await db.execute(stmt)).scalars().all()

    latest: dict[str, AnalysisEvent] = {}
    for ev in rows:
        if ev.stage not in latest:
            latest[ev.stage] = ev
    return latest


async def _terminal_exists_for_run(
    db: AsyncSession,
    site_id: UUID | str,
    run_id: UUID | str,
) -> bool:
    stmt = (
        select(AnalysisEvent.id)
        .where(
            AnalysisEvent.site_id == UUID(str(site_id)),
            AnalysisEvent.run_id == UUID(str(run_id)),
            AnalysisEvent.stage == "pipeline",
            AnalysisEvent.status.in_(TERMINAL_STATUSES),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def _persist_event(
    db: AsyncSession,
    site_id: UUID | str,
    stage: str,
    status: str,
    message: str,
    extra: dict | None = None,
    run_id: UUID | str | None = None,
    ts: datetime | None = None,
) -> None:
    ev = AnalysisEvent(
        site_id=UUID(str(site_id)),
        stage=stage,
        status=status,
        message=message[:500],
        extra=extra or {},
        run_id=UUID(str(run_id)) if run_id else None,
    )
    if ts is not None:
        ev.ts = ts
    db.add(ev)
    await db.commit()


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
        await _persist_event(
            db, site_id, stage, status, message, extra=extra, run_id=run_id,
        )
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

    pipeline_started = await _active_pipeline_started(db, site_id, run_id=run_id)
    if pipeline_started is None:
        return

    pipeline_status: str | None = None
    queued = pipeline_started.extra.get("queued") if pipeline_started.extra else None

    # New pipeline contract: full analysis declares its queued stages
    # up front. Close the wrapper only after ALL queued stages for this
    # exact run_id reached terminal status.
    if run_id is not None and isinstance(queued, list) and queued:
        queued_stages = [str(name) for name in queued if str(name).strip()]
        latest = await _latest_stage_statuses(db, site_id, queued_stages, run_id)
        if len(latest) != len(queued_stages):
            return
        statuses = list(latest.values())
        if not all(stage_status in TERMINAL_STATUSES for stage_status in statuses):
            return
        pipeline_status = _pipeline_terminal_status(statuses)

    # Legacy fallback: old pipelines didn't declare `queued`, so keep
    # the previous close-on-canonical-end behavior for compatibility.
    if pipeline_status is None:
        if not _should_close_pipeline(stage, status):
            return
        pipeline_status = status

    pipeline_msg = _pipeline_message(pipeline_status)
    await log_event(
        db,
        site_id,
        "pipeline",
        pipeline_status,
        pipeline_msg,
        extra,
        run_id=run_id,
    )


async def reconcile_open_pipelines(
    db: AsyncSession,
    site_id: UUID | str,
    limit: int = 50,
) -> int:
    """Backfill terminal rows for historical runs that finished their queued
    stages before pipeline:started was persisted.

    This repairs old data without changing the ordering of newer runs:
    the synthetic terminal is timestamped right after the later of
    pipeline:started and the latest queued-stage terminal.
    """
    started_rows = (await db.execute(
        select(AnalysisEvent)
        .where(
            AnalysisEvent.site_id == UUID(str(site_id)),
            AnalysisEvent.stage == "pipeline",
            AnalysisEvent.status == "started",
            AnalysisEvent.run_id.is_not(None),
        )
        .order_by(desc(AnalysisEvent.ts))
        .limit(limit)
    )).scalars().all()

    repaired = 0
    for started in started_rows:
        if started.run_id is None:
            continue
        if await _terminal_exists_for_run(db, site_id, started.run_id):
            continue

        queued = started.extra.get("queued") if started.extra else None
        if not isinstance(queued, list) or not queued:
            continue

        queued_stages = []
        for name in queued:
            normalized = _normalize_stage_name(name)
            if normalized and normalized not in queued_stages:
                queued_stages.append(normalized)
        if not queued_stages:
            continue

        latest = await _latest_stage_events(db, site_id, queued_stages, started.run_id)
        if len(latest) != len(queued_stages):
            continue

        statuses = [ev.status for ev in latest.values()]
        if not all(stage_status in TERMINAL_STATUSES for stage_status in statuses):
            continue

        pipeline_status = _pipeline_terminal_status(statuses)
        latest_stage_ts = max(
            [ev.ts for ev in latest.values() if ev.ts is not None] or [started.ts],
        )
        terminal_ts = max(started.ts, latest_stage_ts) + timedelta(microseconds=1)
        latest_ev = max(
            latest.values(),
            key=lambda ev: ev.ts or started.ts,
        )

        try:
            await _persist_event(
                db,
                site_id,
                "pipeline",
                pipeline_status,
                _pipeline_message(pipeline_status),
                extra=latest_ev.extra or {},
                run_id=started.run_id,
                ts=terminal_ts,
            )
            repaired += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "activity.reconcile_pipeline_failed site=%s run_id=%s err=%s",
                site_id,
                started.run_id,
                exc,
            )
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                pass

    return repaired


__all__ = [
    "log_event",
    "emit_terminal",
    "reconcile_open_pipelines",
    "TERMINAL_STATUSES",
]
