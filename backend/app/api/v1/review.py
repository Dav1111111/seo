"""Module 3 — Page Review API endpoints.

- POST /sites/{site_id}/reviews/run       → queue site-level review (top-N)
- POST /pages/{page_id}/reviews/run       → queue single-page review
- GET  /sites/{site_id}/reviews           → list PageReview rows
- GET  /reviews/{review_id}               → full detail with recommendations
- GET  /pages/{page_id}/reviews/latest    → most recent review for a page
- PATCH /recommendations/{rec_id}         → mark applied/dismissed/deferred
- GET  /sites/{site_id}/reviews/stats     → aggregate counts
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.database import get_db

router = APIRouter()


class QueuedResponse(BaseModel):
    task_id: str
    status: str


class PatchRecommendationBody(BaseModel):
    user_status: str                                # pending|applied|dismissed|deferred
    note: str | None = None


ALLOWED_USER_STATUSES = {"pending", "applied", "dismissed", "deferred"}


@router.post("/reviews/sites/{site_id}/run", response_model=QueuedResponse)
async def trigger_site_review(site_id: uuid.UUID, top_n: int = 20):
    """Queue top-N strengthen decisions for review on a site."""
    from app.core_audit.review.tasks import review_site_decisions_task
    task = review_site_decisions_task.delay(str(site_id), top_n)
    return QueuedResponse(task_id=task.id, status="queued")


@router.post("/reviews/pages/{page_id}/run", response_model=QueuedResponse)
async def trigger_page_review(page_id: uuid.UUID, decision_id: uuid.UUID | None = None):
    """Queue a single-page review. decision_id is optional but recommended."""
    from app.core_audit.review.tasks import review_page_task
    task = review_page_task.delay(str(page_id), str(decision_id) if decision_id else None)
    return QueuedResponse(task_id=task.id, status="queued")


@router.get("/reviews/sites/{site_id}/reviews")
async def list_site_reviews(
    site_id: uuid.UUID,
    status: str | None = None,
    intent_code: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    q = select(PageReview).where(PageReview.site_id == site_id)
    if status:
        q = q.where(PageReview.status == status)
    if intent_code:
        q = q.where(PageReview.target_intent_code == intent_code)
    q = q.order_by(PageReview.reviewed_at.desc()).limit(limit)
    rows = (await db.execute(q)).scalars().all()

    return {
        "total": len(rows),
        "items": [_review_card(r) for r in rows],
    }


@router.get("/reviews/{review_id}")
async def get_review(review_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    review = (await db.execute(
        select(PageReview).where(PageReview.id == review_id)
    )).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")

    recs = (await db.execute(
        select(PageReviewRecommendation)
        .where(PageReviewRecommendation.review_id == review_id)
        .order_by(PageReviewRecommendation.category, PageReviewRecommendation.priority)
    )).scalars().all()

    grouped: dict[str, list[dict]] = {}
    for r in recs:
        grouped.setdefault(r.category, []).append(_recommendation_dto(r))

    return {
        **_review_card(review),
        "page_level_summary": review.page_level_summary,
        "top_queries_snapshot": review.top_queries_snapshot,
        "recommendations_by_category": grouped,
        "recommendations_total": len(recs),
    }


@router.get("/reviews/pages/{page_id}/reviews/latest")
async def get_latest_page_review(
    page_id: uuid.UUID, db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    review = (await db.execute(
        select(PageReview)
        .where(PageReview.page_id == page_id, PageReview.status == "completed")
        .order_by(PageReview.reviewed_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="no completed review for page")
    return await get_review(review.id, db)


@router.patch("/reviews/recommendations/{rec_id}")
async def patch_recommendation(
    rec_id: uuid.UUID,
    body: PatchRecommendationBody,
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if body.user_status not in ALLOWED_USER_STATUSES:
        raise HTTPException(status_code=400, detail=f"user_status must be one of {sorted(ALLOWED_USER_STATUSES)}")

    rec = (await db.execute(
        select(PageReviewRecommendation).where(PageReviewRecommendation.id == rec_id)
    )).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail="recommendation not found")

    rec.user_status = body.user_status
    rec.user_status_changed_at = datetime.utcnow()
    rec.user_status_changed_by = x_user_email or "anonymous"
    if body.note:
        impact = dict(rec.estimated_impact or {})
        impact["user_note"] = body.note
        rec.estimated_impact = impact
    await db.commit()
    return _recommendation_dto(rec)


@router.get("/reviews/sites/{site_id}/reviews/stats")
async def review_stats(
    site_id: uuid.UUID, db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    # Review-level aggregates
    by_status_rows = await db.execute(
        select(PageReview.status, func.count())
        .where(PageReview.site_id == site_id)
        .group_by(PageReview.status)
    )
    by_status = {s: c for s, c in by_status_rows}

    by_skip_rows = await db.execute(
        select(PageReview.skip_reason, func.count())
        .where(PageReview.site_id == site_id, PageReview.skip_reason.isnot(None))
        .group_by(PageReview.skip_reason)
    )
    by_skip = {s: c for s, c in by_skip_rows if s}

    cost_total = (await db.execute(
        select(func.coalesce(func.sum(PageReview.cost_usd), 0)).where(PageReview.site_id == site_id)
    )).scalar() or 0.0

    last_run_at = (await db.execute(
        select(func.max(PageReview.reviewed_at)).where(PageReview.site_id == site_id)
    )).scalar()

    # Recommendation aggregates
    rec_status_rows = await db.execute(
        select(PageReviewRecommendation.user_status, func.count())
        .where(PageReviewRecommendation.site_id == site_id)
        .group_by(PageReviewRecommendation.user_status)
    )
    rec_by_status = {s: c for s, c in rec_status_rows}

    rec_category_rows = await db.execute(
        select(PageReviewRecommendation.category, func.count())
        .where(PageReviewRecommendation.site_id == site_id)
        .group_by(PageReviewRecommendation.category)
    )
    rec_by_category = {s: c for s, c in rec_category_rows}

    rec_priority_rows = await db.execute(
        select(PageReviewRecommendation.priority, func.count())
        .where(PageReviewRecommendation.site_id == site_id)
        .group_by(PageReviewRecommendation.priority)
    )
    rec_by_priority = {s: c for s, c in rec_priority_rows}

    return {
        "site_id": str(site_id),
        "total_reviews": sum(by_status.values()),
        "by_status": by_status,
        "by_skip_reason": by_skip,
        "cost_total_usd": float(cost_total),
        "last_run_at": last_run_at.isoformat() if last_run_at else None,
        "recommendations": {
            "by_user_status": rec_by_status,
            "by_category": rec_by_category,
            "by_priority": rec_by_priority,
            "total": sum(rec_by_status.values()),
        },
    }


# ── Serializers ──────────────────────────────────────────────────────

def _review_card(r: PageReview) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "page_id": str(r.page_id),
        "site_id": str(r.site_id),
        "coverage_decision_id": str(r.coverage_decision_id) if r.coverage_decision_id else None,
        "target_intent_code": r.target_intent_code,
        "reviewer_model": r.reviewer_model,
        "reviewer_version": r.reviewer_version,
        "status": r.status,
        "skip_reason": r.skip_reason,
        "error": r.error,
        "cost_usd": float(r.cost_usd or 0),
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "duration_ms": r.duration_ms,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
    }


def _recommendation_dto(r: PageReviewRecommendation) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "review_id": str(r.review_id),
        "category": r.category,
        "priority": r.priority,
        "before_text": r.before_text,
        "after_text": r.after_text,
        "reasoning_ru": r.reasoning_ru,
        "estimated_impact": r.estimated_impact,
        "user_status": r.user_status,
        "user_status_changed_at": r.user_status_changed_at.isoformat() if r.user_status_changed_at else None,
        "user_status_changed_by": r.user_status_changed_by,
    }
