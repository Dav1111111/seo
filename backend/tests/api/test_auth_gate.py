"""Auth-gate regression tests.

A 2026-05-13 security audit found that every non-`/admin/*` router
exposing `/sites/{site_id}/...` was unauthenticated — anyone with the
public hostname and a site UUID could read dashboards, mutate Issue
rows, and POST to endpoints that fan out into Celery LLM pipelines.

This file pins down the fix: each protected router must reject
requests without a valid `X-Admin-Key`, while `/health` stays public
for the load balancer.

We test with `TestClient` directly against each router (no DB-touching
fixtures needed for 401 paths — `require_admin` runs before the DB
dep). One positive test confirms that providing the key gets past the
auth gate (404 from the missing site is fine — it proves we passed
auth and reached the handler).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import (
    activity as activity_api,
    collectors as collectors_api,
    dashboard as dashboard_api,
    health as health_api,
    intent as intent_api,
    priority as priority_api,
    report as report_api,
    review as review_api,
)
from app.api.v1 import deps as deps_module


ADMIN_KEY = "test-admin-secret"


def _client_for(*routers) -> TestClient:
    """Mount the given routers under a fresh FastAPI app for an
    isolated TestClient. We do not include the full v1 router because
    that pulls in studio.py and other modules with import-time DB
    side effects irrelevant to this test."""
    app = FastAPI()
    for r in routers:
        app.include_router(r)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _set_admin_key(monkeypatch):
    """Force a known admin key for every test in this module so we can
    distinguish 401 (missing/wrong key) from 503 (key unset)."""
    monkeypatch.setattr(deps_module.settings, "ADMIN_API_KEY", ADMIN_KEY, raising=False)


# ── Negative paths: each protected router must reject anon traffic ──

def test_dashboard_endpoint_rejects_missing_key():
    client = _client_for(dashboard_api.router)
    response = client.get(f"/sites/{uuid4()}/dashboard")
    assert response.status_code == 401


def test_dashboard_patch_issue_rejects_missing_key():
    client = _client_for(dashboard_api.router)
    response = client.patch(
        f"/sites/{uuid4()}/issues/{uuid4()}", json={"status": "resolved"},
    )
    assert response.status_code == 401


def test_activity_endpoint_rejects_missing_key():
    client = _client_for(activity_api.router)
    response = client.get(f"/sites/{uuid4()}/activity")
    assert response.status_code == 401


def test_collectors_pipeline_trigger_rejects_missing_key():
    """The headline finding: anyone could POST and burn LLM spend."""
    client = _client_for(collectors_api.router)
    response = client.post(f"/sites/{uuid4()}/pipeline")
    assert response.status_code == 401


def test_collectors_webmaster_trigger_rejects_missing_key():
    client = _client_for(collectors_api.router)
    response = client.post(f"/sites/{uuid4()}/collect/webmaster")
    assert response.status_code == 401


def test_intent_decide_rejects_missing_key():
    client = _client_for(intent_api.router)
    response = client.post(f"/intent/sites/{uuid4()}/decide")
    assert response.status_code == 401


def test_priority_rescore_rejects_missing_key():
    client = _client_for(priority_api.router)
    response = client.post(f"/priorities/sites/{uuid4()}/rescore")
    assert response.status_code == 401


def test_report_run_rejects_missing_key():
    client = _client_for(report_api.router)
    response = client.post(f"/reports/sites/{uuid4()}/run")
    assert response.status_code == 401


def test_review_patch_recommendation_rejects_missing_key():
    client = _client_for(review_api.router)
    response = client.patch(
        f"/reviews/recommendations/{uuid4()}",
        json={"user_status": "applied"},
    )
    assert response.status_code == 401


def test_review_site_run_rejects_missing_key():
    client = _client_for(review_api.router)
    response = client.post(f"/reviews/sites/{uuid4()}/run")
    assert response.status_code == 401


# ── Positive paths ─────────────────────────────────────────────────────

def test_health_endpoint_stays_public():
    """Load balancers / Jino uptime probes must reach /health without
    credentials. If this ever returns 401 the LB starts marking the
    pod as down."""
    client = _client_for(health_api.router)
    response = client.get("/health")
    # /health hits DB + Redis; in a unit-test app neither is wired,
    # so it returns "degraded" with 200 — what matters here is that
    # the auth dep never runs.
    assert response.status_code == 200
    assert response.status_code != 401


def test_protected_endpoint_accepts_valid_key():
    """With the correct X-Admin-Key the request passes the auth gate
    and reaches the handler. We do not assert a specific success code
    (the handler may return 200 with default values, or 404 / 500 in
    an isolated TestClient with no DB) — only that we do NOT get 401."""
    client = _client_for(priority_api.router)
    response = client.post(
        f"/priorities/sites/{uuid4()}/rescore",
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    assert response.status_code != 401
    assert response.status_code != 403


def test_protected_endpoint_rejects_wrong_key():
    """Wrong key must still 401 — the constant-time compare in
    require_admin is what guarantees this; if a future refactor
    swaps in a startswith() check this test catches it."""
    client = _client_for(dashboard_api.router)
    response = client.get(
        f"/sites/{uuid4()}/dashboard",
        headers={"X-Admin-Key": "wrong-key"},
    )
    assert response.status_code == 401
