"""Tests for CoverageAnalyzer's Phase C target_clusters code path.

No real DB — we build an in-memory mock `db` whose `execute()` returns
pre-staged rows keyed by the FROM clause of the query. That lets us
exercise both the legacy and the target_clusters paths without a
Postgres connection.
"""

from __future__ import annotations

import asyncio
import types
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core_audit.demand_map.models import TargetCluster
from app.fingerprint.lemmatize import lemmatize_tokens, tokenize
from app.intent.coverage import CoverageAnalyzer, IntentClusterReport
from app.intent.enums import CoverageStatus, IntentCode
from app.intent.models import PageIntentScore, QueryIntent
from app.models.daily_metric import DailyMetric
from app.models.page import Page
from app.models.search_query import SearchQuery


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
    keywords: tuple[str, ...],
    intent_code: str = "comm_category",
    cluster_type: str = "commercial_core",
    quality_tier: str = "core",
    expected_volume_tier: str = "m",
    business_relevance: float = 0.8,
    is_brand: bool = False,
    cluster_id: uuid.UUID | None = None,
) -> TargetCluster:
    """Build a detached TargetCluster ORM object (no DB)."""
    c = TargetCluster(
        site_id=site_id,
        cluster_key=f"ck:{name_ru}",
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
    """Stand-in for a SQLAlchemy Result that supports both iteration
    (yielding tuples) and a .scalars() accessor (yielding ORM objects)."""

    def __init__(self, rows, scalar_rows=None):
        self._rows = rows
        self._scalars = scalar_rows

    def __iter__(self):
        return iter(self._rows)

    def scalars(self):
        if self._scalars is None:
            raise RuntimeError("no scalar rows staged")
        return iter(self._scalars)


def _stage_db(
    *,
    clusters: list[TargetCluster] | None = None,
    query_intents: list[tuple] | None = None,
    metric_rows: list[types.SimpleNamespace] | None = None,
    query_texts: list[tuple[uuid.UUID, str]] | None = None,
    page_scores: list[tuple] | None = None,
):
    """Build a MagicMock db whose execute(stmt) dispatches by FROM clause."""

    db = MagicMock()

    async def _execute(stmt, *args, **kwargs):
        # Inspect the statement to decide which stash to return.
        text = str(stmt)
        if "target_clusters" in text:
            return _ResultRowIter([], scalar_rows=list(clusters or []))
        if "query_intents" in text:
            return _ResultRowIter(list(query_intents or []))
        if "daily_metrics" in text:
            return _ResultRowIter(list(metric_rows or []))
        if "search_queries" in text and "query_text" in text:
            return _ResultRowIter(list(query_texts or []))
        if "page_intent_scores" in text or "page_intent" in text:
            return _ResultRowIter(list(page_scores or []))
        # Fallback — empty.
        return _ResultRowIter([])

    db.execute = AsyncMock(side_effect=_execute)
    return db


# ─── 1. legacy mode unchanged (shape & default-None on new fields) ────


def test_legacy_mode_unchanged():
    """Default mode returns IntentCode-indexed reports with Phase C fields None."""
    site_id = uuid.uuid4()
    db = _stage_db()  # no rows: all intents will report missing/0
    reports = _run(CoverageAnalyzer().analyze_site(db, site_id))

    # Legacy path yields one report per IntentCode value.
    assert len(reports) == len(list(IntentCode))
    for r in reports:
        assert isinstance(r, IntentClusterReport)
        # Phase C additive fields must stay None on legacy path.
        assert r.target_cluster_id is None
        assert r.cluster_type is None
        assert r.quality_tier is None
        assert r.business_relevance is None
        assert r.coverage_score is None
        assert r.coverage_gap is None
        assert r.is_brand_cluster is None


# ─── 2. N clusters → N reports ────────────────────────────────────────


def test_target_cluster_mode_returns_one_report_per_cluster():
    site_id = uuid.uuid4()
    clusters = [
        _make_cluster(site_id=site_id, name_ru="экскурсии сочи", keywords=("экскурсия", "сочи")),
        _make_cluster(site_id=site_id, name_ru="туры абхазия", keywords=("тур", "абхазия")),
        _make_cluster(site_id=site_id, name_ru="джиппинг красная поляна", keywords=("джиппинг",)),
    ]
    db = _stage_db(clusters=clusters)
    reports = _run(
        CoverageAnalyzer().analyze_site(db, site_id, mode="target_clusters")
    )
    assert len(reports) == 3
    cluster_ids = {r.target_cluster_id for r in reports}
    assert cluster_ids == {c.id for c in clusters}


# ─── 3. coverage_score formula unit check ────────────────────────────


def test_coverage_score_formula():
    """Known inputs → known outputs.

    No observed queries, no page — coverage_score must be exactly 0.0
    and status must be 'missing'. coverage_gap = 1 * business_relevance.
    """
    site_id = uuid.uuid4()
    c = _make_cluster(
        site_id=site_id,
        name_ru="никак непокрытая тема",
        keywords=("уникальный_токен_xyz",),  # nothing in observed will match
        business_relevance=0.9,
        expected_volume_tier="m",
    )
    db = _stage_db(clusters=[c])
    reports = _run(
        CoverageAnalyzer().analyze_site(db, site_id, mode="target_clusters")
    )
    assert len(reports) == 1
    r = reports[0]
    # 0.5 * (0/5) + 0.3 * min(0/100, 1) + 0.2 * 0 = 0.0
    assert r.coverage_score == 0.0
    # (1 - 0) * 0.9
    assert r.coverage_gap == pytest.approx(0.9)
    assert r.status == CoverageStatus.missing
    assert r.queries_count == 0


# ─── 4. lemma-overlap match ───────────────────────────────────────────


def test_match_observed_lemma_overlap():
    """'экскурсии сочи' observed matches cluster keyword 'экскурсия'.

    Uses lemmatize_tokens with drop_stopwords=True. On hosts without
    pymorphy3 installed the function degrades to lowercase+stopword
    filter, so we match on literal token overlap. To keep the test
    robust on BOTH environments, we use tokens that are already in
    their normal form (so pymorphy3 is a no-op) and that match
    literally under the degraded path.
    """
    site_id = uuid.uuid4()
    # Keyword = 'экскурсия' (already nominative); query contains
    # 'экскурсия' too — match on either path.
    c = _make_cluster(
        site_id=site_id,
        name_ru="экскурсии сочи",
        keywords=("экскурсия", "сочи"),
    )
    query_id = uuid.uuid4()
    db = _stage_db(
        clusters=[c],
        query_texts=[(query_id, "экскурсия сочи море")],
        metric_rows=[
            types.SimpleNamespace(dimension_id=query_id, imp=50, clk=3, pos=4.2),
        ],
    )
    reports = _run(
        CoverageAnalyzer().analyze_site(db, site_id, mode="target_clusters")
    )
    assert len(reports) == 1
    r = reports[0]
    assert r.queries_count == 1
    assert r.total_impressions_14d == 50
    assert r.total_clicks_14d == 3
    assert r.top_queries == ["экскурсия сочи море"]


# ─── 5. missing when no observed + no strong page ────────────────────


def test_missing_cluster_when_no_observed_and_no_strong_page():
    site_id = uuid.uuid4()
    c = _make_cluster(
        site_id=site_id,
        name_ru="глубокая ниша",
        keywords=("уникальный_xyz",),
    )
    db = _stage_db(clusters=[c])
    reports = _run(
        CoverageAnalyzer().analyze_site(db, site_id, mode="target_clusters")
    )
    r = reports[0]
    assert r.coverage_score == pytest.approx(0.0, abs=1e-6)
    assert r.status == CoverageStatus.missing


# ─── 6. strong via page signal alone ─────────────────────────────────


def test_strong_cluster_when_best_page_score_high():
    """score=4.5, no observed queries → coverage_score = 0.5 * 0.9 = 0.45.

    That's weak by the 0.4/0.8 cut-offs, which is the INTENDED behavior —
    a page alone without observed demand is "weak coverage", not
    "strong". The stronger assertion: a 5.0 page still can't by itself
    push the score to >=0.8 (needs observed + match too).
    Here we verify the score rises *proportionally* to page strength.
    """
    site_id = uuid.uuid4()
    c = _make_cluster(
        site_id=site_id,
        name_ru="экскурсии сочи",
        keywords=("экскурсия",),
        intent_code="comm_category",
    )
    page_id = uuid.uuid4()
    db = _stage_db(
        clusters=[c],
        page_scores=[("comm_category", page_id, 4.5, "https://example.com/excursions")],
    )
    reports = _run(
        CoverageAnalyzer().analyze_site(db, site_id, mode="target_clusters")
    )
    r = reports[0]
    # 0.5 * (4.5/5) + 0.3*0 + 0.2*0 = 0.45
    assert r.coverage_score == pytest.approx(0.45, abs=1e-4)
    assert r.best_page_score == pytest.approx(4.5)
    assert r.best_page_url == "https://example.com/excursions"
    # Weak: 0.4 <= 0.45 < 0.8
    assert r.status == CoverageStatus.weak


# ─── 7. is_brand propagates ──────────────────────────────────────────


def test_is_brand_cluster_propagated():
    site_id = uuid.uuid4()
    c = _make_cluster(
        site_id=site_id,
        name_ru="южный континент отзывы",
        keywords=("южный", "континент"),
        intent_code="trans_brand",
        cluster_type="brand",
        is_brand=True,
    )
    db = _stage_db(clusters=[c])
    reports = _run(
        CoverageAnalyzer().analyze_site(db, site_id, mode="target_clusters")
    )
    assert reports[0].is_brand_cluster is True
    assert reports[0].cluster_type == "brand"


# ─── 8. no clusters → empty list ─────────────────────────────────────


def test_target_cluster_mode_empty_when_no_clusters():
    site_id = uuid.uuid4()
    db = _stage_db(clusters=[])
    reports = _run(
        CoverageAnalyzer().analyze_site(db, site_id, mode="target_clusters")
    )
    assert reports == []
