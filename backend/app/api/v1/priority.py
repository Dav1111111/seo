"""Module 4 — Prioritization API.

- POST /priorities/sites/{site_id}/rescore           → queue rescore task
- GET  /priorities/sites/{site_id}                   → ranked list
- GET  /priorities/sites/{site_id}/weekly-plan       → diversified top-N
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.priority.service import PriorityService
from app.database import get_db

router = APIRouter()


class QueuedResponse(BaseModel):
    task_id: str
    status: str
    run_id: str | None = None


@router.post("/priorities/sites/{site_id}/rescore", response_model=QueuedResponse)
async def trigger_rescore(site_id: uuid.UUID):
    from app.core_audit.priority.tasks import priority_rescore_site
    run_id = str(uuid.uuid4())
    task = priority_rescore_site.delay(str(site_id), run_id=run_id)
    return QueuedResponse(task_id=task.id, status="queued", run_id=run_id)


@router.get("/priorities/sites/{site_id}")
async def list_priorities(
    site_id: uuid.UUID,
    top_n: int = 20,
    category: str | None = None,
    priority: str | None = None,
    include_dismissed: bool = False,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    items = await PriorityService().priorities(
        db, site_id,
        top_n=top_n,
        category=category,
        priority=priority,
        include_dismissed=include_dismissed,
    )
    return {"total": len(items), "items": [_item_dto(i) for i in items]}


@router.get("/priorities/sites/{site_id}/weekly-plan")
async def weekly_plan(
    site_id: uuid.UUID,
    top_n: int = 10,
    max_per_page: int = 2,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    plan = await PriorityService().weekly_plan(
        db, site_id, top_n=top_n, max_per_page=max_per_page,
    )
    return {
        "total_in_backlog": plan.total_in_backlog,
        "pages_represented": plan.pages_represented,
        "max_per_page": plan.max_per_page,
        "items": [_item_dto(i) for i in plan.items],
    }


def _item_dto(it) -> dict[str, Any]:
    return {
        "recommendation_id": str(it.recommendation_id),
        "review_id": str(it.review_id),
        "page_id": str(it.page_id),
        "page_url": it.page_url,
        "target_intent_code": it.target_intent_code,
        "category": it.category,
        "priority": it.priority,
        "user_status": it.user_status,
        "reasoning_ru": it.reasoning_ru,
        "before_text": it.before_text,
        "after_text": it.after_text,
        "priority_score": round(it.priority_score, 2),
        "impact": round(it.impact, 3),
        "confidence": round(it.confidence, 3),
        "ease": round(it.ease, 3),
        "scored_at": it.scored_at.isoformat() if it.scored_at else None,
    }
