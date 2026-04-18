"""Tests for app.core_audit.demand_map.suggest.

These exercise the JSONP-parse path + cluster enrichment with a mocked
fetcher so no real HTTP goes out. One test uses monkeypatching of
urllib.request.urlopen to cover the fail-open contract on network errors.
"""

from __future__ import annotations

import io
import json
import types
import uuid
from typing import Sequence

import pytest

from app.core_audit.demand_map.dto import (
    ClusterSource,
    ClusterType,
    QualityTier,
    TargetClusterDTO,
    VolumeTier,
)
from app.core_audit.demand_map import suggest as suggest_module
from app.core_audit.demand_map.suggest import (
    MAX_SUGGEST_CALLS,
    enrich_clusters_with_suggest,
    fetch_suggestions,
)
from app.core_audit.intent_codes import IntentCode


# --------------- fetch_suggestions ------------------------------------------


class _FakeResp:
    def __init__(self, body: str, status: int = 200):
        self._body = body.encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_fetch_suggestions_parses_jsonp_shape(monkeypatch):
    body = json.dumps(["экскурсии сочи", [
        "экскурсии сочи цены",
        "экскурсии сочи отзывы",
        "экскурсии сочи недорого",
    ]])
    monkeypatch.setattr(
        suggest_module, "urlopen",
        lambda req, timeout=None: _FakeResp(body),
    )
    out = fetch_suggestions("экскурсии сочи")
    assert out == (
        "экскурсии сочи цены",
        "экскурсии сочи отзывы",
        "экскурсии сочи недорого",
    )


def test_fetch_suggestions_empty_query_returns_empty(monkeypatch):
    # No HTTP call should happen.
    called = {"n": 0}

    def _boom(*a, **kw):
        called["n"] += 1
        raise AssertionError("should not be called")

    monkeypatch.setattr(suggest_module, "urlopen", _boom)
    assert fetch_suggestions("") == ()
    assert fetch_suggestions("   ") == ()
    assert called["n"] == 0


def test_fetch_suggestions_http_error_returns_empty(monkeypatch):
    def _raise(*a, **kw):
        raise OSError("network down")

    monkeypatch.setattr(suggest_module, "urlopen", _raise)
    assert fetch_suggestions("test") == ()


def test_fetch_suggestions_malformed_json_returns_empty(monkeypatch):
    monkeypatch.setattr(
        suggest_module, "urlopen",
        lambda req, timeout=None: _FakeResp("not json at all{"),
    )
    assert fetch_suggestions("test") == ()


def test_fetch_suggestions_handles_nested_items(monkeypatch):
    # Some Suggest responses wrap each item as [text, metadata].
    body = json.dumps([
        "туры",
        [["туры в сочи", {"meta": 1}], "туры в абхазию"],
    ])
    monkeypatch.setattr(
        suggest_module, "urlopen",
        lambda req, timeout=None: _FakeResp(body),
    )
    out = fetch_suggestions("туры")
    assert out == ("туры в сочи", "туры в абхазию")


def test_fetch_suggestions_caps_at_five(monkeypatch):
    body = json.dumps(["q", [f"q{i}" for i in range(10)]])
    monkeypatch.setattr(
        suggest_module, "urlopen",
        lambda req, timeout=None: _FakeResp(body),
    )
    out = fetch_suggestions("q")
    assert len(out) == 5


# --------------- enrich_clusters_with_suggest -------------------------------


def _mk_cluster(
    *,
    name: str,
    tier: QualityTier = QualityTier.core,
    relevance: float = 0.9,
    cluster_type: ClusterType = ClusterType.commercial_core,
) -> TargetClusterDTO:
    return TargetClusterDTO(
        site_id=uuid.uuid4(),
        cluster_key=f"ck:{name.replace(' ', '_')}",
        name_ru=name,
        intent_code=IntentCode.COMM_CATEGORY,
        cluster_type=cluster_type,
        quality_tier=tier,
        keywords=tuple(name.split()),
        seed_slots={},
        business_relevance=relevance,
        source=ClusterSource.cartesian,
    )


def test_enrich_emits_queries_skipping_identical_suggestion():
    clusters = [_mk_cluster(name="экскурсии сочи")]

    def _fake(query: str) -> tuple[str, ...]:
        # Include an identical suggestion that should be skipped.
        return ("экскурсии сочи", "экскурсии сочи цены", "экскурсии сочи отзывы")

    out = enrich_clusters_with_suggest(
        clusters, per_cluster=3, sleep_s=0, fetcher=_fake
    )
    texts = [q.query_text for q in out]
    assert "экскурсии сочи" not in texts
    assert "экскурсии сочи цены" in texts
    assert "экскурсии сочи отзывы" in texts
    assert all(q.source == ClusterSource.suggest for q in out)


def test_enrich_respects_per_cluster_limit():
    clusters = [_mk_cluster(name="тест один")]
    out = enrich_clusters_with_suggest(
        clusters, per_cluster=2, sleep_s=0,
        fetcher=lambda q: ("a", "b", "c", "d", "e"),
    )
    assert len(out) == 2


def test_enrich_ranks_core_above_secondary_above_exploratory():
    clusters = [
        _mk_cluster(name="ex1", tier=QualityTier.exploratory, relevance=0.5),
        _mk_cluster(name="sec1", tier=QualityTier.secondary, relevance=0.6),
        _mk_cluster(name="core1", tier=QualityTier.core, relevance=0.8),
    ]
    seen: list[str] = []
    out = enrich_clusters_with_suggest(
        clusters, top_n=2, per_cluster=1, sleep_s=0,
        fetcher=lambda q: (seen.append(q) or ("res",)),
    )
    # Only core + secondary should be enriched; exploratory skipped.
    assert "core1" in seen
    assert "sec1" in seen
    assert "ex1" not in seen


def test_enrich_budget_cap_respected():
    # Provide far more clusters than MAX_SUGGEST_CALLS; ensure no overrun.
    clusters = [
        _mk_cluster(name=f"cluster {i}") for i in range(MAX_SUGGEST_CALLS + 10)
    ]
    call_count = {"n": 0}

    def _fake(q: str):
        call_count["n"] += 1
        return ("variant",)

    enrich_clusters_with_suggest(
        clusters, top_n=1000, per_cluster=1, sleep_s=0, fetcher=_fake,
    )
    assert call_count["n"] <= MAX_SUGGEST_CALLS


def test_enrich_skips_competitor_brand_clusters():
    c1 = _mk_cluster(name="конкурент")
    c1 = TargetClusterDTO(
        site_id=c1.site_id,
        cluster_key=c1.cluster_key,
        name_ru=c1.name_ru,
        intent_code=c1.intent_code,
        cluster_type=c1.cluster_type,
        quality_tier=c1.quality_tier,
        keywords=c1.keywords,
        seed_slots=c1.seed_slots,
        is_brand=False,
        is_competitor_brand=True,  # flagged
        expected_volume_tier=c1.expected_volume_tier,
        business_relevance=c1.business_relevance,
        source=c1.source,
    )
    seen: list[str] = []
    enrich_clusters_with_suggest(
        [c1], per_cluster=1, sleep_s=0,
        fetcher=lambda q: (seen.append(q) or ("x",)),
    )
    assert seen == []


def test_enrich_empty_clusters_returns_empty():
    assert enrich_clusters_with_suggest([]) == []


def test_enrich_fetcher_empty_response_is_tolerated():
    clusters = [_mk_cluster(name="тест")]
    out = enrich_clusters_with_suggest(
        clusters, per_cluster=1, sleep_s=0, fetcher=lambda q: (),
    )
    assert out == []
