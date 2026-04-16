"""Idempotency guard — skips a review when the previous COMPLETED one
covered an identical (page, composite_hash, reviewer_version) triple.

Skipped/failed prior rows do NOT short-circuit — those runs were not a
successful review and should be retried.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.review.enums import ReviewStatus
from app.core_audit.review.models import PageReview


async def is_unchanged(
    db: AsyncSession,
    page_id: UUID,
    composite_hash: str,
    reviewer_version: str,
) -> bool:
    """True iff a completed PageReview row exists for this exact triple."""
    stmt = (
        select(PageReview.id)
        .where(
            PageReview.page_id == page_id,
            PageReview.composite_hash == composite_hash,
            PageReview.reviewer_version == reviewer_version,
            PageReview.status == ReviewStatus.completed.value,
        )
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    return row is not None
