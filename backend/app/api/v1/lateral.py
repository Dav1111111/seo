"""Studio API for Lateral Query Expansion (Block A roadmap).

Endpoints:
  GET   /admin/studio/sites/{site_id}/lateral-queries
  PATCH /admin/studio/sites/{site_id}/lateral-queries/{lateral_id}
  POST  /admin/studio/sites/{site_id}/lateral-queries/expand

Kept in its own module so `studio.py` (already a god-file) doesn't grow
further. Same `/admin/studio/...` prefix the frontend already proxies.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import require_admin
from app.core_audit.lateral.persistence import set_status
from app.database import get_db
from app.models.lateral_query import LateralQuery
from app.models.site import Site

router = APIRouter(prefix="/admin/studio")


def _require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    require_admin(x_admin_key or "")


# ── Shapes ────────────────────────────────────────────────────────────


class LateralQueryRow(BaseModel):
    id: uuid.UUID
    query: str
    relation: str
    confidence: float
    rationale: str | None
    source_signal: str
    status: str
    created_at: str
    accepted_at: str | None


class LateralListResponse(BaseModel):
    items: list[LateralQueryRow]
    counts: dict[str, int] = Field(
        description="status → count over the whole site, regardless of filter.",
    )


class LateralStatusPatch(BaseModel):
    status: Literal["new", "accepted", "rejected", "promoted"]


class LateralTriggerResponse(BaseModel):
    status: Literal["queued"]
    task_id: str


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get(
    "/sites/{site_id}/lateral-queries",
    dependencies=[Depends(_require_admin)],
    response_model=LateralListResponse,
)
async def list_lateral_queries(
    site_id: uuid.UUID,
    status: Literal["new", "accepted", "rejected", "promoted", "all"] = "new",
    db: AsyncSession = Depends(get_db),
) -> LateralListResponse:
    """List LLM-proposed lateral query ideas for a site.

    Defaults to `status=new` — the triage view. Pass `status=all` to see
    the whole history (used by the «what did the helper think last week»
    audit panel).
    """
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")

    stmt = select(LateralQuery).where(LateralQuery.site_id == site_id)
    if status != "all":
        stmt = stmt.where(LateralQuery.status == status)
    stmt = stmt.order_by(
        LateralQuery.confidence.desc(),
        LateralQuery.created_at.desc(),
    ).limit(200)

    rows = list((await db.execute(stmt)).scalars())

    items = [
        LateralQueryRow(
            id=r.id,
            query=r.query,
            relation=r.relation,
            confidence=float(r.confidence),
            rationale=r.rationale,
            source_signal=r.source_signal,
            status=r.status,
            created_at=r.created_at.isoformat() if r.created_at else "",
            accepted_at=(
                r.accepted_at.isoformat() if r.accepted_at else None
            ),
        )
        for r in rows
    ]

    # Sitewide counts so the UI badges (new/accepted/rejected) stay
    # accurate regardless of the active filter.
    from sqlalchemy import func as sa_func

    counts_stmt = (
        select(LateralQuery.status, sa_func.count())
        .where(LateralQuery.site_id == site_id)
        .group_by(LateralQuery.status)
    )
    counts = {
        row[0]: int(row[1])
        for row in (await db.execute(counts_stmt)).all()
    }
    return LateralListResponse(items=items, counts=counts)


@router.patch(
    "/sites/{site_id}/lateral-queries/{lateral_id}",
    dependencies=[Depends(_require_admin)],
)
async def patch_lateral_query(
    site_id: uuid.UUID,
    lateral_id: uuid.UUID,
    payload: LateralStatusPatch,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Owner accepts/rejects/promotes one lateral idea.

    The persistence helper enforces the (site_id, lateral_id) scoping
    so a leaked id from one site can't mutate rows in another.
    """
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")

    changed = await set_status(db, site_id, lateral_id, payload.status)
    if not changed:
        raise HTTPException(status_code=404, detail="lateral query not found")
    return {"status": "ok", "new_status": payload.status}


@router.post(
    "/sites/{site_id}/lateral-queries/expand",
    dependencies=[Depends(_require_admin)],
    response_model=LateralTriggerResponse,
)
async def trigger_lateral_expand(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> LateralTriggerResponse:
    """Manually queue a lateral-expansion run for one site (out-of-band).

    Same task the weekly beat fires — the audit trail (`agent_runs`)
    will show `trigger=manual` instead of `scheduled`.
    """
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")

    from app.workers.celery_app import celery_app

    async_result = celery_app.send_task(
        "lateral_expand_site",
        args=[str(site_id)],
    )
    return LateralTriggerResponse(status="queued", task_id=async_result.id)
