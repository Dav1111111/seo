"""Admin operations — one-click pipeline, outcome tracking, edit lists.

All routes gated on X-Admin-Key. Intentionally small surface: each
route drives one UX button in the frontend.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core_audit.activity import log_event
from app.database import get_db
from app.models.daily_metric import DailyMetric
from app.models.outcome_snapshot import OutcomeSnapshot
from app.models.search_query import SearchQuery
from app.models.site import Site

router = APIRouter(prefix="/admin")


def _require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    configured = settings.ADMIN_API_KEY or ""
    if not configured:
        raise HTTPException(status_code=401, detail="admin api disabled")
    if not x_admin_key or x_admin_key != configured:
        raise HTTPException(status_code=401, detail="invalid admin key")


# ── Full pipeline ────────────────────────────────────────────────────────

@router.post(
    "/sites/{site_id}/pipeline/full",
    dependencies=[Depends(_require_admin)],
)
async def trigger_full_pipeline(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """One button: crawl → webmaster → demand map → competitors (auto-chain).

    Owner gets the whole platform running on their site without hunting
    through three pages. Spaces the stages so they don't collide.
    """
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")

    from app.workers.celery_app import celery_app

    # Previously these were staggered by 20/60/90s "just in case" one
    # stage's output fed the next. In practice none of them depend on
    # same-run output of another: crawl writes pages (nobody else reads
    # them within this flow), webmaster pulls fresh queries (demand_map
    # uses queries already in DB from prior nightly collects), and
    # competitors reads target_config + search_queries already there.
    # So fire them simultaneously — worker concurrency (-c 2) queues
    # what it can't run in parallel. Total wall time drops from ~120s
    # to ~30s.
    queued: list[str] = []
    for task_name in ("crawl_site", "collect_site_webmaster",
                      "demand_map_build_site", "competitors_discover_site"):
        celery_app.send_task(task_name, args=[str(site_id)])
        queued.append(task_name.replace("_site", "").replace("collect_", ""))

    await log_event(
        db, site_id, "pipeline", "started",
        "Запустил полный анализ: краулю сайт, тяну Вебмастер, строю карту "
        "спроса, ищу конкурентов. Обычно готово за 30–60 секунд.",
        extra={"queued": queued},
    )

    return {"status": "queued", "queued": queued}


# ── Outcome tracking ─────────────────────────────────────────────────────

class MarkAppliedBody(BaseModel):
    recommendation_id: str = Field(min_length=1, max_length=64)
    source: str = Field(pattern="^(priority|opportunity)$")
    page_url: str | None = None
    note_ru: str | None = Field(default=None, max_length=1000)


async def _baseline_metrics(
    db: AsyncSession, site_id: uuid.UUID, page_url: str | None,
) -> dict[str, Any]:
    """Last-7-days site-wide metrics as baseline.

    Page-level slicing stays out of scope for v1 — Webmaster doesn't
    give us URL-level query-performance reliably enough to break
    attribution by page.
    """
    today = date.today()
    week_ago = today - timedelta(days=7)
    row = (await db.execute(
        select(
            func.coalesce(func.sum(DailyMetric.impressions), 0).label("impressions"),
            func.coalesce(func.sum(DailyMetric.clicks), 0).label("clicks"),
            func.avg(DailyMetric.avg_position).label("avg_position"),
        ).where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.date.between(week_ago, today),
        )
    )).first()
    if row is None:
        return {"impressions_7d": 0, "clicks_7d": 0, "avg_position": None}
    return {
        "impressions_7d": int(row.impressions or 0),
        "clicks_7d": int(row.clicks or 0),
        "avg_position": float(row.avg_position) if row.avg_position else None,
    }


@router.post(
    "/sites/{site_id}/outcomes/applied",
    dependencies=[Depends(_require_admin)],
)
async def mark_applied(
    site_id: uuid.UUID,
    body: MarkAppliedBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Owner says 'I did this' — snapshot baseline for 14-day follow-up.

    Idempotent by (site_id, recommendation_id): second click returns the
    original snapshot instead of creating a duplicate.
    """
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")

    existing = (await db.execute(
        select(OutcomeSnapshot).where(
            OutcomeSnapshot.site_id == site_id,
            OutcomeSnapshot.recommendation_id == body.recommendation_id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return {
            "status": "already_marked",
            "snapshot_id": str(existing.id),
            "applied_at": existing.applied_at.isoformat(),
        }

    baseline = await _baseline_metrics(db, site_id, body.page_url)
    snap = OutcomeSnapshot(
        site_id=site_id,
        recommendation_id=body.recommendation_id,
        source=body.source,
        page_url=body.page_url,
        applied_at=datetime.utcnow(),
        baseline_metrics=baseline,
        note_ru=body.note_ru,
    )
    db.add(snap)
    await db.commit()
    await db.refresh(snap)

    await log_event(
        db, site_id, "outcome", "progress",
        f"Владелец отметил «применил» · измерим результат через 14 дней.",
        extra={
            "recommendation_id": body.recommendation_id,
            "source": body.source,
            "baseline": baseline,
        },
    )
    return {"status": "ok", "snapshot_id": str(snap.id)}


@router.get(
    "/sites/{site_id}/outcomes",
    dependencies=[Depends(_require_admin)],
)
async def list_outcomes(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rows = (await db.execute(
        select(OutcomeSnapshot)
        .where(OutcomeSnapshot.site_id == site_id)
        .order_by(desc(OutcomeSnapshot.applied_at))
        .limit(100)
    )).scalars().all()
    return {
        "outcomes": [
            {
                "id": str(r.id),
                "recommendation_id": r.recommendation_id,
                "source": r.source,
                "page_url": r.page_url,
                "applied_at": r.applied_at.isoformat(),
                "followup_at": r.followup_at.isoformat() if r.followup_at else None,
                "delta": r.delta or {},
                "baseline_metrics": r.baseline_metrics or {},
                "followup_metrics": r.followup_metrics or {},
                "note_ru": r.note_ru,
            }
            for r in rows
        ]
    }


# ── Competitors list edit ────────────────────────────────────────────────

class CompetitorsListBody(BaseModel):
    domains: list[str]


@router.put(
    "/sites/{site_id}/competitors/list",
    dependencies=[Depends(_require_admin)],
)
async def update_competitors_list(
    site_id: uuid.UUID,
    body: CompetitorsListBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Owner manually overrides SERP-derived competitor list.

    Stored on sites.competitor_domains (string list) plus marker in
    target_config so later discovery runs know the list was hand-edited.
    """
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")

    cleaned: list[str] = []
    seen: set[str] = set()
    for d in body.domains:
        d2 = (d or "").strip().lower().removeprefix("https://").removeprefix("http://")
        d2 = d2.removeprefix("www.").rstrip("/")
        if d2 and d2 not in seen:
            seen.add(d2)
            cleaned.append(d2)

    site.competitor_domains = cleaned
    cfg = dict(site.target_config or {})
    cfg["competitor_list_manually_edited_at"] = datetime.utcnow().isoformat()
    site.target_config = cfg
    await db.commit()

    await log_event(
        db, site_id, "competitor_discovery", "progress",
        f"Список конкурентов изменён вручную · теперь {len(cleaned)} доменов.",
        extra={"count": len(cleaned)},
    )
    return {"status": "ok", "competitor_domains": cleaned}


# ── Onboarding restart ───────────────────────────────────────────────────

@router.post(
    "/sites/{site_id}/onboarding/restart",
    dependencies=[Depends(_require_admin)],
)
async def restart_onboarding(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Reset wizard state so owner can re-do the 7-step onboarding.

    Leaves collected analytics data intact (that's expensive to rebuild).
    Only clears onboarding_step + understanding narrative.
    """
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")

    site.onboarding_step = "pending_analyze"
    # Clearing understanding forces step 1 to regenerate it
    site.understanding = None
    await db.commit()

    await log_event(
        db, site_id, "onboarding", "started",
        "Владелец запустил онбординг заново.",
    )
    return {"status": "ok", "onboarding_step": site.onboarding_step}
