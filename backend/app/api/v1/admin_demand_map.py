"""Admin-only endpoints for the Target Demand Map (Phase B).

Authentication is a simple shared-secret header `X-Admin-Key` matching
`settings.ADMIN_API_KEY`. This is NOT a user-facing auth mechanism —
the endpoints are for ops / manual rebuilds / CSV export and are
expected to be locked down at the nginx layer as well.

Endpoints
---------
    POST /api/v1/admin/sites/{site_id}/target-config
        Overwrite the site's target_config (validated Pydantic body).

    POST /api/v1/admin/sites/{site_id}/demand-map/rebuild
        Queue a `demand_map_build_site` Celery task. Returns task_id.

    GET  /api/v1/admin/sites/{site_id}/demand-map
        List clusters with optional filters.

    GET  /api/v1/admin/sites/{site_id}/demand-map/export.csv
        CSV export for spreadsheet review.

All endpoints return 401 when the header is missing or wrong. If
`ADMIN_API_KEY` is the empty string (i.e. never configured) EVERY
request is rejected — fail-safe posture in Phase B.
"""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core_audit.demand_map.models import TargetCluster, TargetQuery
from app.database import get_db
from app.models.site import Site

router = APIRouter(prefix="/admin")


def _require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    """Header-based gate. Empty server-side key blocks everything."""
    configured = settings.ADMIN_API_KEY or ""
    if not configured:
        raise HTTPException(status_code=401, detail="admin api disabled")
    if not x_admin_key or x_admin_key != configured:
        raise HTTPException(status_code=401, detail="invalid admin key")


# ---------- Pydantic bodies -------------------------------------------------


class TargetConfigBody(BaseModel):
    """Minimal validator — mirrors the target_config JSONB schema.

    We accept the same shape the Phase A expander already consumes so
    that round-trips are lossless. Missing keys default to [] so ops
    can PATCH-style post a partial config without killing the whole
    site's map.
    """

    services: list[str] = Field(default_factory=list)
    excluded_services: list[str] = Field(default_factory=list)
    geo_primary: list[str] = Field(default_factory=list)
    geo_secondary: list[str] = Field(default_factory=list)
    excluded_geo: list[str] = Field(default_factory=list)
    competitor_brands: list[str] = Field(default_factory=list)
    months: list[str] = Field(default_factory=list)
    day_counts: list[str] = Field(default_factory=list)
    service_weights: dict[str, float] = Field(default_factory=dict)
    geo_weights: dict[str, float] = Field(default_factory=dict)
    # Future-proof: accept unknown keys without 422 (they are persisted as-is).

    model_config = {"extra": "allow"}


class QueuedResponse(BaseModel):
    task_id: str
    status: str


# ---------- endpoints -------------------------------------------------------


