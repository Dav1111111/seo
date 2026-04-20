"""Phase E — Diagnostic section unit tests.

No real DB. We build mock AsyncSession objects whose `execute()` dispatches
on FROM-clause text, reusing the pattern from test_coverage_target_clusters.
All LLM calls are stubbed to force the deterministic template fallback so
tests are hermetic and cheap.
"""

from __future__ import annotations

import asyncio
import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core_audit.demand_map.models import TargetCluster
from app.core_audit.report.dto import DiagnosticSection
from app.core_audit.report.sections import diagnostic as diag_mod


# ─── helpers ──────────────────────────────────────────────────────────


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_cluster(
    *,
    site_id: uuid.UUID,
    name_ru: str,
    keywords: tuple[str, ...] = (),
    intent_code: str = "comm_category",
    cluster_type: str = "commercial_core",
    quality_tier: str = "core",
    expected_volume_tier: str = "m",
    business_relevance: float = 0.8,
    is_brand: bool = False,
    cluster_id: uuid.UUID | None = None,
    cluster_key: str | None = None,
) -> TargetCluster:
    c = TargetCluster(
        site_id=site_id,
        cluster_key=cluster_key or f"ck:{name_ru}",
        name_ru=name_ru,
        intent_code=intent_code,
        cluster_type=cluster_type,
        quality_tier=quality_tier,
        keywords=list(keywords),
        seed_slots={},
        is_brand=is_brand,
        is_competitor_brand=False,
        expected_volume_tier=expected_volume_tier,
        business_relevance=business_relevance,
        source="cartesian",
    )
    c.id = cluster_id or uuid.uuid4()
    return c


class _ResultRowIter:
    """Stand-in for a SQLAlchemy Result. Iteration yields tuples; scalars()
    returns scalar rows; .scalar() returns first scalar value."""

    def __init__(self, rows, scalar_rows=None, scalar_value=None):
        self._rows = rows
        self._scalars = scalar_rows
        self._scalar_value = scalar_value

    def __iter__(self):
        return iter(self._rows)

    def scalars(self):
        if self._scalars is None:
            raise RuntimeError("no scalar rows staged")
        return iter(self._scalars)

    def scalar(self):
        return self._scalar_value


def _stage_db(
    *,
    clusters: list[TargetCluster] | None = None,
    brand_split_rows: list[types.SimpleNamespace] | None = None,
    pages_total: int = 0,
    low_pri_rows: list[tuple] | None = None,
    # CoverageAnalyzer inputs:
    metric_rows: list[types.SimpleNamespace] | None = None,
    query_texts: list[tuple[uuid.UUID, str]] | None = None,
    page_scores: list[tuple] | None = None,
    query_intents: list[tuple] | None = None,
):
    """Build a MagicMock db that dispatches by statement text."""
    db = MagicMock()

    clusters_list = list(clusters or [])

    async def _execute(stmt, *args, **kwargs):
        text = str(stmt).lower()

        # Diagnostic's own queries.
        # 1) pages count: uses func.count(Page.id) from "pages"
        if "count(" in text and "pages" in text and "page_intent" not in text:
            return _ResultRowIter([], scalar_value=pages_total)

        # 2) Brand split: joins query_intents with daily_metrics and groups
        #    by is_brand.
        if "daily_metrics" in text and "query_intents" in text:
            return _ResultRowIter(list(brand_split_rows or []))

        # 3) Low-priority recs pull.
        if "page_review_recommendations" in text:
            return _ResultRowIter(list(low_pri_rows or []))

        # 4) target_clusters — could be the LIMIT-? existence check or
        #    the full SELECT from both the diagnostic and the analyzer.
        if "target_clusters" in text:
            if "limit" in text:
                # Existence probe — iterable with 1 row iff clusters exist.
                rows_for_probe = (
                    [(clusters_list[0].id,)] if clusters_list else []
                )
                return _ResultRowIter(rows_for_probe, scalar_rows=clusters_list)
            return _ResultRowIter([], scalar_rows=clusters_list)

        # CoverageAnalyzer pre-loads.
        if "query_intents" in text:
            return _ResultRowIter(list(query_intents or []))
        if "daily_metrics" in text:
            return _ResultRowIter(list(metric_rows or []))
        if "search_queries" in text and "query_text" in text:
            return _ResultRowIter(list(query_texts or []))
        if "page_intent_scores" in text or "page_intent" in text:
            return _ResultRowIter(list(page_scores or []))

        return _ResultRowIter([])

    db.execute = AsyncMock(side_effect=_execute)
    return db


# ─── 1. flag=False → skeleton, no DB work ─────────────────────────────


def test_flag_off_returns_skeleton():
    site_id = uuid.uuid4()
    db = _stage_db()
    with patch.object(diag_mod.settings, "USE_TARGET_DEMAND_MAP", False):
        res = _run(diag_mod.build_diagnostic(db, site_id))
    assert isinstance(res, DiagnosticSection)
    assert res.available is False
    assert res.root_problem_classification == "insufficient_data"
    assert "Target Demand Map" in res.root_problem_ru
    assert res.prose_source == "skeleton"
    # Must NOT have touched the DB.
    assert db.execute.await_count == 0


