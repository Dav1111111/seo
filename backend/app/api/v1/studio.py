"""Studio API surface — module-scoped, isolated from old `/sites/{id}/...` routes.

Mounted under `/api/v1/admin/studio/...` because the frontend goes through
the Next.js admin-proxy (`/admin-proxy/<path>` → `/api/v1/admin/<path>`),
which holds the admin key in server env. The `/studio/` segment keeps
us namespaced away from legacy admin routes per CONCEPT.md §2.6.

Modules served from this router (one section per Studio module):

  /admin/studio/sites/{site_id}/queries              — PR-S2 (this PR)
  /admin/studio/sites/{site_id}/queries/discover     — PR-S2 (this PR)
  /admin/studio/sites/{site_id}/queries/wordstat-refresh — PR-S2 (this PR)
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


router = APIRouter(prefix="/admin/studio")


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


@router.post(
    "/sites/{site_id}/queries/wordstat-discover",
    response_model=TriggerResponse,
    dependencies=[Depends(_require_admin)],
)
async def trigger_wordstat_discover(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> TriggerResponse:
    """Discover new search phrases through Wordstat `/topRequests` —
    the «что ищут со словом X» semantic expansion that manual
    wordstat.yandex.ru shows.

    For each `service × geo_primary` pair from the site's `target_config`
    we ask Wordstat what people search around that combination, and
    upsert the results into `search_queries`. Distinct stage from the
    Cartesian-based `discover` (demand_map) so the user can use either,
    or both, and tell from the activity feed which fed which phrases.
    """
    await _site_or_404(db, site_id)

    recent = await _recent_started_event(db, site_id, "wordstat_discover")
    if recent is not None:
        return TriggerResponse(
            status="deduped",
            task_id=None,
            run_id=str(recent.run_id) if recent.run_id else "",
            deduped=True,
        )

    from app.collectors.tasks import wordstat_discover_site

    run_id = str(uuid.uuid4())
    task = wordstat_discover_site.delay(str(site_id), run_id=run_id)
    return TriggerResponse(status="queued", task_id=task.id, run_id=run_id)


# ── Module: Indexation (PR-S3) ────────────────────────────────────────

class IndexedPage(BaseModel):
    url: str
    title: str
    position: int


class IndexationDiagnosis(BaseModel):
    verdict: str           # human-readable cause: "robots.txt блокирует весь сайт"
    cause_ru: str          # plain-Russian explanation
    action_ru: str         # specific next step
    severity: str          # "critical" | "high" | "medium" | "low"


class IndexationState(BaseModel):
    site_id: str
    domain: str
    last_check_at: datetime | None
    status: str            # "fresh" | "stale_7d+" | "never_checked" | "running" | "failed"
    pages_found: int | None
    pages: list[IndexedPage]
    diagnosis: IndexationDiagnosis | None
    is_running: bool
    error: str | None


# 7-day window: indexation rarely shifts faster than that, so anything
# older shows up as "устарело" with a CTA to re-check. Critical issues
# (blocked robots.txt, soft-404) get fixed and stay fixed; the value
# of a daily re-check is low compared to the API budget.
INDEXATION_STALE_AFTER_DAYS = 7


@router.get(
    "/sites/{site_id}/indexation",
    response_model=IndexationState,
    dependencies=[Depends(_require_admin)],
)
async def get_indexation(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> IndexationState:
    """Read latest indexation state from the activity feed.

    The truth lives in `analysis_events` (latest event with
    `stage="indexation"`). Both `studio_indexation_run` (this PR's task)
    and the pipeline-internal `check_site_indexation` write to the same
    stage so re-checks initiated through other entry points are visible
    here too.
    """
    site = await _site_or_404(db, site_id)

    # Latest indexation event — terminal OR started.
    result = await db.execute(
        select(AnalysisEvent)
        .where(
            AnalysisEvent.site_id == site_id,
            AnalysisEvent.stage == "indexation",
        )
        .order_by(desc(AnalysisEvent.ts))
        .limit(1),
    )
    latest = result.scalar_one_or_none()

    if latest is None:
        return IndexationState(
            site_id=str(site_id),
            domain=site.domain,
            last_check_at=None,
            status="never_checked",
            pages_found=None,
            pages=[],
            diagnosis=None,
            is_running=False,
            error=None,
        )

    is_running = latest.status == "started"
    if is_running:
        # Started without terminal yet — show "идёт проверка" state.
        return IndexationState(
            site_id=str(site_id),
            domain=site.domain,
            last_check_at=latest.ts,
            status="running",
            pages_found=None,
            pages=[],
            diagnosis=None,
            is_running=True,
            error=None,
        )

    extra = latest.extra or {}
    pages_found = extra.get("pages_found")
    raw_pages = extra.get("pages") or []
    pages = [
        IndexedPage(
            url=p.get("url", ""),
            title=p.get("title", ""),
            position=int(p.get("position", 0)),
        )
        for p in raw_pages
        if isinstance(p, dict) and p.get("url")
    ]

    raw_diag = extra.get("diagnosis")
    diagnosis = None
    if isinstance(raw_diag, dict) and raw_diag.get("verdict"):
        diagnosis = IndexationDiagnosis(
            verdict=raw_diag.get("verdict", ""),
            cause_ru=raw_diag.get("cause_ru", ""),
            action_ru=raw_diag.get("action_ru", ""),
            severity=raw_diag.get("severity", "medium"),
        )

    error = extra.get("error") if latest.status == "failed" else None

    if latest.status == "failed":
        status = "failed"
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=INDEXATION_STALE_AFTER_DAYS)
        status = "fresh" if latest.ts >= cutoff else "stale_7d+"

    return IndexationState(
        site_id=str(site_id),
        domain=site.domain,
        last_check_at=latest.ts,
        status=status,
        pages_found=pages_found,
        pages=pages,
        diagnosis=diagnosis,
        is_running=False,
        error=error,
    )


@router.post(
    "/sites/{site_id}/indexation/check",
    response_model=TriggerResponse,
    dependencies=[Depends(_require_admin)],
)
async def trigger_indexation_check(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> TriggerResponse:
    """Trigger a fresh indexation probe + auto-diagnosis if coverage
    is low. Runs the same logic as `playground.indexation` scenario
    but condensed to one task → one verdict.
    """
    await _site_or_404(db, site_id)

    recent = await _recent_started_event(db, site_id, "indexation")
    if recent is not None:
        return TriggerResponse(
            status="deduped",
            task_id=None,
            run_id=str(recent.run_id) if recent.run_id else "",
            deduped=True,
        )

    from app.collectors.tasks import studio_indexation_run

    run_id = str(uuid.uuid4())
    task = studio_indexation_run.delay(str(site_id), run_id=run_id)
    return TriggerResponse(status="queued", task_id=task.id, run_id=run_id)


__all__ = ["router"]
