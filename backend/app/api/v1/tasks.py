"""
SEO Tasks API — backlog of actionable items with ready-to-paste content.
"""

import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.task import Task

router = APIRouter()


VALID_STATUSES = {"backlog", "planned", "in_progress", "done", "measuring", "completed", "failed", "cancelled"}


@router.get("/sites/{site_id}/tasks")
async def list_tasks(
    site_id: uuid.UUID,
    status: str | None = None,
    task_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List SEO tasks for a site."""
    q = select(Task).where(Task.site_id == site_id)
    if status:
        if status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status. Valid: {VALID_STATUSES}")
        q = q.where(Task.status == status)
    if task_type:
        q = q.where(Task.task_type == task_type)

    total_q = await db.execute(select(func.count()).select_from(q.subquery()))
    total = total_q.scalar() or 0

    q = q.order_by(Task.priority.desc(), Task.created_at.desc()).offset(offset).limit(limit)
    rows = await db.execute(q)
    tasks = rows.scalars().all()

    # Group counts by status for summary
    status_counts_q = await db.execute(
        select(Task.status, func.count()).where(Task.site_id == site_id).group_by(Task.status)
    )
    status_counts = {row[0]: row[1] for row in status_counts_q}

    return {
        "total": total,
        "status_counts": status_counts,
        "items": [
            {
                "id": str(t.id),
                "title": t.title,
                "description": t.description,
                "task_type": t.task_type,
                "priority": t.priority,
                "estimated_impact": t.estimated_impact,
                "estimated_effort": t.estimated_effort,
                "status": t.status,
                "target_query": t.target_query,
                "target_cluster": t.target_cluster,
                "target_page_url": t.target_page_url,
                "generated_content": t.generated_content,
                "assigned_week": t.assigned_week.isoformat() if t.assigned_week else None,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "effect_result": t.effect_result,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in tasks
        ],
    }


@router.get("/sites/{site_id}/tasks/{task_id}")
async def get_task(
    site_id: uuid.UUID,
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get single task with full details."""
    row = await db.execute(
        select(Task).where(Task.id == task_id, Task.site_id == site_id)
    )
    task = row.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return {
        "id": str(task.id),
        "title": task.title,
        "description": task.description,
        "task_type": task.task_type,
        "priority": task.priority,
        "estimated_impact": task.estimated_impact,
        "estimated_effort": task.estimated_effort,
        "status": task.status,
        "target_query": task.target_query,
        "target_cluster": task.target_cluster,
        "target_page_url": task.target_page_url,
        "generated_content": task.generated_content,
        "assigned_week": task.assigned_week.isoformat() if task.assigned_week else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "effect_result": task.effect_result,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


class TaskUpdate(BaseModel):
    status: str | None = None
    priority: int | None = None
    assigned_week: date | None = None


@router.patch("/sites/{site_id}/tasks/{task_id}")
async def update_task(
    site_id: uuid.UUID,
    task_id: uuid.UUID,
    body: TaskUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update task status, priority, or week."""
    row = await db.execute(
        select(Task).where(Task.id == task_id, Task.site_id == site_id)
    )
    task = row.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if body.status is not None:
        if body.status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status. Valid: {VALID_STATUSES}")
        task.status = body.status

        # Auto-set timestamps based on status transition
        today = date.today()
        if body.status == "in_progress" and not task.started_at:
            task.started_at = today
        if body.status in ("done", "measuring", "completed") and not task.completed_at:
            task.completed_at = today

    if body.priority is not None:
        task.priority = max(1, min(100, body.priority))

    if body.assigned_week is not None:
        task.assigned_week = body.assigned_week

    await db.flush()
    return {"id": str(task.id), "status": task.status}


@router.delete("/sites/{site_id}/tasks/{task_id}")
async def delete_task(
    site_id: uuid.UUID,
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Delete a task."""
    row = await db.execute(
        select(Task).where(Task.id == task_id, Task.site_id == site_id)
    )
    task = row.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.delete(task)
    await db.flush()
    return {"deleted": str(task_id)}


class CollectResponse(BaseModel):
    task_id: str
    status: str


@router.post("/sites/{site_id}/crawl", response_model=CollectResponse)
async def trigger_site_crawl(site_id: uuid.UUID):
    """Trigger site crawler — fetches sitemap + pages, extracts SEO data."""
    from app.collectors.tasks import crawl_site
    t = crawl_site.delay(str(site_id))
    return CollectResponse(task_id=t.id, status="queued")


@router.post("/sites/{site_id}/generate-tasks", response_model=CollectResponse)
async def trigger_task_generation(site_id: uuid.UUID):
    """Trigger AI task generation — creates concrete SEO tasks with ready content."""
    from app.agents.tasks import generate_seo_tasks
    t = generate_seo_tasks.delay(str(site_id))
    return CollectResponse(task_id=t.id, status="queued")


@router.get("/sites/{site_id}/pages")
async def list_pages(
    site_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List crawled pages with SEO data."""
    from app.models.page import Page
    rows = await db.execute(
        select(Page).where(Page.site_id == site_id).limit(limit)
    )
    pages = rows.scalars().all()
    return {
        "total": len(pages),
        "items": [
            {
                "id": str(p.id),
                "url": p.url,
                "path": p.path,
                "title": p.title,
                "meta_description": p.meta_description,
                "h1": p.h1,
                "word_count": p.word_count,
                "images_count": p.images_count,
                "has_schema": p.has_schema,
                "http_status": p.http_status,
                "last_crawled_at": p.last_crawled_at.isoformat() if p.last_crawled_at else None,
            }
            for p in pages
        ],
    }
