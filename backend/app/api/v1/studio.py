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
from app.models.outcome_snapshot import OutcomeSnapshot
from app.models.page import Page
from app.models.site import Site
from app.core_audit.review.models import PageReview, PageReviewRecommendation
from sqlalchemy import func as sa_func


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


# ── Module: Pages (PR-S4) ─────────────────────────────────────────────
#
# /studio/pages and /studio/pages/{id} are the workspace where owner
# sees one page's content review, recommendations, applied history and
# (eventually) per-page positions. v1 surfaces the data we already have:
# Page row, latest PageReview, recommendations list, OutcomeSnapshots
# filtered by page_url.
#
# What's deliberately deferred (CONCEPT.md §5: empty cards must explain
# WHY rather than ship half-baked):
#   - position graph per query for the page (no page↔query link table —
#     the right place is to add one in PR-S5 competitors work)
#   - content versioning / true before-after (we only store last crawl)
#   - per-page outcome baseline (current OutcomeSnapshot._baseline_metrics
#     is site-wide; converting it is its own scope)
# Cross-links to /studio/competitors are rendered as <DisabledLink> per
# IMPLEMENTATION.md §2.1 until PR-S5 ships.


class PageListItem(BaseModel):
    """Compact card payload for the /studio/pages list."""
    page_id: str
    url: str
    path: str
    title: str | None
    in_index: bool
    in_sitemap: bool
    http_status: int | None
    last_crawled_at: datetime | None
    has_review: bool
    last_reviewed_at: datetime | None
    n_recommendations: int       # total recs on latest review
    n_pending: int               # recs still in user_status="pending"
    n_applied: int               # recs in user_status="applied"


class PageListResponse(BaseModel):
    site_id: str
    total: int
    items: list[PageListItem]


PAGES_LIST_DEFAULT_LIMIT = 100
PAGES_LIST_MAX_LIMIT = 500


