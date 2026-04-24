"""Activity feed API — a live stream of what Celery is doing.

GET /sites/{site_id}/activity               → last N events (history)
GET /sites/{site_id}/activity/last          → per-stage latest event
                                              (for "last updated" badges)
GET /sites/{site_id}/activity/current-run   → only events of the latest
                                              pipeline run; lets the UI
                                              show a single run cleanly
                                              without mixing two clicks.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core_audit.activity import reconcile_open_pipelines
from app.models.analysis_event import AnalysisEvent

router = APIRouter()


def _serialize(ev: AnalysisEvent) -> dict[str, Any]:
    # ts is stored naive UTC in Postgres (datetime.utcnow). Append "Z"
    # so JS clients interpret it as UTC instead of local time.
    return {
        "id": ev.id,
        "stage": ev.stage,
        "status": ev.status,
        "message": ev.message,
        "ts": (ev.ts.isoformat() + "Z") if ev.ts else None,
        "extra": ev.extra or {},
        "run_id": str(ev.run_id) if ev.run_id else None,
    }


@router.get("/sites/{site_id}/activity")
async def get_activity_feed(
    site_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return last N events, newest first.

    Owners open the dashboard and see "платформа собрала SERP… нашла 7
    конкурентов… готово, 15 точек роста" — proof the system is alive.
    """
    await reconcile_open_pipelines(db, site_id)
    stmt = (
        select(AnalysisEvent)
        .where(AnalysisEvent.site_id == site_id)
        .order_by(desc(AnalysisEvent.ts))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {"events": [_serialize(ev) for ev in rows]}


@router.get("/sites/{site_id}/activity/last")
async def get_last_per_stage(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the most recent event per stage — drives "last updated X ago"
    badges on dashboard/competitors/reports pages."""
    await reconcile_open_pipelines(db, site_id)
    # Fetch more than needed and dedupe per-stage in Python (avoids a
    # PG-specific DISTINCT ON when the set is small).
    stmt = (
        select(AnalysisEvent)
        .where(AnalysisEvent.site_id == site_id)
        .order_by(desc(AnalysisEvent.ts))
        .limit(200)
    )
    rows = (await db.execute(stmt)).scalars().all()
    by_stage: dict[str, dict[str, Any]] = {}
    for ev in rows:
        if ev.stage in by_stage:
            continue
        by_stage[ev.stage] = _serialize(ev)
    return {"by_stage": by_stage}


@router.get("/sites/{site_id}/activity/current-run")
async def get_current_run(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Events of the latest pipeline run only — so two back-to-back
    clicks don't merge in the UI.

    Definition of "current run":
      - If the newest event has run_id → return all events with that
        run_id (pipeline-driven).
      - If newest has run_id=None → return events with run_id=NULL
        from the last 5 minutes (standalone button click).
      - Empty feed → {"events": [], "run_id": None}.
    """
    await reconcile_open_pipelines(db, site_id)
    newest = (await db.execute(
        select(AnalysisEvent)
        .where(AnalysisEvent.site_id == site_id)
        .order_by(desc(AnalysisEvent.ts))
        .limit(1)
    )).scalar_one_or_none()
    if newest is None:
        return {"events": [], "run_id": None}

    stmt = (
        select(AnalysisEvent)
        .where(AnalysisEvent.site_id == site_id)
        .order_by(desc(AnalysisEvent.ts))
        .limit(50)
    )
    if newest.run_id is not None:
        stmt = stmt.where(AnalysisEvent.run_id == newest.run_id)
    else:
        from datetime import datetime, timedelta
        cutoff = (newest.ts or datetime.utcnow()) - timedelta(minutes=5)
        stmt = stmt.where(
            AnalysisEvent.run_id.is_(None),
            AnalysisEvent.ts >= cutoff,
        )
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "run_id": str(newest.run_id) if newest.run_id else None,
        "events": [_serialize(ev) for ev in rows],
    }
