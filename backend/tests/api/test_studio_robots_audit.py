"""Studio /robots-audit endpoint — integration coverage for the manual
trigger + cached read path defined by `_run_robots_audit_for_site`.

These tests call the route functions directly (same pattern as
`test_studio_indexation.py` / `test_studio_queries.py`) so the
SQL/cache logic is exercised against the real DB via the rolled-back
session fixture in `tests/conftest.py`. The auth-only test boots a
TestClient with the studio router mounted so we exercise the
`require_admin` dependency end-to-end.

`fetch_robots_txt` is monkey-patched in every DB test — we never hit
the network. The real `audit_yandex_robots` function is used, so
these tests double as a smoke test for the integration contract
between studio.py and the core auditor module.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import deps as deps_module
from app.api.v1.studio import (
    get_robots_audit,
    run_robots_audit,
    router as studio_router,
)
from app.models.analysis_event import AnalysisEvent
from app.models.site import Site


# Module-level marker is `asyncio` for the DB-touching tests below;
# the sync auth test opts out with its own `@pytest.mark.asyncio(False)`-
# equivalent (we just don't apply the mark by using a per-test decorator
# on the async ones instead).



# A small, deterministic robots.txt — enough to exercise group parsing,
# Sitemap extraction, and one Disallow without depending on the exact
# rule set of any real production site.
_FAKE_ROBOTS_BODY = (
    "User-agent: Yandex\n"
    "Disallow: /admin/\n"
    "\n"
    "User-agent: *\n"
    "Allow: /\n"
    "\n"
    "Sitemap: https://x/sitemap.xml\n"
)


def _install_fake_fetcher(
    monkeypatch: pytest.MonkeyPatch,
    *,
    body: str | None = _FAKE_ROBOTS_BODY,
    status: int | None = 200,
    url: str = "https://example.com/robots.txt",
) -> None:
    """Patch `fetch_robots_txt` on the collectors module so the studio
    route never reaches the network. Both the studio module's lazy
    import path and the collectors module attribute are patched, since
    the route does `from app.collectors.robots_fetcher import ...` at
    call time."""
    async def _fake_fetch(domain: str) -> dict[str, Any]:
        return {
            "url": url,
            "status": status,
            "body": body,
            "size_bytes": len(body.encode("utf-8")) if body else 0,
        }

    import app.collectors.robots_fetcher as robots_fetcher_mod
    monkeypatch.setattr(
        robots_fetcher_mod, "fetch_robots_txt", _fake_fetch, raising=True,
    )


# ── 1. POST runs the audit and writes an analysis_events row ─────────


@pytest.mark.asyncio
async def test_robots_audit_endpoint_runs_and_caches(
    db: AsyncSession,
    test_site: Site,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST → 200 with serialized audit, plus exactly one analysis_events
    row with stage='robots_audit'. Verifies the studio-level contract:
    the response shape is the dataclass dict + `cached_at`, and the
    cached row mirrors the same payload so the GET path can find it."""
    _install_fake_fetcher(monkeypatch)

    result = await run_robots_audit(site_id=test_site.id, db=db)

    # Response is the dataclass dict augmented with cached_at.
    assert isinstance(result, dict)
    assert result.get("robots_url") == "https://example.com/robots.txt"
    assert result.get("http_status") == 200
    assert result.get("is_accessible") is True
    # Sitemap from the fake body must round-trip into the result.
    assert "https://x/sitemap.xml" in result.get("sitemaps", [])
    assert "cached_at" in result and isinstance(result["cached_at"], str)

    # Exactly one cached event for this site, with the full payload.
    rows = (await db.execute(
        select(AnalysisEvent)
        .where(
            AnalysisEvent.site_id == test_site.id,
            AnalysisEvent.stage == "robots_audit",
        )
    )).scalars().all()
    assert len(rows) == 1
    event = rows[0]
    assert event.status == "done"
    assert event.run_id is None  # manual trigger, not a pipeline run
    assert isinstance(event.extra, dict)
    assert event.extra.get("robots_url") == "https://example.com/robots.txt"
    assert "https://x/sitemap.xml" in event.extra.get("sitemaps", [])


# ── 2. GET returns the cached result from the latest event row ──────


@pytest.mark.asyncio
async def test_robots_audit_get_returns_cached(
    db: AsyncSession,
    test_site: Site,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After POST writes a row, GET reads it back verbatim plus the
    cached_at timestamp. We do NOT re-run the audit on GET."""
    _install_fake_fetcher(monkeypatch)

    posted = await run_robots_audit(site_id=test_site.id, db=db)

    # Now break the fetcher — if GET tries to re-run, the test fails.
    async def _explode(domain: str):
        raise AssertionError("GET must read from cache, not re-fetch")

    import app.collectors.robots_fetcher as robots_fetcher_mod
    monkeypatch.setattr(
        robots_fetcher_mod, "fetch_robots_txt", _explode, raising=True,
    )

    fetched = await get_robots_audit(site_id=test_site.id, db=db)

    assert fetched.get("robots_url") == posted.get("robots_url")
    assert fetched.get("http_status") == posted.get("http_status")
    assert fetched.get("sitemaps") == posted.get("sitemaps")
    assert fetched.get("issues") == posted.get("issues")
    assert isinstance(fetched.get("cached_at"), str)


# ── 3. GET on a site with no prior audit returns 404 ────────────────


@pytest.mark.asyncio
async def test_robots_audit_get_404_when_never_run(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """No analysis_events row with stage='robots_audit' → 404. Frontend
    uses this to render the "никогда не проверялся" CTA."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await get_robots_audit(site_id=test_site.id, db=db)
    assert exc.value.status_code == 404


# ── 4. POST without admin key is rejected by the auth gate ─────────


ADMIN_KEY = "test-admin-secret"


def test_robots_audit_requires_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without `X-Admin-Key` the POST is 401 (or 503 if the server-side
    key is unset). We force a known key, so the only outcome that
    proves the auth gate is wired is 401."""
    monkeypatch.setattr(
        deps_module.settings, "ADMIN_API_KEY", ADMIN_KEY, raising=False,
    )

    app = FastAPI()
    app.include_router(studio_router)
    client = TestClient(app)

    # Endpoint lives under the studio router prefix `/admin/studio`.
    site_id = uuid.uuid4()
    response = client.post(f"/admin/studio/sites/{site_id}/robots-audit")
    assert response.status_code == 401

    # And a wrong key is still 401, not 200/404.
    response = client.post(
        f"/admin/studio/sites/{site_id}/robots-audit",
        headers={"X-Admin-Key": "wrong-key"},
    )
    assert response.status_code == 401
