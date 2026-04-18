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
    }


__all__ = ["router"]
