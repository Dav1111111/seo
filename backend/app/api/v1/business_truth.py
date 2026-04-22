"""BusinessTruth API — read + rebuild the 3-picture view.

Lives under /admin prefix because the rebuild is an owner-initiated
action that queues a background task and writes to target_config.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.site import Site

router = APIRouter(prefix="/admin")


def _require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    configured = settings.ADMIN_API_KEY or ""
    if not configured:
        raise HTTPException(status_code=401, detail="admin api disabled")
    if not x_admin_key or x_admin_key != configured:
        raise HTTPException(status_code=401, detail="invalid admin key")


@router.get(
    "/sites/{site_id}/business-truth",
    dependencies=[Depends(_require_admin)],
)
async def get_business_truth(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the stored BusinessTruth JSONB. Empty shape if not built yet."""
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")
    bt = (site.target_config or {}).get("business_truth")
    if not bt:
        return {
            "directions": [],
            "sources_used": {},
            "built_at": None,
            "traffic_coverage": None,
        }
    return bt


@router.post(
    "/sites/{site_id}/business-truth/rebuild",
    dependencies=[Depends(_require_admin)],
)
async def rebuild_business_truth_endpoint(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Queue a background rebuild of BusinessTruth.

    Owner clicks "пересобрать понимание" on the dashboard → we generate
    a run_id + fire the Celery task. Activity feed shows progress.
    """
    site = await db.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="site not found")

    from app.workers.celery_app import celery_app
    run_id = str(uuid.uuid4())
    celery_app.send_task(
        "business_truth_rebuild_site",
        args=[str(site_id)],
        kwargs={"run_id": run_id},
    )
    return {"status": "queued", "run_id": run_id}