@router.get(
    "/sites/{site_id}/pages",
    response_model=PageListResponse,
    dependencies=[Depends(_require_admin)],
)
async def list_pages(
    site_id: uuid.UUID,
    sort: str = Query("recent_review", pattern="^(recent_review|crawl|alpha|recs)$"),
    limit: int = Query(PAGES_LIST_DEFAULT_LIMIT, ge=1, le=PAGES_LIST_MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
) -> PageListResponse:
    """List pages with review/recommendation summary.

    sort:
      recent_review — pages with the freshest review on top, then those
                      without a review at all. Default — owner usually
                      wants to act on what was just analysed.
      crawl         — by last_crawled_at desc (what's freshest from the
                      crawler regardless of review status).
      alpha         — by url alphabetical (auditable scan).
      recs          — by total recommendation count desc (where the
                      work is concentrated).
    """
    site = await _site_or_404(db, site_id)

    pages = (await db.execute(
        select(Page).where(Page.site_id == site.id),
    )).scalars().all()

    page_ids = [p.id for p in pages]

    # Load the latest review per page in one query: pick the row with
    # max reviewed_at per page_id. SQLAlchemy + Postgres distinct on
    # would be cleaner, but a simple grouping works on current scale
    # (site has <500 pages).
    review_rows = (await db.execute(
        select(PageReview)
        .where(PageReview.site_id == site.id)
        .order_by(desc(PageReview.reviewed_at)),
    )).scalars().all()
    latest_review_by_page: dict[uuid.UUID, PageReview] = {}
    for r in review_rows:
        if r.page_id not in latest_review_by_page:
            latest_review_by_page[r.page_id] = r

    # Recommendation aggregates per review_id.
    rec_counts: dict[uuid.UUID, dict] = {}
    if latest_review_by_page:
        review_ids = [r.id for r in latest_review_by_page.values()]
        rec_rows = (await db.execute(
            select(
                PageReviewRecommendation.review_id,
                PageReviewRecommendation.user_status,
            ).where(PageReviewRecommendation.review_id.in_(review_ids)),
        )).all()
        for review_id, user_status in rec_rows:
            d = rec_counts.setdefault(
                review_id, {"total": 0, "pending": 0, "applied": 0},
            )
            d["total"] += 1
            if user_status == "pending":
                d["pending"] += 1
            elif user_status == "applied":
                d["applied"] += 1

    items: list[PageListItem] = []
    for page in pages:
        review = latest_review_by_page.get(page.id)
        counts = rec_counts.get(review.id) if review else None
        items.append(PageListItem(
            page_id=str(page.id),
            url=page.url,
            path=page.path,
            title=page.title,
            in_index=page.in_index,
            in_sitemap=page.in_sitemap,
            http_status=page.http_status,
            last_crawled_at=page.last_crawled_at,
            has_review=review is not None,
            last_reviewed_at=review.reviewed_at if review else None,
            n_recommendations=counts["total"] if counts else 0,
            n_pending=counts["pending"] if counts else 0,
            n_applied=counts["applied"] if counts else 0,
        ))

    if sort == "recent_review":
        items.sort(
            key=lambda it: (
                it.last_reviewed_at is None,
                # Reverse for recency: negate by using min as fallback
                -(it.last_reviewed_at.timestamp() if it.last_reviewed_at else 0),
            ),
        )
    elif sort == "crawl":
        items.sort(
            key=lambda it: -(
                it.last_crawled_at.timestamp() if it.last_crawled_at else 0
            ),
        )
    elif sort == "alpha":
        items.sort(key=lambda it: it.url.lower())
    elif sort == "recs":
        items.sort(key=lambda it: -it.n_recommendations)

    return PageListResponse(
        site_id=str(site_id),
        total=len(items),
        items=items[:limit],
    )


# ── Page detail (PR-S4) ──────────────────────────────────────────────


class RecommendationOut(BaseModel):
    rec_id: str
    category: str
    priority: str
    user_status: str
    before_text: str | None
    after_text: str | None
    reasoning_ru: str
    priority_score: float | None
    impact_score: float | None
    confidence_score: float | None
    ease_score: float | None


class PageReviewOut(BaseModel):
    review_id: str
    status: str
    skip_reason: str | None
    reviewer_model: str
    reviewed_at: datetime
    cost_usd: float
    page_level_summary: dict | None
    top_queries_snapshot: dict | None
    recommendations: list[RecommendationOut]


class OutcomeOut(BaseModel):
    snapshot_id: str
    recommendation_id: str
    source: str
    applied_at: datetime
    baseline_metrics: dict | None
    followup_at: datetime | None
    followup_metrics: dict | None
    delta: dict | None
    note_ru: str | None


class PageDetail(BaseModel):
    page_id: str
    site_id: str
    url: str
    path: str
    title: str | None
    h1: str | None
    meta_description: str | None
    word_count: int | None
    in_index: bool
    in_sitemap: bool
    http_status: int | None
    has_schema: bool
    last_crawled_at: datetime | None
    review: PageReviewOut | None    # null if site never had its review run
    outcomes: list[OutcomeOut]      # snapshots filtered by page_url
    # Cross-link readiness: frontend uses this to enable/disable links.
    # Source of truth = IMPLEMENTATION.md §1 status table.
    cross_links: dict[str, bool]


@router.get(
    "/sites/{site_id}/pages/{page_id}",
    response_model=PageDetail,
    dependencies=[Depends(_require_admin)],
)
async def get_page_detail(
    site_id: uuid.UUID,
    page_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> PageDetail:
    """Page workspace payload — page + latest review + recs + outcomes."""
    site = await _site_or_404(db, site_id)

    page = (await db.execute(
        select(Page).where(Page.id == page_id, Page.site_id == site.id),
    )).scalar_one_or_none()
    if page is None:
        raise HTTPException(status_code=404, detail="page not found")

    # Latest review row.
    review = (await db.execute(
        select(PageReview)
        .where(PageReview.page_id == page.id)
        .order_by(desc(PageReview.reviewed_at))
        .limit(1),
    )).scalar_one_or_none()

    review_out: PageReviewOut | None = None
    if review is not None:
        rec_rows = (await db.execute(
            select(PageReviewRecommendation)
            .where(PageReviewRecommendation.review_id == review.id)
            .order_by(
                # Pending first, then by priority_score desc.
                PageReviewRecommendation.user_status.asc(),
                desc(PageReviewRecommendation.priority_score),
            ),
        )).scalars().all()

        recommendations = [
            RecommendationOut(
                rec_id=str(r.id),
                category=r.category,
                priority=r.priority,
                user_status=r.user_status,
                before_text=r.before_text,
                after_text=r.after_text,
                reasoning_ru=r.reasoning_ru,
                priority_score=float(r.priority_score) if r.priority_score is not None else None,
                impact_score=float(r.impact_score) if r.impact_score is not None else None,
                confidence_score=float(r.confidence_score) if r.confidence_score is not None else None,
                ease_score=float(r.ease_score) if r.ease_score is not None else None,
            )
            for r in rec_rows
        ]

        review_out = PageReviewOut(
            review_id=str(review.id),
            status=review.status,
            skip_reason=review.skip_reason,
            reviewer_model=review.reviewer_model,
            reviewed_at=review.reviewed_at,
            cost_usd=float(review.cost_usd) if review.cost_usd is not None else 0.0,
            page_level_summary=review.page_level_summary,
            top_queries_snapshot=review.top_queries_snapshot,
            recommendations=recommendations,
        )

    # Outcome snapshots tied to this page via page_url. The current
    # baseline metric is site-wide (see _baseline_metrics in admin_ops),
    # but the snapshot itself is per-recommendation, so per-page filter
    # by URL gives the right list of "what was applied for this page".
    outcome_rows = (await db.execute(
        select(OutcomeSnapshot)
        .where(
            OutcomeSnapshot.site_id == site.id,
            OutcomeSnapshot.page_url == page.url,
        )
        .order_by(desc(OutcomeSnapshot.applied_at)),
    )).scalars().all()

    outcomes = [
        OutcomeOut(
            snapshot_id=str(o.id),
            recommendation_id=o.recommendation_id,
            source=o.source,
            applied_at=o.applied_at,
            baseline_metrics=o.baseline_metrics,
            followup_at=o.followup_at,
            followup_metrics=o.followup_metrics,
            delta=o.delta,
            note_ru=o.note_ru,
        )
        for o in outcome_rows
    ]

    # Cross-link readiness flags. Source of truth: IMPLEMENTATION.md §1.
    # Hardcoded here intentionally — there is no runtime "module
    # registry" table, and the doc-before-merge rule means flipping a
    # status is a doc change + this file change, paired in one PR.
    cross_links = {
        "queries": True,        # PR-S2 ✅
        "indexation": True,     # PR-S3 ✅
        "competitors": True,    # PR-S5 ✅
        "analytics": False,     # PR-S6 ⏳
        "outcomes": False,      # PR-S8 ⏳
    }

    return PageDetail(
        page_id=str(page.id),
        site_id=str(site.id),
        url=page.url,
        path=page.path,
        title=page.title,
        h1=page.h1,
        meta_description=page.meta_description,
        word_count=page.word_count,
        in_index=page.in_index,
        in_sitemap=page.in_sitemap,
        http_status=page.http_status,
        has_schema=page.has_schema,
        last_crawled_at=page.last_crawled_at,
        review=review_out,
        outcomes=outcomes,
        cross_links=cross_links,
    )


# ── Module: Analytics (PR-S6) ─────────────────────────────────────────
#
# /studio/analytics surfaces site-wide trends from both Webmaster
# (search visibility) and Metrica (visitor behaviour). One endpoint
# returns four daily series so the frontend renders all charts from a
# single fetch — no waterfall, no per-chart loading flicker.
#
# Data lag (be honest about it in the UI):
#   - Webmaster query_performance: 5–10 days behind today's date.
#   - Webmaster indexing/search_events: 5–10 days behind.
#   - Metrica site_traffic: 1 day behind (yesterday available).
#
# Aggregation pattern (verified vs dashboard.py): SUM impressions/
# clicks across queries by date, AVG positions by date.


from app.models.daily_metric import DailyMetric


class AnalyticsPoint(BaseModel):
    """One daily data point. Numbers are nullable when the source
    didn't report that day (rendering = gap in the chart).
    """
    date: str                 # ISO yyyy-mm-dd
    impressions: int | None = None
    clicks: int | None = None
    avg_position: float | None = None
    visits: int | None = None
    pageviews: int | None = None
    bounce_rate: float | None = None
    avg_duration_sec: float | None = None
    pages_indexed: int | None = None


class AnalyticsTotals(BaseModel):
    """Plain-number totals to render above each chart, so owner sees
    «за 90 дней: 12 000 показов» without doing math from the chart."""
    impressions_sum: int = 0
    clicks_sum: int = 0
    visits_sum: int = 0
    pageviews_sum: int = 0
    avg_position_mean: float | None = None
    avg_bounce_rate_mean: float | None = None
    indexed_latest: int | None = None
    days_with_search_data: int = 0
    days_with_traffic_data: int = 0


class AnalyticsResponse(BaseModel):
    site_id: str
    days: int
    series: list[AnalyticsPoint]
    totals: AnalyticsTotals
    # Honest lag indicators — UI shows "Webmaster данные с лагом 5 дней"
    webmaster_latest_date: str | None
    metrica_latest_date: str | None


ANALYTICS_DEFAULT_DAYS = 90
ANALYTICS_MAX_DAYS = 365


@router.get(
    "/sites/{site_id}/analytics",
    response_model=AnalyticsResponse,
    dependencies=[Depends(_require_admin)],
)
async def get_analytics(
    site_id: uuid.UUID,
    days: int = Query(ANALYTICS_DEFAULT_DAYS, ge=7, le=ANALYTICS_MAX_DAYS),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsResponse:
    """Daily series for the last `days` days, all four metric_types
    merged into one timeline by date.
    """
    site = await _site_or_404(db, site_id)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()

    # 1. Webmaster query_performance — aggregate across all queries.
    search_rows = (await db.execute(
        select(
            DailyMetric.date,
            sa_func.sum(DailyMetric.impressions).label("impressions"),
            sa_func.sum(DailyMetric.clicks).label("clicks"),
            sa_func.avg(DailyMetric.avg_position).label("avg_position"),
        )
        .where(
            DailyMetric.site_id == site.id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.date >= cutoff,
        )
        .group_by(DailyMetric.date)
        .order_by(DailyMetric.date),
    )).all()

    # 2. Metrica site_traffic — site-wide rows (dimension_id IS NULL).
    traffic_rows = (await db.execute(
        select(
            DailyMetric.date,
            DailyMetric.impressions,    # repurposed: Metrica writes visits here? Check below.
            DailyMetric.clicks,
            DailyMetric.ctr,
            DailyMetric.avg_position,
            DailyMetric.extra,
        )
        .where(
            DailyMetric.site_id == site.id,
            DailyMetric.metric_type == "site_traffic",
            DailyMetric.date >= cutoff,
        )
        .order_by(DailyMetric.date),
    )).all()

    # 3. Webmaster indexing — daily snapshot of pages_indexed.
    indexing_rows = (await db.execute(
        select(
            DailyMetric.date,
            DailyMetric.impressions.label("pages_indexed"),
        )
        .where(
            DailyMetric.site_id == site.id,
            DailyMetric.metric_type == "indexing",
            DailyMetric.date >= cutoff,
        )
        .order_by(DailyMetric.date),
    )).all()

    # Merge by date.
    by_date: dict[str, dict] = {}

    def _ensure(d) -> dict:
        key = d.isoformat()
        if key not in by_date:
            by_date[key] = {"date": key}
        return by_date[key]

    webmaster_latest: str | None = None
    metrica_latest: str | None = None

    for row in search_rows:
        bucket = _ensure(row.date)
        bucket["impressions"] = int(row.impressions or 0)
        bucket["clicks"] = int(row.clicks or 0)
        bucket["avg_position"] = (
            float(row.avg_position) if row.avg_position is not None else None
        )
        webmaster_latest = max(webmaster_latest or "", row.date.isoformat())

    for row in traffic_rows:
        bucket = _ensure(row.date)
        # MetricaCollector packs visits/pageviews/bounce/duration into
        # `extra` JSONB. Fall back to direct columns if extra is empty
        # (defensive for older rows written before that contract).
        extra = row.extra or {}
        bucket["visits"] = int(extra.get("visits") or row.impressions or 0)
        bucket["pageviews"] = int(extra.get("pageviews") or row.clicks or 0)
        if extra.get("bounce_rate") is not None:
            bucket["bounce_rate"] = float(extra["bounce_rate"])
        if extra.get("avg_duration_seconds") is not None:
            bucket["avg_duration_sec"] = float(extra["avg_duration_seconds"])
        metrica_latest = max(metrica_latest or "", row.date.isoformat())

    for row in indexing_rows:
        bucket = _ensure(row.date)
        bucket["pages_indexed"] = int(row.pages_indexed or 0)

    series = [
        AnalyticsPoint(**by_date[k])
        for k in sorted(by_date.keys())
    ]

    # Totals.
    impressions_sum = sum(p.impressions or 0 for p in series)
    clicks_sum = sum(p.clicks or 0 for p in series)
    visits_sum = sum(p.visits or 0 for p in series)
    pageviews_sum = sum(p.pageviews or 0 for p in series)
    pos_values = [p.avg_position for p in series if p.avg_position is not None]
    avg_pos_mean = sum(pos_values) / len(pos_values) if pos_values else None
    bounce_values = [p.bounce_rate for p in series if p.bounce_rate is not None]
    avg_bounce_mean = sum(bounce_values) / len(bounce_values) if bounce_values else None
    indexed_pts = [p.pages_indexed for p in series if p.pages_indexed is not None]
    indexed_latest = indexed_pts[-1] if indexed_pts else None

    days_with_search = sum(1 for p in series if p.impressions is not None)
    days_with_traffic = sum(1 for p in series if p.visits is not None)

    return AnalyticsResponse(
        site_id=str(site_id),
        days=days,
        series=series,
        totals=AnalyticsTotals(
            impressions_sum=impressions_sum,
            clicks_sum=clicks_sum,
            visits_sum=visits_sum,
            pageviews_sum=pageviews_sum,
            avg_position_mean=avg_pos_mean,
            avg_bounce_rate_mean=avg_bounce_mean,
            indexed_latest=indexed_latest,
            days_with_search_data=days_with_search,
            days_with_traffic_data=days_with_traffic,
        ),
        webmaster_latest_date=webmaster_latest,
        metrica_latest_date=metrica_latest,
    )


__all__ = ["router"]
