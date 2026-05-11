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
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import case, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import require_admin
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
    """Thin wrapper preserved for in-module Depends() callers.

    All policy lives in :func:`app.api.v1.deps.require_admin` which uses
    :func:`secrets.compare_digest` to avoid timing leaks (single source
    of truth for admin auth across the v1 surface).
    """
    require_admin(x_admin_key or "")


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

    # Load ALL queries — coverage counters MUST count the full site, not
    # the sliced top-N. Same goes for the `position` sort: position
    # lives in daily_metrics (not on SearchQuery), so a SQL limit before
    # Python sort would hide the actual top-position queries when the
    # site has more rows than the limit. Worst case ≈ 2000 SearchQuery
    # rows per site — tiny payload, scan is cheap.
    base = select(SearchQuery).where(SearchQuery.site_id == site_id)
    if sort == "volume":
        base = base.order_by(SearchQuery.wordstat_volume.desc().nulls_last())
    elif sort == "recent":
        base = base.order_by(SearchQuery.last_seen_at.desc().nulls_last())
    elif sort == "alpha":
        base = base.order_by(SearchQuery.query_text.asc())
    # NOTE: no SQL .limit() here — Python slicing happens after coverage
    # math + position sort below.

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

    # Slice AFTER coverage math + sort so the UI strip is honest while
    # the table still respects the limit.
    full_total = len(items)
    sliced_items = items[:limit]

    return QueriesResponse(
        site_id=site_id,
        total=full_total,
        items=sliced_items,
        coverage=coverage,
        relevance_counts=relevance_counts,
    )


# ── Trigger endpoints ─────────────────────────────────────────────────

# Idempotency window: don't re-queue the same module's task more than
# once per N seconds per site. Owner double-clicking should not burn
# the daily Wordstat / API quota twice.
#
# Default is 60 s — fits fast collector tasks (Webmaster, sitemap).
# LLM-driven tasks need a longer window because the task itself runs
# 30-120 s — a 60 s guard would let a re-click at t=70 spawn a second
# concurrent LLM call AND let it stomp the first task's commit (the
# stale-snapshot race that hurt missing_landings before B1 fix).
TRIGGER_DEDUP_WINDOW_SEC = 60
TRIGGER_DEDUP_WINDOW_LLM_SEC = 600  # 10 min — covers retries + cushion


class TriggerResponse(BaseModel):
    status: str         # "queued" | "deduped"
    task_id: str | None
    run_id: str
    deduped: bool = False


