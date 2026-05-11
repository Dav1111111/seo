from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v1 import sites as sites_api


def test_site_mutations_reject_missing_admin_key(monkeypatch):
    monkeypatch.setattr(sites_api.settings, "ADMIN_API_KEY", "secret", raising=False)

    with pytest.raises(HTTPException) as exc:
        sites_api._require_admin(None)

    assert exc.value.status_code == 401


def test_site_mutations_accept_admin_key(monkeypatch):
    monkeypatch.setattr(sites_api.settings, "ADMIN_API_KEY", "secret", raising=False)

    assert sites_api._require_admin("secret") is None


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(sites_api.router, prefix="/sites")
    app.include_router(sites_api.admin_router)
    return TestClient(app)


def test_public_create_site_requires_admin_before_db(monkeypatch):
    monkeypatch.setattr(sites_api.settings, "ADMIN_API_KEY", "secret", raising=False)

    response = _client().post("/sites", json={"domain": "example.com"})

    assert response.status_code == 401


def test_public_update_site_requires_admin_before_db(monkeypatch):
    monkeypatch.setattr(sites_api.settings, "ADMIN_API_KEY", "secret", raising=False)

    response = _client().patch(f"/sites/{uuid4()}", json={"display_name": "Example"})

    assert response.status_code == 401


def test_admin_alias_create_site_requires_admin_before_db(monkeypatch):
    monkeypatch.setattr(sites_api.settings, "ADMIN_API_KEY", "secret", raising=False)

    response = _client().post("/admin/sites", json={"domain": "example.com"})

    assert response.status_code == 401


def test_admin_alias_update_site_requires_admin_before_db(monkeypatch):
    monkeypatch.setattr(sites_api.settings, "ADMIN_API_KEY", "secret", raising=False)

    response = _client().patch(f"/admin/sites/{uuid4()}", json={"display_name": "Example"})

    assert response.status_code == 401
