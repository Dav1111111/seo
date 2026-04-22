"""Day 1-2 invariant: every pipeline started reaches a terminal state.

The "идёт сейчас…" bug comes from pipeline:started events that never
got a matching pipeline:done/failed/skipped because one of the tasks
took an early-exit path (no queries, no competitors, crash) without
notifying the pipeline stage.

Each test here models one of those paths and asserts the invariant.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core_audit.activity import emit_terminal, log_event
from app.models.analysis_event import AnalysisEvent
from app.models.site import Site


async def _events(db, site_id, stage=None):
    stmt = select(AnalysisEvent).where(AnalysisEvent.site_id == site_id)
    if stage:
        stmt = stmt.where(AnalysisEvent.stage == stage)
    stmt = stmt.order_by(AnalysisEvent.ts)
    return (await db.execute(stmt)).scalars().all()


async def test_emit_terminal_closes_open_pipeline(db, test_site: Site):
    """Happy path: pipeline:started → stage terminal → pipeline closes."""
    await log_event(db, test_site.id, "pipeline", "started", "Запустил…")
    await emit_terminal(
        db, test_site.id, "opportunities", "done", "15 точек роста",
    )

    pipeline_evts = await _events(db, test_site.id, stage="pipeline")
    stages = [(e.stage, e.status) for e in pipeline_evts]
    assert stages == [("pipeline", "started"), ("pipeline", "done")]


async def test_emit_terminal_closes_pipeline_on_failure(db, test_site: Site):
    """Failure path: stage failure also closes the pipeline."""
    await log_event(db, test_site.id, "pipeline", "started", "Запустил…")
    await emit_terminal(
        db, test_site.id, "competitor_discovery", "failed",
        "Яндекс вернул 500",
    )
    pipe = await _events(db, test_site.id, stage="pipeline")
    statuses = [e.status for e in pipe]
    assert statuses == ["started", "failed"]


async def test_emit_terminal_closes_pipeline_on_skip(db, test_site: Site):
    """Skip path: no queries available still closes the pipeline."""
    await log_event(db, test_site.id, "pipeline", "started", "Запустил…")
    await emit_terminal(
        db, test_site.id, "competitor_discovery", "skipped",
        "Нет запросов для разведки",
    )
    pipe = await _events(db, test_site.id, stage="pipeline")
    statuses = [e.status for e in pipe]
    assert statuses == ["started", "skipped"]


async def test_emit_terminal_standalone_run_no_phantom_close(db, test_site: Site):
    """Standalone run (no pipeline:started) must NOT emit phantom pipeline:done."""
    # No pipeline:started — e.g. user clicked "Пересобрать список" directly
    await emit_terminal(
        db, test_site.id, "competitor_discovery", "done",
        "Разведка готова: 10 конкурентов",
    )
    pipe = await _events(db, test_site.id, stage="pipeline")
    assert pipe == [], "No pipeline event should exist — run was standalone"
    # But stage terminal IS there
    disc = await _events(db, test_site.id, stage="competitor_discovery")
    assert len(disc) == 1 and disc[0].status == "done"


async def test_emit_terminal_doesnt_double_close(db, test_site: Site):
    """If pipeline already closed, calling emit_terminal again does not
    reopen or double-close."""
    await log_event(db, test_site.id, "pipeline", "started", "Start")
    await emit_terminal(
        db, test_site.id, "competitor_discovery", "done", "Discovery done",
    )
    # Second stage completion (e.g. opportunities runs after discovery)
    await emit_terminal(
        db, test_site.id, "opportunities", "done", "Opps done",
    )
    pipe = await _events(db, test_site.id, stage="pipeline")
    # Only one pair: started + first done. Second done did NOT add
    # a third pipeline event.
    statuses = [e.status for e in pipe]
    assert statuses == ["started", "done"], (
        f"Expected exactly one started+done pair, got {statuses}"
    )


async def test_emit_terminal_rejects_non_terminal_status(db, test_site: Site):
    """Guardrail: emit_terminal must only be called with done/failed/skipped."""
    import pytest
    with pytest.raises(ValueError):
        await emit_terminal(
            db, test_site.id, "competitor_discovery", "progress", "x",
        )
