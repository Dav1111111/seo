"""Studio API surface — module-scoped, isolated from old `/sites/{id}/...` routes.

All endpoints are namespaced under `/studio/...` so they never collide
with legacy routes per CONCEPT.md §2.6, and so frontend SWR keys (also
prefixed `studio:`, see `frontend/lib/studio-keys.ts`) can't share
cache state across the old/new boundary.

Modules served from this router (one section per Studio module):

  /studio/sites/{site_id}/queries              — PR-S2 (this PR)
  /studio/sites/{site_id}/queries/discover     — PR-S2 (this PR)
  /studio/sites/{site_id}/queries/wordstat-refresh — PR-S2 (this PR)
  ... future PRs add: /indexation, /pages, /competitors, /analytics,
      /ads, /outcomes — each in its own section below.

Auth model (CONCEPT.md §2): single-tenant with admin-key gate. Same
header `_require_admin` already used by `app/api/v1/admin_ops.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.analysis_event import AnalysisEvent
from app.models.daily_metric import DailyMetric
from app.models.search_query import SearchQuery
from app.models.site import Site


router = APIRouter(prefix="/studio")


# ── Auth ──────────────────────────────────────────────────────────────

def _require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    """Same gate as admin_ops — single source of truth would be nicer
    but introducing a shared module just for two callers is more
    indirection than benefit at this size."""
    configured = settings.ADMIN_API_KEY or ""
    if not configured:
        raise HTTPException(status_code=401, detail="admin api disabled")
    if not x_admin_key or x_admin_key != configured:
        raise HTTPException(status_code=401, detail="invalid admin key")


# ── Helpers ───────────────────────────────────────────────────────────

# Stale = wordstat_updated_at older than this threshold. Wordstat
# data refreshes monthly, so 30 days is the natural staleness cliff.
WORDSTAT_STALE_AFTER_DAYS = 30


def _wordstat_status(updated_at: datetime | None, has_volume: bool) -> str:
    """Three-state freshness label so the UI can pick its own copy.

    See CONCEPT.md §5: empty cards must explain WHY they're empty —
    we never return just `wordstat_volume: null` without context.
    """
    if updated_at is None:
        return "never_fetched"
    age = datetime.now(timezone.utc) - updated_at
    if age > timedelta(days=WORDSTAT_STALE_AFTER_DAYS):
        return "stale_30d+"
    if not has_volume:
        # We tried recently but Wordstat had no data for this phrase
        return "fetch_returned_empty"
    return "fresh"


async def _site_or_404(db: AsyncSession, site_id: uuid.UUID) -> Site:
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")
    return site


# ── PR-S2 · Queries module ────────────────────────────────────────────

class QueryRow(BaseModel):
    """One row of the Studio /queries table.

    Shape is stable across the module — pinned in CONCEPT.md §3 (per-
    module endpoint contract). If a new field is needed, prefer adding
    optional rather than changing existing.
    """
    query_id: uuid.UUID
    query_text: str
    is_branded: bool
    cluster: str | None
    wordstat_volume: int | None
    wordstat_status: str
    wordstat_updated_at: datetime | None
    wordstat_trend: list[dict] | None
    last_position: float | None
    last_impressions_14d: int | None
    last_seen_at: datetime | None


class QueriesResponse(BaseModel):
    site_id: uuid.UUID
    total: int
    items: list[QueryRow]
    # Module-level summary so UI can render a header line without
    # iterating items:
    coverage: dict[str, int]   # {"with_volume": N, "without_volume": M, ...}


@router.get(
    "/sites/{site_id}/queries",
    response_model=QueriesResponse,
    dependencies=[Depends(_require_admin)],
)
async def list_queries(
    site_id: uuid.UUID,
    limit: int = Query(default=200, ge=1, le=1000),
    sort: str = Query(default="volume", pattern="^(volume|recent|alpha|position)$"),
    db: AsyncSession = Depends(get_db),
) -> QueriesResponse:
    """List a site's queries with Wordstat volume + last known position.

    Sort modes:
      volume    — wordstat_volume desc, NULL last (default — most actionable on top)
      recent    — last_seen_at desc (recently active in Webmaster)
      alpha     — query_text asc (predictable for UI)
      position  — best position asc (closest to top first)
    """
    await _site_or_404(db, site_id)

    base = select(SearchQuery).where(SearchQuery.site_id == site_id)
    if sort == "volume":
        base = base.order_by(SearchQuery.wordstat_volume.desc().nulls_last())
    elif sort == "recent":
        base = base.order_by(SearchQuery.last_seen_at.desc().nulls_last())
    elif sort == "alpha":
        base = base.order_by(SearchQuery.query_text.asc())
    # `position` is sorted Python-side because positions live in
    # daily_metrics, not on SearchQuery itself
    base = base.limit(limit)

    rows = (await db.execute(base)).scalars().all()
    if not rows:
        return QueriesResponse(
            site_id=site_id,
            total=0,
            items=[],
            coverage={"total": 0, "with_volume": 0, "without_volume": 0, "stale": 0},
        )

    # One round-trip to grab the latest position per query, scoped to
    # this site. We pull the most recent `query_performance` row per
    # dimension_id and join in Python rather than do a per-row
    # subquery in SQL — N is small (<1000 typically) and the join is
    # trivial in memory.
    metrics_q = (
        select(DailyMetric)
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
        )
        .order_by(desc(DailyMetric.date))
    )
    metric_rows = (await db.execute(metrics_q)).scalars().all()

    latest_metric_by_qid: dict[uuid.UUID, DailyMetric] = {}
    impressions_14d_window_start = (datetime.now(timezone.utc) - timedelta(days=14)).date()
    impressions_14d_by_qid: dict[uuid.UUID, int] = {}
    for m in metric_rows:
        if m.dimension_id is None:
            continue
        # First sighting per query == latest because rows are sorted desc
        if m.dimension_id not in latest_metric_by_qid:
            latest_metric_by_qid[m.dimension_id] = m
        if m.date >= impressions_14d_window_start:
            impressions_14d_by_qid[m.dimension_id] = (
                impressions_14d_by_qid.get(m.dimension_id, 0) + (m.impressions or 0)
            )

    items: list[QueryRow] = []
    coverage = {"total": 0, "with_volume": 0, "without_volume": 0, "stale": 0}

    for q in rows:
        coverage["total"] += 1
        has_volume = q.wordstat_volume is not None and q.wordstat_volume > 0
        status = _wordstat_status(q.wordstat_updated_at, has_volume)
        if has_volume:
            coverage["with_volume"] += 1
        else:
            coverage["without_volume"] += 1
        if status == "stale_30d+":
            coverage["stale"] += 1

        m = latest_metric_by_qid.get(q.id)
        last_position = float(m.avg_position) if m and m.avg_position is not None else None

        # `wordstat_trend` is JSONB. SQLAlchemy hydrates as Python
        # types, but the column allows a free-form dict — coerce to
        # the list shape we wrote in fetch_volume() to keep the API
        # type stable. Anything unexpected becomes None so the UI
        # doesn't crash.
        trend_raw = q.wordstat_trend
        trend: list[dict] | None
        if isinstance(trend_raw, list):
            trend = [t for t in trend_raw if isinstance(t, dict)]
        else:
            trend = None

        items.append(
            QueryRow(
                query_id=q.id,
                query_text=q.query_text,
                is_branded=q.is_branded,
                cluster=q.cluster,
                wordstat_volume=q.wordstat_volume,
                wordstat_status=status,
                wordstat_updated_at=q.wordstat_updated_at,
                wordstat_trend=trend,
                last_position=last_position,
                last_impressions_14d=impressions_14d_by_qid.get(q.id),
                last_seen_at=q.last_seen_at,
            )
        )

    if sort == "position":
        # Python-side sort: lower position = better. Queries without a
        # known position go to the bottom.
        items.sort(key=lambda r: (r.last_position is None, r.last_position or 0))

    return QueriesResponse(
        site_id=site_id,
        total=coverage["total"],
        items=items,
        coverage=coverage,
    )


# ── Trigger endpoints ─────────────────────────────────────────────────

# Idempotency window: don't re-queue the same module's task more than
# once per 60 seconds per site. Owner double-clicking should not burn
# the daily Wordstat / API quota twice.
TRIGGER_DEDUP_WINDOW_SEC = 60


class TriggerResponse(BaseModel):
    status: str         # "queued" | "deduped"
    task_id: str | None
    run_id: str
    deduped: bool = False


async def _recent_started_event(
    db: AsyncSession, site_id: uuid.UUID, stage: str,
) -> AnalysisEvent | None:
    """Find a `<stage>:started` event for this site within the dedup
    window. If present, second trigger reuses its run_id instead of
    queueing a duplicate task — same pattern as admin_ops.trigger_full_pipeline.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=TRIGGER_DEDUP_WINDOW_SEC)
    result = await db.execute(
        select(AnalysisEvent)
        .where(
            AnalysisEvent.site_id == site_id,
            AnalysisEvent.stage == stage,
            AnalysisEvent.status == "started",
            AnalysisEvent.ts >= cutoff,
        )
        .order_by(desc(AnalysisEvent.ts))
        .limit(1),
    )
    return result.scalar_one_or_none()


