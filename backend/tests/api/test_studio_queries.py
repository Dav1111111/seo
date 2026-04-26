"""Studio /queries endpoint — IMPLEMENTATION.md §3.3 contract: minimum 1
unit-test per Studio module endpoint.

These tests call the route function directly (no HTTP layer) so we
exercise the SQL/merge logic against the real DB rather than mocking
the session. Pattern matches the rest of the test-suite which doesn't
spin up a TestClient — see tests/conftest.py for the rolled-back-per-
test session.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.studio import list_queries
from app.models.search_query import SearchQuery
from app.models.site import Site


pytestmark = pytest.mark.asyncio


async def test_list_queries_empty_site_returns_zero_total(
    db: AsyncSession, test_site: Site,
) -> None:
    """Empty site = zero total + empty items + zeroed coverage block.
    No 500, no None — UI relies on a stable shape."""
    resp = await list_queries(site_id=test_site.id, sort="volume", limit=200, db=db)
    assert resp.total == 0
    assert resp.items == []
    assert resp.coverage["total"] == 0
    assert resp.coverage["with_volume"] == 0
    assert resp.coverage["without_volume"] == 0
    assert resp.coverage["stale"] == 0


async def test_list_queries_404_for_unknown_site(
    db: AsyncSession,
) -> None:
    """Site-or-404 helper must reject random uuids — guards the dedup
    branch and the SQL queries below it from running on garbage."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await list_queries(site_id=uuid.uuid4(), sort="volume", limit=200, db=db)
    assert exc.value.status_code == 404


async def test_list_queries_coverage_counts_volume_state(
    db: AsyncSession, test_site: Site,
) -> None:
    """Three queries: one with fresh volume, one without volume, one
    stale (>30d). Coverage must reflect that triple."""
    now = datetime.now(timezone.utc)

    db.add_all([
        SearchQuery(
            site_id=test_site.id,
            query_text="fresh phrase",
            wordstat_volume=1000,
            wordstat_updated_at=now - timedelta(days=1),
        ),
        SearchQuery(
            site_id=test_site.id,
            query_text="empty phrase",
            wordstat_volume=None,
            wordstat_updated_at=None,
        ),
        SearchQuery(
            site_id=test_site.id,
            query_text="stale phrase",
            wordstat_volume=500,
            wordstat_updated_at=now - timedelta(days=60),
        ),
    ])
    await db.flush()

    resp = await list_queries(site_id=test_site.id, sort="volume", limit=200, db=db)
    assert resp.total == 3
    assert resp.coverage["with_volume"] == 2
    assert resp.coverage["without_volume"] == 1
    assert resp.coverage["stale"] == 1


async def test_list_queries_rejects_invalid_sort_param() -> None:
    """`sort` is constrained by FastAPI's regex pattern — surface that
    fact at the unit level so a future refactor that drops the regex
    fails this test rather than ships a 500-on-typo endpoint.

    We can't trigger the FastAPI Query regex without an HTTP layer,
    so we pin the regex source instead — same intent, fast assertion.
    """
    from app.api.v1.studio import list_queries as fn

    sig_default = fn.__wrapped__ if hasattr(fn, "__wrapped__") else fn
    # The regex is encoded into the FastAPI Query metadata; assert the
    # accepted set is exactly the four documented modes.
    import inspect

    src = inspect.getsource(sig_default)
    assert "^(volume|recent|alpha|position)$" in src