# ─── 2. flag=True but no clusters → skeleton ──────────────────────────


def test_flag_on_but_no_clusters_returns_skeleton():
    site_id = uuid.uuid4()
    db = _stage_db(clusters=[])
    with patch.object(diag_mod.settings, "USE_TARGET_DEMAND_MAP", True):
        res = _run(diag_mod.build_diagnostic(db, site_id))
    assert res.available is False
    assert res.root_problem_classification == "insufficient_data"
    assert "демand" in res.root_problem_ru.lower() or "спрос" in res.root_problem_ru.lower()


# ─── 3. brand_bias composite trigger — ALL 4 fire ─────────────────────


def test_composite_trigger_brand_bias():
    """Inputs crafted so all 4 thresholds fire simultaneously."""
    site_id = uuid.uuid4()

    # 5 brand clusters, 20 non-brand clusters (low relevance, so the
    # expected floor is modest but nonzero; observed non-brand imp = 0 ⇒
    # blind_spot_score = 1.0).
    brand_clusters = [
        _make_cluster(
            site_id=site_id,
            name_ru=f"бренд {i}",
            is_brand=True,
            business_relevance=0.9,
            expected_volume_tier="m",
        )
        for i in range(5)
    ]
    non_brand_clusters = [
        _make_cluster(
            site_id=site_id,
            name_ru=f"экскурсии X{i}",
            keywords=(f"уникальный_токен_{i}",),  # won't match anything observed
            business_relevance=0.8,
            expected_volume_tier="m",
        )
        for i in range(20)
    ]
    clusters = brand_clusters + non_brand_clusters

    # Brand impression split: 6000 brand / 1000 non-brand ⇒ ratio = 0.857
    brand_split = [
        types.SimpleNamespace(imp=6000, is_brand=True),
        types.SimpleNamespace(imp=1000, is_brand=False),
    ]

    db = _stage_db(
        clusters=clusters,
        brand_split_rows=brand_split,
        pages_total=25,  # >= 3
    )

    # Force template fallback (keep tests hermetic).
    with patch.object(diag_mod.settings, "USE_TARGET_DEMAND_MAP", True), patch(
        "app.core_audit.report.sections.diagnostic.generate_diagnostic_prose",
        return_value=(None, {"cost_usd": 0.0}),
    ):
        res = _run(diag_mod.build_diagnostic(db, site_id))

    assert res.available is True
    sig = res.signals
    assert sig["trigger_brand_bias"] is True
    assert sig["blind_spot_score"] >= 0.80
    assert sig["non_brand_coverage_ratio"] < 0.20
    assert sig["brand_imp_ratio"] > 0.50
    assert sig["pages_total"] >= 3
    assert res.root_problem_classification == "brand_bias"
    assert res.prose_source == "template"
    # Demand split shape.
    assert res.brand_demand["clusters"] == 5
    assert res.non_brand_demand["clusters"] == 20
    assert res.non_brand_demand["missing"] == 20  # nothing matched
    # missing_target_clusters top-10 present.
    assert len(res.missing_target_clusters) == 10
    # Root prose must mention brand ratio in the template fallback.
    assert "брендов" in res.root_problem_ru.lower()


# ─── 4. Not brand_bias — low_coverage path ────────────────────────────


def test_classification_low_coverage_when_brand_not_dominant():
    """brand_imp_ratio low → trigger doesn't fire, but coverage_ratio<0.4
    still yields 'low_coverage' classification."""
    site_id = uuid.uuid4()
    non_brand_clusters = [
        _make_cluster(
            site_id=site_id,
            name_ru=f"нишевый {i}",
            keywords=(f"никаких_совпадений_{i}",),
            business_relevance=0.8,
        )
        for i in range(10)
    ]
    # Dominant NON-brand impressions — brand_imp_ratio << 0.5.
    brand_split = [
        types.SimpleNamespace(imp=100, is_brand=True),
        types.SimpleNamespace(imp=9000, is_brand=False),
    ]
    db = _stage_db(
        clusters=non_brand_clusters,
        brand_split_rows=brand_split,
        pages_total=5,
    )
    with patch.object(diag_mod.settings, "USE_TARGET_DEMAND_MAP", True), patch(
        "app.core_audit.report.sections.diagnostic.generate_diagnostic_prose",
        return_value=(None, {"cost_usd": 0.0}),
    ):
        res = _run(diag_mod.build_diagnostic(db, site_id))

    assert res.available is True
    assert res.signals["trigger_brand_bias"] is False
    assert res.signals["brand_imp_ratio"] < 0.5
    assert res.root_problem_classification == "low_coverage"


# ─── 5. weak_technical — pages_total < 3 ──────────────────────────────


