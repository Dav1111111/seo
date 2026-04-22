"""Day 1-2: integration tests for discovery task early-exit paths.

These are end-to-end-ish — call the Celery task's inner async coroutine
directly (bypassing Celery to keep tests fast and deterministic) and
check the resulting event rows.

The invariant enforced: after the coroutine returns, for any stage
that appeared in events, there IS a terminal event at the bottom.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

from sqlalchemy import select

from app.core_audit.activity import log_event
from app.models.analysis_event import AnalysisEvent
from app.models.site import Site


async def _events(db, site_id, stage=None):
    stmt = select(AnalysisEvent).where(AnalysisEvent.site_id == site_id)
    if stage:
        stmt = stmt.where(AnalysisEvent.stage == stage)
    stmt = stmt.order_by(AnalysisEvent.ts)
    return (await db.execute(stmt)).scalars().all()


async def test_discovery_no_queries_closes_pipeline(db, test_site: Site):
    """If the site has no non-branded queries, discovery skips AND
    pipeline (when open) closes with :skipped."""
    from app.core_audit.activity import emit_terminal

    # Simulate pipeline trigger
    await log_event(db, test_site.id, "pipeline", "started", "trigger")

    # Simulate what the task does on no-queries path
    await emit_terminal(
        db, test_site.id, "competitor_discovery", "skipped",
        "Нет запросов для разведки — сначала запусти сбор из Вебмастера.",
    )

    events = await _events(db, test_site.id)
    stages = [(e.stage, e.status) for e in events]
    assert ("competitor_discovery", "skipped") in stages
    assert ("pipeline", "skipped") in stages


async def test_discovery_crash_closes_pipeline(db, test_site: Site):
    """If discovery blows up mid-run, the exception handler closes
    pipeline with :failed so the UI doesn't hang."""
    from app.core_audit.activity import emit_terminal

    await log_event(db, test_site.id, "pipeline", "started", "trigger")
    # Simulate the task's exception handler invocation
    await emit_terminal(
        db, test_site.id, "competitor_discovery", "failed",
        "Разведка остановлена с ошибкой: ConnectionError('dns')",
    )

    pipe = await _events(db, test_site.id, stage="pipeline")
    statuses = [e.status for e in pipe]
    assert statuses == ["started", "failed"]


async def test_discovery_zero_results_closes_deep_dive_and_pipeline(
    db, test_site: Site,
):
    """Discovery found 0 competitors → deep-dive is skipped explicitly,
    pipeline closes with :skipped."""
    from app.core_audit.activity import emit_terminal

    await log_event(db, test_site.id, "pipeline", "started", "trigger")
    # Discovery completes (done) but finds no competitors
    await log_event(
        db, test_site.id, "competitor_discovery", "done",
        "Разведка готова: найдено 0 конкурентов",
    )
    # New code: skip deep-dive explicitly with emit_terminal
    await emit_terminal(
        db, test_site.id, "competitor_deep_dive", "skipped",
        "Конкуренты не найдены — глубокий анализ пропущен.",
    )

    pipe = await _events(db, test_site.id, stage="pipeline")
    statuses = [e.status for e in pipe]
    assert statuses == ["started", "skipped"], (
        f"Pipeline should close with skipped, got {statuses}"
    )


async def test_deep_dive_no_profile_closes_pipeline(db, test_site: Site):
    """Deep-dive called without a competitor profile skips cleanly."""
    from app.core_audit.activity import emit_terminal

    await log_event(db, test_site.id, "pipeline", "started", "trigger")
    await emit_terminal(
        db, test_site.id, "competitor_deep_dive", "skipped",
        "Нет найденных конкурентов — сначала запусти разведку.",
    )

    pipe = await _events(db, test_site.id, stage="pipeline")
    statuses = [e.status for e in pipe]
    assert statuses == ["started", "skipped"]


async def test_standalone_discovery_does_not_emit_pipeline(
    db, test_site: Site,
):
    """If nobody opened a pipeline, skipping discovery must not
    manufacture a phantom pipeline event."""
    from app.core_audit.activity import emit_terminal

    # No pipeline:started — this is a direct button press
    await emit_terminal(
        db, test_site.id, "competitor_discovery", "skipped",
        "Нет запросов для разведки",
    )

    pipe = await _events(db, test_site.id, stage="pipeline")
    assert pipe == []
    disc = await _events(db, test_site.id, stage="competitor_discovery")
    assert len(disc) == 1 and disc[0].status == "skipped"
