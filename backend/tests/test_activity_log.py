"""Canonical test — the activity event helper actually persists events.

Serves as the test-harness smoke check. If this fails, the conftest
fixtures are broken before we look at any business logic.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core_audit.activity import log_event
from app.models.analysis_event import AnalysisEvent
from app.models.site import Site


async def test_log_event_persists_to_db(db, test_site: Site):
    """log_event writes a row readable back in the same session."""
    await log_event(
        db, test_site.id, "competitor_discovery", "started",
        "Harness smoke check",
        extra={"queries_count": 7},
    )

    rows = (await db.execute(
        select(AnalysisEvent).where(AnalysisEvent.site_id == test_site.id)
    )).scalars().all()

    assert len(rows) == 1
    ev = rows[0]
    assert ev.stage == "competitor_discovery"
    assert ev.status == "started"
    assert ev.message == "Harness smoke check"
    assert ev.extra == {"queries_count": 7}
    assert ev.ts is not None


async def test_log_event_swallows_errors(db, test_site: Site):
    """log_event is best-effort — a malformed stage str must not crash."""
    # Stage is a VARCHAR(50); passing a 5000-char string would blow up
    # at the DB boundary, but log_event catches and logs. Caller
    # keeps running.
    huge_stage = "x" * 5000
    await log_event(db, test_site.id, huge_stage, "started", "never written")
    # Session stays usable after swallowed failure
    count = (await db.execute(
        select(AnalysisEvent).where(AnalysisEvent.site_id == test_site.id)
    )).scalars().all()
    assert count == []  # nothing persisted, but test didn't raise
