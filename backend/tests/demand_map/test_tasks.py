"""Integration-style tests for app.core_audit.demand_map.tasks.

Celery + DB are not spun up here — we exercise the inner coroutine by
patching:
  * `task_session` → yield an in-memory mock DB session
  * `get_profile`  → return a tiny profile with 1 template
  * `expand_for_site` → return a fixed list of DTOs (Phase A is
    tested exhaustively in test_expander.py)
  * Suggest + LLM + persistence → all mocked
  * observed loader → return canned observed queries

The goal is to verify the orchestration glue (fail-open behaviour,
feature flag, rescore dispatch, persistence call), NOT the inner stages
(which have their own tests).

Celery may or may not be installed in the test environment. We guard
with an importorskip so the rest of the suite stays green either way.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

celery = pytest.importorskip("celery")  # skip whole module if Celery missing

from app.core_audit.demand_map.dto import (
    ClusterSource,
    ClusterType,
    QualityTier,
    TargetClusterDTO,
)
from app.core_audit.intent_codes import IntentCode


SITE_ID = uuid.uuid4()


def _mk_cluster(key: str = "ck:x", name: str = "экскурсии сочи") -> TargetClusterDTO:
    return TargetClusterDTO(
        site_id=SITE_ID,
        cluster_key=key,
        name_ru=name,
        intent_code=IntentCode.COMM_CATEGORY,
        cluster_type=ClusterType.commercial_core,
        quality_tier=QualityTier.core,
        keywords=tuple(name.split()),
        seed_slots={},
        business_relevance=0.75,
        source=ClusterSource.cartesian,
    )


class _MockSite:
    def __init__(self, target_config):
        self.vertical = "tourism"
        self.business_model = "tour_operator"
        self.target_config = target_config


class _CtxAsync:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_):
        return False


def _fake_task_session(db):
    def _factory():
        return _CtxAsync(db)
    return _factory


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --- helpers to call the inner coroutine directly (skip Celery runtime) ----

def _invoke_task(site_id: uuid.UUID, patches: dict) -> dict:
    """Execute the task's _inner coroutine via patched internals."""
    from app.core_audit.demand_map import tasks as tasks_mod

    # We have to dig into the function to exec its async body without Celery.
    # Easiest: call the underlying .run() — Celery task decorator exposes it.
    # But .run() calls _run(_inner()). We patch _run to a pass-through that
    # just awaits the coroutine synchronously.
    with (
        patch.object(tasks_mod, "task_session", patches["task_session"]),
        patch.object(tasks_mod, "get_profile", patches["get_profile"]),
        patch.object(tasks_mod, "expand_for_site", patches["expand_for_site"]),
        patch.object(tasks_mod, "persist_demand_map", patches["persist_demand_map"]),
        patch.object(tasks_mod, "load_observed_queries", patches["load_observed_queries"]),
        patch.object(tasks_mod, "rescore_with_observed_overlap", patches["rescore"]),
    ):
        # Replace _run with pass-through sync executor.
        with patch.object(tasks_mod, "_run", _run):
            return tasks_mod.demand_map_build_site_task.run(str(site_id))


# --------------- tests ------------------------------------------------------


def test_task_skips_when_no_target_config():
    mock_db = MagicMock()
    mock_db.get = AsyncMock(return_value=_MockSite({}))

    out = _invoke_task(SITE_ID, {
        "task_session": _fake_task_session(mock_db),
        "get_profile": lambda *a, **kw: None,
        "expand_for_site": lambda *a, **kw: [],
        "persist_demand_map": AsyncMock(return_value={}),
        "load_observed_queries": AsyncMock(return_value=[]),
        "rescore": lambda c, o: c,
    })
    assert out["status"] == "skipped"
    assert out["reason"] == "no_target_config"


def test_task_skips_when_site_missing():
    mock_db = MagicMock()
    mock_db.get = AsyncMock(return_value=None)

    out = _invoke_task(SITE_ID, {
        "task_session": _fake_task_session(mock_db),
        "get_profile": lambda *a, **kw: None,
        "expand_for_site": lambda *a, **kw: [],
        "persist_demand_map": AsyncMock(return_value={}),
        "load_observed_queries": AsyncMock(return_value=[]),
        "rescore": lambda c, o: c,
    })
    assert out["status"] == "skipped"
    assert out["reason"] == "site_not_found"


def test_task_happy_path_persists_clusters_and_queries():
    clusters = [_mk_cluster("ck:1"), _mk_cluster("ck:2", name="туры сочи")]
    mock_db = MagicMock()
    mock_db.get = AsyncMock(return_value=_MockSite({"services": ["экскурсии"]}))

    persist = AsyncMock(return_value={
        "clusters_written": 2,
        "queries_written": 0,
        "clusters_deleted": 0,
        "queries_skipped_unknown_key": 0,
    })

    out = _invoke_task(SITE_ID, {
        "task_session": _fake_task_session(mock_db),
        "get_profile": lambda *a, **kw: object(),
        "expand_for_site": lambda *a, **kw: clusters,
        "persist_demand_map": persist,
        "load_observed_queries": AsyncMock(return_value=[]),
        "rescore": lambda c, o: c,
    })
    assert out["status"] == "ok"
    assert out["clusters_written"] == 2
    # persist was invoked exactly once.
    assert persist.await_count == 1