@router.post(
    "/sites/{site_id}/target-config",
    dependencies=[Depends(_require_admin)],
)
async def set_target_config(
    site_id: uuid.UUID,
    body: TargetConfigBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Overwrite a site's target_config. Returns the stored config."""
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")

    # Pydantic v2: model_dump() converts to plain dict, preserving extras.
    payload = body.model_dump(exclude_none=False)
    site.target_config = payload
    await db.flush()

    return {"site_id": str(site_id), "target_config": payload}


@router.post(
    "/sites/{site_id}/demand-map/rebuild",
    response_model=QueuedResponse,
    dependencies=[Depends(_require_admin)],
)
async def trigger_rebuild(site_id: uuid.UUID) -> QueuedResponse:
    """Queue a per-site demand map rebuild."""
    from app.core_audit.demand_map.tasks import demand_map_build_site_task
    task = demand_map_build_site_task.delay(str(site_id))
    return QueuedResponse(task_id=task.id, status="queued")


@router.get(
    "/sites/{site_id}/demand-map",
    dependencies=[Depends(_require_admin)],
)
async def list_demand_map(
    site_id: uuid.UUID,
    tier: str | None = Query(default=None),
    cluster_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return clusters with optional filters."""
    stmt = select(TargetCluster).where(TargetCluster.site_id == site_id)
    if tier:
        stmt = stmt.where(TargetCluster.quality_tier == tier)
    if cluster_type:
        stmt = stmt.where(TargetCluster.cluster_type == cluster_type)
    stmt = stmt.order_by(
        TargetCluster.business_relevance.desc(),
        TargetCluster.cluster_key.asc(),
    ).limit(limit)

    rows = (await db.execute(stmt)).scalars().all()

    # Query counts per cluster for the listing view.
    counts: dict[uuid.UUID, int] = {}
    if rows:
        ids = [r.id for r in rows]
        cnt_rows = await db.execute(
            select(TargetQuery.cluster_id, func.count(TargetQuery.id))
            .where(TargetQuery.cluster_id.in_(ids))
            .group_by(TargetQuery.cluster_id)
        )
        for cid, cnt in cnt_rows.all():
            counts[cid] = int(cnt)

    # Total count (unfiltered) for pagination context.
    total_stmt = (
        select(func.count(TargetCluster.id))
        .where(TargetCluster.site_id == site_id)
    )
    total = (await db.execute(total_stmt)).scalar_one()

    return {
        "clusters_total": int(total or 0),
        "items": [_cluster_dto(r, counts.get(r.id, 0)) for r in rows],
    }


@router.get(
    "/sites/{site_id}/demand-map/export.csv",
    dependencies=[Depends(_require_admin)],
)
async def export_demand_map_csv(
    site_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> Response:
    """CSV export — all clusters for the site, joined with query counts."""
    stmt = (
        select(TargetCluster)
        .where(TargetCluster.site_id == site_id)
        .order_by(
            TargetCluster.quality_tier.asc(),
            TargetCluster.business_relevance.desc(),
            TargetCluster.cluster_key.asc(),
        )
    )
    rows = (await db.execute(stmt)).scalars().all()

    counts: dict[uuid.UUID, int] = {}
    if rows:
        ids = [r.id for r in rows]
        cnt_rows = await db.execute(
            select(TargetQuery.cluster_id, func.count(TargetQuery.id))
            .where(TargetQuery.cluster_id.in_(ids))
            .group_by(TargetQuery.cluster_id)
        )
        for cid, cnt in cnt_rows.all():
            counts[cid] = int(cnt)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "cluster_key", "name_ru", "cluster_type", "intent_code",
        "quality_tier", "business_relevance", "expected_volume_tier",
        "is_competitor_brand", "source", "queries_count",
    ])
    for r in rows:
        writer.writerow([
            r.cluster_key,
            r.name_ru,
            r.cluster_type,
            r.intent_code,
            r.quality_tier,
            f"{float(r.business_relevance or 0):.3f}",
            r.expected_volume_tier,
            "1" if r.is_competitor_brand else "0",
            r.source,
            counts.get(r.id, 0),
        ])
    csv_text = buf.getvalue()

    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="demand_map_{site_id}.csv"'
            ),
        },
    )


# ---------- Phase F — Draft Profile endpoints ------------------------------


class CommitDraftBody(BaseModel):
    """Body for the commit-draft endpoint.

    `confirm=False` is a read-only preview (no write happens). Any
    `field_overrides` keys replace the corresponding keys on top of the
    draft_config before the final write.
    """

    confirm: bool = True
    field_overrides: dict[str, Any] = Field(default_factory=dict)


@router.post(
    "/sites/{site_id}/draft-profile/rebuild",
    response_model=QueuedResponse,
    dependencies=[Depends(_require_admin)],
)
async def trigger_draft_profile_rebuild(site_id: uuid.UUID) -> QueuedResponse:
    """Queue a `draft_profile_build_site` Celery task for the site."""
    from app.core_audit.draft_profile.tasks import (
        draft_profile_build_site_task,
    )
    task = draft_profile_build_site_task.delay(str(site_id))
    return QueuedResponse(task_id=task.id, status="queued")


@router.get(
    "/sites/{site_id}/draft-profile",
    dependencies=[Depends(_require_admin)],
)
async def get_draft_profile(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the stored draft profile blob for the site."""
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")
    draft = dict(site.target_config_draft or {})
    return {
        "site_id": str(site_id),
        "draft": draft,
        "has_draft": bool(draft),
    }


@router.post(
    "/sites/{site_id}/target-config/commit-draft",
    dependencies=[Depends(_require_admin)],
)
async def commit_draft_to_target_config(
    site_id: uuid.UUID,
    body: CommitDraftBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Copy `target_config_draft.draft_config` onto `target_config`.

    Applies optional `field_overrides` on top of the draft before
    writing. When `confirm=False`, returns a preview of the merged
    config without writing.
    """
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")

    draft_blob = dict(site.target_config_draft or {})
    draft_config = dict(draft_blob.get("draft_config") or {})
    if not draft_config:
        raise HTTPException(
            status_code=400, detail="no draft profile to commit",
        )

    merged: dict[str, Any] = dict(draft_config)
    for k, v in (body.field_overrides or {}).items():
        merged[k] = v

    if not body.confirm:
        return {
            "committed": False,
            "preview": True,
            "target_config": merged,
        }

    site.target_config = merged
    await db.flush()
    return {
        "committed": True,
        "target_config": merged,
    }


# ---------- Этап 1 — Onboarding wizard endpoints --------------------------


class OnboardingStepBody(BaseModel):
    """Body for PATCH /onboarding/step — advance or rewind wizard state."""

    onboarding_step: str


class UnderstandingPatchBody(BaseModel):
    """Body for PATCH /understanding — owner-edited version of the agent output."""

    narrative_ru: str | None = None
    detected_niche: str | None = None
    detected_positioning: str | None = None
    detected_usp: str | None = None


class ProductsPatchBody(BaseModel):
    """Body for PATCH /onboarding/products — step 2 output.

    `primary_product` is a human-readable marker of what the business
    considers its main offering (e.g. "багги-экспедиции"). The effective
    scoring happens via `service_weights` — a dict where the primary gets
    weight 1.0 and secondaries something in [0.2, 0.6]. Zeros effectively
    drop the service from the demand map.
    """

    primary_product: str | None = None
    service_weights: dict[str, float] = Field(default_factory=dict)
    secondary_products: list[str] = Field(default_factory=list)


class CompetitorsPatchBody(BaseModel):
    """Body for PATCH /onboarding/competitors — step 3 output."""

    competitor_domains: list[str] = Field(default_factory=list)
    competitor_brands: list[str] = Field(default_factory=list)


class ClusterReviewBody(BaseModel):
    """Body for PATCH /onboarding/clusters/{id} — step 4 output.

    Sent once per cluster when the owner reviews the demand map. `growth_intent`
    (grow/ignore/not_mine) drives which clusters feed the scorer hot path.
    """

    user_confirmed: bool | None = None
    growth_intent: str | None = None   # grow | ignore | not_mine


class KPIPatchBody(BaseModel):
    """Body for PATCH /onboarding/kpi — step 7 output."""

    baseline: dict[str, Any] = Field(default_factory=dict)
    target_3m: dict[str, Any] = Field(default_factory=dict)
    target_6m: dict[str, Any] = Field(default_factory=dict)
    target_12m: dict[str, Any] = Field(default_factory=dict)


VALID_GROWTH_INTENTS = {"grow", "ignore", "not_mine"}


VALID_ONBOARDING_STEPS = {
    "pending_analyze", "confirm_business", "confirm_products",
    "confirm_competitors", "confirm_queries", "confirm_positions",
    "confirm_plan", "confirm_kpi", "active",
}


@router.post(
    "/sites/{site_id}/onboarding/understanding/analyze",
    response_model=QueuedResponse,
    dependencies=[Depends(_require_admin)],
)
async def trigger_understanding_analyze(site_id: uuid.UUID) -> QueuedResponse:
    """Queue the BusinessUnderstandingAgent for a site (step 1)."""
    from app.core_audit.onboarding.tasks import onboarding_understand_site_task
    task = onboarding_understand_site_task.delay(str(site_id))
    return QueuedResponse(task_id=task.id, status="queued")


@router.get(
    "/sites/{site_id}/onboarding",
    dependencies=[Depends(_require_admin)],
)
async def get_onboarding_state(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Full onboarding state for a site — used by the wizard layout loader."""
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")
    return {
        "site_id": str(site_id),
        "domain": site.domain,
        "display_name": site.display_name,
        "onboarding_step": site.onboarding_step,
        "understanding": dict(site.understanding or {}),
        "target_config": dict(site.target_config or {}),
        "target_config_draft": dict(site.target_config_draft or {}),
        "competitor_domains": list(site.competitor_domains or []),
        "kpi_targets": dict(site.kpi_targets or {}),
    }


@router.patch(
    "/sites/{site_id}/onboarding/step",
    dependencies=[Depends(_require_admin)],
)
async def patch_onboarding_step(
    site_id: uuid.UUID,
    body: OnboardingStepBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Advance or rewind the wizard state. Validates against allowed values."""
    if body.onboarding_step not in VALID_ONBOARDING_STEPS:
        raise HTTPException(
            status_code=400,
            detail=f"onboarding_step must be one of {sorted(VALID_ONBOARDING_STEPS)}",
        )
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")
    site.onboarding_step = body.onboarding_step
    await db.flush()
    return {"site_id": str(site_id), "onboarding_step": site.onboarding_step}


@router.patch(
    "/sites/{site_id}/onboarding/products",
    dependencies=[Depends(_require_admin)],
)
async def patch_onboarding_products(
    site_id: uuid.UUID,
    body: ProductsPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Step 2 — primary product + service weights.

    Merges into sites.target_config (existing services, geo fields stay
    intact). The demand-map builder's compute_relevance() already reads
    service_weights, so this drives priority scoring directly.
    """
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")

    cfg = dict(site.target_config or {})
    if body.primary_product is not None:
        cfg["primary_product"] = body.primary_product
    if body.secondary_products:
        cfg["secondary_products"] = list(body.secondary_products)
    if body.service_weights:
        # Clip each weight into [0, 1] defensively — compute_relevance
        # multiplies these into r_service, so out-of-range values would
        # warp scoring silently.
        cleaned = {
            str(k): max(0.0, min(1.0, float(v)))
            for k, v in body.service_weights.items()
        }
        cfg["service_weights"] = cleaned

    site.target_config = cfg
    await db.flush()
    return {"site_id": str(site_id), "target_config": cfg}


@router.patch(
    "/sites/{site_id}/onboarding/understanding",
    dependencies=[Depends(_require_admin)],
)
async def patch_understanding(
    site_id: uuid.UUID,
    body: UnderstandingPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Owner-edited override on top of the agent output (step 1)."""
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")
    current = dict(site.understanding or {})
    for field_name in (
        "narrative_ru", "detected_niche",
        "detected_positioning", "detected_usp",
    ):
        value = getattr(body, field_name)
        if value is not None:
            current[field_name] = value
    current["user_edited_at"] = True  # cheap marker; timestamp via updated_at
    site.understanding = current
    await db.flush()
    return {"site_id": str(site_id), "understanding": current}


@router.patch(
    "/sites/{site_id}/onboarding/competitors",
    dependencies=[Depends(_require_admin)],
)
async def patch_onboarding_competitors(
    site_id: uuid.UUID,
    body: CompetitorsPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Step 3 — competitor domains + brands."""
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")
    site.competitor_domains = list(body.competitor_domains)
    cfg = dict(site.target_config or {})
    cfg["competitor_brands"] = list(body.competitor_brands)
    site.target_config = cfg
    await db.flush()
    return {
        "site_id": str(site_id),
        "competitor_domains": site.competitor_domains,
        "competitor_brands": cfg["competitor_brands"],
    }


@router.patch(
    "/sites/{site_id}/onboarding/clusters/{cluster_id}",
    dependencies=[Depends(_require_admin)],
)
async def patch_cluster_review(
    site_id: uuid.UUID,
    cluster_id: uuid.UUID,
    body: ClusterReviewBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Step 4 — owner confirms/rejects a cluster and sets its growth intent."""
    if body.growth_intent is not None and body.growth_intent not in VALID_GROWTH_INTENTS:
        raise HTTPException(
            status_code=400,
            detail=f"growth_intent must be one of {sorted(VALID_GROWTH_INTENTS)}",
        )
    cluster = (await db.execute(
        select(TargetCluster).where(
            TargetCluster.id == cluster_id,
            TargetCluster.site_id == site_id,
        )
    )).scalar_one_or_none()
    if cluster is None:
        raise HTTPException(status_code=404, detail="cluster not found")
    if body.user_confirmed is not None:
        cluster.user_confirmed = body.user_confirmed
    if body.growth_intent is not None:
        cluster.growth_intent = body.growth_intent
    await db.flush()
    return {
        "id": str(cluster.id),
        "user_confirmed": cluster.user_confirmed,
        "growth_intent": cluster.growth_intent,
    }


@router.patch(
    "/sites/{site_id}/onboarding/kpi",
    dependencies=[Depends(_require_admin)],
)
async def patch_onboarding_kpi(
    site_id: uuid.UUID,
    body: KPIPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Step 7 — persist KPI baseline + 3/6/12 month targets."""
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")
    site.kpi_targets = {
        "baseline": body.baseline,
        "target_3m": body.target_3m,
        "target_6m": body.target_6m,
        "target_12m": body.target_12m,
    }
    await db.flush()
    return {"site_id": str(site_id), "kpi_targets": site.kpi_targets}


@router.post(
    "/sites/{site_id}/onboarding/complete",
    dependencies=[Depends(_require_admin)],
)
async def complete_onboarding(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Finalize the wizard — flip onboarding_step to 'active'.

    After this, nightly Celery pipelines start including the site. The
    owner can still re-run individual steps later via PATCH /step.
    """
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")
    site.onboarding_step = "active"
    await db.flush()
    return {"site_id": str(site_id), "onboarding_step": "active"}


def _cluster_dto(r: TargetCluster, queries_count: int) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "cluster_key": r.cluster_key,
        "name_ru": r.name_ru,
        "intent_code": r.intent_code,
        "cluster_type": r.cluster_type,
        "quality_tier": r.quality_tier,
        "keywords": list(r.keywords or []),
        "seed_slots": dict(r.seed_slots or {}),
        "is_brand": bool(r.is_brand),
        "is_competitor_brand": bool(r.is_competitor_brand),
        "expected_volume_tier": r.expected_volume_tier,
        "business_relevance": float(r.business_relevance or 0),
        "source": r.source,
        "queries_count": queries_count,
        # Этап 1 — user-confirmation layer (step 4/5 of wizard)
        "user_confirmed": r.user_confirmed,
        "growth_intent": r.growth_intent,
        "query_intent": r.query_intent,
        "seasonality_peak_months": list(r.seasonality_peak_months or []),
        "page_intent_fit": r.page_intent_fit,
        "page_intent_fit_reason_ru": r.page_intent_fit_reason_ru,
    }


__all__ = ["router"]
