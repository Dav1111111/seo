from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.brain.snapshot import _review
from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.models.page import Page
from app.models.site import Site


pytestmark = pytest.mark.asyncio


async def test_review_snapshot_uses_latest_completed_reviews_per_intent(
    db: AsyncSession,
    test_site: Site,
) -> None:
    page = Page(site_id=test_site.id, url="https://x/a", path="/a")
    skipped_only_page = Page(site_id=test_site.id, url="https://x/b", path="/b")
    db.add_all([page, skipped_only_page])
    await db.flush()

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    old_info = PageReview(
        site_id=test_site.id,
        page_id=page.id,
        target_intent_code="info",
        composite_hash="old-info",
        status="completed",
        reviewed_at=base,
    )
    latest_info = PageReview(
        site_id=test_site.id,
        page_id=page.id,
        target_intent_code="info",
        composite_hash="latest-info",
        status="completed",
        reviewed_at=base + timedelta(days=1),
    )
    latest_commercial = PageReview(
        site_id=test_site.id,
        page_id=page.id,
        target_intent_code="commercial",
        composite_hash="latest-commercial",
        status="completed",
        reviewed_at=base + timedelta(days=2),
    )
    skipped_newer = PageReview(
        site_id=test_site.id,
        page_id=page.id,
        target_intent_code="info",
        composite_hash="skipped-info",
        status="skipped",
        skip_reason="unchanged_hash",
        reviewed_at=base + timedelta(days=3),
    )
    skipped_only = PageReview(
        site_id=test_site.id,
        page_id=skipped_only_page.id,
        target_intent_code="info",
        composite_hash="skipped-only",
        status="skipped",
        skip_reason="unchanged_hash",
        reviewed_at=base + timedelta(days=4),
    )
    db.add_all([
        old_info,
        latest_info,
        latest_commercial,
        skipped_newer,
        skipped_only,
    ])
    await db.flush()

    db.add_all([
        PageReviewRecommendation(
            site_id=test_site.id,
            review_id=old_info.id,
            category="title",
            priority="critical",
            user_status="pending",
            priority_score=99,
            reasoning_ru="old recommendation must not reach chat",
        ),
        PageReviewRecommendation(
            site_id=test_site.id,
            review_id=latest_info.id,
            category="title",
            priority="high",
            user_status="pending",
            priority_score=8,
            reasoning_ru="current info recommendation",
        ),
        PageReviewRecommendation(
            site_id=test_site.id,
            review_id=latest_commercial.id,
            category="h1",
            priority="medium",
            user_status="pending",
            priority_score=6,
            reasoning_ru="current commercial recommendation",
        ),
        PageReviewRecommendation(
            site_id=test_site.id,
            review_id=skipped_newer.id,
            category="title",
            priority="critical",
            user_status="pending",
            priority_score=100,
            reasoning_ru="skipped recommendation must not reach chat",
        ),
        PageReviewRecommendation(
            site_id=test_site.id,
            review_id=skipped_only.id,
            category="title",
            priority="critical",
            user_status="pending",
            priority_score=100,
            reasoning_ru="skipped-only recommendation must not reach chat",
        ),
    ])
    await db.flush()

    facts = await _review(db, test_site.id)

    assert facts.pages_with_review == 1
    assert facts.pages_without_review == 1
    assert facts.recs_pending == 2
    assert facts.recs_high_priority_pending == 1
    assert facts.sample_unreviewed_urls == ["https://x/b"]
    assert [r["reasoning_ru"] for r in facts.top_pending_recommendations] == [
        "current info recommendation",
        "current commercial recommendation",
    ]
