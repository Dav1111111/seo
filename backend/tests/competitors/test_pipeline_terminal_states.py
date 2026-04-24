"""Day 1-2 invariant: every pipeline started reaches a terminal state.

The "идёт сейчас…" bug comes from pipeline:started events that never
got a matching pipeline:done/failed/skipped because one of the tasks
took an early-exit path (no queries, no competitors, crash) without
notifying the pipeline stage.

Each test here models one of those paths and asserts the invariant.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core_audit.activity import emit_terminal, log_event, reconcile_open_pipelines
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


async def test_crawl_done_does_not_close_pipeline(db, test_site: Site):
    """Regression: crawl:done MUST NOT close a pipeline that still has
    discovery/deep-dive/opportunities queued. Before this guard, the
    first-finishing fan-out task (crawl, ~0.5s) closed pipeline 20s
    before competitors were done."""
    await log_event(db, test_site.id, "pipeline", "started", "trigger")
    await emit_terminal(
        db, test_site.id, "crawl", "done",
        "Краулинг: 15 страниц, 0 ошибок.",
    )
    pipe = await _events(db, test_site.id, stage="pipeline")
    assert [e.status for e in pipe] == ["started"], (
        "crawl:done should NOT have closed the pipeline"
    )


async def test_webmaster_done_does_not_close_pipeline(db, test_site: Site):
    """Same gate: webmaster:done isn't the canonical end of the pipeline."""
    await log_event(db, test_site.id, "pipeline", "started", "trigger")
    await emit_terminal(db, test_site.id, "webmaster", "done", "42 queries")
    pipe = await _events(db, test_site.id, stage="pipeline")
    assert [e.status for e in pipe] == ["started"]


async def test_demand_map_done_does_not_close_pipeline(db, test_site: Site):
    """Same gate: demand_map is ancillary; opportunities is the true end."""
    await log_event(db, test_site.id, "pipeline", "started", "trigger")
    await emit_terminal(db, test_site.id, "demand_map", "done", "280 clusters")
    pipe = await _events(db, test_site.id, stage="pipeline")
    assert [e.status for e in pipe] == ["started"]


async def test_discovery_done_does_not_close_pipeline(db, test_site: Site):
    """discovery:done is expected mid-run; deep-dive still runs after it."""
    await log_event(db, test_site.id, "pipeline", "started", "trigger")
    await emit_terminal(
        db, test_site.id, "competitor_discovery", "done",
        "10 конкурентов",
    )
    pipe = await _events(db, test_site.id, stage="pipeline")
    assert [e.status for e in pipe] == ["started"]


async def test_opportunities_done_closes_pipeline(db, test_site: Site):
    """opportunities is the canonical end — done status closes pipeline."""
    await log_event(db, test_site.id, "pipeline", "started", "trigger")
    await emit_terminal(
        db, test_site.id, "opportunities", "done", "15 opps",
    )
    pipe = await _events(db, test_site.id, stage="pipeline")
    assert [e.status for e in pipe] == ["started", "done"]


async def test_queued_pipeline_closes_only_after_last_stage(db, test_site: Site):
    """New full-analysis contract: pipeline closes when all queued
    stages for the same run_id reached terminal state."""
    run = uuid.uuid4()
    await log_event(
        db,
        test_site.id,
        "pipeline",
        "started",
        "trigger",
        extra={"queued": ["crawl", "webmaster", "demand_map"]},
        run_id=run,
    )

    await emit_terminal(db, test_site.id, "crawl", "done", "15 pages", run_id=run)
    await emit_terminal(db, test_site.id, "webmaster", "done", "42 q", run_id=run)

    pipe_mid = await _events(db, test_site.id, stage="pipeline")
    assert [e.status for e in pipe_mid] == ["started"]

    await emit_terminal(
        db, test_site.id, "demand_map", "done", "280 clusters", run_id=run,
    )
    pipe_end = await _events(db, test_site.id, stage="pipeline")
    assert [e.status for e in pipe_end] == ["started", "done"]


async def test_queued_pipeline_collapses_failure_status(db, test_site: Site):
    """If any queued stage fails, pipeline closes failed once the last
    queued stage finishes."""
    run = uuid.uuid4()
    await log_event(
        db,
        test_site.id,
        "pipeline",
        "started",
        "trigger",
        extra={"queued": ["crawl", "webmaster", "demand_map"]},
        run_id=run,
    )

    await emit_terminal(db, test_site.id, "crawl", "done", "15 pages", run_id=run)
    await emit_terminal(db, test_site.id, "webmaster", "failed", "500", run_id=run)

    pipe_mid = await _events(db, test_site.id, stage="pipeline")
    assert [e.status for e in pipe_mid] == ["started"]

    await emit_terminal(
        db, test_site.id, "demand_map", "done", "280 clusters", run_id=run,
    )
    pipe_end = await _events(db, test_site.id, stage="pipeline")
    assert [e.status for e in pipe_end] == ["started", "failed"]


async def test_reconcile_open_pipelines_backfills_missing_terminal(db, test_site: Site):
    """Historical race: queued stages finished before pipeline:started was
    persisted, so no terminal got written. Reconcile should backfill it."""
    run = uuid.uuid4()

    await log_event(db, test_site.id, "crawl", "done", "15 pages", run_id=run)
    await log_event(db, test_site.id, "webmaster", "done", "42 q", run_id=run)
    await log_event(db, test_site.id, "demand_map", "done", "280 clusters", run_id=run)
    await log_event(
        db,
        test_site.id,
        "pipeline",
        "started",
        "trigger",
        extra={"queued": ["crawl", "webmaster", "demand_map"]},
        run_id=run,
    )

    repaired = await reconcile_open_pipelines(db, test_site.id)
    assert repaired == 1

    pipe = await _events(db, test_site.id, stage="pipeline")
    assert [e.status for e in pipe] == ["started", "done"]
    assert pipe[-1].ts >= pipe[0].ts


async def test_reconcile_open_pipelines_skips_incomplete_run(db, test_site: Site):
    run = uuid.uuid4()
    await log_event(
        db,
        test_site.id,
        "pipeline",
        "started",
        "trigger",
        extra={"queued": ["crawl", "webmaster", "demand_map"]},
        run_id=run,
    )
    await log_event(db, test_site.id, "crawl", "done", "15 pages", run_id=run)
    await log_event(db, test_site.id, "webmaster", "done", "42 q", run_id=run)

    repaired = await reconcile_open_pipelines(db, test_site.id)
    assert repaired == 0

    pipe = await _events(db, test_site.id, stage="pipeline")
    assert [e.status for e in pipe] == ["started"]


async def test_reconcile_open_pipelines_understands_legacy_stage_aliases(
    db, test_site: Site,
):
    run = uuid.uuid4()
    await log_event(db, test_site.id, "crawl", "done", "15 pages", run_id=run)
    await log_event(db, test_site.id, "webmaster", "done", "42 q", run_id=run)
    await log_event(db, test_site.id, "demand_map", "done", "280 clusters", run_id=run)
    await log_event(
        db,
        test_site.id,
        "pipeline",
        "started",
        "trigger",
        extra={"queued": ["crawl", "webmaster", "demand_map_build"]},
        run_id=run,
    )

    repaired = await reconcile_open_pipelines(db, test_site.id)
    assert repaired == 1

    pipe = await _events(db, test_site.id, stage="pipeline")
    assert [e.status for e in pipe] == ["started", "done"]