async def _recent_started_event(
    db: AsyncSession,
    site_id: uuid.UUID,
    stage: str,
    *,
    window_seconds: int = TRIGGER_DEDUP_WINDOW_SEC,
    extra_match: dict[str, str] | None = None,
) -> AnalysisEvent | None:
    """Find a `<stage>:started` event for this site within the dedup
    window. If present, second trigger reuses its run_id instead of
    queueing a duplicate task — same pattern as admin_ops.trigger_full_pipeline.

    Pass `window_seconds=TRIGGER_DEDUP_WINDOW_LLM_SEC` for stages whose
    underlying task makes a long LLM call — the default 60 s window
    is shorter than the task itself, so re-clicks during execution
    would slip through.

    Pass `extra_match` to scope dedup to a specific JSONB payload —
    e.g. `extra_match={"page_id": str(pid)}` so a review of page A
    doesn't dedup a review of page B.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    stmt = (
        select(AnalysisEvent)
        .where(
            AnalysisEvent.site_id == site_id,
            AnalysisEvent.stage == stage,
            AnalysisEvent.status == "started",
            AnalysisEvent.ts >= cutoff,
        )
        .order_by(desc(AnalysisEvent.ts))
        .limit(1)
    )
    if extra_match:
        for key, value in extra_match.items():
            stmt = stmt.where(AnalysisEvent.extra[key].astext == value)
    result = await db.execute(stmt)
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

    recent = await _recent_started_event(
        db, site_id, "harmful_diagnose",
        window_seconds=TRIGGER_DEDUP_WINDOW_LLM_SEC,
    )
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

    recent = await _recent_started_event(
        db, site_id, "classify_queries",
        window_seconds=TRIGGER_DEDUP_WINDOW_LLM_SEC,
    )
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

    # 3. Webmaster — prefer the per-URL data from
    # `webmaster_url_indexation_site_task` (count of Pages with
    # `in_yandex_index=True`) when present. Fall back to the
    # aggregated `metric_type='indexing'` daily_metrics row when
    # the per-URL pull hasn't run yet. The per-URL signal is the
    # owner's true answer to «how many pages are indexed», whereas
    # the aggregated daily_metrics value is just a number from
    # Yandex's history feed (often stale by 5-10 days).
    wm_per_url_count = (await db.execute(
        select(sa_func.count())
        .where(
            Page.site_id == site.id,
            Page.in_yandex_index.is_(True),
        ),
    )).scalar_one()
    wm_per_url_latest = (await db.execute(
        select(sa_func.max(Page.yandex_index_checked_at))
        .where(Page.site_id == site.id),
    )).scalar_one_or_none()

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
                int(wm_per_url_count)
                if wm_per_url_latest is not None
                else (int(wm_row.pages_indexed or 0) if wm_row else None)
            ),
            "last_updated_at": (
                wm_per_url_latest.isoformat()
                if wm_per_url_latest is not None
                else (wm_row.date.isoformat() if wm_row else None)
            ),
            "status": (
                "ok"
                if wm_per_url_latest is not None or wm_row
                else "no_data"
            ),
            "note": (
                "Сколько страниц Яндекс реально держит в индексе. "
                "Источник: per-URL данные из Webmaster API (точные, без "
                "лага), либо агрегированная история (лаг 5–10 дней) "
                "если per-URL ещё не подтянули. Кнопка «Webmaster: статус "
                "каждого URL» обновляет точные данные."
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
    total: int                     # total rows for the site (no filter)
    filtered_total: int            # rows after `only=` filter, before slicing
    truncated: bool                # filtered_total > len(items) — UI must page
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

    filtered_total = len(items)
    sliced = items[:limit]

    return UrlsResponse(
        site_id=str(site_id),
        total=len(pages) + len(extra_search_urls),
        filtered_total=filtered_total,
        truncated=filtered_total > len(sliced),
        items=sliced,
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
    # 3-state Webmaster verdict: True=in index, False=excluded, None=unknown.
    # Replaces dead `in_index` column which was never populated by collectors.
    in_yandex_index: bool | None
    yandex_excluded_reason: str | None
    yandex_index_checked_at: datetime | None
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

    # Current actionable state = latest COMPLETED review per
    # (page_id, target_intent_code). A single page can have several
    # intents; collapsing to "latest review per page" hides valid
    # recommendations from the other intents.
    latest_reviews = (
        select(
            PageReview.id.label("id"),
            PageReview.page_id.label("page_id"),
            PageReview.reviewed_at.label("reviewed_at"),
            sa_func.row_number().over(
                partition_by=(
                    PageReview.page_id,
                    PageReview.target_intent_code,
                ),
                order_by=PageReview.reviewed_at.desc(),
            ).label("rn"),
        )
        .where(
            PageReview.site_id == site.id,
            PageReview.status == "completed",
        )
        .subquery()
    )
    latest_review_rows = (await db.execute(
        select(
            latest_reviews.c.id,
            latest_reviews.c.page_id,
            latest_reviews.c.reviewed_at,
        ).where(latest_reviews.c.rn == 1),
    )).all()

    latest_review_ids_by_page: dict[uuid.UUID, list[uuid.UUID]] = {}
    last_reviewed_at_by_page: dict[uuid.UUID, datetime] = {}
    review_page_by_id: dict[uuid.UUID, uuid.UUID] = {}
    for review_id, page_id, reviewed_at in latest_review_rows:
        latest_review_ids_by_page.setdefault(page_id, []).append(review_id)
        review_page_by_id[review_id] = page_id
        current_last = last_reviewed_at_by_page.get(page_id)
        if current_last is None or reviewed_at > current_last:
            last_reviewed_at_by_page[page_id] = reviewed_at

    # Recommendation aggregates per page across its latest intent reviews.
    rec_counts_by_page: dict[uuid.UUID, dict[str, int]] = {}
    if review_page_by_id:
        review_ids = list(review_page_by_id)
        rec_rows = (await db.execute(
            select(
                PageReviewRecommendation.review_id,
                PageReviewRecommendation.user_status,
            ).where(PageReviewRecommendation.review_id.in_(review_ids)),
        )).all()
        for review_id, user_status in rec_rows:
            page_id_for_review = review_page_by_id.get(review_id)
            if page_id_for_review is None:
                continue
            d = rec_counts_by_page.setdefault(
                page_id_for_review, {"total": 0, "pending": 0, "applied": 0},
            )
            d["total"] += 1
            if user_status == "pending":
                d["pending"] += 1
            elif user_status == "applied":
                d["applied"] += 1

    items: list[PageListItem] = []
    for page in pages:
        counts = rec_counts_by_page.get(page.id)
        last_reviewed_at = last_reviewed_at_by_page.get(page.id)
        items.append(PageListItem(
            page_id=str(page.id),
            url=page.url,
            path=page.path,
            title=page.title,
            in_yandex_index=page.in_yandex_index,
            yandex_excluded_reason=page.yandex_excluded_reason,
            yandex_index_checked_at=page.yandex_index_checked_at,
            in_sitemap=page.in_sitemap,
            http_status=page.http_status,
            last_crawled_at=page.last_crawled_at,
            has_review=page.id in latest_review_ids_by_page,
            last_reviewed_at=last_reviewed_at,
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
    # 3-state Webmaster verdict: True=in index, False=excluded, None=unknown.
    # Replaces dead `in_index` column.
    in_yandex_index: bool | None
    yandex_excluded_reason: str | None
    yandex_index_checked_at: datetime | None
    in_sitemap: bool
    http_status: int | None
    has_schema: bool
    last_crawled_at: datetime | None
    review: PageReviewOut | None    # null if site never had its review run
    outcomes: list[OutcomeOut]      # snapshots filtered by page_url
    # Cross-link readiness: frontend uses this to enable/disable links.
    # Source of truth = IMPLEMENTATION.md §1 status table.
    cross_links: dict[str, bool]


@router.post(
    "/sites/{site_id}/pages/{page_id}/review",
    response_model=TriggerResponse,
    dependencies=[Depends(_require_admin)],
)
async def trigger_page_review(
    site_id: uuid.UUID,
    page_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> TriggerResponse:
    """Studio v2 etap 3 — trigger review for ONE page on demand.

    Reuses the existing Reviewer pipeline; the wrapper task adds
    activity events so the page workspace can show «идёт ревью…»
    and auto-refresh on completion. Underlying composite-hash dedup
    means re-clicking is cheap if content hasn't changed.
    """
    site = await _site_or_404(db, site_id)
    page = (await db.execute(
        select(Page).where(Page.id == page_id, Page.site_id == site.id),
    )).scalar_one_or_none()
    if page is None:
        raise HTTPException(status_code=404, detail="page not found")

    recent = await _recent_started_event(
        db, site_id, "page_review",
        window_seconds=TRIGGER_DEDUP_WINDOW_LLM_SEC,
        extra_match={"page_id": str(page_id)},
    )
    if recent is not None:
        return TriggerResponse(
            status="deduped",
            task_id=None,
            run_id=str(recent.run_id) if recent.run_id else "",
            deduped=True,
        )

    from app.collectors.tasks import studio_review_page_task

    run_id = str(uuid.uuid4())
    task = studio_review_page_task.delay(
        str(site_id), str(page_id), run_id=run_id,
    )
    return TriggerResponse(status="queued", task_id=task.id, run_id=run_id)


@router.post(
    "/sites/{site_id}/pages/{page_id}/recrawl",
    response_model=TriggerResponse,
    dependencies=[Depends(_require_admin)],
)
async def trigger_page_recrawl(
    site_id: uuid.UUID,
    page_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> TriggerResponse:
    """Re-fetch a single page on demand. Owner edits page → clicks
    «Обновить страницу» → system pulls fresh title/h1/meta. Without
    this the only refresh path was the weekly site-wide crawl."""
    site = await _site_or_404(db, site_id)
    page = (await db.execute(
        select(Page).where(Page.id == page_id, Page.site_id == site.id),
    )).scalar_one_or_none()
    if page is None:
        raise HTTPException(status_code=404, detail="page not found")

    # Per-page dedup, scoped by page_id so re-clicking page A doesn't
    # block page B's re-crawl.
    recent = await _recent_started_event(
        db, site_id, "page_recrawl",
        extra_match={"page_id": str(page_id)},
    )
    if recent is not None:
        return TriggerResponse(
            status="deduped",
            task_id=None,
            run_id=str(recent.run_id) if recent.run_id else "",
            deduped=True,
        )

    from app.collectors.tasks import crawl_single_page_task

    run_id = str(uuid.uuid4())
    task = crawl_single_page_task.delay(
        str(site_id), str(page_id), run_id=run_id,
    )
    return TriggerResponse(status="queued", task_id=task.id, run_id=run_id)


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

    review_out: PageReviewOut | None = None
    latest_completed_reviews_ranked = (
        select(
            PageReview.id.label("id"),
            sa_func.row_number().over(
                partition_by=(
                    PageReview.page_id,
                    PageReview.target_intent_code,
                ),
                order_by=PageReview.reviewed_at.desc(),
            ).label("rn"),
        )
        .where(
            PageReview.page_id == page.id,
            PageReview.site_id == site.id,
            PageReview.status == "completed",
        )
        .subquery()
    )
    completed_reviews = (await db.execute(
        select(PageReview)
        .join(
            latest_completed_reviews_ranked,
            latest_completed_reviews_ranked.c.id == PageReview.id,
        )
        .where(latest_completed_reviews_ranked.c.rn == 1)
        .order_by(desc(PageReview.reviewed_at)),
    )).scalars().all()

    if completed_reviews:
        review_ids = [r.id for r in completed_reviews]
        primary_review = completed_reviews[0]
    else:
        # If the page has no completed review yet, still expose the most
        # recent skipped/failed row so the workspace can explain why.
        fallback_review = (await db.execute(
            select(PageReview)
            .where(PageReview.page_id == page.id, PageReview.site_id == site.id)
            .order_by(desc(PageReview.reviewed_at))
            .limit(1),
        )).scalar_one_or_none()
        review_ids = [fallback_review.id] if fallback_review else []
        primary_review = fallback_review

    if primary_review is not None:
        rec_rows = (await db.execute(
            select(PageReviewRecommendation)
            .where(PageReviewRecommendation.review_id.in_(review_ids))
            .order_by(
                # Pending first, then by priority_score desc.
                case(
                    (PageReviewRecommendation.user_status == "pending", 0),
                    (PageReviewRecommendation.user_status == "deferred", 1),
                    (PageReviewRecommendation.user_status == "applied", 2),
                    else_=3,
                ),
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

        reviewed_at = (
            max(r.reviewed_at for r in completed_reviews)
            if completed_reviews else primary_review.reviewed_at
        )
        cost_usd = (
            sum(float(r.cost_usd) if r.cost_usd is not None else 0.0 for r in completed_reviews)
            if completed_reviews
            else float(primary_review.cost_usd) if primary_review.cost_usd is not None else 0.0
        )

        review_out = PageReviewOut(
            review_id=str(primary_review.id),
            status=primary_review.status,
            skip_reason=primary_review.skip_reason,
            reviewer_model=primary_review.reviewer_model,
            reviewed_at=reviewed_at,
            cost_usd=cost_usd,
            page_level_summary=primary_review.page_level_summary,
            top_queries_snapshot=primary_review.top_queries_snapshot,
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
        in_yandex_index=page.in_yandex_index,
        yandex_excluded_reason=page.yandex_excluded_reason,
        yandex_index_checked_at=page.yandex_index_checked_at,
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


# ── Studio v2 etap 6 · Missing landings ───────────────────────────────


class MissingLandingItem(BaseModel):
    service_name: str
    evidence_quote: str
    closest_existing_url: str | None
    suggested_url_path: str
    why_it_matters_ru: str
    priority: str  # high|medium|low


class MissingLandingsOut(BaseModel):
    site_id: str
    items: list[MissingLandingItem]
    summary_ru: str
    model: str | None
    cost_usd: float | None
    input_pages: int | None
    rejected_no_evidence: int | None
    computed_at: datetime | None


@router.post(
    "/sites/{site_id}/missing-landings/scan",
    response_model=TriggerResponse,
    dependencies=[Depends(_require_admin)],
)
async def trigger_missing_landings_scan(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> TriggerResponse:
    """Run the missing-landings detector. Idempotent within 60 seconds
    via the standard recent-event guard — re-clicking returns the same
    run_id instead of queuing a duplicate LLM call.
    """
    site = await _site_or_404(db, site_id)
    if not (
        (site.understanding or {}).get("narrative_ru") or ""
    ).strip():
        raise HTTPException(
            status_code=409,
            detail=(
                "no business understanding yet — narrative_ru is empty. "
                "Run business understanding first."
            ),
        )

    recent = await _recent_started_event(
        db, site_id, "missing_landings",
        window_seconds=TRIGGER_DEDUP_WINDOW_LLM_SEC,
    )
    if recent is not None:
        return TriggerResponse(
            status="deduped",
            task_id=None,
            run_id=str(recent.run_id) if recent.run_id else "",
            deduped=True,
        )

    from app.collectors.tasks import missing_landings_scan_task

    run_id = str(uuid.uuid4())
    task = missing_landings_scan_task.delay(str(site_id), run_id=run_id)
    return TriggerResponse(status="queued", task_id=task.id, run_id=run_id)


@router.get(
    "/sites/{site_id}/missing-landings",
    response_model=MissingLandingsOut,
    dependencies=[Depends(_require_admin)],
)
async def get_missing_landings(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> MissingLandingsOut:
    """Return the cached missing-landings result for a site.

    Empty payload (`items=[]`, `computed_at=null`) means the scan has
    never been run — UI should surface a «нажми «Найти услуги без
    страниц»» empty state.
    """
    site = await _site_or_404(db, site_id)
    cfg = site.target_config or {}
    payload = cfg.get("missing_landings") or {}

    items_raw = payload.get("items") or []
    items: list[MissingLandingItem] = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        items.append(MissingLandingItem(
            service_name=str(it.get("service_name") or ""),
            evidence_quote=str(it.get("evidence_quote") or ""),
            closest_existing_url=it.get("closest_existing_url") or None,
            suggested_url_path=str(it.get("suggested_url_path") or ""),
            why_it_matters_ru=str(it.get("why_it_matters_ru") or ""),
            priority=str(it.get("priority") or "medium"),
        ))

    computed_at = payload.get("computed_at")
    computed_dt: datetime | None = None
    if computed_at:
        try:
            computed_dt = datetime.fromisoformat(str(computed_at).replace("Z", "+00:00"))
        except ValueError:
            computed_dt = None

    return MissingLandingsOut(
        site_id=str(site_id),
        items=items,
        summary_ru=str(payload.get("summary_ru") or ""),
        model=payload.get("model") or None,
        cost_usd=(
            float(payload["cost_usd"])
            if isinstance(payload.get("cost_usd"), (int, float))
            else None
        ),
        input_pages=(
            int(payload["input_pages"])
            if isinstance(payload.get("input_pages"), int)
            else None
        ),
        rejected_no_evidence=(
            int(payload["rejected_no_evidence"])
            if isinstance(payload.get("rejected_no_evidence"), int)
            else None
        ),
        computed_at=computed_dt,
    )


# ── Studio v2 etap 7 · Brain — «what to do this week» plan ──────────


class BrainActionExample(BaseModel):
    label: str           # query text, URL, service name…
    kind: str            # "url" | "spam" | "disputed" | "high"|"medium"|"low"
    hint: str | None = None  # optional reason / quote from the data


class BrainActionOut(BaseModel):
    id: str
    severity: str
    title: str
    body_ru: str
    what_to_do_ru: str
    link_to: str
    link_label: str
    examples: list[BrainActionExample] = []
    evidence: dict[str, Any]
    in_focus: bool = False  # Phase E step 2 — set when action signals overlap focus tokens


class BrainPlanOut(BaseModel):
    site_id: str
    domain: str
    actions: list[BrainActionOut]
    diagnostics: list[str]
    computed_at: datetime
    focus_label: str | None = None  # owner's «Сейчас работаем над…» if any


_EXPORT_PRIORITY_RANK = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


def _normalise_export_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _recommendation_export_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    action = _normalise_export_text(item.get("after_text"))
    if not action:
        action = _normalise_export_text(item.get("reasoning_ru"))
    return (
        _normalise_export_text(item.get("url")),
        _normalise_export_text(item.get("category")),
        action,
        _normalise_export_text(item.get("before_text")),
    )


def _recommendation_sort_key(item: dict[str, Any]) -> tuple[int, float, str, str]:
    priority = _normalise_export_text(item.get("priority"))
    score = item.get("priority_score")
    return (
        _EXPORT_PRIORITY_RANK.get(priority, 9),
        -(float(score) if score is not None else -1.0),
        str(item.get("url") or ""),
        str(item.get("category") or ""),
    )


def _dedupe_recommendation_export_items(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in sorted(items, key=_recommendation_sort_key):
        key = _recommendation_export_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _render_recommendations_markdown(
    *,
    domain: str,
    items: list[dict[str, Any]],
    total_before_dedupe: int,
    computed_at: datetime,
) -> str:
    duplicate_count = max(0, total_before_dedupe - len(items))
    lines = [
        f"# Рекомендации для {domain}",
        "",
        f"Собрано уникальных рекомендаций: {len(items)}.",
        f"Повторы убраны: {duplicate_count}.",
        f"Сформировано: {computed_at.isoformat()}",
        "",
        "В файл попали актуальные pending/deferred-рекомендации из последних "
        "завершённых ревью страниц. Если одна и та же рекомендация повторялась "
        "после повторных ревью, оставлена одна запись.",
        "",
    ]

    if not items:
        lines.extend([
            "## Рекомендаций пока нет",
            "",
            "Запусти проверку страниц или полный анализ, чтобы система собрала рекомендации.",
            "",
        ])
        return "\n".join(lines)

    current_priority: str | None = None
    for idx, item in enumerate(items, start=1):
        priority = str(item.get("priority") or "unknown")
        if priority != current_priority:
            current_priority = priority
            lines.extend(["", f"## {priority.upper()}", ""])

        lines.extend([
            f"### {idx}. {item.get('category') or 'recommendation'}",
            "",
            f"- Страница: {item.get('url') or '-'}",
            f"- Статус: {item.get('user_status') or '-'}",
            f"- Приоритет: {priority}",
        ])
        if item.get("priority_score") is not None:
            lines.append(f"- Score: {item['priority_score']}")
        if item.get("target_intent_code"):
            lines.append(f"- Интент: {item['target_intent_code']}")
        if item.get("before_text"):
            lines.append(f"- Было: {item['before_text']}")
        if item.get("after_text"):
            lines.append(f"- Что сделать: {item['after_text']}")
        lines.extend([
            f"- Почему: {item.get('reasoning_ru') or '-'}",
            f"- ID рекомендации: {item.get('rec_id')}",
            "",
        ])

    return "\n".join(lines)


@router.get(
    "/sites/{site_id}/recommendations/export",
    dependencies=[Depends(_require_admin)],
)
async def export_site_recommendations(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Download current actionable page recommendations as one Markdown file."""
    site = await _site_or_404(db, site_id)
    latest_reviews = (
        select(
            PageReview.id.label("id"),
            sa_func.row_number()
            .over(
                partition_by=(PageReview.page_id, PageReview.target_intent_code),
                order_by=PageReview.reviewed_at.desc(),
            )
            .label("rn"),
        )
        .where(
            PageReview.site_id == site_id,
            PageReview.status == "completed",
        )
        .subquery()
    )

    rows = (await db.execute(
        select(
            PageReviewRecommendation.id,
            PageReviewRecommendation.category,
            PageReviewRecommendation.priority,
            PageReviewRecommendation.user_status,
            PageReviewRecommendation.before_text,
            PageReviewRecommendation.after_text,
            PageReviewRecommendation.reasoning_ru,
            PageReviewRecommendation.priority_score,
            Page.url,
            PageReview.target_intent_code,
        )
        .join(PageReview, PageReview.id == PageReviewRecommendation.review_id)
        .join(latest_reviews, latest_reviews.c.id == PageReview.id)
        .join(Page, Page.id == PageReview.page_id)
        .where(
            latest_reviews.c.rn == 1,
            PageReviewRecommendation.site_id == site_id,
            PageReviewRecommendation.user_status.in_(("pending", "deferred")),
        )
    )).all()

    items = [
        {
            "rec_id": str(r[0]),
            "category": r[1],
            "priority": r[2],
            "user_status": r[3],
            "before_text": r[4],
            "after_text": r[5],
            "reasoning_ru": r[6],
            "priority_score": float(r[7]) if r[7] is not None else None,
            "url": r[8],
            "target_intent_code": r[9],
        }
        for r in rows
    ]
    unique_items = _dedupe_recommendation_export_items(items)
    computed_at = datetime.now(timezone.utc)
    markdown = _render_recommendations_markdown(
        domain=site.domain,
        items=unique_items,
        total_before_dedupe=len(items),
        computed_at=computed_at,
    )
    safe_domain = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "-"
        for ch in site.domain.lower()
    ).strip("-") or "site"
    filename = f"recommendations-{safe_domain}-{computed_at.date().isoformat()}.md"

    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "content-disposition": f'attachment; filename="{filename}"',
            "cache-control": "no-store",
        },
    )


