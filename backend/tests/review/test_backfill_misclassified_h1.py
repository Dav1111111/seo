"""Tests for the misclassified h1_structure cleanup.

The script under test wipes recs in the ``h1_structure`` category whose
``after_text`` is paragraph prose (long marketing blurb, multi-sentence)
rather than a proper H1 shape. We exercise the prose detector and the
``--dry-run`` flag against a real Postgres session via the ``db``
fixture from ``conftest.py``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.review.backfill_misclassified_h1_structure import (
    cleanup_misclassified_h1_recs,
)
from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.models.page import Page
from app.models.site import Site


pytestmark = pytest.mark.asyncio


async def _make_page(db: AsyncSession, site: Site) -> Page:
    s = uuid.uuid4().hex[:8]
    page = Page(
        site_id=site.id,
        url=f"https://example.com/p/{s}",
        path=f"/p/{s}",
    )
    db.add(page)
    await db.flush()
    return page


async def _make_review(db: AsyncSession, site: Site, page: Page) -> PageReview:
    review = PageReview(
        site_id=site.id,
        page_id=page.id,
        target_intent_code="commercial_modified",
        composite_hash=f"hash-{uuid.uuid4().hex[:8]}",
        status="completed",
    )
    db.add(review)
    await db.flush()
    return review


async def _make_rec(
    db: AsyncSession,
    site: Site,
    review: PageReview,
    *,
    category: str = "h1_structure",
    after_text: str | None = None,
) -> PageReviewRecommendation:
    rec = PageReviewRecommendation(
        site_id=site.id,
        review_id=review.id,
        category=category,
        priority="medium",
        user_status="pending",
        before_text="",
        after_text=after_text,
        reasoning_ru="test",
    )
    db.add(rec)
    await db.flush()
    return rec


async def _count_recs(db: AsyncSession, site: Site) -> int:
    stmt = select(func.count()).select_from(PageReviewRecommendation).where(
        PageReviewRecommendation.site_id == site.id,
    )
    return int((await db.execute(stmt)).scalar() or 0)


async def test_short_h1_advice_preserved(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    # Proper H1 shape — short, no terminal punctuation.
    await _make_rec(
        db, test_site, review,
        after_text="Экскурсии в Сочи — частный гид",
    )

    result = await cleanup_misclassified_h1_recs(db, site_id=test_site.id)

    assert result["total_matched"] == 1
    assert result["deleted"] == 0
    assert await _count_recs(db, test_site) == 1


async def test_paragraph_prose_deleted(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    # 240+ chars, two sentence terminators — fails BOTH heuristics.
    prose = (
        "Путешествие к озеру Рица — это незабываемое приключение. "
        "Мы проведём вас по самым живописным местам Абхазии и расскажем "
        "о её истории. Программа рассчитана на целый день, обед включён."
    )
    assert len(prose) > 200
    await _make_rec(db, test_site, review, after_text=prose)

    result = await cleanup_misclassified_h1_recs(db, site_id=test_site.id)

    assert result["total_matched"] == 1
    assert result["deleted"] == 1
    assert await _count_recs(db, test_site) == 0


async def test_two_sentence_short_prose_still_deleted(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    # Below 200 chars but two sentence-enders → still prose.
    short_prose = "Хорошая экскурсия. С опытным гидом!"
    assert len(short_prose) < 200
    await _make_rec(db, test_site, review, after_text=short_prose)

    result = await cleanup_misclassified_h1_recs(db, site_id=test_site.id)

    assert result["deleted"] == 1
    assert await _count_recs(db, test_site) == 0


async def test_non_h1_categories_preserved(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    long_prose = "A. " * 80  # 240 chars, lots of sentence ends — prose
    # Same prose content in non-h1 categories must survive.
    await _make_rec(
        db, test_site, review, category="title", after_text=long_prose,
    )
    await _make_rec(
        db, test_site, review, category="meta_description",
        after_text=long_prose,
    )
    await _make_rec(
        db, test_site, review, category="schema", after_text=long_prose,
    )

    result = await cleanup_misclassified_h1_recs(db, site_id=test_site.id)

    # No h1_structure rows at all → nothing matched, nothing deleted.
    assert result["total_matched"] == 0
    assert result["deleted"] == 0
    assert await _count_recs(db, test_site) == 3


async def test_dry_run_does_not_delete(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    prose = "First sentence. Second sentence. Third sentence." + " padding" * 30
    await _make_rec(db, test_site, review, after_text=prose)

    result = await cleanup_misclassified_h1_recs(
        db, site_id=test_site.id, dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["deleted"] == 1  # would-be deletion still credited
    # …but the row is still there.
    assert await _count_recs(db, test_site) == 1
