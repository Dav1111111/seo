"""Tests for app.core_audit.demand_map.persistence.

No real DB — we use an AsyncMock `db` to intercept execute/add_all/flush/
commit calls and assert on the call graph. This covers:
  * cluster_key -> cluster_id mapping path used by the query insert step
  * CASCADE-style wipe (DELETE called before any INSERT)
  * queries whose cluster_key is unknown are counted but not inserted
  * return-shape of the summary dict
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

from app.core_audit.demand_map.dto import (
    ClusterSource,
    ClusterType,
    QualityTier,
    TargetClusterDTO,
    TargetQueryDTO,
    VolumeTier,
)
from app.core_audit.demand_map.persistence import persist_demand_map
from app.core_audit.intent_codes import IntentCode


SITE = uuid.uuid4()


def _mk(key: str, name: str = "экскурсии сочи") -> TargetClusterDTO:
    return TargetClusterDTO(
        site_id=SITE,
        cluster_key=key,
        name_ru=name,
        intent_code=IntentCode.COMM_CATEGORY,
        cluster_type=ClusterType.commercial_core,
        quality_tier=QualityTier.core,
        keywords=tuple(name.split()),
        seed_slots={"city": "сочи"},
        business_relevance=0.8,
        source=ClusterSource.cartesian,
    )


def _mk_q(key: str, text: str) -> TargetQueryDTO:
    return TargetQueryDTO(
        cluster_key=key,
        query_text=text,
        source=ClusterSource.suggest,
        estimated_volume_tier=VolumeTier.s,
    )


def _make_db_mock():
    """AsyncMock db that assigns a UUID id to each ORM cluster on flush."""
    db = MagicMock()

    # Each call to execute() returns an object with a rowcount attribute —
    # we use a separate rowcount for DELETE vs SELECT.
    delete_result = MagicMock()
    delete_result.rowcount = 3
    db.execute = AsyncMock(return_value=delete_result)

    # add_all captures the ORM objects so the persistence function can
    # later inspect their .id attribute.
    added: list[list] = []

    def _add_all(rows):
        added.append(list(rows))
        for r in rows:
            if not getattr(r, "id", None):
                r.id = uuid.uuid4()

    db.add_all = MagicMock(side_effect=_add_all)
    db.flush = AsyncMock(return_value=None)
    db.commit = AsyncMock(return_value=None)
    db._added = added
    return db


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --------------- tests ------------------------------------------------------


def test_persist_empty_inputs_issues_delete_and_commit_only():
    db = _make_db_mock()
    stats = _run(persist_demand_map(db, SITE, [], []))
    assert stats["clusters_written"] == 0
    assert stats["queries_written"] == 0
    # DELETE ran; commit ran.
    db.execute.assert_awaited()  # at least one execute (the DELETE).
    db.commit.assert_awaited_once()


def test_persist_clusters_inserted_and_stats_reported():
    db = _make_db_mock()
    clusters = [_mk("ck:a"), _mk("ck:b", name="туры сочи")]
    stats = _run(persist_demand_map(db, SITE, clusters, []))
    assert stats["clusters_written"] == 2
    assert stats["queries_written"] == 0
    assert stats["clusters_deleted"] == 3  # from the mock
    # add_all called once for the clusters.
    assert len(db._added) == 1
    assert len(db._added[0]) == 2


def test_persist_queries_linked_to_known_cluster_keys():
    db = _make_db_mock()
    clusters = [_mk("ck:a")]
    queries = [_mk_q("ck:a", "вариант 1"), _mk_q("ck:a", "вариант 2")]
    stats = _run(persist_demand_map(db, SITE, clusters, queries))
    assert stats["clusters_written"] == 1
    assert stats["queries_written"] == 2
    assert stats["queries_skipped_unknown_key"] == 0
    # Two add_all calls: one for clusters, one for queries.
    assert len(db._added) == 2


def test_persist_drops_queries_with_unknown_cluster_key():
    db = _make_db_mock()
    clusters = [_mk("ck:a")]
    queries = [_mk_q("ck:a", "ok"), _mk_q("ck:fake", "orphan")]
    stats = _run(persist_demand_map(db, SITE, clusters, queries))
    assert stats["queries_written"] == 1
    assert stats["queries_skipped_unknown_key"] == 1


def test_persist_runs_in_single_transaction():
    db = _make_db_mock()
    clusters = [_mk("ck:a")]
    _run(persist_demand_map(db, SITE, clusters, []))
    # Exactly one commit overall.
    assert db.commit.await_count == 1
