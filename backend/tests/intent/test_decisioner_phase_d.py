"""Phase D Decisioner flag wiring.

Verifies `run_for_site` selects the right CoverageAnalyzer mode based
on `settings.USE_TARGET_DEMAND_MAP` and still produces CoverageDecision
rows without crashing for both modes.

These tests patch the heavy dependencies (classifier/LLM/service/page
scorer) so we don't need a real DB; they focus on the one thing Phase D
introduces: the mode kwarg passed to analyze_site.
"""

from __future__ import annotations

import asyncio
import sys
import types
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The decisioner transitively imports app.intent.llm_classifier, which
# imports the real anthropic SDK. In this test env the SDK isn't
# installed; stub both modules before importing the decisioner so the
# test file is collectable.
if "anthropic" not in sys.modules:
    _fake_anthropic = types.ModuleType("anthropic")
    _fake_anthropic.Anthropic = object  # type: ignore[attr-defined]
    sys.modules["anthropic"] = _fake_anthropic

if "app.intent.llm_classifier" not in sys.modules:
    _fake_llm = types.ModuleType("app.intent.llm_classifier")

    def _classify_ambiguous_batch(*_a, **_kw):  # noqa: ARG001
        return []

    _fake_llm.classify_ambiguous_batch = _classify_ambiguous_batch
    sys.modules["app.intent.llm_classifier"] = _fake_llm

from app.intent.decisioner import Decisioner  # noqa: E402
from app.intent.enums import CoverageStatus, IntentCode  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _fake_report():
    """Minimal IntentClusterReport-like object the decisioner will loop
    over. Only the attributes actually read by DecisionTree + decisioner
    persistence matter; we patch the tree so only persistence path runs."""
    return SimpleNamespace(
        intent_code=IntentCode.COMM_CATEGORY,
        queries_count=0,
        total_impressions_14d=0,
        total_clicks_14d=0,
        avg_position=None,
        top_queries=[],
        ambiguous_queries_count=0,
        best_page_id=None,
        best_page_url=None,
        best_page_score=0.0,
        pages_with_score_gte_4=0,
        pages_with_score_2_3=0,
        status=CoverageStatus.missing,
        target_cluster_id=None,
        cluster_type=None,
        quality_tier=None,
        business_relevance=None,
        coverage_score=None,
        coverage_gap=None,
        is_brand_cluster=None,
    )


def _mock_db():
    """AsyncSession mock: scalar_one_or_none → None Site; execute → empty."""
    db = MagicMock()

    class _ExecResult:
        def scalar_one_or_none(self):
            return None

        def __iter__(self):
            return iter([])

        def scalars(self):
            return iter([])

    db.execute = AsyncMock(return_value=_ExecResult())
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()
    return db


class _FakeDecision:
    """Shape compatible with decisioner persistence code."""
    def __init__(self):
        self.intent_code = IntentCode.COMM_CATEGORY
        self.cluster_key = "fake"
        self.action = SimpleNamespace(value="leave")
        self.justification_ru = "ok"
        self.target_page_id = None
        self.proposed_url = None
        self.proposed_title = None
        self.queries_count = 0
        self.total_impressions = 0
        self.expected_lift_impressions = 0
        self.standalone_test = None
        self.safety_verdict = None
        self.evidence = None


def _run_decisioner(monkeypatch, flag_value: bool, analyzer_spy):
    """Execute the decisioner with patched dependencies.
    Returns the mode kwarg passed to CoverageAnalyzer.analyze_site."""
    from app.intent import decisioner as dec_mod

    # Flip the flag via patch on the settings instance inside the module.
    monkeypatch.setattr(dec_mod.settings, "USE_TARGET_DEMAND_MAP", flag_value)

    # IntentService: skip real query-/page-classification.
    svc_mock = MagicMock()
    svc_mock.classify_site_queries = AsyncMock(return_value={"classified": 0})
    svc_mock.score_site_pages = AsyncMock(return_value={"scored": 0})

    # DecisionTree: return a fake decision so persistence loop still runs.
    tree_mock = MagicMock()
    tree_mock.decide = AsyncMock(return_value=_FakeDecision())

    # get_profile: return a plausible profile object (decisioner only
    # forwards it to other mocked components).
    profile_stub = SimpleNamespace(vertical="tourism", business_model="tour_operator")

    with (
        patch.object(dec_mod, "IntentService", return_value=svc_mock),
        patch.object(dec_mod, "DecisionTree", return_value=tree_mock),
        patch.object(dec_mod, "get_profile", return_value=profile_stub),
        patch.object(dec_mod, "CoverageAnalyzer", return_value=analyzer_spy),
    ):
        stats = _run(
            Decisioner().run_for_site(
                _mock_db(),
                uuid.uuid4(),
                use_llm_fallback=False,
                rebuild_decisions=False,
            )
        )
    return stats


# ─── 1. Flag OFF: analyze_site called with mode="legacy_intents" ─────


def test_flag_off_uses_legacy_mode(monkeypatch):
    analyzer_spy = MagicMock()
    analyzer_spy.analyze_site = AsyncMock(return_value=[_fake_report()])

    stats = _run_decisioner(monkeypatch, flag_value=False, analyzer_spy=analyzer_spy)

    assert analyzer_spy.analyze_site.await_count == 1
    call = analyzer_spy.analyze_site.await_args
    assert call.kwargs.get("mode") == "legacy_intents"
    assert stats["coverage_mode"] == "legacy_intents"
    assert stats["intents_analyzed"] == 1


# ─── 2. Flag ON: analyze_site called with mode="target_clusters" ─────


def test_flag_on_uses_target_clusters_mode(monkeypatch):
    analyzer_spy = MagicMock()
    analyzer_spy.analyze_site = AsyncMock(return_value=[_fake_report()])

    stats = _run_decisioner(monkeypatch, flag_value=True, analyzer_spy=analyzer_spy)

    assert analyzer_spy.analyze_site.await_count == 1
    call = analyzer_spy.analyze_site.await_args
    assert call.kwargs.get("mode") == "target_clusters"
    assert stats["coverage_mode"] == "target_clusters"
    assert stats["intents_analyzed"] == 1


# ─── 3. Both paths run the persistence loop without crashing ─────────


def test_both_paths_produce_decisions(monkeypatch):
    analyzer_spy = MagicMock()
    analyzer_spy.analyze_site = AsyncMock(
        return_value=[_fake_report(), _fake_report()]
    )

    for flag in (False, True):
        stats = _run_decisioner(monkeypatch, flag_value=flag, analyzer_spy=analyzer_spy)
        assert stats["intents_analyzed"] == 2
        # Decisions-by-action is always initialised → at least the keys exist
        # even if no row actually persisted (db mock is a no-op).
        assert "decisions_by_action" in stats
