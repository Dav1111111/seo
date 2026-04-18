"""Tests for app.core_audit.demand_map.llm_expansion.

The LLM client is injected via the `caller=` argument so we never hit
the network. We cover:
  * happy path — cluster_key echoed back, Russian text passes filter
  * unknown cluster_key — dropped
  * non-Russian text — dropped
  * competitor brand mention — dropped
  * caller raising — fail-open returns []
  * truncation at MAX_ADDITIONAL_QUERIES
"""

from __future__ import annotations

import uuid

from app.core_audit.demand_map.dto import (
    ClusterSource,
    ClusterType,
    QualityTier,
    TargetClusterDTO,
    VolumeTier,
)
from app.core_audit.demand_map.llm_expansion import (
    MAX_ADDITIONAL_QUERIES,
    expand_with_llm,
)
from app.core_audit.intent_codes import IntentCode


def _mk_cluster(key: str, name: str = "экскурсии сочи") -> TargetClusterDTO:
    return TargetClusterDTO(
        site_id=uuid.uuid4(),
        cluster_key=key,
        name_ru=name,
        intent_code=IntentCode.COMM_CATEGORY,
        cluster_type=ClusterType.commercial_core,
        quality_tier=QualityTier.core,
        keywords=tuple(name.split()),
        seed_slots={},
        business_relevance=0.8,
        source=ClusterSource.cartesian,
    )


def _fake_caller(*, tool_input: dict, usage: dict | None = None):
    def _inner(*, model_tier, system, user_message, tool, max_tokens=2048):
        return tool_input, (usage or {"cost_usd": 0.001})
    return _inner


def test_expand_happy_path_accepts_valid_russian_queries():
    cluster = _mk_cluster("ck:a")
    tool_input = {
        "additional_queries": [
            {"cluster_key": "ck:a", "query_text": "экскурсии сочи с детьми",
             "estimated_volume": "m"},
            {"cluster_key": "ck:a", "query_text": "экскурсии сочи из адлера",
             "estimated_volume": "s"},
        ]
    }
    out = expand_with_llm(
        {"services": ["экскурсии"], "geo_primary": ["сочи"]},
        [cluster],
        caller=_fake_caller(tool_input=tool_input),
    )
    assert len(out) == 2
    assert all(q.source == ClusterSource.llm for q in out)
    assert out[0].estimated_volume_tier == VolumeTier.m
    assert out[1].estimated_volume_tier == VolumeTier.s


def test_expand_drops_unknown_cluster_key():
    cluster = _mk_cluster("ck:real")
    tool_input = {
        "additional_queries": [
            {"cluster_key": "ck:fake", "query_text": "вымышленный запрос"},
            {"cluster_key": "ck:real", "query_text": "настоящий запрос"},
        ]
    }
    out = expand_with_llm({}, [cluster], caller=_fake_caller(tool_input=tool_input))
    assert len(out) == 1
    assert out[0].cluster_key == "ck:real"


def test_expand_drops_english_text():
    cluster = _mk_cluster("ck:a")
    tool_input = {
        "additional_queries": [
            {"cluster_key": "ck:a", "query_text": "cheap tours sochi"},
            {"cluster_key": "ck:a", "query_text": "экскурсии недорого"},
        ]
    }
    out = expand_with_llm({}, [cluster], caller=_fake_caller(tool_input=tool_input))
    assert len(out) == 1
    assert "экскурсии" in out[0].query_text


def test_expand_drops_competitor_brand_queries():
    cluster = _mk_cluster("ck:a")
    tool_input = {
        "additional_queries": [
            {"cluster_key": "ck:a", "query_text": "sputnik8 экскурсии"},
            {"cluster_key": "ck:a", "query_text": "экскурсии с гидом"},
        ]
    }
    cfg = {"competitor_brands": ["sputnik8"]}
    out = expand_with_llm(cfg, [cluster], caller=_fake_caller(tool_input=tool_input))
    # First is dropped (english-ish + brand); second is clean.
    texts = [q.query_text for q in out]
    assert "экскурсии с гидом" in texts
    assert not any("sputnik8" in t for t in texts)


def test_expand_fail_open_on_caller_exception():
    cluster = _mk_cluster("ck:a")

    def _boom(**kw):
        raise RuntimeError("anthropic down")

    out = expand_with_llm({}, [cluster], caller=_boom)
    assert out == []


def test_expand_empty_clusters_returns_empty():
    out = expand_with_llm({}, [], caller=_fake_caller(tool_input={"additional_queries": []}))
    assert out == []


def test_expand_truncates_at_max_additional_queries():
    cluster = _mk_cluster("ck:a")
    tool_input = {
        "additional_queries": [
            {"cluster_key": "ck:a", "query_text": f"запрос номер {i}"}
            for i in range(MAX_ADDITIONAL_QUERIES + 20)
        ]
    }
    out = expand_with_llm({}, [cluster], caller=_fake_caller(tool_input=tool_input))
    assert len(out) == MAX_ADDITIONAL_QUERIES


def test_expand_dedups_identical_queries():
    cluster = _mk_cluster("ck:a")
    tool_input = {
        "additional_queries": [
            {"cluster_key": "ck:a", "query_text": "экскурсии по городу"},
            {"cluster_key": "ck:a", "query_text": "экскурсии по городу"},
            {"cluster_key": "ck:a", "query_text": "экскурсии по городу"},
        ]
    }
    out = expand_with_llm({}, [cluster], caller=_fake_caller(tool_input=tool_input))
    assert len(out) == 1


def test_expand_handles_bad_tool_input_shape():
    cluster = _mk_cluster("ck:a")
    # Caller returns a string instead of a dict — fail-open.

    def _bad_caller(**kw):
        return "not a dict", {}

    out = expand_with_llm({}, [cluster], caller=_bad_caller)
    assert out == []


def test_expand_invalid_volume_tier_defaults_to_s():
    cluster = _mk_cluster("ck:a")
    tool_input = {
        "additional_queries": [
            {"cluster_key": "ck:a", "query_text": "тест запрос",
             "estimated_volume": "huge"},  # invalid
        ]
    }
    out = expand_with_llm({}, [cluster], caller=_fake_caller(tool_input=tool_input))
    assert len(out) == 1
    assert out[0].estimated_volume_tier == VolumeTier.s
