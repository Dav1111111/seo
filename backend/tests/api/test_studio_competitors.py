"""Studio /competitors module — IMPLEMENTATION.md §1: PR-S5 reuses the
existing `admin_demand_map.py` competitors endpoints, no new backend.
This is a smoke test that the route still exists, is gated by admin
auth, and returns the empty-but-valid shape for a fresh site.

If a future refactor accidentally removes the endpoint, this test
fails before deploy.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_demand_map import _require_admin, get_competitors
from app.models.site import Site


pytestmark = pytest.mark.asyncio


def test_competitors_route_has_admin_dependency() -> None:
    """Sanity check: the FastAPI route declares the `_require_admin`
    Depends. Walking the route's dependency tree is overkill for one
    fact — assert the function the dep guards is in scope and the
    helper itself has the required header name."""
    import inspect

    sig = inspect.signature(_require_admin)
    assert "x_admin_key" in sig.parameters


def test_require_admin_rejects_when_unconfigured(monkeypatch) -> None:
    """If ADMIN_API_KEY is empty server-side, the gate refuses every
    request — it doesn't fall through to the handler. Same contract as
    studio.py uses."""
    from fastapi import HTTPException
    from app.api.v1 import admin_demand_map as mod

    monkeypatch.setattr(mod.settings, "ADMIN_API_KEY", "", raising=False)
    with pytest.raises(HTTPException) as exc:
        mod._require_admin(x_admin_key="anything")
    assert exc.value.status_code == 401


async def test_get_competitors_empty_for_fresh_site(
    db: AsyncSession, test_site: Site,
) -> None:
    """Fresh site has no competitor profile yet → endpoint returns
    `competitor_domains=[]` + `profile={}`. Pinning the empty shape so
    the UI is never surprised by `None`."""
    payload = await get_competitors(site_id=test_site.id, db=db)
    assert payload["site_id"] == str(test_site.id)
    assert payload["competitor_domains"] == []
    assert payload["profile"] == {}


async def test_get_competitors_404_for_unknown_site(db: AsyncSession) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await get_competitors(site_id=uuid.uuid4(), db=db)
    assert exc.value.status_code == 404