@router.get(
    "/sites/{site_id}/plan",
    response_model=BrainPlanOut,
    dependencies=[Depends(_require_admin)],
)
async def get_brain_plan(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> BrainPlanOut:
    """Studio v2 etap 7 — synthesised «do this first» plan.

    Pure SQL aggregation + Russian rules, no LLM. Each Action carries
    the raw counts that triggered it under `evidence`, so the UI can
    show the receipt («3 вредных запроса в spam, 8 в disputed»)
    instead of generative summaries.

    Latency budget: <500 ms (six small COUNTs + one JSONB read).
    """
    site = await _site_or_404(db, site_id)

    from app.core_audit.brain import build_plan, build_snapshot

    snap = await build_snapshot(db, site)
    plan = build_plan(snap, target_config=site.target_config or {})

    return BrainPlanOut(
        site_id=plan.site_id,
        domain=plan.domain,
        actions=[
            BrainActionOut(
                id=a.id,
                severity=a.severity,
                title=a.title,
                body_ru=a.body_ru,
                what_to_do_ru=a.what_to_do_ru,
                link_to=a.link_to,
                link_label=a.link_label,
                examples=[
                    BrainActionExample(
                        label=str(ex.get("label") or ""),
                        kind=str(ex.get("kind") or ""),
                        hint=(ex.get("hint") or None),
                    )
                    for ex in (a.examples or [])
                ],
                evidence=dict(a.evidence),
                in_focus=bool(a.in_focus),
            )
            for a in plan.actions
        ],
        diagnostics=plan.diagnostics,
        computed_at=snap.computed_at,
        focus_label=plan.focus_label,
    )


# ── Studio v2 etap 7 (Phase B) · Brain chat ──────────────────────────


class BrainChatMessage(BaseModel):
    role: str           # "user" | "assistant"
    content: str


class BrainChatRequest(BaseModel):
    message: str
    history: list[BrainChatMessage] = []


class BrainChatResponse(BaseModel):
    reply: str
    cost_usd: float
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@router.post(
    "/sites/{site_id}/plan/{action_id}/chat",
    response_model=BrainChatResponse,
    dependencies=[Depends(_require_admin)],
)
async def brain_action_chat(
    site_id: uuid.UUID,
    action_id: str,
    body: BrainChatRequest,
    db: AsyncSession = Depends(get_db),
) -> BrainChatResponse:
    """Chat about ONE specific action from the brain plan.

    Stateless: client sends the conversation history each turn, server
    appends the new message and sends one Haiku call. The system prompt
    is cache-marked so cost stabilises around $0.003/turn.
    """
    site = await _site_or_404(db, site_id)
    msg = (body.message or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="message is empty")

    from app.core_audit.brain import build_plan, build_snapshot
    from app.core_audit.brain.chat import (
        MAX_HISTORY_MESSAGES,
        chat_about_action,
    )

    snap = await build_snapshot(db, site)
    plan = build_plan(snap, max_actions=10, target_config=site.target_config or {})
    action = next((a for a in plan.actions if a.id == action_id), None)
    if action is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"action '{action_id}' is not currently in the plan — "
                "it may have been resolved or the data has changed. "
                "Re-fetch /plan to see the current actions."
            ),
        )

    sanitised: list[dict[str, str]] = []
    for m in (body.history or [])[-MAX_HISTORY_MESSAGES:]:
        role = m.role if m.role in ("user", "assistant") else "user"
        content = (m.content or "").strip()
        if content:
            sanitised.append({"role": role, "content": content})

    import anyio
    result = await anyio.to_thread.run_sync(
        lambda: chat_about_action(
            action=action,
            snap=snap,
            history=sanitised,
            new_message=msg,
            target_config=site.target_config or {},
        ),
    )

    return BrainChatResponse(
        reply=result["reply"],
        cost_usd=result["cost_usd"],
        model=result.get("model") or None,
        input_tokens=result.get("input_tokens"),
        output_tokens=result.get("output_tokens"),
    )


