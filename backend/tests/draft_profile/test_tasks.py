"""Tests for app.core_audit.draft_profile.tasks.

Celery may or may not be installed. We importorskip so the rest of the
suite stays green on environments without celery.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

celery = pytest.importorskip("celery")  # skip whole module if missing


from app.core_audit.draft_profile.dto import DraftProfile


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _AsyncCtx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_):
        return False


def test_task_happy_path_returns_ok_summary():
    from app.core_audit.draft_profile import tasks as tasks_mod

    site_id = str(uuid.uuid4())
    fake_db = AsyncMock()
    fake_db.commit = AsyncMock()

    mock_profile = DraftProfile(
        site_id=uuid.UUID(site_id),
        draft_config={"services": ["туры"], "geo_primary": ["сочи"]},
        confidences=[],
        overall_confidence=0.42,
        generated_at=datetime.now(tz=timezone.utc),
        signals={"pages_analyzed": 3, "queries_analyzed": 5},
    )

    async def _fake_build(db, sid, **kw):
        return mock_profile

    with (
        patch.object(tasks_mod, "task_session", lambda: _AsyncCtx(fake_db)),
        patch.object(tasks_mod, "build_draft_profile", _fake_build),
        patch.object(tasks_mod, "_run", _run),
    ):
        out = tasks_mod.draft_profile_build_site_task.run(site_id)

    assert out["status"] == "ok"
    assert out["overall_confidence"] == pytest.approx(0.42)
    assert out["pages_analyzed"] == 3


def test_task_missing_site_returns_skipped():
    from app.core_audit.draft_profile import tasks as tasks_mod

    async def _raises(db, sid, **kw):
        raise LookupError(f"site not found: {sid}")

    site_id = str(uuid.uuid4())
    fake_db = AsyncMock()

    with (
        patch.object(tasks_mod, "task_session", lambda: _AsyncCtx(fake_db)),
        patch.object(tasks_mod, "build_draft_profile", _raises),
        patch.object(tasks_mod, "_run", _run),
    ):
        out = tasks_mod.draft_profile_build_site_task.run(site_id)

    assert out["status"] == "skipped"
    assert out["reason"] == "site_not_found"


def test_task_unexpected_error_returns_error_status():
    from app.core_audit.draft_profile import tasks as tasks_mod

    async def _boom(db, sid, **kw):
        raise RuntimeError("db blew up")

    site_id = str(uuid.uuid4())
    fake_db = AsyncMock()

    with (
        patch.object(tasks_mod, "task_session", lambda: _AsyncCtx(fake_db)),
        patch.object(tasks_mod, "build_draft_profile", _boom),
        patch.object(tasks_mod, "_run", _run),
    ):
        out = tasks_mod.draft_profile_build_site_task.run(site_id)

    assert out["status"] == "error"
    assert "db blew up" in out["err"]
