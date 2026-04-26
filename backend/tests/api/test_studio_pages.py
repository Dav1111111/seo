"""Studio /pages and /pages/{id} endpoints — IMPLEMENTATION.md §3.3.

list_pages relies on Postgres `DISTINCT ON` to grab the latest review
per page in one query — pin that path with at least one row, otherwise
a regression to the all-history scan reappears silently.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.studio import get_page_detail, list_pages
from app.models.page import Page
from app.models.site import Site


pytestmark = pytest.mark.asyncio


async def test_list_pages_empty_returns_zero(
    db: AsyncSession, test_site: Site,
) -> None:
    resp = await list_pages(site_id=test_site.id, db=db)
    assert resp.total == 0
    assert resp.items == []
    assert resp.site_id == str(test_site.id)


async def test_list_pages_404_for_unknown_site(db: AsyncSession) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await list_pages(site_id=uuid.uuid4(), db=db)
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

    resp = await list_pages(site_id=test_site.id, db=db)
    assert resp.total == 2
    assert all(item.has_review is False for item in resp.items)
    assert all(item.n_recommendations == 0 for item in resp.items)


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
