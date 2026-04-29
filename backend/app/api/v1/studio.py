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
    # Studio v2 etap 4 — relevance fields
    relevance: str                    # own / adjacent / disputed / spam / unclassified
    relevance_set_by: str | None      # rules / llm / user
    relevance_set_at: datetime | None
    relevance_reason_ru: str | None


class QueriesResponse(BaseModel):
    site_id: uuid.UUID
    total: int
    items: list[QueryRow]
    # Module-level summary so UI can render a header line without
    # iterating items:
    coverage: dict[str, int]   # {"with_volume": N, "without_volume": M, ...}
    # Relevance distribution — UI shows it as a progress strip on top.
    relevance_counts: dict[str, int]  # {"own": N, "adjacent": M, ...}


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
            relevance_counts={
                "own": 0, "adjacent": 0, "disputed": 0, "spam": 0,
                "unclassified": 0,
            },
        )

    # One round-trip to grab the latest position per query, scoped to
    # this site. We pull recent `query_performance` rows per
    # dimension_id and join in Python rather than do a per-row
    # subquery in SQL — N is small (<1000 typically) and the join is
    # trivial in memory.
    #
    # Date floor (last 30 days): positions are stable; the owner doesn't
    # need historic series here, and pulling the entire history of
    # query_performance for a chatty site can mean tens of thousands of
    # rows. The 14-day impressions window we compute below sits inside
    # this floor, so the math doesn't change.
    metrics_cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).date()
    metrics_q = (
        select(DailyMetric)
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.date >= metrics_cutoff,
        )
        .order_by(desc(DailyMetric.date))
        .limit(50000)  # safety net; >50k rows in 30d means a real problem
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
                relevance=q.relevance or "unclassified",
                relevance_set_by=q.relevance_set_by,
                relevance_set_at=q.relevance_set_at,
                relevance_reason_ru=q.relevance_reason_ru,
            )
        )

    if sort == "position":
        # Python-side sort: lower position = better. Queries without a
        # known position go to the bottom.
        items.sort(key=lambda r: (r.last_position is None, r.last_position or 0))

    # Relevance distribution — counted on the FULL site, not just the
    # paginated `items` slice, so the UI strip is honest.
    relevance_counts = {k: 0 for k in (
        "own", "adjacent", "disputed", "spam", "unclassified",
    )}
    full_count_rows = (await db.execute(
        select(SearchQuery.relevance, sa_func.count())
        .where(SearchQuery.site_id == site_id)
        .group_by(SearchQuery.relevance)
    )).all()
    for rel, n in full_count_rows:
        if rel in relevance_counts:
            relevance_counts[rel] = int(n)

    return QueriesResponse(
        site_id=site_id,
        total=coverage["total"],
        items=items,
        coverage=coverage,
        relevance_counts=relevance_counts,
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

    # Generate the run_id up-front so the deduped branch returns a
    # valid id even when the recent event is missing one (older rows /
    # legacy paths). Frontend keys SWR/toast off run_id — empty string
    # silently breaks the activity stream subscription.
    run_id = str(uuid.uuid4())

    recent = await _recent_started_event(db, site_id, "demand_map")
    if recent is not None:
        return TriggerResponse(
            status="deduped",
            task_id=None,
            run_id=str(recent.run_id) if recent.run_id else run_id,
            deduped=True,
        )

    from app.core_audit.demand_map.tasks import demand_map_build_site_task

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

    run_id = str(uuid.uuid4())

    recent = await _recent_started_event(db, site_id, "wordstat")
    if recent is not None:
        return TriggerResponse(
            status="deduped",
            task_id=None,
            run_id=str(recent.run_id) if recent.run_id else run_id,
            deduped=True,
        )

    from app.collectors.tasks import wordstat_refresh_site

    task = wordstat_refresh_site.delay(str(site_id), run_id=run_id)
    return TriggerResponse(status="queued", task_id=task.id, run_id=run_id)


# ── Harmful visibility report (Studio v2 etap 5) ────────────────────
#
# Once queries are classified, the natural follow-up question is:
# «по каким нерелевантным фразам я уже ранжируюсь?». That's wasted
# crawl budget, dilutes topical authority, and means a real customer
# searching for our actual product sees us alongside «джинсы багги».
#
# This endpoint surfaces queries where:
#   - relevance IN ('spam', 'disputed')
#   - we DO have a position in the last 30 days (otherwise there's
#     nothing to fix — Yandex isn't ranking us for it)
#
# Owner-action shape: each item carries a short suggested_action_ru
# generated by a rule (no LLM here — once relevance is known the
# recommendation is deterministic).


class HarmfulQueryItem(BaseModel):
    query_id: uuid.UUID
    query_text: str
    relevance: str                # "spam" | "disputed"
    relevance_set_by: str | None
    relevance_reason_ru: str | None
    last_position: float | None
    last_impressions_14d: int | None
    wordstat_volume: int | None
    suggested_action_ru: str
    # Studio v2 etap 5+: detailed LLM diagnosis. Null until owner
    # runs «Разобрать причины» — that triggers the Celery task.
    harmful_diagnosis: dict | None = None
    harmful_diagnosed_at: datetime | None = None


class HarmfulVisibilityResponse(BaseModel):
    site_id: uuid.UUID
    counts: dict[str, int]   # {"spam": N, "disputed": M, "total": K}
    items: list[HarmfulQueryItem]


# Position threshold: top-30 means «реально нас находят». Beyond 30
# Yandex shows us only on rare deep scrolls — nothing to fix. Owners
# don't need this noise.
HARMFUL_POSITION_THRESHOLD = 30


def _suggested_action(relevance: str, position: float | None) -> str:
    if relevance == "spam":
        if position is not None and position <= 10:
            return (
                "Это нерелевантный запрос, и мы по нему в топ-10. Перепиши "
                "title и H1 страницы которая ранжируется — убери "
                "двусмысленные слова, добавь явный туристический контекст "
                "(тур, экспедиция, регион). Если страница не нужна — "
                "поставь noindex."
            )
        return (
            "Нерелевантный запрос, мы попали в выдачу случайно. Если "
            "страница нужна — перепиши заголовки чтобы убрать двусмысленность. "
            "Иначе можно игнорировать."
        )
    # disputed
    if position is not None and position <= 10:
        return (
            "Спорный запрос, и мы в топ-10. Открой страницу и реши: твой ли "
            "это запрос? Если да — пометь «наш» в столбце «Класс», и "
            "ничего больше не трогай. Если нет — перепиши page как для "
            "спама."
        )
    return (
        "Спорный запрос. Открой страницу и реши категорию: «наш» или "
        "«мусор». Кнопка в столбце «Класс» зафиксирует решение навсегда."
    )


@router.get(
    "/sites/{site_id}/queries/harmful",
    response_model=HarmfulVisibilityResponse,
    dependencies=[Depends(_require_admin)],
)
async def list_harmful_visibility(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HarmfulVisibilityResponse:
    """Queries where we rank in top-30 but the query is spam/disputed.

    Read-only: this is a lens on existing data, no new computations.
    Recommended actions are rule-based (LLM-free) since relevance was
    already decided by the classifier.
    """
    await _site_or_404(db, site_id)

    # Pull only spam/disputed rows for this site.
    rows = (await db.execute(
        select(SearchQuery).where(
            SearchQuery.site_id == site_id,
            SearchQuery.relevance.in_(("spam", "disputed")),
        ).order_by(
            SearchQuery.relevance.desc(),
            SearchQuery.wordstat_volume.desc().nulls_last(),
        ),
    )).scalars().all()
    if not rows:
        return HarmfulVisibilityResponse(
            site_id=site_id,
            counts={"spam": 0, "disputed": 0, "total": 0},
            items=[],
        )

    # Latest position + 14-day impressions per query, last 30 days only.
    metrics_cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).date()
    impressions_14d_window_start = (
        datetime.now(timezone.utc) - timedelta(days=14)
    ).date()
    metric_rows = (await db.execute(
        select(DailyMetric)
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.date >= metrics_cutoff,
        )
        .order_by(desc(DailyMetric.date))
        .limit(50000)
    )).scalars().all()

    latest_metric_by_qid: dict[uuid.UUID, DailyMetric] = {}
    impressions_14d_by_qid: dict[uuid.UUID, int] = {}
    for m in metric_rows:
        if m.dimension_id is None:
            continue
        if m.dimension_id not in latest_metric_by_qid:
            latest_metric_by_qid[m.dimension_id] = m
        if m.date >= impressions_14d_window_start:
            impressions_14d_by_qid[m.dimension_id] = (
                impressions_14d_by_qid.get(m.dimension_id, 0)
                + (m.impressions or 0)
            )

    items: list[HarmfulQueryItem] = []
    counts = {"spam": 0, "disputed": 0}

    for q in rows:
        m = latest_metric_by_qid.get(q.id)
        position = float(m.avg_position) if m and m.avg_position is not None else None
        # Only surface what we actually rank for. No position → nothing
        # to fix (Yandex isn't ranking us for it). The whole point of
        # «вредная видимость» is видимость we DO have.
        if position is None or position > HARMFUL_POSITION_THRESHOLD:
            continue
        if q.relevance in counts:
            counts[q.relevance] += 1
        items.append(HarmfulQueryItem(
            query_id=q.id,
            query_text=q.query_text,
            relevance=q.relevance,
            relevance_set_by=q.relevance_set_by,
            relevance_reason_ru=q.relevance_reason_ru,
            last_position=position,
            last_impressions_14d=impressions_14d_by_qid.get(q.id),
            wordstat_volume=q.wordstat_volume,
            suggested_action_ru=_suggested_action(q.relevance, position),
            harmful_diagnosis=q.harmful_diagnosis,
            harmful_diagnosed_at=q.harmful_diagnosed_at,
        ))

    counts["total"] = counts["spam"] + counts["disputed"]
    return HarmfulVisibilityResponse(
        site_id=site_id,
        counts=counts,
        items=items,
    )