def test_task_enrichment_failures_do_not_block_persistence():
    """Suggest + LLM raise → Cartesian still persisted."""
    from app.core_audit.demand_map import tasks as tasks_mod

    clusters = [_mk_cluster("ck:1")]
    mock_db = MagicMock()
    mock_db.get = AsyncMock(return_value=_MockSite({"services": ["x"]}))
    persist = AsyncMock(return_value={
        "clusters_written": 1,
        "queries_written": 0,
        "clusters_deleted": 0,
        "queries_skipped_unknown_key": 0,
    })

    def _boom_suggest(*a, **kw):
        raise RuntimeError("suggest exploded")

    def _boom_llm(*a, **kw):
        raise RuntimeError("llm exploded")

    # Force enrichment flag on (it's default True, but be explicit).
    from app.config import settings
    with patch.object(settings, "USE_DEMAND_MAP_ENRICHMENT", True):
        with (
            patch.object(tasks_mod, "task_session", _fake_task_session(mock_db)),
            patch.object(tasks_mod, "get_profile", lambda *a, **kw: object()),
            patch.object(tasks_mod, "expand_for_site", lambda *a, **kw: clusters),
            patch.object(tasks_mod, "persist_demand_map", persist),
            patch.object(tasks_mod, "load_observed_queries", AsyncMock(return_value=[])),
            patch.object(tasks_mod, "rescore_with_observed_overlap", lambda c, o: c),
            patch.object(tasks_mod, "_run", _run),
            patch("app.core_audit.demand_map.suggest.enrich_clusters_with_suggest", _boom_suggest),
            patch("app.core_audit.demand_map.llm_expansion.expand_with_llm", _boom_llm),
        ):
            out = tasks_mod.demand_map_build_site_task.run(str(SITE_ID))

    assert out["status"] == "ok"
    assert out["clusters_written"] == 1
    assert persist.await_count == 1


def test_task_feature_flag_skips_enrichment():
    """When USE_DEMAND_MAP_ENRICHMENT=False, neither Suggest nor LLM are called."""
    from app.core_audit.demand_map import tasks as tasks_mod

    clusters = [_mk_cluster("ck:1")]
    mock_db = MagicMock()
    mock_db.get = AsyncMock(return_value=_MockSite({"services": ["x"]}))
    persist = AsyncMock(return_value={
        "clusters_written": 1,
        "queries_written": 0,
        "clusters_deleted": 0,
        "queries_skipped_unknown_key": 0,
    })

    calls = {"suggest": 0, "llm": 0}

    def _count_suggest(*a, **kw):
        calls["suggest"] += 1
        return []

    def _count_llm(*a, **kw):
        calls["llm"] += 1
        return []

    from app.config import settings
    with patch.object(settings, "USE_DEMAND_MAP_ENRICHMENT", False):
        with (
            patch.object(tasks_mod, "task_session", _fake_task_session(mock_db)),
            patch.object(tasks_mod, "get_profile", lambda *a, **kw: object()),
            patch.object(tasks_mod, "expand_for_site", lambda *a, **kw: clusters),
            patch.object(tasks_mod, "persist_demand_map", persist),
            patch.object(tasks_mod, "load_observed_queries", AsyncMock(return_value=[])),
            patch.object(tasks_mod, "rescore_with_observed_overlap", lambda c, o: c),
            patch.object(tasks_mod, "_run", _run),
            patch("app.core_audit.demand_map.suggest.enrich_clusters_with_suggest", _count_suggest),
            patch("app.core_audit.demand_map.llm_expansion.expand_with_llm", _count_llm),
        ):
            out = tasks_mod.demand_map_build_site_task.run(str(SITE_ID))

    assert out["status"] == "ok"
    assert out["enrichment_enabled"] is False
    assert calls["suggest"] == 0
    assert calls["llm"] == 0


def test_task_observed_load_failure_is_tolerated():
    """If loading observed queries raises, we fall back to [] and continue."""
    from app.core_audit.demand_map import tasks as tasks_mod

    clusters = [_mk_cluster("ck:1")]
    mock_db = MagicMock()
    mock_db.get = AsyncMock(return_value=_MockSite({"services": ["x"]}))
    persist = AsyncMock(return_value={
        "clusters_written": 1,
        "queries_written": 0,
        "clusters_deleted": 0,
        "queries_skipped_unknown_key": 0,
    })

    from app.config import settings
    with patch.object(settings, "USE_DEMAND_MAP_ENRICHMENT", False):
        with (
            patch.object(tasks_mod, "task_session", _fake_task_session(mock_db)),
            patch.object(tasks_mod, "get_profile", lambda *a, **kw: object()),
            patch.object(tasks_mod, "expand_for_site", lambda *a, **kw: clusters),
            patch.object(tasks_mod, "persist_demand_map", persist),
            patch.object(
                tasks_mod, "load_observed_queries",
                AsyncMock(side_effect=RuntimeError("db glitch")),
            ),
            patch.object(tasks_mod, "rescore_with_observed_overlap", lambda c, o: c),
            patch.object(tasks_mod, "_run", _run),
        ):
            out = tasks_mod.demand_map_build_site_task.run(str(SITE_ID))

    assert out["status"] == "ok"
    assert persist.await_count == 1
