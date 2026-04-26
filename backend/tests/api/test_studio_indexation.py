"""Studio /indexation endpoint — IMPLEMENTATION.md §3.3 contract.

Pins the never_checked / fresh / stale / running / failed status
decoder. The endpoint reads from `analysis_events` so we seed events
directly rather than running the underlying task.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.studio import (
    INDEXATION_STALE_AFTER_DAYS,
    get_indexation,
)
from app.models.analysis_event import AnalysisEvent
from app.models.site import Site


pytestmark = pytest.mark.asyncio


async def test_get_indexation_never_checked(
    db: AsyncSession, test_site: Site,
) -> None:
    """No event for the site → status is `never_checked`, no diagnosis,
    not running. UI uses this to render the empty-CTA state."""
    state = await get_indexation(site_id=test_site.id, db=db)
    assert state.status == "never_checked"
    assert state.last_check_at is None
    assert state.pages_found is None
    assert state.pages == []
    assert state.diagnosis is None
    assert state.is_running is False
    assert state.error is None


async def test_get_indexation_404_for_unknown_site(db: AsyncSession) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await get_indexation(site_id=uuid.uuid4(), db=db)
    assert exc.value.status_code == 404


async def test_get_indexation_running_when_started_event(
    db: AsyncSession, test_site: Site,
) -> None:
    """`started` without a terminal yet → running state (no pages)."""
    db.add(AnalysisEvent(
        site_id=test_site.id,
        stage="indexation",
        status="started",
        message="Проверяю индексацию…",
        run_id=uuid.uuid4(),
        ts=datetime.now(timezone.utc),
    ))
    await db.flush()

    state = await get_indexation(site_id=test_site.id, db=db)
    assert state.status == "running"
    assert state.is_running is True
    assert state.pages_found is None


async def test_get_indexation_fresh_decodes_extra(
    db: AsyncSession, test_site: Site,
) -> None:
    """A done event within stale-window → `fresh`, pages list parsed
    from extra, diagnosis carried through."""
    db.add(AnalysisEvent(
        site_id=test_site.id,
        stage="indexation",
        status="done",
        message="Индексация проверена",
        run_id=uuid.uuid4(),
        ts=datetime.now(timezone.utc) - timedelta(hours=1),
        extra={
            "pages_found": 2,
            "pages": [
                {"url": "https://x/y", "title": "Y", "position": 1},
                {"url": "https://x/z", "title": "Z", "position": 2},
            ],
            "diagnosis": {
                "verdict": "robots.txt OK",
                "cause_ru": "no issues",
                "action_ru": "nothing to do",
                "severity": "low",
            },
        },
    ))
    await db.flush()

    state = await get_indexation(site_id=test_site.id, db=db)
    assert state.status == "fresh"
    assert state.pages_found == 2
    assert len(state.pages) == 2
    assert state.diagnosis is not None
    assert state.diagnosis.verdict == "robots.txt OK"


async def test_get_indexation_stale_after_threshold(
    db: AsyncSession, test_site: Site,
) -> None:
    """A done event older than INDEXATION_STALE_AFTER_DAYS is `stale_7d+`.
    Pins the threshold so a silent change in the constant trips a test."""
    old_ts = (
        datetime.now(timezone.utc)
        - timedelta(days=INDEXATION_STALE_AFTER_DAYS + 1)
    )
    db.add(AnalysisEvent(
        site_id=test_site.id,
        stage="indexation",
        status="done",
        message="Индексация проверена давно",
        run_id=uuid.uuid4(),
        ts=old_ts,
        extra={"pages_found": 5, "pages": []},
    ))
    await db.flush()

    state = await get_indexation(site_id=test_site.id, db=db)
    assert state.status == "stale_7d+"


async def test_get_indexation_failed_carries_error(
    db: AsyncSession, test_site: Site,
) -> None:
    db.add(AnalysisEvent(
        site_id=test_site.id,
        stage="indexation",
        status="failed",
        message="Проверка индексации упала",
        run_id=uuid.uuid4(),
        ts=datetime.now(timezone.utc),
        extra={"error": "Yandex API timeout"},
    ))
    await db.flush()

    state = await get_indexation(site_id=test_site.id, db=db)
    assert state.status == "failed"
    assert state.error == "Yandex API timeout"
