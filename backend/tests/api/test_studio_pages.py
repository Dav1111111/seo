"""Studio /pages and /pages/{id} endpoints — IMPLEMENTATION.md §3.3.

The current recommendation state is the latest completed review per
(page, target_intent_code). Pin that path so `/studio/pages` does not
silently collapse a multi-intent page to a single review.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.studio import get_page_detail, list_pages
from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.models.page import Page
from app.models.site import Site


pytestmark = pytest.mark.asyncio


async def test_list_pages_empty_returns_zero(
    db: AsyncSession, test_site: Site,
) -> None:
    resp = await list_pages(site_id=test_site.id, sort="recent_review", limit=100, db=db)
    assert resp.total == 0
    assert resp.items == []
    assert resp.site_id == str(test_site.id)


async def test_list_pages_404_for_unknown_site(db: AsyncSession) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await list_pages(site_id=uuid.uuid4(), sort="recent_review", limit=100, db=db)
    assert exc.value.status_code == 404


async def test_list_pages_with_rows_no_review(
    db: AsyncSession, test_site: Site,
) -> None:
    """Two pages, no reviews → both surface with has_review=False
    and zero recommendation counts."""
    db.add_all([
        Page(site_id=test_site.id, url="https://x/a", path="/a"),
        Page(site_id=test_site.id, url="https://x/b", path="/b"),
    ])
    await db.flush()

    resp = await list_pages(site_id=test_site.id, sort="recent_review", limit=100, db=db)
    assert resp.total == 2
    assert all(item.has_review is False for item in resp.items)
    assert all(item.n_recommendations == 0 for item in resp.items)


async def test_list_pages_counts_latest_completed_reviews_per_intent(
    db: AsyncSession, test_site: Site,
) -> None:
    """A page can have several current reviews, one for each intent."""
    page = Page(site_id=test_site.id, url="https://x/a", path="/a")
    db.add(page)
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
        composite_hash="skipped:unchanged_hash",
        status="skipped",
        skip_reason="unchanged_hash",
        reviewed_at=base + timedelta(days=3),
    )
    db.add_all([old_info, latest_info, latest_commercial, skipped_newer])
    await db.flush()
    db.add_all([
        PageReviewRecommendation(
            site_id=test_site.id,
            review_id=old_info.id,
            category="title",
            priority="high",
            user_status="pending",
            reasoning_ru="old recommendation must not count",
        ),
        PageReviewRecommendation(
            site_id=test_site.id,
            review_id=latest_info.id,
            category="title",
            priority="high",
            user_status="pending",
            reasoning_ru="current info",
        ),
        PageReviewRecommendation(
            site_id=test_site.id,
            review_id=latest_commercial.id,
            category="h1",
            priority="medium",
            user_status="applied",
            reasoning_ru="current commercial",
        ),
    ])
    await db.flush()

    resp = await list_pages(site_id=test_site.id, sort="recs", limit=100, db=db)

    assert resp.total == 1
    item = resp.items[0]
    assert item.has_review is True
    assert item.last_reviewed_at == latest_commercial.reviewed_at
    assert item.n_recommendations == 2
    assert item.n_pending == 1
    assert item.n_applied == 1


async def test_get_page_detail_404_unknown_page(
    db: AsyncSession, test_site: Site,
) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await get_page_detail(
            site_id=test_site.id, page_id=uuid.uuid4(), db=db,
        )
    assert exc.value.status_code == 404


async def test_get_page_detail_returns_no_review_when_unreviewed(
    db: AsyncSession, test_site: Site,
) -> None:
    """Page exists, no review row → review=None, outcomes=[].
    Cross-links dict carries the readiness flags but never the dead
    `analytics` key (P1 #5)."""
    page = Page(site_id=test_site.id, url="https://x/a", path="/a")
    db.add(page)
    await db.flush()

    detail = await get_page_detail(
        site_id=test_site.id, page_id=page.id, db=db,
    )
    assert detail.review is None
    assert detail.outcomes == []
    assert "analytics" not in detail.cross_links
    # The remaining keys are the contract — frontend keys off them.
    assert set(detail.cross_links.keys()) == {
        "queries", "indexation", "competitors", "outcomes",
    }


async def test_get_page_detail_merges_latest_completed_intent_reviews(
    db: AsyncSession, test_site: Site,
) -> None:
    page = Page(site_id=test_site.id, url="https://x/a", path="/a")
    db.add(page)
    await db.flush()

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    info_review = PageReview(
        site_id=test_site.id,
        page_id=page.id,
        target_intent_code="info",
        composite_hash="info",
        status="completed",
        reviewed_at=base,
    )
    commercial_review = PageReview(
        site_id=test_site.id,
        page_id=page.id,
        target_intent_code="commercial",
        composite_hash="commercial",
        status="completed",
        reviewed_at=base + timedelta(days=1),
    )
    db.add_all([info_review, commercial_review])
    await db.flush()
    db.add_all([
        PageReviewRecommendation(
            site_id=test_site.id,
            review_id=commercial_review.id,
            category="title",
            priority="high",
            user_status="applied",
            reasoning_ru="applied commercial",
            priority_score=8,
        ),
        PageReviewRecommendation(
            site_id=test_site.id,
            review_id=info_review.id,
            category="h1",
            priority="high",
            user_status="pending",
            reasoning_ru="pending info",
            priority_score=6,
        ),
    ])
    await db.flush()

    detail = await get_page_detail(
        site_id=test_site.id, page_id=page.id, db=db,
    )

    assert detail.review is not None
    assert detail.review.review_id == str(commercial_review.id)
    assert detail.review.reviewed_at == commercial_review.reviewed_at
    assert [r.reasoning_ru for r in detail.review.recommendations] == [
        "pending info",
        "applied commercial",
    ]