# ── Studio v2 etap 7 (Phase C) · Free chat (whole-site context) ──────


class FreeChatRequest(BaseModel):
    """Phase D: client sends an existing `conversation_id` to continue
    a saved thread, or null to start a new one. The server returns
    `conversation_id` either way so the client can save it (URL or
    localStorage) and re-attach next turn."""
    message: str
    conversation_id: uuid.UUID | None = None
    mode: Literal["answer", "discussion", "battle_plan"] = "answer"


class FocusProposalOut(BaseModel):
    """Phase E step 2 — when the model calls propose_strategic_focus,
    we hand the structured payload to the frontend so it can render
    the «Применить фокус?» modal. Owner confirms, frontend POSTs to
    /strategic-focus/from-proposal — server validates and persists."""
    label: str
    products: list[str] = []
    regions: list[str] = []
    query_signals: list[str] = []
    deprioritised: list[str] = []
    exit_criterion: str | None = None
    owner_note: str | None = None
    deadline: str | None = None
    rationale: str = ""


class FreeChatResponse(BaseModel):
    conversation_id: uuid.UUID
    reply: str | None = None
    proposal: FocusProposalOut | None = None
    cost_usd: float
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    # Set when the LLM hit `max_tokens` and the answer is mid-thought.
    # Frontend uses this to render a «ответ обрезан» warning + retry
    # button. Without surfacing this the truncated text would silently
    # become «complete» on the next reload.
    truncated: bool = False
    # `true` when this turn was served from a 60-second idempotency
    # cache (same user message replayed) — frontend skips optimistic
    # state updates because nothing new happened on the server.
    deduped: bool = False


@router.post(
    "/sites/{site_id}/chat",
    response_model=FreeChatResponse,
    dependencies=[Depends(_require_admin)],
)
async def brain_free_chat(
    site_id: uuid.UUID,
    body: FreeChatRequest,
    db: AsyncSession = Depends(get_db),
) -> FreeChatResponse:
    """Free-form chat about the whole site, with persistent history.

    Wider context than per-action chat: business profile +
    understanding narrative + full snapshot + current plan.
    The system prompt enforces the same anti-hallucination contract
    (no fabrication, refer to plan for «what to do», explain terms,
    trust owner overrides).

    Phase D persistence:
      - If `conversation_id` is null → create a new ChatConversation,
        return its id in the response. Client saves it (URL `?c=<id>`
        or localStorage) and sends back next turn.
      - If `conversation_id` is set → load that thread's messages
        from DB as history (overrides whatever the client thinks the
        history is — DB is the source of truth).
      - After the LLM call we persist BOTH the user turn and the
        assistant turn in one transaction, with cost / tokens / model
        attached to the assistant row.
    """
    site = await _site_or_404(db, site_id)
    msg = (body.message or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="message is empty")

    from app.core_audit.brain import (
        battle_plan_result,
        build_plan,
        build_snapshot,
    )
    from app.core_audit.brain.free_chat import (
        MAX_HISTORY_MESSAGES,
        free_chat,
    )
    from app.models.chat import ChatConversation, ChatMessage
    from datetime import datetime, timezone
    from decimal import Decimal

    # Resolve / create the conversation up-front so we have an id to
    # attach to all writes below.
    if body.conversation_id is not None:
        conv = (await db.execute(
            select(ChatConversation).where(
                ChatConversation.id == body.conversation_id,
                ChatConversation.site_id == site_id,
                ChatConversation.kind == "free",
            )
        )).scalar_one_or_none()
        if conv is None:
            raise HTTPException(
                status_code=404,
                detail="conversation not found for this site",
            )
    else:
        conv = ChatConversation(
            site_id=site_id,
            kind="free",
            action_id=None,
            title=None,
            message_count=0,
            total_cost_usd=Decimal("0"),
        )
        db.add(conv)
        await db.flush()

    # Idempotency: if the exact same user message arrived for this
    # conversation in the last 60 seconds (double-click, F5 during
    # in-flight, second tab), serve the cached assistant reply instead
    # of paying for another LLM call AND avoid duplicating ChatMessage
    # rows / inflating message_count + total_cost_usd.
    dedup_cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
    recent_user = (await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == conv.id,
            ChatMessage.role == "user",
            ChatMessage.content == msg,
            ChatMessage.created_at >= dedup_cutoff,
        )
        .order_by(desc(ChatMessage.created_at))
        .limit(1)
    )).scalar_one_or_none()
    if recent_user is not None:
        cached_asst = (await db.execute(
            select(ChatMessage)
            .where(
                ChatMessage.conversation_id == conv.id,
                ChatMessage.role == "assistant",
                ChatMessage.created_at > recent_user.created_at,
            )
            .order_by(ChatMessage.created_at.asc())
            .limit(1)
        )).scalar_one_or_none()
        if cached_asst is not None:
            return FreeChatResponse(
                conversation_id=conv.id,
                reply=cached_asst.content or None,
                proposal=None,
                cost_usd=float(cached_asst.cost_usd or 0),
                model=cached_asst.model,
                input_tokens=cached_asst.input_tokens,
                output_tokens=cached_asst.output_tokens,
                truncated=False,
                deduped=True,
            )

    # Load persisted history from DB. The client may pass nothing on
    # reload — DB is the source of truth, so we always read.
    history_rows = (await db.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conv.id)
        .order_by(ChatMessage.created_at.asc())
    )).scalars().all()
    history = [
        {"role": r.role, "content": r.content}
        for r in history_rows[-MAX_HISTORY_MESSAGES:]
    ]

    snap = await build_snapshot(db, site)
    plan = build_plan(snap, max_actions=10, target_config=site.target_config or {})

    # Long-term memory across this owner's previous conversations on
    # this site. Lets the assistant ground its tone in "things you
    # already told me" instead of starting cold every chat.
    from app.core_audit.brain.memory import load_recent_owner_turns
    long_term_memory = await load_recent_owner_turns(
        db, site.id, exclude_conversation_id=conv.id,
    )

    import anyio

    if body.mode == "battle_plan":
        # If the owner sent the plain preset ("собери боевой план под
        # цель топ-5...") we serve the cheap deterministic renderer.
        # Any extra wording — "только индексация", "коротко в 3
        # шага", "без E-E-A-T", "разверни punkt 2" — means they want
        # an adapted answer, so we pass the deterministic plan as a
        # FACT SEED into the LLM and let it reshape it. No new facts
        # invented; only re-ordering / filtering / re-wording.
        msg_l = (msg or "").lower()
        is_default_preset = (
            "цель топ-5" in msg_l
            and "5 самых сильных" in msg_l
        )
        if is_default_preset:
            result = battle_plan_result(snap, plan)
        else:
            from app.core_audit.brain.battle_plan import render_battle_plan_reply
            seed = render_battle_plan_reply(snap, plan)
            result = await anyio.to_thread.run_sync(
                lambda: free_chat(
                    domain=site.domain,
                    target_config=site.target_config or {},
                    understanding=site.understanding or {},
                    snap=snap,
                    plan=plan,
                    history=history,
                    new_message=msg,
                    mode="battle_plan",
                    long_term_memory=long_term_memory,
                    battle_plan_seed=seed,
                ),
            )
    else:
        result = await anyio.to_thread.run_sync(
            lambda: free_chat(
                domain=site.domain,
                target_config=site.target_config or {},
                understanding=site.understanding or {},
                snap=snap,
                plan=plan,
                history=history,
                new_message=msg,
                mode=body.mode,
                long_term_memory=long_term_memory,
            ),
        )

    # Persist both turns atomically. Title set on first user turn.
    now = datetime.now(timezone.utc)
    user_row = ChatMessage(
        conversation_id=conv.id,
        role="user",
        content=msg,
        cost_usd=Decimal("0"),
    )
    # When the model called the focus tool with no accompanying text,
    # store a stable placeholder so the conversation thread reads
    # naturally on reload — UI also gets `proposal` separately.
    proposal = result.get("proposal")
    if result.get("reply"):
        assistant_content = result["reply"]
    elif proposal:
        rationale = (proposal.get("rationale") or "").strip()
        label = (proposal.get("label") or "").strip()
        assistant_content = (
            f"📌 Предложил установить стратегический фокус: «{label}». "
            f"{rationale}".strip()
        )
    else:
        assistant_content = ""
    asst_row = ChatMessage(
        conversation_id=conv.id,
        role="assistant",
        content=assistant_content,
        model=result.get("model") or None,
        cost_usd=Decimal(str(result.get("cost_usd") or 0.0)),
        input_tokens=int(result.get("input_tokens") or 0),
        output_tokens=int(result.get("output_tokens") or 0),
    )
    db.add(user_row)
    db.add(asst_row)

    cost_delta = Decimal(str(result.get("cost_usd") or 0.0))
    # Atomic increment: read-modify-write would lose updates if two
    # tabs send messages near-concurrently for the same conversation.
    # Anyway after this UPDATE, conv on the session is stale — refresh
    # before returning so the response reflects truth.
    from sqlalchemy import update as sa_update
    title_set: dict = {}
    if conv.title is None:
        title_set["title"] = msg[:120]
    await db.execute(
        sa_update(ChatConversation)
        .where(ChatConversation.id == conv.id)
        .values(
            message_count=ChatConversation.message_count + 2,
            total_cost_usd=ChatConversation.total_cost_usd + cost_delta,
            last_message_at=now,
            **title_set,
        )
    )

    await db.commit()

    proposal_out: FocusProposalOut | None = None
    if proposal:
        proposal_out = FocusProposalOut(
            label=str(proposal.get("label") or ""),
            products=list(proposal.get("products") or []),
            regions=list(proposal.get("regions") or []),
            query_signals=list(proposal.get("query_signals") or []),
            deprioritised=list(proposal.get("deprioritised") or []),
            exit_criterion=proposal.get("exit_criterion"),
            owner_note=proposal.get("owner_note"),
            deadline=proposal.get("deadline"),
            rationale=str(proposal.get("rationale") or ""),
        )

    return FreeChatResponse(
        conversation_id=conv.id,
        reply=result.get("reply"),
        proposal=proposal_out,
        cost_usd=result["cost_usd"],
        model=result.get("model") or None,
        input_tokens=result.get("input_tokens"),
        output_tokens=result.get("output_tokens"),
        truncated=bool(result.get("truncated")),
        deduped=False,
    )