@router.post(
    "/sites/{site_id}/queries/discover",
    response_model=TriggerResponse,
    dependencies=[Depends(_require_admin)],
)
async def trigger_discover(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> TriggerResponse:
    """Find new queries via demand_map. Reuses the existing
    `demand_map_build_site_task` — Studio is a UI, not a duplicate
    pipeline (CONCEPT.md §2.2: independence + reuse)."""
    await _site_or_404(db, site_id)

    recent = await _recent_started_event(db, site_id, "demand_map")
    if recent is not None:
        return TriggerResponse(
            status="deduped",
            task_id=None,
            run_id=str(recent.run_id) if recent.run_id else "",
            deduped=True,
        )

    from app.core_audit.demand_map.tasks import demand_map_build_site_task

    run_id = str(uuid.uuid4())
    task = demand_map_build_site_task.delay(str(site_id), run_id=run_id)
    return TriggerResponse(status="queued", task_id=task.id, run_id=run_id)


@router.post(
    "/sites/{site_id}/queries/wordstat-refresh",
    response_model=TriggerResponse,
    dependencies=[Depends(_require_admin)],
)
async def trigger_wordstat_refresh(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> TriggerResponse:
    """Refresh Wordstat volumes for all queries of this site.

    Long-running (1 query/sec): a 200-query site = ~3.5 min wall time.
    Owner sees progress through the activity stream (`stage="wordstat"`
    started/done events), not through this endpoint blocking.
    """
    await _site_or_404(db, site_id)

    recent = await _recent_started_event(db, site_id, "wordstat")
    if recent is not None:
        return TriggerResponse(
            status="deduped",
            task_id=None,
            run_id=str(recent.run_id) if recent.run_id else "",
            deduped=True,
        )

    from app.collectors.tasks import wordstat_refresh_site

    run_id = str(uuid.uuid4())
    task = wordstat_refresh_site.delay(str(site_id), run_id=run_id)
    return TriggerResponse(status="queued", task_id=task.id, run_id=run_id)


__all__ = ["router"]
