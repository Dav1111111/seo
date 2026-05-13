"""Tests for the ``backfill_false_field_present`` cleanup.

Each test seeds a (page, deep_extract, recommendation) trio and checks
that the cleanup either deletes or preserves the rec based on whether
the matching regex actually fires on ``page_deep_extracts.full_text``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.review.backfill_false_field_present import (
    cleanup_false_field_present,
)
from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.models.page import Page
from app.models.page_deep_extract import PageDeepExtract
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
    category: str,
    after_text: str,
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


async def _make_extract(
    db: AsyncSession, site: Site, page: Page, *, full_text: str | None,
) -> PageDeepExtract:
    de = PageDeepExtract(
        site_id=site.id,
        page_id=page.id,
        url=f"https://example.com{page.path}",
        is_competitor=False,
        status="completed",
        full_text=full_text,
    )
    db.add(de)
    await db.flush()
    return de


async def _count_recs(db: AsyncSession, site: Site) -> int:
    stmt = select(func.count()).select_from(PageReviewRecommendation).where(
        PageReviewRecommendation.site_id == site.id,
    )
    return int((await db.execute(stmt)).scalar() or 0)


# ---------------------------------------------------------------------------
# Phone
# ---------------------------------------------------------------------------

async def test_phone_rec_deleted_when_phone_present(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    await _make_rec(
        db, test_site, review,
        category="commercial",
        after_text="Добавьте телефон в шапку сайта",
    )
    await _make_extract(
        db, test_site, page,
        full_text="Звоните нам: +7 (495) 123-45-67 ежедневно",
    )

    result = await cleanup_false_field_present(db, site_id=test_site.id)

    assert result["checked"] == 1
    assert result["deleted_by_signal"]["phone"] == 1
    assert result["skipped_no_extract"] == 0
    assert await _count_recs(db, test_site) == 0


async def test_phone_rec_preserved_when_no_phone(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    await _make_rec(
        db, test_site, review,
        category="commercial",
        after_text="Добавьте телефон в шапку",
    )
    # full_text crawled, no phone anywhere.
    await _make_extract(
        db, test_site, page,
        full_text="Программа экскурсии: озеро Рица, Гагра, обед.",
    )

    result = await cleanup_false_field_present(db, site_id=test_site.id)

    assert result["checked"] == 1
    assert result["deleted_by_signal"]["phone"] == 0
    assert await _count_recs(db, test_site) == 1


async def test_phone_rec_skipped_when_no_extract(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    await _make_rec(
        db, test_site, review,
        category="commercial",
        after_text="Добавьте телефон в шапку",
    )
    # NO PageDeepExtract row → no evidence → skip.

    result = await cleanup_false_field_present(db, site_id=test_site.id)

    assert result["checked"] == 1
    assert result["skipped_no_extract"] == 1
    assert result["deleted_by_signal"]["phone"] == 0
    assert await _count_recs(db, test_site) == 1


# ---------------------------------------------------------------------------
# РТО
# ---------------------------------------------------------------------------

async def test_rto_rec_deleted_when_rto_present(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    await _make_rec(
        db, test_site, review,
        category="eeat",
        after_text="Укажите номер РТО (реестровый туроператор)",
    )
    await _make_extract(
        db, test_site, page,
        full_text="ООО Тур, РТО № 012345, ИНН 7700000000",
    )

    result = await cleanup_false_field_present(db, site_id=test_site.id)

    assert result["deleted_by_signal"]["rto"] == 1
    assert await _count_recs(db, test_site) == 0


# ---------------------------------------------------------------------------
# Not-in-scope categories / unrelated recs
# ---------------------------------------------------------------------------

async def test_non_targeted_schema_rec_preserved(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    # Category=schema and no after_text substring in our table — should
    # never enter the cleanup pipeline at all.
    await _make_rec(
        db, test_site, review,
        category="schema",
        after_text="Добавьте Organization JSON-LD",
    )
    await _make_extract(
        db, test_site, page,
        full_text="Звоните: +7 (495) 123-45-67",
    )

    result = await cleanup_false_field_present(db, site_id=test_site.id)

    assert result["checked"] == 0
    for v in result["deleted_by_signal"].values():
        assert v == 0
    assert await _count_recs(db, test_site) == 1


async def test_phone_rec_in_wrong_category_preserved(
    db: AsyncSession, test_site: Site,
) -> None:
    """Phone rule restricts to category=commercial.

    A rec mentioning "телефон" but filed under e.g. ``eeat`` is out of
    scope — the prefilter pulls it in but ``_rec_matches_signal``
    rejects it, so ``checked`` stays at 0.
    """
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    await _make_rec(
        db, test_site, review,
        category="eeat",
        after_text="Покажите телефон рядом с фото гида",
    )
    await _make_extract(
        db, test_site, page,
        full_text="Тел: +7 (495) 000-00-00",
    )

    result = await cleanup_false_field_present(db, site_id=test_site.id)

    assert result["checked"] == 0
    assert result["deleted_by_signal"]["phone"] == 0
    assert await _count_recs(db, test_site) == 1


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

async def test_dry_run_counts_but_does_not_delete(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    await _make_rec(
        db, test_site, review,
        category="commercial",
        after_text="Добавьте телефон",
    )
    await _make_extract(
        db, test_site, page,
        full_text="+7 (812) 555-77-88",
    )

    result = await cleanup_false_field_present(
        db, site_id=test_site.id, dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["checked"] == 1
    # Dry-run still credits the would-be deletion in the per-signal
    # count, mirroring the schema-cargo cleanup contract.
    assert result["deleted_by_signal"]["phone"] == 1
    # …but the row is still there.
    assert await _count_recs(db, test_site) == 1