# ── Conversation list / detail / delete ──────────────────────────────


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str | None
    message_count: int
    total_cost_usd: float
    last_message_at: datetime | None
    created_at: datetime


class ConversationMessage(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    model: str | None = None
    cost_usd: float
    input_tokens: int
    output_tokens: int
    created_at: datetime


class ConversationDetail(BaseModel):
    id: uuid.UUID
    title: str | None
    message_count: int
    total_cost_usd: float
    last_message_at: datetime | None
    created_at: datetime
    messages: list[ConversationMessage]


@router.get(
    "/sites/{site_id}/conversations",
    response_model=list[ConversationSummary],
    dependencies=[Depends(_require_admin)],
)
async def list_conversations(
    site_id: uuid.UUID,
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
) -> list[ConversationSummary]:
    """List free-chat conversations for the site, newest activity
    first. Used by the sidebar list in /studio/chat."""
    await _site_or_404(db, site_id)
    from app.models.chat import ChatConversation

    rows = (await db.execute(
        select(ChatConversation)
        .where(
            ChatConversation.site_id == site_id,
            ChatConversation.kind == "free",
        )
        .order_by(
            ChatConversation.last_message_at.desc().nulls_last(),
            ChatConversation.created_at.desc(),
        )
        .limit(max(1, min(limit, 100)))
    )).scalars().all()
    return [
        ConversationSummary(
            id=c.id,
            title=c.title,
            message_count=c.message_count or 0,
            total_cost_usd=float(c.total_cost_usd or 0),
            last_message_at=c.last_message_at,
            created_at=c.created_at,
        )
        for c in rows
    ]


@router.get(
    "/sites/{site_id}/conversations/{conversation_id}",
    response_model=ConversationDetail,
    dependencies=[Depends(_require_admin)],
)
async def get_conversation(
    site_id: uuid.UUID,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ConversationDetail:
    """Full message log for one conversation. Used when /studio/chat
    opens with `?c=<id>` — we hydrate the UI from this."""
    await _site_or_404(db, site_id)
    from app.models.chat import ChatConversation, ChatMessage

    conv = (await db.execute(
        select(ChatConversation).where(
            ChatConversation.id == conversation_id,
            ChatConversation.site_id == site_id,
            ChatConversation.kind == "free",
        )
    )).scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    msgs = (await db.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conv.id)
        .order_by(ChatMessage.created_at.asc())
    )).scalars().all()

    return ConversationDetail(
        id=conv.id,
        title=conv.title,
        message_count=conv.message_count or 0,
        total_cost_usd=float(conv.total_cost_usd or 0),
        last_message_at=conv.last_message_at,
        created_at=conv.created_at,
        messages=[
            ConversationMessage(
                id=m.id,
                role=m.role,
                content=m.content,
                model=m.model,
                cost_usd=float(m.cost_usd or 0),
                input_tokens=m.input_tokens or 0,
                output_tokens=m.output_tokens or 0,
                created_at=m.created_at,
            )
            for m in msgs
        ],
    )