@router.post(
    "/sites/{site_id}/queries/harmful/diagnose",
    response_model=TriggerResponse,
    dependencies=[Depends(_require_admin)],
)
async def trigger_harmful_diagnose(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> TriggerResponse:
    """Studio v2 etap 5+ — for each spam/disputed query in top-30,
    find OUR ranking page (Yandex SERP probe) → load its content
    from `pages` → ask LLM for cause + concrete edits. Persist on
    SearchQuery.harmful_diagnosis.

    Idempotent: rows with diagnosis already cached are skipped. To
    re-diagnose after page edits, the diagnosis JSONB needs to be
    cleared (manual SQL or future re-trigger button).
    """
    await _site_or_404(db, site_id)

    recent = await _recent_started_event(db, site_id, "harmful_diagnose")
    if recent is not None:
        return TriggerResponse(
            status="deduped",
            task_id=None,
            run_id=str(recent.run_id) if recent.run_id else "",
            deduped=True,
        )

    from app.collectors.tasks import diagnose_harmful_queries_site_task

    run_id = str(uuid.uuid4())
    task = diagnose_harmful_queries_site_task.delay(str(site_id), run_id=run_id)
    return TriggerResponse(status="queued", task_id=task.id, run_id=run_id)


class RelevanceOverrideBody(BaseModel):
    relevance: str  # "own" | "adjacent" | "disputed" | "spam" — no "unclassified" from user


@router.patch(
    "/sites/{site_id}/queries/{query_id}/relevance",
    dependencies=[Depends(_require_admin)],
)
async def patch_query_relevance(
    site_id: uuid.UUID,
    query_id: uuid.UUID,
    body: RelevanceOverrideBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Owner override for the classifier verdict.

    set_by='user' wins forever — the classify task NEVER overwrites
    rows where set_by='user' (see classify_queries_site_task in
    collectors/tasks.py).
    """
    from sqlalchemy import update
    from app.core_audit.relevance import RELEVANCE_VALUES

    if body.relevance not in RELEVANCE_VALUES or body.relevance == "unclassified":
        raise HTTPException(
            status_code=422,
            detail=(
                f"relevance must be one of: "
                f"{[v for v in RELEVANCE_VALUES if v != 'unclassified']}"
            ),
        )

    site = await _site_or_404(db, site_id)
    q = (await db.execute(
        select(SearchQuery).where(
            SearchQuery.id == query_id,
            SearchQuery.site_id == site.id,
        )
    )).scalar_one_or_none()
    if q is None:
        raise HTTPException(status_code=404, detail="query not found")

    now = datetime.now(timezone.utc)
    await db.execute(
        update(SearchQuery)
        .where(SearchQuery.id == query_id)
        .values(
            relevance=body.relevance,
            relevance_set_by="user",
            relevance_set_at=now,
            relevance_reason_ru="Помечено вручную владельцем — классификатор не перезатрёт.",
        )
    )
    await db.commit()

    return {
        "query_id": str(query_id),
        "relevance": body.relevance,
        "relevance_set_by": "user",
        "relevance_set_at": now.isoformat(),
    }


@router.post(
    "/sites/{site_id}/queries/classify",
    response_model=TriggerResponse,
    dependencies=[Depends(_require_admin)],
)
async def trigger_classify_queries(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> TriggerResponse:
    """Studio v2 etap 4 — kick off rules + LLM classification of all
    SearchQuery rows for this site.

    Idempotent on the user-override invariant: rows where
    relevance_set_by='user' are NEVER touched. Rules + LLM verdicts
    can be re-run freely.
    """
    await _site_or_404(db, site_id)

    recent = await _recent_started_event(db, site_id, "classify_queries")
    if recent is not None:
        return TriggerResponse(
            status="deduped",
            task_id=None,
            run_id=str(recent.run_id) if recent.run_id else "",
            deduped=True,
        )

    from app.collectors.tasks import classify_queries_site_task

    run_id = str(uuid.uuid4())
    task = classify_queries_site_task.delay(str(site_id), run_id=run_id)
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

    run_id = str(uuid.uuid4())

    recent = await _recent_started_event(db, site_id, "wordstat_discover")
    if recent is not None:
        return TriggerResponse(
            status="deduped",
            task_id=None,
            run_id=str(recent.run_id) if recent.run_id else run_id,
            deduped=True,
        )

    from app.collectors.tasks import wordstat_discover_site

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


# ── Indexation: 4-source reconciliation (Studio v2 etap 1+2) ─────────
#
# `/studio/indexation` already shows the Search API result. That's
# one source of truth out of four:
#
#   1. sitemap.xml          — what we declare exists
#   2. crawler              — what our SiteCrawler actually saw
#   3. Webmaster API        — what Yandex says is indexed
#   4. Yandex Search API    — what `site:domain` returns right now
#
# When these four diverge — and they often do — the owner needs to
# see WHO disagrees with WHOM, not just one number. This endpoint
# returns all four side-by-side so the UI can render a comparison.


class IndexationSourcesResponse(BaseModel):
    site_id: str
    domain: str
    sources: dict[str, dict[str, Any]]
    # {source_name: {count, last_updated_at, status, note}}
    # source_name in: sitemap, crawler, webmaster, search_api


@router.get(
    "/sites/{site_id}/indexation/sources",
    response_model=IndexationSourcesResponse,
    dependencies=[Depends(_require_admin)],
)
async def get_indexation_sources(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> IndexationSourcesResponse:
    """Cross-source indexation reconciliation.

    Returns the four numbers that should agree (and usually don't):
    sitemap-declared, crawler-discovered, Webmaster-indexed, Search-API
    site:domain. UI renders them side-by-side so the owner sees which
    source disagrees with which.

    Read-only — no probes triggered. The Webmaster + Search API
    numbers come from data already collected by their respective
    Celery tasks. If those haven't run, the source returns null with
    a status hint.
    """
    site = await _site_or_404(db, site_id)

    # 1. Sitemap — count of Page rows where in_sitemap=True.
    sitemap_count = (await db.execute(
        select(sa_func.count())
        .where(Page.site_id == site.id, Page.in_sitemap.is_(True)),
    )).scalar_one()

    # Crawler «when last» — max(last_crawled_at) on Page rows.
    last_crawl_at = (await db.execute(
        select(sa_func.max(Page.last_crawled_at))
        .where(Page.site_id == site.id),
    )).scalar_one_or_none()

    # 2. Crawler — total Page rows for site.
    crawler_count = (await db.execute(
        select(sa_func.count()).where(Page.site_id == site.id),
    )).scalar_one()

    # 3. Webmaster — latest daily_metrics row with metric_type='indexing'.
    wm_row = (await db.execute(
        select(DailyMetric)
        .where(
            DailyMetric.site_id == site.id,
            DailyMetric.metric_type == "indexing",
        )
        .order_by(desc(DailyMetric.date))
        .limit(1),
    )).scalar_one_or_none()

    # 4. Search API — latest analysis_events row with stage='indexation'
    #    that has a terminal status (not 'started').
    search_event = (await db.execute(
        select(AnalysisEvent)
        .where(
            AnalysisEvent.site_id == site.id,
            AnalysisEvent.stage == "indexation",
            AnalysisEvent.status.in_(("done", "skipped", "failed")),
        )
        .order_by(desc(AnalysisEvent.ts))
        .limit(1),
    )).scalar_one_or_none()
    search_count: int | None = None
    if search_event and isinstance(search_event.extra, dict):
        raw = search_event.extra.get("pages_found")
        if isinstance(raw, int):
            search_count = raw

    sources: dict[str, dict[str, Any]] = {
        "sitemap": {
            "count": int(sitemap_count or 0),
            "last_updated_at": (
                last_crawl_at.isoformat() if last_crawl_at else None
            ),
            "status": "ok" if sitemap_count else "empty",
            "note": (
                "Страницы где `in_sitemap=true` после последнего обхода. "
                "Источник: краулер парсит sitemap.xml при обходе."
            ),
        },
        "crawler": {
            "count": int(crawler_count or 0),
            "last_updated_at": (
                last_crawl_at.isoformat() if last_crawl_at else None
            ),
            "status": "ok" if crawler_count else "never_crawled",
            "note": (
                "Страницы которые наш SiteCrawler реально загрузил. "
                "Если меньше чем sitemap — у нас ошибки HTTP, если больше — "
                "сайт публикует страницы вне sitemap."
            ),
        },
        "webmaster": {
            "count": (
                int(wm_row.pages_indexed or 0) if wm_row else None
            ),
            "last_updated_at": (
                wm_row.date.isoformat() if wm_row else None
            ),
            "status": (
                "ok" if wm_row else "no_data"
            ),
            "note": (
                "Сколько страниц Яндекс держит в индексе по данным "
                "Вебмастера. Лагает 5–10 дней. Если этот источник "
                "пуст — проверь подключение Webmaster в /studio/connections."
            ),
        },
        "search_api": {
            "count": search_count,
            "last_updated_at": (
                search_event.ts.isoformat() if search_event else None
            ),
            "status": (
                search_event.status if search_event else "never_checked"
            ),
            "note": (
                "Что Яндекс показывает по запросу site:domain прямо сейчас. "
                "Кнопка «Перепроверить» сверху обновляет это число."
            ),
        },
    }

    return IndexationSourcesResponse(
        site_id=str(site_id),
        domain=site.domain,
        sources=sources,
    )


# ── Per-URL indexation table ─────────────────────────────────────────


class UrlIndexationRow(BaseModel):
    page_id: str
    url: str
    path: str
    in_sitemap: bool
    in_index: bool                 # crawler-declared
    http_status: int | None
    last_crawled_at: datetime | None
    found_in_search_api: bool      # appeared in latest site:domain probe
    title: str | None
    # Studio v2 — per-URL Yandex Webmaster status (None = unknown)
    in_yandex_index: bool | None = None
    yandex_excluded_reason: str | None = None
    yandex_index_checked_at: datetime | None = None


class UrlsResponse(BaseModel):
    site_id: str
    total: int
    items: list[UrlIndexationRow]
    # Slim summary for the UI banner:
    only_in_sitemap: int           # in_sitemap=True, NOT in latest search-api
    only_in_search: int            # in latest search-api, NOT in our Page table
    fully_aligned: int             # everywhere


URLS_DEFAULT_LIMIT = 200
URLS_MAX_LIMIT = 1000


@router.get(
    "/sites/{site_id}/indexation/urls",
    response_model=UrlsResponse,
    dependencies=[Depends(_require_admin)],
)
async def list_indexation_urls(
    site_id: uuid.UUID,
    limit: int = Query(URLS_DEFAULT_LIMIT, ge=1, le=URLS_MAX_LIMIT),
    only: str = Query(
        "all",
        pattern="^(all|missing_in_search|only_in_search|broken_http|yandex_excluded|yandex_unknown)$",
    ),
    db: AsyncSession = Depends(get_db),
) -> UrlsResponse:
    """Per-URL signals for the «развёрнутая таблица» on /studio/indexation.

    Joins Page rows with the latest Search API probe so each URL has a
    `found_in_search_api` flag. Filter modes:

      all                — full list
      missing_in_search  — in our Page table but Search API didn't show it
      only_in_search     — Search API showed a URL we don't have crawled
      broken_http        — http_status >= 400

    Page-level data is the trustworthy source for «what exists on the
    site» — Search API is best-effort by Yandex. The mismatch is
    interesting either way.
    """
    site = await _site_or_404(db, site_id)

    # Latest indexation event with a `pages` array (URL list).
    search_event = (await db.execute(
        select(AnalysisEvent)
        .where(
            AnalysisEvent.site_id == site.id,
            AnalysisEvent.stage == "indexation",
            AnalysisEvent.status == "done",
        )
        .order_by(desc(AnalysisEvent.ts))
        .limit(1),
    )).scalar_one_or_none()

    search_urls: set[str] = set()
    if search_event and isinstance(search_event.extra, dict):
        raw_pages = search_event.extra.get("pages") or []
        for p in raw_pages:
            if isinstance(p, dict):
                u = p.get("url") or ""
                if isinstance(u, str) and u:
                    search_urls.add(u.strip())

    # All Page rows for the site.
    pages = (await db.execute(
        select(Page).where(Page.site_id == site.id),
    )).scalars().all()

    page_urls = {(p.url or "").strip() for p in pages if p.url}

    items: list[UrlIndexationRow] = []
    only_in_sitemap = 0
    only_in_search = 0
    fully_aligned = 0

    for p in pages:
        url = (p.url or "").strip()
        in_search = url in search_urls if url else False
        items.append(UrlIndexationRow(
            page_id=str(p.id),
            url=p.url,
            path=p.path,
            in_sitemap=bool(p.in_sitemap),
            in_index=bool(p.in_index),
            http_status=p.http_status,
            last_crawled_at=p.last_crawled_at,
            found_in_search_api=in_search,
            title=p.title,
            in_yandex_index=p.in_yandex_index,
            yandex_excluded_reason=p.yandex_excluded_reason,
            yandex_index_checked_at=p.yandex_index_checked_at,
        ))
        if p.in_sitemap and not in_search:
            only_in_sitemap += 1
        if in_search and p.in_sitemap:
            fully_aligned += 1

    # «only_in_search» — search-api showed URLs we don't have on Page.
    # Surface those as synthetic rows at the top of the list (no
    # page_id — owner sees Yandex knows about them but we don't).
    extra_search_urls = [u for u in search_urls if u not in page_urls]
    for u in extra_search_urls:
        only_in_search += 1
        items.append(UrlIndexationRow(
            page_id="",  # synthetic — no Page row
            url=u,
            path="",
            in_sitemap=False,
            in_index=False,
            http_status=None,
            last_crawled_at=None,
            found_in_search_api=True,
            title=None,
        ))

    # Filtering happens after the merge so synthetic «only_in_search»
    # rows participate.
    if only == "missing_in_search":
        items = [
            it for it in items
            if it.in_sitemap and not it.found_in_search_api
        ]
    elif only == "only_in_search":
        items = [it for it in items if it.found_in_search_api and not it.in_sitemap]
    elif only == "broken_http":
        items = [
            it for it in items
            if it.http_status is not None and it.http_status >= 400
        ]
    elif only == "yandex_excluded":
        items = [it for it in items if it.in_yandex_index is False]
    elif only == "yandex_unknown":
        items = [
            it for it in items
            if it.in_yandex_index is None and it.in_sitemap
        ]

    # Sort: search-api hits first (Yandex showed them today), then
    # by URL alphabetical for stability.
    items.sort(key=lambda it: (not it.found_in_search_api, it.url.lower()))

    return UrlsResponse(
        site_id=str(site_id),
        total=len(items),
        items=items[:limit],
        only_in_sitemap=only_in_sitemap,
        only_in_search=only_in_search,
        fully_aligned=fully_aligned,
    )


@router.post(
    "/sites/{site_id}/indexation/refresh-urls",
    response_model=TriggerResponse,
    dependencies=[Depends(_require_admin)],
)
async def trigger_url_indexation_refresh(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> TriggerResponse:
    """Studio v2 etap 1+2 deep — pull per-URL index status from
    Webmaster API. Updates Page.in_yandex_index +
    Page.yandex_excluded_reason for every page on the site.

    Different from /indexation/check (which probes Yandex Search API
    `site:domain`): this hits Webmaster's authoritative per-URL list
    so we can finally answer «is THIS page in the index, and if not,
    why?» — closing the «7 vs 15» gap the owner noticed.
    """
    await _site_or_404(db, site_id)

    recent = await _recent_started_event(db, site_id, "url_indexation")
    if recent is not None:
        return TriggerResponse(
            status="deduped",
            task_id=None,
            run_id=str(recent.run_id) if recent.run_id else "",
            deduped=True,
        )

    from app.collectors.tasks import webmaster_url_indexation_site_task

    run_id = str(uuid.uuid4())
    task = webmaster_url_indexation_site_task.delay(str(site_id), run_id=run_id)
    return TriggerResponse(status="queued", task_id=task.id, run_id=run_id)


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

    run_id = str(uuid.uuid4())

    recent = await _recent_started_event(db, site_id, "indexation")
    if recent is not None:
        return TriggerResponse(
            status="deduped",
            task_id=None,
            run_id=str(recent.run_id) if recent.run_id else run_id,
            deduped=True,
        )

    from app.collectors.tasks import studio_indexation_run

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

    # Latest review per page in ONE query via Postgres `DISTINCT ON`.
    # Postgres requires the `DISTINCT ON` columns to match the leading
    # `ORDER BY` columns, so page_id comes first and reviewed_at desc
    # second. Naive `select(PageReview).order_by(reviewed_at desc)` was
    # pulling all history (≈ 500 pages × 20 reviews per page) — this
    # cuts it to one row per page.
    latest_reviews = (await db.execute(
        select(PageReview)
        .where(PageReview.site_id == site.id)
        .distinct(PageReview.page_id)
        .order_by(PageReview.page_id, desc(PageReview.reviewed_at)),
    )).scalars().all()
    latest_review_by_page: dict[uuid.UUID, PageReview] = {
        r.page_id: r for r in latest_reviews
    }

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
        # Same two-tuple pattern as `recent_review`: pages without a
        # crawl timestamp go to the BOTTOM, not the top. The earlier
        # `-(ts or 0)` form ranked None as 0 which floated never-crawled
        # pages above genuine recent crawls.
        items.sort(
            key=lambda it: (
                it.last_crawled_at is None,
                -(it.last_crawled_at.timestamp() if it.last_crawled_at else 0),
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
    #
    # `analytics` is deliberately NOT exposed as a per-page cross-link:
    # we don't have per-page analytics data — Webmaster query_performance
    # and Metrica site_traffic are site-wide. The frontend page workspace
    # links to `/studio/analytics` from the site-level shell instead.
    cross_links = {
        "queries": True,        # PR-S2 ✅
        "indexation": True,     # PR-S3 ✅
        "competitors": True,    # PR-S5 ✅
        "outcomes": True,       # PR-S8 ✅
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
    # MetricaCollector writes visits/pageviews/bounce_rate/avg_duration
    # into the dedicated columns (see collectors/metrica.py:131-148);
    # those columns are the canonical source.
    traffic_rows = (await db.execute(
        select(
            DailyMetric.date,
            DailyMetric.visits,
            DailyMetric.pageviews,
            DailyMetric.bounce_rate,
            DailyMetric.avg_duration,
        )
        .where(
            DailyMetric.site_id == site.id,
            DailyMetric.metric_type == "site_traffic",
            DailyMetric.date >= cutoff,
        )
        .order_by(DailyMetric.date),
    )).all()

    # 3. Webmaster indexing — daily snapshot of pages_indexed.
    # webmaster.py:286-299 writes the count into the dedicated
    # `pages_indexed` column; `impressions` is 0 for these rows.
    indexing_rows = (await db.execute(
        select(
            DailyMetric.date,
            DailyMetric.pages_indexed,
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
        # MetricaCollector writes to the dedicated columns directly —
        # read them straight, no JSONB indirection.
        bucket["visits"] = int(row.visits or 0)
        bucket["pageviews"] = int(row.pageviews or 0)
        if row.bounce_rate is not None:
            bucket["bounce_rate"] = float(row.bounce_rate)
        if row.avg_duration is not None:
            bucket["avg_duration_sec"] = float(row.avg_duration)
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


# ── Module: Outcomes (PR-S8) ──────────────────────────────────────────
#
# /studio/outcomes is the «before / after» module — it reads what was
# applied (PR-S4 «Применил & замерить эффект» button + PR-S5 opportunity
# apply) and shows the 14-day delta when followup_at fills in.
#
# Trigger flow (already shipped):
#   user clicks Применил → POST /admin/sites/{id}/outcomes/applied
#                         → OutcomeSnapshot row with baseline_metrics
#                         → 14 days pass
#                         → outcomes_followup_daily_task fills
#                            followup_metrics + delta
#
# This endpoint is read-only — it groups snapshots by source / page_url
# so owner sees «what I changed for this page → what happened» rather
# than a flat list of recommendation_ids.


class OutcomeListItem(BaseModel):
    snapshot_id: str
    recommendation_id: str
    source: str                 # "priority" | "opportunity"
    page_url: str | None
    applied_at: datetime
    followup_at: datetime | None
    baseline_metrics: dict | None
    followup_metrics: dict | None
    delta: dict | None
    note_ru: str | None
    days_since_applied: int     # for «замер через N дней» UI hint


class OutcomeStats(BaseModel):
    """Top-of-page summary numbers."""
    total: int
    awaiting_followup: int       # applied < 14 days ago
    measured: int                # followup_at is not null
    avg_impressions_pct: float | None
    avg_clicks_pct: float | None
    avg_position_delta: float | None


class OutcomesResponse(BaseModel):
    site_id: str
    stats: OutcomeStats
    items: list[OutcomeListItem]


@router.get(
    "/sites/{site_id}/outcomes",
    response_model=OutcomesResponse,
    dependencies=[Depends(_require_admin)],
)
async def list_outcomes(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> OutcomesResponse:
    """List of all outcome snapshots for a site, newest first."""
    await _site_or_404(db, site_id)

    rows = (await db.execute(
        select(OutcomeSnapshot)
        .where(OutcomeSnapshot.site_id == site_id)
        .order_by(desc(OutcomeSnapshot.applied_at)),
    )).scalars().all()

    now = datetime.now(timezone.utc)
    items: list[OutcomeListItem] = []
    measured_deltas: list[dict] = []
    awaiting = 0

    for r in rows:
        applied_at = r.applied_at
        # OutcomeSnapshot.applied_at may be naive in older rows; coerce
        # to UTC-aware so the subtraction below doesn't blow up.
        if applied_at.tzinfo is None:
            applied_at = applied_at.replace(tzinfo=timezone.utc)
        days_since = max(0, (now - applied_at).days)

        if r.followup_at is None:
            awaiting += 1
        else:
            if isinstance(r.delta, dict):
                measured_deltas.append(r.delta)

        items.append(OutcomeListItem(
            snapshot_id=str(r.id),
            recommendation_id=r.recommendation_id,
            source=r.source,
            page_url=r.page_url,
            applied_at=applied_at,
            followup_at=r.followup_at,
            baseline_metrics=r.baseline_metrics,
            followup_metrics=r.followup_metrics,
            delta=r.delta,
            note_ru=r.note_ru,
            days_since_applied=days_since,
        ))

    def _avg(key: str) -> float | None:
        vals = [d.get(key) for d in measured_deltas if d.get(key) is not None]
        if not vals:
            return None
        return round(sum(vals) / len(vals), 2)

    stats = OutcomeStats(
        total=len(items),
        awaiting_followup=awaiting,
        measured=len(measured_deltas),
        avg_impressions_pct=_avg("impressions_pct"),
        avg_clicks_pct=_avg("clicks_pct"),
        avg_position_delta=_avg("position_delta"),
    )

    return OutcomesResponse(
        site_id=str(site_id),
        stats=stats,
        items=items,
    )


# ── Module: Profile editor (Studio v2 prerequisite) ───────────────────
#
# /studio/profile lets the owner inspect and edit `target_config` —
# what the system thinks is their business. This is a HARD prerequisite
# for the query classifier (v2 etap 4): if the profile says they
# rent buggies but their actual product is buggy expeditions, the
# classifier will incorrectly tag rental queries as relevant.
#
# v1 onboarding generates this via LLM and persists it on confirm,
# but the LLM hallucinates ("прокат" leaked into grandtourspirit's
# services list — pure invention). The editor lets owner override.
#
# Source of truth: `sites.target_config` JSONB. Read & write only the
# fields owner-editable in v1. `business_truth`, `service_weights`,
# `competitor_*`, `growth_opportunities` etc. are computed by tasks
# and out of scope here.


class ProfileEditable(BaseModel):
    """The owner-facing slice of target_config. v1 editor scope."""
    primary_product: str = ""
    services: list[str] = []
    secondary_products: list[str] = []
    geo_primary: list[str] = []
    geo_secondary: list[str] = []
    narrative_ru: str = ""


class ProfileResponse(BaseModel):
    site_id: str
    domain: str
    profile: ProfileEditable
    # Reflect when the editor saved last + by whom — telemetry for
    # detecting drift between LLM-generated and human-edited state.
    last_edited_at: datetime | None
    last_edited_by: str | None  # "onboarding" | "owner" | None


@router.get(
    "/sites/{site_id}/profile",
    response_model=ProfileResponse,
    dependencies=[Depends(_require_admin)],
)
async def get_profile(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ProfileResponse:
    """Read the editable slice of target_config."""
    site = await _site_or_404(db, site_id)
    cfg = site.target_config or {}

    def _strs(key: str) -> list[str]:
        raw = cfg.get(key) or []
        return [str(x).strip() for x in raw if x and str(x).strip()]

    profile = ProfileEditable(
        primary_product=str(cfg.get("primary_product") or "").strip(),
        services=_strs("services"),
        secondary_products=_strs("secondary_products"),
        geo_primary=_strs("geo_primary"),
        geo_secondary=_strs("geo_secondary"),
        narrative_ru=str(cfg.get("narrative_ru") or "").strip(),
    )

    edited = cfg.get("_profile_edited") or {}
    return ProfileResponse(
        site_id=str(site_id),
        domain=site.domain,
        profile=profile,
        last_edited_at=(
            datetime.fromisoformat(edited["at"])
            if isinstance(edited.get("at"), str)
            else None
        ),
        last_edited_by=edited.get("by"),
    )


PROFILE_PRIMARY_MAX_LEN = 80
PROFILE_LIST_MAX_ITEMS = 30
PROFILE_LIST_ITEM_MAX_LEN = 80
PROFILE_NARRATIVE_MAX_LEN = 4000


@router.put(
    "/sites/{site_id}/profile",
    response_model=ProfileResponse,
    dependencies=[Depends(_require_admin)],
)
async def put_profile(
    site_id: uuid.UUID,
    body: ProfileEditable,
    db: AsyncSession = Depends(get_db),
) -> ProfileResponse:
    """Persist owner-edited profile back into target_config.

    Validation:
      - primary_product non-empty (the classifier anchors on it)
      - geo_primary non-empty (regions are the second anchor)
      - list lengths capped to keep target_config reasonable in
        downstream LLM prompts
      - per-item length capped to prevent prompt-injection via huge
        strings
    """
    from sqlalchemy import update

    site = await _site_or_404(db, site_id)

    primary = body.primary_product.strip()
    if not primary:
        raise HTTPException(
            status_code=422,
            detail="primary_product is required — the classifier anchors on it",
        )
    if len(primary) > PROFILE_PRIMARY_MAX_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"primary_product longer than {PROFILE_PRIMARY_MAX_LEN} chars",
        )

    def _clean_list(items: list[str], label: str) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for it in items:
            v = (it or "").strip()
            if not v:
                continue
            if len(v) > PROFILE_LIST_ITEM_MAX_LEN:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{label} item longer than "
                        f"{PROFILE_LIST_ITEM_MAX_LEN} chars: {v[:30]}…"
                    ),
                )
            key = v.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(v)
            if len(out) >= PROFILE_LIST_MAX_ITEMS:
                break
        return out

    services = _clean_list(body.services, "services")
    secondary = _clean_list(body.secondary_products, "secondary_products")
    geo_primary = _clean_list(body.geo_primary, "geo_primary")
    geo_secondary = _clean_list(body.geo_secondary, "geo_secondary")

    if not geo_primary:
        raise HTTPException(
            status_code=422,
            detail="geo_primary is required — pick at least one region",
        )

    narrative = body.narrative_ru.strip()
    if len(narrative) > PROFILE_NARRATIVE_MAX_LEN:
        raise HTTPException(
            status_code=422,
            detail=(
                f"narrative_ru longer than "
                f"{PROFILE_NARRATIVE_MAX_LEN} chars"
            ),
        )

    new_cfg = dict(site.target_config or {})
    new_cfg["primary_product"] = primary
    new_cfg["services"] = services
    new_cfg["secondary_products"] = secondary
    new_cfg["geo_primary"] = geo_primary
    new_cfg["geo_secondary"] = geo_secondary
    new_cfg["narrative_ru"] = narrative
    new_cfg["_profile_edited"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "by": "owner",
    }

    await db.execute(
        update(Site).where(Site.id == site.id).values(target_config=new_cfg),
    )
    await db.commit()

    # Re-read so we return the canonical post-write shape.
    return await get_profile(site_id, db)


__all__ = ["router"]
