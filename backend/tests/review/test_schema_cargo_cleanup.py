"""Tests for the cargo-cult schema recommendation cleanup.

The module under test wipes false ``page_review_recommendation`` rows
left over from a (separately patched) hallucination bug in the schema
reviewer. There are two modes — conservative and smart — and one knob
(``dry_run``); we cover the matrix end-to-end against a real Postgres
session via the ``db`` fixture from ``conftest.py``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.review.backfill_schema_cargo_cleanup import (
    CARGO_CULT_SCHEMA_TYPES,
    cleanup_schema_cargo_cult_recs,
)
from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.models.page import Page
from app.models.page_deep_extract import PageDeepExtract
from app.models.site import Site


pytestmark = pytest.mark.asyncio


async def _make_page(
    db: AsyncSession, site: Site, *, suffix: str | None = None,
) -> Page:
    """Insert one Page on the test site. Suffix keeps URLs unique."""
    s = suffix or uuid.uuid4().hex[:8]
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
    category: str = "schema",
    before_text: str | None = None,
) -> PageReviewRecommendation:
    rec = PageReviewRecommendation(
        site_id=site.id,
        review_id=review.id,
        category=category,
        priority="medium",
        user_status="pending",
        before_text=before_text,
        after_text="…",
        reasoning_ru="test",
    )
    db.add(rec)
    await db.flush()
    return rec


async def _make_deep_extract(
    db: AsyncSession, site: Site, page: Page, schema_types: list[str] | None,
) -> PageDeepExtract:
    """Insert a page_deep_extract with the given list of @type names.

    Passing ``None`` for ``schema_types`` leaves ``schema_blocks`` NULL,
    which models a deep-extract that ran but found no schema markup at
    all (still counts as evidence in smart-mode).
    """
    blocks: list[dict] | None
    if schema_types is None:
        blocks = None
    else:
        blocks = [{"@type": t} for t in schema_types]
    de = PageDeepExtract(
        site_id=site.id,
        page_id=page.id,
        url=f"https://example.com{page.path}",
        is_competitor=False,
        status="completed",
        schema_blocks=blocks,
    )
    db.add(de)
    await db.flush()
    return de


async def _count_recs(db: AsyncSession, site: Site) -> int:
    stmt = select(func.count()).select_from(PageReviewRecommendation).where(
        PageReviewRecommendation.site_id == site.id,
    )
    return int((await db.execute(stmt)).scalar() or 0)


async def test_conservative_deletes_all_cargo_cult_recs(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    for t in CARGO_CULT_SCHEMA_TYPES:
        await _make_rec(db, test_site, review, before_text=t)
    assert await _count_recs(db, test_site) == 5

    result = await cleanup_schema_cargo_cult_recs(db, site_id=test_site.id)

    assert result["mode"] == "conservative"
    assert result["total_matched"] == 5
    assert result["deleted"] == 5
    assert result["skipped"] == 0
    assert result["errors"] == []
    assert await _count_recs(db, test_site) == 0


async def test_conservative_preserves_non_schema_recs(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    # One cargo-cult schema rec + two recs in other categories that
    # happen to share the cargo-cult before_text. They must survive.
    await _make_rec(db, test_site, review, before_text="TouristTrip")
    await _make_rec(
        db, test_site, review, category="title", before_text="TouristTrip",
    )
    await _make_rec(
        db, test_site, review, category="meta", before_text="Event",
    )

    result = await cleanup_schema_cargo_cult_recs(db, site_id=test_site.id)

    assert result["deleted"] == 1
    assert await _count_recs(db, test_site) == 2


async def test_conservative_preserves_schema_recs_with_other_before_text(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    # category=schema but before_text NOT in the cargo-cult set →
    # legitimate, must survive.
    await _make_rec(db, test_site, review, before_text="Product")
    await _make_rec(db, test_site, review, before_text="Organization")
    # Plus one real cargo-cult so we can prove the filter works.
    await _make_rec(db, test_site, review, before_text="TouristTrip")

    result = await cleanup_schema_cargo_cult_recs(db, site_id=test_site.id)

    assert result["deleted"] == 1
    survivors = (
        await db.execute(
            select(PageReviewRecommendation.before_text).where(
                PageReviewRecommendation.site_id == test_site.id,
            )
        )
    ).scalars().all()
    assert set(survivors) == {"Product", "Organization"}


async def test_smart_keeps_when_type_in_deep_extract(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    await _make_rec(db, test_site, review, before_text="TouristTrip")
    await _make_deep_extract(db, test_site, page, ["TouristTrip"])

    result = await cleanup_schema_cargo_cult_recs(
        db, site_id=test_site.id, check_deep_extract=True,
    )

    assert result["mode"] == "smart"
    assert result["total_matched"] == 1
    assert result["deleted"] == 0
    assert result["skipped"] == 1
    assert await _count_recs(db, test_site) == 1


async def test_smart_deletes_when_type_not_in_deep_extract(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    await _make_rec(db, test_site, review, before_text="TouristTrip")
    # Deep-extract proves the page is actually a BlogPosting — the
    # TouristTrip rec is provably hallucinated.
    await _make_deep_extract(db, test_site, page, ["BlogPosting"])

    result = await cleanup_schema_cargo_cult_recs(
        db, site_id=test_site.id, check_deep_extract=True,
    )

    assert result["deleted"] == 1
    assert result["skipped"] == 0
    assert await _count_recs(db, test_site) == 0


async def test_smart_skips_when_no_deep_extract(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    await _make_rec(db, test_site, review, before_text="TouristTrip")
    # NO deep-extract row → no evidence → smart-mode refuses to delete.

    result = await cleanup_schema_cargo_cult_recs(
        db, site_id=test_site.id, check_deep_extract=True,
    )

    assert result["total_matched"] == 1
    assert result["deleted"] == 0
    assert result["skipped"] == 1
    assert await _count_recs(db, test_site) == 1


async def test_dry_run_does_not_delete(
    db: AsyncSession, test_site: Site,
) -> None:
    page = await _make_page(db, test_site)
    review = await _make_review(db, test_site, page)
    for t in CARGO_CULT_SCHEMA_TYPES:
        await _make_rec(db, test_site, review, before_text=t)

    result = await cleanup_schema_cargo_cult_recs(
        db, site_id=test_site.id, dry_run=True,
    )

    # Counts report what *would* happen…
    assert result["dry_run"] is True
    assert result["total_matched"] == 5
    assert result["deleted"] == 5
    # …but the rows are still there.
    assert await _count_recs(db, test_site) == 5
