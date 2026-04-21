"""Activity feed API — a live stream of what Celery is doing.

GET /sites/{site_id}/activity            → last 20 events
GET /sites/{site_id}/activity/last       → per-stage latest event
                                           (for "last updated" badges)
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.analysis_event import AnalysisEvent

router = APIRouter()


def _serialize(ev: AnalysisEvent) -> dict[str, Any]:
    return {
        "id": ev.id,
        "stage": ev.stage,
        "status": ev.status,
        "message": ev.message,
        "ts": ev.ts.isoformat() if ev.ts else None,
        "extra": ev.extra or {},
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