@router.delete(
    "/sites/{site_id}/conversations/{conversation_id}",
    status_code=204,
    dependencies=[Depends(_require_admin)],
)
async def delete_conversation(
    site_id: uuid.UUID,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Owner-initiated «удалить чат». Cascades to messages via FK."""
    await _site_or_404(db, site_id)
    from app.models.chat import ChatConversation

    conv = (await db.execute(
        select(ChatConversation).where(
            ChatConversation.id == conversation_id,
            ChatConversation.site_id == site_id,
            ChatConversation.kind == "free",
        )
    )).scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    await db.delete(conv)
    await db.commit()


# ── Studio v2 etap 7 (Phase E) · Strategic focus ─────────────────────


class StrategicFocusOut(BaseModel):
    label: str
    active_since: str
    set_by: str
    products: list[str]
    regions: list[str]
    query_signals: list[str]
    deprioritised: list[str]
    exit_criterion: str | None
    owner_note: str | None
    deadline: str | None


class StrategicFocusIn(BaseModel):
    """Owner-supplied focus shape. Server validates + normalises via
    core_audit.strategic_focus.validate_and_normalise. All fields are
    optional in JSON terms, but at least one of products / regions /
    query_signals must be populated — enforced server-side."""
    label: str
    products: list[str] = []
    regions: list[str] = []
    query_signals: list[str] = []
    deprioritised: list[str] = []
    exit_criterion: str | None = None
    owner_note: str | None = None
    deadline: str | None = None


@router.get(
    "/sites/{site_id}/strategic-focus",
    response_model=StrategicFocusOut | None,
    dependencies=[Depends(_require_admin)],
)
async def get_strategic_focus(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> StrategicFocusOut | None:
    """Return the site's current focus, or null if none."""
    site = await _site_or_404(db, site_id)
    from app.core_audit.strategic_focus import from_target_config

    focus = from_target_config(site.target_config or {})
    if focus is None:
        return None
    return StrategicFocusOut(**focus.to_jsonb())


@router.put(
    "/sites/{site_id}/strategic-focus",
    response_model=StrategicFocusOut,
    dependencies=[Depends(_require_admin)],
)
async def set_strategic_focus(
    site_id: uuid.UUID,
    body: StrategicFocusIn,
    db: AsyncSession = Depends(get_db),
) -> StrategicFocusOut:
    """Manual set/update of focus from the /studio/profile UI.

    Replaces any existing focus. The chat tool-call path uses the
    same logic but tags `set_by='owner_via_chat'` — see
    apply_strategic_focus_proposal below.
    """
    site = await _site_or_404(db, site_id)
    from app.core_audit.sites.locks import lock_site_target_config
    from app.core_audit.strategic_focus import (
        FocusValidationError,
        validate_and_normalise,
    )

    try:
        focus = validate_and_normalise(
            body.model_dump(),
            set_by="owner_via_ui",
        )
    except FocusValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    await lock_site_target_config(db, site_id)
    fresh = (await db.execute(
        select(Site).where(Site.id == site_id)
    )).scalar_one()
    cfg = dict(fresh.target_config or {})
    cfg["strategic_focus"] = focus.to_jsonb()
    fresh.target_config = cfg
    await db.commit()

    return StrategicFocusOut(**focus.to_jsonb())


@router.delete(
    "/sites/{site_id}/strategic-focus",
    status_code=204,
    dependencies=[Depends(_require_admin)],
)
async def clear_strategic_focus(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Owner-initiated «снять фокус» — back to general mode."""
    await _site_or_404(db, site_id)
    from app.core_audit.sites.locks import lock_site_target_config

    await lock_site_target_config(db, site_id)
    fresh = (await db.execute(
        select(Site).where(Site.id == site_id)
    )).scalar_one()
    cfg = dict(fresh.target_config or {})
    if "strategic_focus" in cfg:
        del cfg["strategic_focus"]
        fresh.target_config = cfg
        await db.commit()


@router.post(
    "/sites/{site_id}/strategic-focus/from-proposal",
    response_model=StrategicFocusOut,
    dependencies=[Depends(_require_admin)],
)
async def apply_strategic_focus_proposal(
    site_id: uuid.UUID,
    body: StrategicFocusIn,
    db: AsyncSession = Depends(get_db),
) -> StrategicFocusOut:
    """Apply a focus that originated from a chat proposal. Same shape
    as PUT but tagged `set_by='owner_via_chat'` for telemetry — lets
    us tell apart manual edits from chat-driven applications."""
    site = await _site_or_404(db, site_id)
    from app.core_audit.sites.locks import lock_site_target_config
    from app.core_audit.strategic_focus import (
        FocusValidationError,
        validate_and_normalise,
    )

    try:
        focus = validate_and_normalise(
            body.model_dump(),
            set_by="owner_via_chat",
        )
    except FocusValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    await lock_site_target_config(db, site_id)
    fresh = (await db.execute(
        select(Site).where(Site.id == site_id)
    )).scalar_one()
    cfg = dict(fresh.target_config or {})
    cfg["strategic_focus"] = focus.to_jsonb()
    fresh.target_config = cfg
    await db.commit()

    return StrategicFocusOut(**focus.to_jsonb())


# ── Deep extract (Playwright-rendered page snapshot) ─────────────────


class DeepExtractTriggerOut(BaseModel):
    status: str
    task_id: str
    url: str


class DeepExtractRow(BaseModel):
    id: str
    url: str
    is_competitor: bool
    competitor_domain: str | None
    status: str
    error: str | None
    extracted_at: str
    duration_ms: int | None
    title: str | None
    h1: str | None
    meta_description: str | None
    headings_tree: list[dict] | None
    cta_inventory: list[dict] | None
    forms_inventory: list[dict] | None
    images_inventory: list[dict] | None
    css_palette: list[dict] | None
    fonts: list[dict] | None
    layout_meta: dict | None
    performance: dict | None
    js_errors: list[dict] | None
    schema_blocks: list[dict] | None
    has_screenshot_desktop: bool
    has_screenshot_mobile: bool


@router.post(
    "/sites/{site_id}/pages/{page_id}/deep-extract",
    response_model=DeepExtractTriggerOut,
    dependencies=[Depends(_require_admin)],
)
async def trigger_deep_extract_own_page(
    site_id: uuid.UUID,
    page_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DeepExtractTriggerOut:
    """Run Playwright-rendered extraction on one of our pages.

    Resolves the URL from the Page row, queues `deep_extract_own_page`.
    Frontend polls /deep-extract for the latest row.
    """
    site = await _site_or_404(db, site_id)
    page = (await db.execute(
        select(Page).where(Page.id == page_id, Page.site_id == site.id)
    )).scalar_one_or_none()
    if page is None:
        raise HTTPException(status_code=404, detail="page not found for this site")

    from app.workers.celery_app import celery_app as _ca
    r = _ca.send_task(
        "deep_extract_own_page",
        args=[str(site_id), str(page_id)],
    )
    return DeepExtractTriggerOut(status="queued", task_id=r.id, url=page.url)


class CompetitorDeepExtractIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


@router.post(
    "/sites/{site_id}/competitors/deep-extract",
    response_model=DeepExtractTriggerOut,
    dependencies=[Depends(_require_admin)],
)
async def trigger_deep_extract_competitor(
    site_id: uuid.UUID,
    body: CompetitorDeepExtractIn,
    db: AsyncSession = Depends(get_db),
) -> DeepExtractTriggerOut:
    """Run Playwright-rendered extraction on any competitor URL.

    No page_id (competitor pages aren't in our pages table). The
    extracted row is tagged is_competitor=True and grouped by
    competitor_domain in the UI.
    """
    site = await _site_or_404(db, site_id)
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")

    from app.workers.celery_app import celery_app as _ca
    r = _ca.send_task(
        "deep_extract_competitor_url",
        args=[str(site_id), url],
    )
    return DeepExtractTriggerOut(status="queued", task_id=r.id, url=url)


def _row_to_extract(row) -> DeepExtractRow:
    return DeepExtractRow(
        id=str(row.id),
        url=row.url,
        is_competitor=row.is_competitor,
        competitor_domain=row.competitor_domain,
        status=row.status,
        error=row.error,
        extracted_at=row.extracted_at.isoformat() if row.extracted_at else "",
        duration_ms=row.duration_ms,
        title=row.title,
        h1=row.h1,
        meta_description=row.meta_description,
        headings_tree=row.headings_tree,
        cta_inventory=row.cta_inventory,
        forms_inventory=row.forms_inventory,
        images_inventory=row.images_inventory,
        css_palette=row.css_palette,
        fonts=row.fonts,
        layout_meta=row.layout_meta,
        performance=row.performance,
        js_errors=row.js_errors,
        schema_blocks=row.schema_blocks,
        has_screenshot_desktop=bool(row.screenshot_desktop_path),
        has_screenshot_mobile=bool(row.screenshot_mobile_path),
    )


@router.get(
    "/sites/{site_id}/pages/{page_id}/deep-extract",
    response_model=DeepExtractRow | None,
    dependencies=[Depends(_require_admin)],
)
async def get_deep_extract_for_page(
    site_id: uuid.UUID,
    page_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DeepExtractRow | None:
    """Return the latest deep extract for one of our pages, or null."""
    from app.models.page_deep_extract import PageDeepExtract

    row = (await db.execute(
        select(PageDeepExtract)
        .where(
            PageDeepExtract.site_id == site_id,
            PageDeepExtract.page_id == page_id,
        )
        .order_by(PageDeepExtract.extracted_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if row is None:
        return None
    return _row_to_extract(row)


@router.get(
    "/sites/{site_id}/competitors/deep-extracts",
    dependencies=[Depends(_require_admin)],
)
async def list_competitor_deep_extracts(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List latest competitor extracts grouped by competitor_domain."""
    from app.models.page_deep_extract import PageDeepExtract

    rows = (await db.execute(
        select(PageDeepExtract)
        .where(
            PageDeepExtract.site_id == site_id,
            PageDeepExtract.is_competitor.is_(True),
        )
        .order_by(PageDeepExtract.extracted_at.desc())
        .limit(50)
    )).scalars().all()
    return {"items": [_row_to_extract(r).model_dump() for r in rows]}


class DeepExtractAnalyzeOut(BaseModel):
    extract_id: str
    summary_md: str
    cost_usd: float
    model: str


_DEEP_ANALYZE_SYSTEM = """\
Ты SEO/CRO/GEO-аудитор уровня senior для русского туристического рынка
2026 года. Тебе дают живой снимок страницы (после JavaScript-рендера) +
бизнес-контекст сайта. Твоя задача — превратить это в **конкретный**
список правок для топ-5 в Яндексе И для AI-цитирования (Алиса/Нейро).

ПРАВИЛО P0 — JS-ошибки и hydration:
Если в данных есть `JS-ошибок при рендере: N` и N≥3 — это **пункт №1**
в «Что мешает топ-5», всё остальное вторично. React hydration errors
(#418, #419) означают: DOM перерисовывается, Яндексбот может видеть
разный контент в разные обходы, FAQ/Schema могут не попадать в индекс,
CLS=0 в данных снят до гидратации (на клиенте может быть высоким),
INP резко страдает. Пиши прямо: «починить hydration ДО любых других
правок, иначе они не дадут эффекта».

ПРАВИЛО P1 — бизнес-контекст важнее косметики:
Если в данных есть блок «БИЗНЕС-КОНТЕКСТ» — учитывай его. Если бизнес
выезжает из 3+ городов — НЕ ПРЕДЛАГАЙ убирать гео из H1; вместо этого
рекомендуй мульти-гео через areaServed/programmatic/H2-блоки (см.
чек-лист #5). Если бизнес имеет узкий фокус (strategic_focus) —
советы должны идти именно под этот фокус.

ГЛАВНОЕ ПРАВИЛО О ЦИФРАХ: **каждая цифра в ответе — со ссылкой**.
Используешь цифру из чек-листов ниже — ставишь `[источник: …]`.
Своя цифра без источника — пишешь `[оценка, не измерено]`. Не выдавай
мнение за факт.

ЗАПРЕТ НА GENERIC-СОВЕТЫ:
- «сделать контрастнее» / «улучшить кнопку» / «использовать яркий цвет»
  без указания текущего hex, фона, contrast-ratio — запрещено.
- «CTA-цвет слабый» — только если видишь конкретный hex и contrast<4.5:1
  по WCAG AA. Иначе не пиши вообще.
- «LCP без запаса» при LCP≤2200 мс — не пиши, это в Good-зоне.
- «слишком много CTA выше fold» — не путай меню (навигацию) с CTA. Меню
  из 5-7 пунктов на homepage — норма.

────────────────────────────────────────────────────────────────────
ЧЕК-ЛИСТ #1 — TECHNICAL SEO (Яндекс 2026, Vega v2)
────────────────────────────────────────────────────────────────────
- **Canonical в DOM** (`<link rel="canonical">`). Без него Яндекс склеивает дубли по своему усмотрению (UTM, ?from=, /index.html).
- **Meta robots / X-Robots в DOM**. Один забытый `noindex` после переноса с staging — самая частая причина обвала туристического трафика.
- **Title 50-60 симв., description 140-160**, обязательно гео в первых 30 симв. — иначе мобильный CTR падает [мобайл = 60-70% трафика, Mediascope 2025].
- **Ровно один H1** = {услуга}+{гео}. Vega v2 использует заголовочную иерархию для кластеризации — размытый H1 ломает её.
- **lang="ru" на `<html>`**. Англ. атрибут на странице про Сочи режет показы в РФ.
- **JS-rendering зависимость**: контент должен быть в исходном HTML, не только после гидратации. Яндексбот рендерит JS с задержкой 2-7 дней против моментального SSR — для сезонного туризма это потеря окна продаж.
- **Viewport meta**: `width=device-width, initial-scale=1`, без `user-scalable=no`. Без него — автоминус в мобильной выдаче Яндекса.
- **Touch-targets ≥44×44 px** [Apple HIG, WCAG 2.2]. Маленькие кнопки = рост INP, отказов, минус ПФ.
- **LCP ≤2.5s, CLS ≤0.1, INP ≤200мс** [web.dev/vitals 2026 — INP заменил FID с марта 2024]. LCP-элемент часто = hero-картинка без `width/height` или lazy-load на первом экране. **При наличии JS-ошибок hydration — CLS=0 в данных НЕ значит CLS=0 на клиенте**, реальное значение измерить нельзя без чистого клиентского замера.

────────────────────────────────────────────────────────────────────
ЧЕК-ЛИСТ #2 — GEO/AI SEARCH (Алиса, Нейро, llms.txt)
────────────────────────────────────────────────────────────────────
- **Длина passage 134-167 слов** [Seer Interactive 2024]. Простыни >250 и огрызки <40 слов резко теряют цитируемость в Алисе/Нейро.
- **Прямой ответ в первых 40-60 словах после H2**. «Экскурсия длится 4 часа, 2500₽», а не «мы команда с 2010 года». Нейро-сниппет берёт первые ~50 слов как ответ.
- **Вопросные H2/H3** («Сколько стоит…», «Что входит…») коррелируют с попаданием в AI Overviews ~+40% [Ahrefs AIO 2024].
- **FAQPage schema + видимый FAQ-блок** — вопросы из schema должны быть в DOM (cloaking-риск, иначе игнор Яндексом).
- **Конкретика с цифрами и единицами**: `\\d+ ₽`, длительность ч/мин, км, чел. LLM-аудиторы экстрагируют именно числовые сущности; «доступные цены» отбрасываются.
- **Self-contained answer blocks** — каждый раздел читается изолированно. Без «как выше», «эта экскурсия» без указания какая.
- **Brand-mention сигналы**: упоминание бренда рядом с ключевой сущностью (город, объект); ссылки на VK/Дзен/Я.Карты/Отзовик. YouTube-упоминания корреляция ~0.737 с AI-цитатами [Seer 2024]; РФ-эквивалент — Дзен + VK Видео.

────────────────────────────────────────────────────────────────────
ЧЕК-ЛИСТ #3 — SCHEMA.ORG (валидация webmaster.yandex.com/tools/microtest/)
────────────────────────────────────────────────────────────────────
- **TouristTrip / Trip как основной @type** с `name`, `itinerary`, `provider`, `offers`, `touristType`. Только `Product`/`Event` без TouristTrip — Яндекс хуже понимает турпродукт [schema.org/TouristTrip].
- **Product+Offer**: `price` числом, `priceCurrency: "RUB"`, `availability: InStock`, `validFrom`+`priceValidUntil` (ISO 8601). `"от 2500"` строкой — Яндекс/Google игнорируют. Корректные офферы → +20-30% CTR [Google Search Central 2024].
- **AggregateRating + Review** на странице (не только в JSON). `reviewCount` ≥1 и те же отзывы рендерятся в DOM — иначе бан с 2023 [Яндекс Вебмастер].
- **Organization/TravelAgency с РТО+ИНН+ОГРН** через `taxID`, `identifier: {propertyID: "RTO", value: ...}`, `legalName`. Critical trust-сигнал для туризма по 132-ФЗ.
- **TouristAttraction с `geo.GeoCoordinates`** (`latitude`/`longitude`). Без geo Яндекс не привязывает страницу к гео-выдаче «Сочи».
- **BreadcrumbList**: `itemListElement` упорядочен (`position: 1,2,3`), все `item` — абсолютные URL. Хлебные крошки заменяют URL в сниппете и снижают bounce.
- **TouristTrip+AggregateRating+Review = +52% rich-сниппетов** [Schema.org case study + Yandex.Webmaster blog 2025].
- **Чистка**: нет deprecated (`HowTo`, `SpecialAnnouncement`); все `@context = "https://schema.org"` (https); даты ISO 8601; нет плейсхолдеров `[Название]`.

────────────────────────────────────────────────────────────────────
ЧЕК-ЛИСТ #4 — CONTENT QUALITY / E-E-A-T (Google QRG Sept 2025 + 132-ФЗ РФ)
────────────────────────────────────────────────────────────────────
- **Объём ≥800 слов** для service page, ≥1500 для гайдов [Google QRG 2025 §4.5].
- **Программа дня по часам** — H2 «Программа», ≥5 пунктов с таймингом и локациями [Experience signal, QRG §4.5.3].
- **РТО+ИНН+ОГРН в футере или «О нас»**. Critical Trust для YMYL-туризма [QRG §2.6.1, ФЗ-132].
- **Блоки «Что включено» / «Не включено»** — два списка по 4+ пункта с расшифровкой трансфера, билетов, обеда [QRG §2.6.3 transactional clarity].
- **Прозрачная цена со всеми сборами**: явная сумма + явные доплаты. «от X ₽» без условий — нарушение ст.10 ЗоЗПП РФ.
- **Гид: имя+опыт+фото**. Безличное «опытные гиды» — слабый Experience-сигнал [E-E-A-T 20%+25%].
- **Отзывы с атрибуцией** ≥10, имена+даты+источник (Я.Карты, TripAdvisor), ссылка на внешний агрегатор [QRG §2.6.2].
- **Картинки**: alt-coverage ≥90%, имена файлов не `image1.jpg`, ≥8 уникальных фото [QRG §4.5.3 first-hand visual evidence].
- **Читабельность**: 1 H2 на 200-300 слов, абзацы ≤4 предложений, Flesch-Kincaid ru ≥60.
- **Density главного ключа >3% — риск Баден-Бадена** [Searchengines.guru, фильтр Яндекса с 2017]. Каннибализация title↔H1 — опасно.

────────────────────────────────────────────────────────────────────
ЧЕК-ЛИСТ #5 — МУЛЬТИ-ГЕО ДЛЯ ТУРИЗМА (новый, специально под РФ-туризм)
────────────────────────────────────────────────────────────────────
Когда бизнес обслуживает несколько городов/регионов (выезд из Сочи,
Адлера, Красной Поляны, Гагры — типичный случай), нельзя пихать все
города в title. Правильные техники без переспама:

- **Schema.org `areaServed` с массивом городов** в TouristTrip/Service/TravelAgency. Самый дешёвый сигнал, ~1 час работы. Яндекс/AI читают как «обслуживает эти 4 города».
- **Programmatic-посадочные `/iz-{город}/`** — отдельная страница на каждый город выезда. Шаблон тот же, отличается первый абзац (где встречаемся, тайминг). Это **не дубли** — Tripster, Sputnik8 так делают [классика programmatic SEO 2024-26].
- **`sameAs` к VK / Дзен / Я.Карты / Я.Бизнес / YouTube** в Organization/TravelAgency. Brand-mention корреляция с AI-цитированием ~0.737 для YouTube [Seer Interactive 2024]. РФ-эквивалент — Дзен + VK Видео + Я.Карты.
- **Latent-geo H2-блоки в тексте**: «Выезд из Сочи», «Выезд из Адлера», «Выезд из Красной Поляны» — каждый с трансферным таймингом. Это даёт ранжирование по всем 4 гео без переспама H1.
- **FAQ-вопросы с геогородами**: «Откуда вы забираете?», «Можно ли из Гагры?» — естественные упоминания, любит и Яндекс, и Алиса.
- **Я.Бизнес карточка с НЕСКОЛЬКИМИ точками сбора** (Сочи, Адлер, Красная Поляна, Гагра). Отдельный канал, до 40% трафика для туризма Сочи [Profi.Travel MITT 2026].
- **llms.txt в корне** с перечислением гео и услуг. ChatGPT/Claude/Perplexity читают; 7.4% Fortune 500 уже внедрили [ProGEO 2026].

────────────────────────────────────────────────────────────────────
ОБЩИЕ UX-ЭВРИСТИКИ (без цифр)
────────────────────────────────────────────────────────────────────
- Цена выше fold обязательна для туризма [−30% конверсии без неё, Calltouch 2024]
- Контрастный CTA (оранжевый/зелёный на белом) — не серый
- Закон Хика: пятая+ кнопка одинакового размера ломает иерархию
- lr-коды Яндекса: Сочи 239, Адлер 20064, Красная Поляна 21622 — на каждый субрегион нужна отдельная посадочная

ПРАВИЛА КАК ОТВЕЧАТЬ:

1. ОПИРАЙСЯ ТОЛЬКО НА ДАННЫЕ. Не придумывай элементов которых нет в снимке.
2. КАЖДАЯ ПРАВКА КОНКРЕТНА. «Переписать «Узнать цену» на «Забронировать за 3200₽»», а не «улучшить кнопку».
3. ЦИФРЫ — С ИСТОЧНИКОМ из чек-листов выше: `[источник: web.dev CWV]`.
4. СВОЯ ЦИФРА — `[оценка, не измерено]`.
5. УЧИТЫВАЙ ЦВЕТА И ПОЗИЦИИ ИЗ ДАННЫХ.
6. **СТРОГАЯ ПРИОРИТИЗАЦИЯ**: упорядочивай пункты «Что мешает» и «План
   правок» НЕ по порядку чек-листов, а по реальной критичности:
   (a) JS-ошибки/hydration ≥3 → P0, всегда первым
   (b) Schema-пропуски (Offer, AggregateRating, areaServed) → P1
   (c) E-E-A-T trust (РТО, ИНН, отзывы) → P2
   (d) Content (FAQ, программа, «что включено») → P3
   (e) H1/title-семантика → P4
   (f) CRO-косметика (CTA-текст, цена в кнопке) → P5
7. **АНТИ-ГЕНЕРИК**: запрещены пункты «сделать контрастнее», «добавить
   яркости», «улучшить читабельность» без замера. Не пиши их вообще.

ФОРМАТ ОТВЕТА — строго Markdown. ВАЖНО: уложись в лимиты пунктов
ниже, иначе ответ обрежется.

## ✅ Что уже хорошо
**ровно 3-4 пункта**, по 1-2 строки каждый, с цитатами источников.

## 🔴 Что мешает топ-5
**ровно 6-8 пунктов** (не 10!), в порядке критичности (см. правило 6).
По каждому 1-2 предложения: что в данных + чек-лист (#1-#5) + почему.

## 🛠️ План правок (в порядке ROI)
**ровно 6-8 пунктов** (первый = самый выгодный). Каждый — компактный:
- **что**: одно предложение действия
- **почему**: одна короткая фраза с источником
- **сложность**: 5 мин / 30 мин / 2 часа / 1 день

(БЕЗ отдельной строки «как проверить» — не пиши её, чтобы влезть в лимит.)

## 🌐 Проверить вне страницы (off-page)
**ровно 3-5 пунктов** — рекомендации, которые НЕ видны в снимке но
критичны для туристического сайта в Яндекс 2026:
- Я.Бизнес карточка: есть/нет, рейтинг, количество фото
- Я.Карты: pin'ы для всех точек выезда?
- `llms.txt` в корне домена
- Внешние brand-mention (VK / Дзен / Tripadvisor / Отзовик)
- `sameAs` в Organization-schema (есть в данных?)

## 📚 Источники
Bullet-список процитированных источников, без повторов.

Без воды. Без «следует рассмотреть». Без длинных обоснований.
Прямо: «перепиши X на Y». Если упираешься в лимит — режь объяснения,
а не пункты.
"""


def _format_business_context(site) -> str:
    """Compact business context block from Site.target_config / understanding.

    Surfaces the few facts the analyzer must know to avoid bad advice
    (e.g. "remove geo from H1" when business serves 4 cities). Falls
    back to a stub if site has no onboarding-derived data yet.
    """
    if site is None:
        return ""
    tc = site.target_config or {}
    u = site.understanding or {}
    lines: list[str] = ["БИЗНЕС-КОНТЕКСТ САЙТА (учитывай это):"]
    domain = getattr(site, "domain", None)
    if domain:
        lines.append(f"  домен: {domain}")
    primary = tc.get("primary_product") or u.get("detected_niche")
    if primary:
        lines.append(f"  основной продукт: {str(primary)[:300]}")
    services = tc.get("services") or []
    if services:
        names = ", ".join(
            (s.get("name") if isinstance(s, dict) else str(s))
            for s in services[:6]
        )
        lines.append(f"  услуги: {names}")
    geo_primary = tc.get("geo_primary") or []
    geo_secondary = tc.get("geo_secondary") or []
    if geo_primary or geo_secondary:
        all_geo = []
        for g in geo_primary[:6]:
            all_geo.append(g if isinstance(g, str) else g.get("name", ""))
        for g in geo_secondary[:6]:
            all_geo.append(g if isinstance(g, str) else g.get("name", ""))
        all_geo = [g for g in all_geo if g]
        if all_geo:
            lines.append(f"  регионы (выезд/обслуживание): {', '.join(all_geo)}")
            if len(all_geo) >= 3:
                lines.append(
                    "  ⚠️ ВНИМАНИЕ: бизнес обслуживает 3+ городов — "
                    "НЕ предлагай убирать гео из H1; применяй чек-лист #5 "
                    "(areaServed, programmatic, latent-geo H2)."
                )
    sf = tc.get("strategic_focus")
    if sf:
        if isinstance(sf, dict):
            focus_str = f"продукты: {sf.get('products', '—')}; регионы: {sf.get('regions', '—')}"
        else:
            focus_str = str(sf)[:300]
        lines.append(f"  текущий strategic_focus: {focus_str}")
    narrative = tc.get("narrative_ru") or u.get("narrative_ru")
    if narrative:
        lines.append(f"  как сам бизнес себя описывает: «{str(narrative)[:400]}»")
    if len(lines) == 1:  # only header — no useful data
        return ""
    return "\n".join(lines)


def _format_extract_for_llm(extract) -> str:
    """Compact one-shot text representation of the extract for LLM."""
    parts: list[str] = []
    parts.append(f"URL: {extract.url}")
    if extract.is_competitor:
        parts.append(f"(это страница КОНКУРЕНТА — анализируй чтобы понять что у них работает лучше)")
    # P0 surface: JS errors before anything else — analyzer must see them
    # at the top so they don't get buried among 30 other facts.
    if extract.js_errors:
        n = len(extract.js_errors)
        sample = "; ".join((e.get("message") or "")[:80] for e in extract.js_errors[:3])
        priority = "🚨 P0" if n >= 3 else "⚠️"
        parts.append(
            f"{priority} JS-ОШИБОК ПРИ РЕНДЕРЕ: {n} штук. "
            f"Первые 3: «{sample}». "
            f"Если это hydration #418/#419 — это пункт №1 всего отчёта."
        )
    parts.append(f"title: {extract.title or '—'}")
    parts.append(f"H1: {extract.h1 or '—'}")
    parts.append(f"meta description: {extract.meta_description or '—'}")
    if extract.full_text:
        parts.append(f"первые 800 символов текста: «{extract.full_text[:800]}»")
    if extract.headings_tree:
        parts.append("заголовки по порядку:")
        for h in extract.headings_tree[:25]:
            parts.append(f"  H{h.get('level')}: {h.get('text', '')[:120]}")
    perf = extract.performance or {}
    parts.append(
        f"скорость: LCP={perf.get('lcp')} мс, FCP={perf.get('fcp')} мс, "
        f"CLS={perf.get('cls')}"
    )
    layout = extract.layout_meta or {}
    parts.append(
        f"viewport={layout.get('viewport_w')}×{layout.get('viewport_h')}, "
        f"высота страницы={layout.get('doc_height')}, "
        f"sticky_header={layout.get('sticky_header')}, "
        f"sticky_cta={layout.get('sticky_cta')}"
    )
    if extract.cta_inventory:
        parts.append(f"кнопки на странице ({len(extract.cta_inventory)}):")
        for c in extract.cta_inventory[:15]:
            parts.append(
                f"  «{c.get('text','')[:60]}» цвет {c.get('color')} "
                f"фон {c.get('bg_color')} {c.get('width')}×{c.get('height')} "
                f"y={c.get('top')} {'выше fold' if c.get('above_fold') else 'ниже fold'}"
            )
    if extract.forms_inventory:
        parts.append(f"формы ({len(extract.forms_inventory)}):")
        for f in extract.forms_inventory[:5]:
            field_types = ", ".join(
                str((x or {}).get("type", "")) for x in (f.get("fields") or [])
            )
            parts.append(
                f"  {f.get('field_count')} полей ({field_types}) "
                f"{'выше fold' if f.get('above_fold') else 'ниже fold'}"
            )
    if extract.images_inventory:
        with_alt = sum(1 for i in extract.images_inventory if i.get("alt"))
        lazy = sum(1 for i in extract.images_inventory if i.get("lazy"))
        parts.append(
            f"картинки: всего {len(extract.images_inventory)}, "
            f"с alt={with_alt}, lazy-loaded={lazy}"
        )
        sample_alts = [
            (i.get("alt") or "").strip()[:80]
            for i in extract.images_inventory[:10]
            if i.get("alt")
        ]
        if sample_alts:
            parts.append("  примеры alt'ов (оцени уникальность / не generic ли):")
            for a in sample_alts:
                parts.append(f"    - «{a}»")
    if extract.css_palette:
        top_colors = ", ".join(
            f"{p.get('color')}×{p.get('count')}"
            for p in (extract.css_palette or [])[:6]
        )
        parts.append(f"цветовая палитра: {top_colors}")
    if extract.fonts:
        parts.append(
            "шрифты: "
            + ", ".join(f"{f.get('family')}×{f.get('count')}" for f in (extract.fonts or [])[:5])
        )
    if extract.schema_blocks:
        types = []
        for s in extract.schema_blocks:
            t = s.get("@type") if isinstance(s, dict) else None
            if t:
                types.append(str(t))
        parts.append(f"Schema.org блоки: {', '.join(types) if types else '—'}")
    else:
        parts.append("Schema.org блоки: НЕТ — Яндекс не получит rich-сниппет")
    # NB: js_errors already surfaced at top of report — don't repeat here.
    return "\n".join(parts)


@router.post(
    "/sites/{site_id}/deep-extracts/{extract_id}/analyze",
    response_model=DeepExtractAnalyzeOut,
    dependencies=[Depends(_require_admin)],
)
async def analyze_deep_extract(
    site_id: uuid.UUID,
    extract_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DeepExtractAnalyzeOut:
    """Run LLM analysis of a deep extract, return Markdown report.

    Caches the result on `ai_summary_md` so repeat clicks are cheap —
    if the row already has summary_md AND nothing newer, return cached.
    To force re-analyze, call with `?force=1`. (We rely on UI passing
    `?force=1` when the user clicks «Перезапустить» on the report.)
    """
    from app.agents.llm_client import call_plain
    from app.models.page_deep_extract import PageDeepExtract
    from app.models.site import Site

    row = (await db.execute(
        select(PageDeepExtract).where(
            PageDeepExtract.id == extract_id,
            PageDeepExtract.site_id == site_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="extract not found")
    if row.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"extract is not completed: {row.status}",
        )
    if row.ai_summary_md:
        return DeepExtractAnalyzeOut(
            extract_id=str(row.id),
            summary_md=row.ai_summary_md,
            cost_usd=0.0,
            model="cached",
        )

    site_row = (await db.execute(
        select(Site).where(Site.id == site_id)
    )).scalar_one_or_none()
    business_ctx = _format_business_context(site_row)
    extract_block = _format_extract_for_llm(row)
    user_msg = (
        "Вот живой снимок страницы (после JavaScript-рендера) + "
        "бизнес-контекст. Превратите это в конкретный план правок "
        "по правилам выше.\n\n"
        + (business_ctx + "\n\n" if business_ctx else "")
        + extract_block
    )

    import anyio
    # Vercel proxy free-tier hard-caps function execution at ~60s, so we use
    # the fast `cheap` tier (gpt-5.4-mini) — quality is plenty for structured
    # CRO recommendations and it answers in 20-30s vs 90+s for the full model.
    # 3000 tokens fits a complete answer (4 sections + 8-10 items each + sources)
    # without truncation; tested ~22s wall time end-to-end.
    text, usage = await anyio.to_thread.run_sync(
        lambda: call_plain(
            model_tier="cheap",
            system=_DEEP_ANALYZE_SYSTEM,
            user_message=user_msg,
            max_tokens=3000,
        )
    )

    summary_md = (text or "").strip()
    if not summary_md:
        raise HTTPException(status_code=502, detail="LLM returned empty summary")

    row.ai_summary_md = summary_md
    await db.commit()

    return DeepExtractAnalyzeOut(
        extract_id=str(row.id),
        summary_md=summary_md,
        cost_usd=float(usage.get("cost_usd") or 0.0),
        model=usage.get("model") or "",
    )


@router.get(
    "/sites/{site_id}/deep-extracts/{extract_id}/screenshot/{kind}",
    dependencies=[Depends(_require_admin)],
)
async def download_deep_extract_screenshot(
    site_id: uuid.UUID,
    extract_id: uuid.UUID,
    kind: str,
    db: AsyncSession = Depends(get_db),
):
    """Stream the desktop or mobile screenshot file."""
    from fastapi.responses import FileResponse
    from app.models.page_deep_extract import PageDeepExtract

    if kind not in ("desktop", "mobile"):
        raise HTTPException(status_code=400, detail="kind must be desktop|mobile")
    row = (await db.execute(
        select(PageDeepExtract).where(
            PageDeepExtract.id == extract_id,
            PageDeepExtract.site_id == site_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="extract not found")
    path = row.screenshot_desktop_path if kind == "desktop" else row.screenshot_mobile_path
    if not path:
        raise HTTPException(status_code=404, detail="screenshot not available")
    import os
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="file missing on disk")
    return FileResponse(path, media_type="image/png")


__all__ = ["router"]
