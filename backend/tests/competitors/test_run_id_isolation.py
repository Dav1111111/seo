"""Day 3-4 invariants: two runs with different run_ids don't mix.

Before this change, quick back-to-back clicks merged visually in the
activity feed because events were just "everything for this site,
newest first". run_id lets the UI show "current run only" and the
invariant test below lives or dies on it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core_audit.activity import emit_terminal, log_event
from app.models.analysis_event import AnalysisEvent
from app.models.site import Site


async def _events(db, site_id, run_id=None, stage=None):
    stmt = select(AnalysisEvent).where(AnalysisEvent.site_id == site_id)
    if run_id is not None:
        stmt = stmt.where(AnalysisEvent.run_id == run_id)
    if stage is not None:
        stmt = stmt.where(AnalysisEvent.stage == stage)
    return (await db.execute(stmt.order_by(AnalysisEvent.ts))).scalars().all()


async def test_log_event_accepts_run_id(db, test_site: Site):
    """Basic pass-through: log_event persists the run_id we pass in."""
    run = uuid.uuid4()
    await log_event(
        db, test_site.id, "competitor_discovery", "started", "go", run_id=run,
    )
    rows = await _events(db, test_site.id)
    assert len(rows) == 1
    assert rows[0].run_id == run


async def test_log_event_run_id_optional(db, test_site: Site):
    """Backward compat: calls without run_id still work and store NULL."""
    await log_event(db, test_site.id, "stage", "started", "legacy")
    rows = await _events(db, test_site.id)
    assert len(rows) == 1
    assert rows[0].run_id is None


async def test_two_runs_events_isolate_by_run_id(db, test_site: Site):
    """Two pipelines fired back-to-back — events split cleanly by run_id."""
    run_a = uuid.uuid4()
    run_b = uuid.uuid4()
    await log_event(db, test_site.id, "pipeline", "started", "A", run_id=run_a)
    await log_event(
        db, test_site.id, "competitor_discovery", "done", "A done", run_id=run_a,
    )
    await log_event(db, test_site.id, "pipeline", "started", "B", run_id=run_b)

    assert len(await _events(db, test_site.id, run_id=run_a)) == 2
    assert len(await _events(db, test_site.id, run_id=run_b)) == 1


async def test_emit_terminal_closes_only_matching_run(db, test_site: Site):
    """emit_terminal with run_id=A must close only A's pipeline, not B's."""
    run_a = uuid.uuid4()
    run_b = uuid.uuid4()
    await log_event(db, test_site.id, "pipeline", "started", "A", run_id=run_a)
    await log_event(db, test_site.id, "pipeline", "started", "B", run_id=run_b)

    # Close A only
    await emit_terminal(
        db, test_site.id, "opportunities", "done", "A done", run_id=run_a,
    )

    pipe_a = await _events(db, test_site.id, run_id=run_a, stage="pipeline")
    pipe_b = await _events(db, test_site.id, run_id=run_b, stage="pipeline")
    assert [e.status for e in pipe_a] == ["started", "done"]
    assert [e.status for e in pipe_b] == ["started"]  # B still open


async def test_emit_terminal_without_run_id_falls_back_to_timewindow(
    db, test_site: Site,
):
    """Legacy path: if caller passes no run_id, behavior matches old
    time-window lookup so ad-hoc buttons still close the pipeline."""
    await log_event(db, test_site.id, "pipeline", "started", "legacy trigger")
    # No run_id threaded through
    await emit_terminal(
        db, test_site.id, "competitor_discovery", "skipped", "legacy skip",
    )
    pipe = await _events(db, test_site.id, stage="pipeline")
    assert [e.status for e in pipe] == ["started", "skipped"]