def test_classification_weak_technical_when_few_pages():
    site_id = uuid.uuid4()
    clusters = [
        _make_cluster(
            site_id=site_id,
            name_ru="кластер",
            keywords=("никаких_совпадений",),
            business_relevance=0.8,
        )
    ]
    db = _stage_db(
        clusters=clusters,
        brand_split_rows=[],  # no metrics at all
        pages_total=1,  # < 3
    )
    with patch.object(diag_mod.settings, "USE_TARGET_DEMAND_MAP", True), patch(
        "app.core_audit.report.sections.diagnostic.generate_diagnostic_prose",
        return_value=(None, {"cost_usd": 0.0}),
    ):
        res = _run(diag_mod.build_diagnostic(db, site_id))

    assert res.available is True
    # pages_total=1 means the brand_bias trigger cannot fire (pages>=3 fails).
    assert res.signals["trigger_brand_bias"] is False
    assert res.signals["pages_total"] == 1
    assert res.root_problem_classification == "weak_technical"


# ─── 6. DTO embedding — WeeklyReport must accept diagnostic as first ──


def test_weekly_report_accepts_diagnostic_first():
    """Sanity check: WeeklyReport with explicit diagnostic + minimal other
    sections serialises cleanly and diagnostic round-trips."""
    from datetime import date, datetime, timezone
    from app.core_audit.report.dto import (
        ActionPlanSection,
        CoverageSection,
        ExecutiveSection,
        PageFindingsSection,
        QueryTrendsSection,
        ReportMeta,
        TechnicalSection,
        WeeklyReport,
    )

    diag = DiagnosticSection(
        available=True,
        root_problem_classification="brand_bias",
        root_problem_ru="test",
        supporting_symptoms_ru=["a"],
        recommended_first_actions_ru=["b"],
        signals={"trigger_brand_bias": True},
        brand_demand={"clusters": 1, "observed_impressions": 10},
        non_brand_demand={
            "clusters": 1,
            "observed_impressions": 0,
            "covered": 0,
            "missing": 1,
            "coverage_ratio": 0.0,
        },
    )

    r = WeeklyReport(
        diagnostic=diag,
        meta=ReportMeta(
            site_id=uuid.uuid4(),
            site_host="x",
            week_start=date(2026, 4, 13),
            week_end=date(2026, 4, 19),
            generated_at=datetime.now(timezone.utc),
            builder_version="1.0.0",
        ),
        executive=ExecutiveSection(health_score=50, prose_ru="e"),
        action_plan=ActionPlanSection(),
        coverage=CoverageSection(),
        query_trends=QueryTrendsSection(),
        page_findings=PageFindingsSection(),
        technical=TechnicalSection(),
    )
    blob = r.to_jsonb()
    assert "diagnostic" in blob
    assert blob["diagnostic"]["available"] is True
    assert blob["diagnostic"]["root_problem_classification"] == "brand_bias"


# ─── 7. Default factory — diagnostic optional on WeeklyReport ─────────


def test_weekly_report_default_diagnostic_skeleton():
    """Callers that don't pass diagnostic still get a valid report with a
    skeleton DiagnosticSection (available=False)."""
    from datetime import date, datetime, timezone
    from app.core_audit.report.dto import (
        ActionPlanSection,
        CoverageSection,
        ExecutiveSection,
        PageFindingsSection,
        QueryTrendsSection,
        ReportMeta,
        TechnicalSection,
        WeeklyReport,
    )

    r = WeeklyReport(
        meta=ReportMeta(
            site_id=uuid.uuid4(),
            site_host="x",
            week_start=date(2026, 4, 13),
            week_end=date(2026, 4, 19),
            generated_at=datetime.now(timezone.utc),
            builder_version="1.0.0",
        ),
        executive=ExecutiveSection(health_score=50, prose_ru="e"),
        action_plan=ActionPlanSection(),
        coverage=CoverageSection(),
        query_trends=QueryTrendsSection(),
        page_findings=PageFindingsSection(),
        technical=TechnicalSection(),
    )
    assert r.diagnostic.available is False
    assert r.diagnostic.root_problem_classification == "insufficient_data"


# ─── 8. Template fallback output shape ───────────────────────────────


def test_template_diagnostic_all_classifications():
    """Every classification path returns the full 3-field dict."""
    for cls in ("brand_bias", "weak_technical", "low_coverage", "none", "insufficient_data"):
        payload = {
            "classification": cls,
            "signals": {
                "blind_spot_score": 0.9,
                "non_brand_coverage_ratio": 0.1,
                "brand_imp_ratio": 0.85,
                "pages_total": 10,
                "trigger_brand_bias": cls == "brand_bias",
            },
            "brand_demand": {"clusters": 5, "observed_impressions": 1000},
            "non_brand_demand": {
                "clusters": 20,
                "observed_impressions": 100,
                "covered": 2,
                "missing": 18,
                "coverage_ratio": 0.1,
            },
        }
        from app.core_audit.report.prose import template_diagnostic
        out = template_diagnostic(payload)
        assert set(out.keys()) == {
            "root_problem_ru",
            "supporting_symptoms_ru",
            "recommended_first_actions_ru",
        }
        assert out["root_problem_ru"]
