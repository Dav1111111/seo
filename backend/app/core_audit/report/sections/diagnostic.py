"""Section 0 — Diagnostic (Phase E: Target Demand Map).

Computes a composite root-cause classification for a site. Gated by
`settings.USE_TARGET_DEMAND_MAP`. When the flag is off, or the site has
no rows in `target_clusters`, the section returns an `available=False`
skeleton with no DB queries beyond a cheap existence check and no LLM
call — so enabling Phase E on a cold site costs ~$0.

Composite "brand_bias" trigger — fires when ALL four conditions hold:

    blind_spot_score                    >= 0.80
    non_brand_covered / total_non_brand <  0.20
    brand_imp / total_imp               >  0.50
    pages_total                         >= 3

Each condition is transparently surfaced in `signals` so downstream agents
can audit the classification.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core_audit.demand_map.models import TargetCluster
from app.core_audit.report.dto import ClusterRef, DiagnosticSection
from app.core_audit.report.prose import (
    generate_diagnostic_prose,
    template_diagnostic,
)
from app.core_audit.review.models import PageReviewRecommendation
from app.intent.coverage import CoverageAnalyzer
from app.intent.models import QueryIntent
from app.models.daily_metric import DailyMetric
from app.models.page import Page

logger = logging.getLogger(__name__)


# Same benchmark floors as CoverageAnalyzer (Phase C); kept local because the
# diagnostic also needs them to compute the *expected* non-brand impressions
# floor — a quantity the coverage analyzer does not expose.
_VOLUME_TIER_BENCHMARK_14D: dict[str, int] = {
    "xs": 10,
    "s": 30,
    "m": 100,
    "l": 300,
    "xl": 1000,
}

# Trigger thresholds for brand_bias classification.
_BLIND_SPOT_THRESHOLD = 0.80
_NON_BRAND_COVERAGE_MAX = 0.20
_BRAND_IMP_DOMINANCE = 0.50
_PAGES_MIN = 3
_COVERED_SCORE_MIN = 0.6
_MISSING_SCORE_MAX = 0.4


async def build_diagnostic(
    db: AsyncSession,
    site_id: uuid.UUID,
) -> DiagnosticSection:
    """Build the Diagnostic section for a site.

    Returns an `available=False` skeleton when:
      * `USE_TARGET_DEMAND_MAP` is False, or
      * the site has no rows in `target_clusters`.

    Otherwise computes the 4 signals, classifies the root problem,
    and emits brand/non-brand demand split + top covered/missing clusters.
    Prose is produced by a single Haiku call (cached system prompt);
    template fallback on any failure.
    """
    # ── Flag gate ────────────────────────────────────────────────────
    if not settings.USE_TARGET_DEMAND_MAP:
        return _skeleton("Target Demand Map ещё не активирован для этого сайта.")

    # ── Existence check (cheap) ──────────────────────────────────────
    has_clusters_row = await db.execute(
        select(TargetCluster.id).where(TargetCluster.site_id == site_id).limit(1)
    )
    if not list(has_clusters_row):
        return _skeleton(
            "Целевой спрос ещё не построен для этого сайта — запустите расширение "
            "(POST /api/v1/admin/demand-map/sites/{id}/expand)."
        )

    # ── Coverage analysis via CoverageAnalyzer target_clusters path ──
    analyzer = CoverageAnalyzer()
    coverage_reports = await analyzer.analyze_site(
        db, site_id, mode="target_clusters"
    )
    # Index coverage reports by target_cluster_id for O(1) lookup.
    cov_by_cid: dict[uuid.UUID, object] = {
        r.target_cluster_id: r
        for r in coverage_reports
        if r.target_cluster_id is not None
    }

    # ── Load full cluster rows (need cluster_key/name_ru/etc) ────────
    cluster_rows = await db.execute(
        select(TargetCluster).where(TargetCluster.site_id == site_id)
    )
    if hasattr(cluster_rows, "scalars"):
        clusters: list[TargetCluster] = list(cluster_rows.scalars())
    else:
        clusters = [c[0] if isinstance(c, tuple) else c for c in cluster_rows]

    if not clusters:
        return _skeleton("Нет целевых кластеров в базе для этого сайта.")

    # ── Partition brand vs non-brand ─────────────────────────────────
    brand_clusters = [c for c in clusters if bool(c.is_brand)]
    non_brand_clusters = [c for c in clusters if not bool(c.is_brand)]

    # ── Signal 1: blind_spot_score ───────────────────────────────────
    # Expected floor = Σ business_relevance * benchmark_imp(volume_tier)
    # over non-brand clusters. Observed = Σ total_impressions_14d over
    # the matching coverage reports.
    expected_non_brand_floor = 0.0
    observed_non_brand_imp = 0
    for c in non_brand_clusters:
        relevance = float(c.business_relevance or 0.0)
        bench = _VOLUME_TIER_BENCHMARK_14D.get(c.expected_volume_tier or "s", 30)
        expected_non_brand_floor += relevance * bench
        rep = cov_by_cid.get(c.id)
        if rep is not None:
            observed_non_brand_imp += int(rep.total_impressions_14d or 0)

    if expected_non_brand_floor > 0:
        blind_spot_score = 1.0 - min(
            observed_non_brand_imp / expected_non_brand_floor, 1.0
        )
    else:
        blind_spot_score = 0.0
    blind_spot_score = max(0.0, min(1.0, blind_spot_score))

    # ── Signal 2: non_brand_coverage_ratio ───────────────────────────
    total_non_brand = len(non_brand_clusters)
    non_brand_covered = sum(
        1
        for c in non_brand_clusters
        if (cov_by_cid.get(c.id) is not None)
        and (cov_by_cid[c.id].coverage_score or 0.0) >= _COVERED_SCORE_MIN
    )
    non_brand_missing = total_non_brand - non_brand_covered
    non_brand_coverage_ratio = (
        non_brand_covered / total_non_brand if total_non_brand > 0 else 0.0
    )

    # ── Signal 3: brand_imp_ratio (DailyMetric ⋈ QueryIntent) ────────
    brand_imp, total_imp = await _brand_impression_split(db, site_id)
    brand_imp_ratio = (brand_imp / total_imp) if total_imp > 0 else 0.0

    # ── Signal 4: pages_total ────────────────────────────────────────
    pages_row = await db.execute(
        select(func.count(Page.id)).where(Page.site_id == site_id)
    )
    pages_total = int(pages_row.scalar() or 0)

    # ── Composite trigger ────────────────────────────────────────────
    trigger_brand_bias = (
        blind_spot_score >= _BLIND_SPOT_THRESHOLD
        and non_brand_coverage_ratio < _NON_BRAND_COVERAGE_MAX
        and brand_imp_ratio > _BRAND_IMP_DOMINANCE
        and pages_total >= _PAGES_MIN
    )

    # ── Classification ───────────────────────────────────────────────
    if trigger_brand_bias:
        classification = "brand_bias"
    elif pages_total < _PAGES_MIN:
        classification = "weak_technical"
    elif total_non_brand > 0 and non_brand_coverage_ratio < 0.40:
        classification = "low_coverage"
    else:
        classification = "none"

    # ── Brand / non-brand demand split ───────────────────────────────
    brand_observed_imp = sum(
        int((cov_by_cid.get(c.id).total_impressions_14d or 0))
        for c in brand_clusters
        if cov_by_cid.get(c.id) is not None
    )

    brand_demand = {
        "clusters": len(brand_clusters),
        "observed_impressions": brand_observed_imp,
    }
    non_brand_demand = {
        "clusters": total_non_brand,
        "observed_impressions": observed_non_brand_imp,
        "covered": non_brand_covered,
        "missing": non_brand_missing,
        "coverage_ratio": round(non_brand_coverage_ratio, 4),
    }

    # ── Top covered / missing clusters ───────────────────────────────
    def _ref(c: TargetCluster, rep) -> ClusterRef:
        return ClusterRef(
            cluster_key=c.cluster_key,
            name_ru=c.name_ru,
            cluster_type=c.cluster_type,
            quality_tier=c.quality_tier,
            business_relevance=float(c.business_relevance or 0.0),
            coverage_score=(
                float(rep.coverage_score) if rep and rep.coverage_score is not None
                else None
            ),
            is_brand=bool(c.is_brand),
        )

    covered_pool = [
        (c, cov_by_cid[c.id])
        for c in non_brand_clusters
        if cov_by_cid.get(c.id) is not None
        and (cov_by_cid[c.id].coverage_score or 0.0) >= _COVERED_SCORE_MIN
    ]
    covered_pool.sort(
        key=lambda t: float(t[0].business_relevance or 0.0), reverse=True
    )
    covered_target_clusters = [_ref(c, r) for c, r in covered_pool[:5]]

    missing_pool = [
        (c, cov_by_cid.get(c.id))
        for c in non_brand_clusters
        if (cov_by_cid.get(c.id) is None)
        or ((cov_by_cid[c.id].coverage_score or 0.0) < _MISSING_SCORE_MAX)
    ]
    missing_pool.sort(
        key=lambda t: float(t[0].business_relevance or 0.0), reverse=True
    )
    missing_target_clusters = [_ref(c, r) for c, r in missing_pool[:10]]

    # ── Signals dict (audit-friendly) ────────────────────────────────
    signals = {
        "blind_spot_score": round(blind_spot_score, 4),
        "non_brand_coverage_ratio": round(non_brand_coverage_ratio, 4),
        "brand_imp_ratio": round(brand_imp_ratio, 4),
        "pages_total": pages_total,
        "trigger_brand_bias": trigger_brand_bias,
    }

    # ── Low-priority findings (brand-page title/h1 tweaks) ───────────
    low_priority_findings: list[str] = []
    if trigger_brand_bias:
        low_priority_findings = await _low_priority_brand_findings(db, site_id)

    # ── LLM prose ────────────────────────────────────────────────────
    prose_payload = {
        "classification": classification,
        "signals": signals,
        "brand_demand": brand_demand,
        "non_brand_demand": non_brand_demand,
        "missing_target_clusters": [
            {
                "name_ru": r.name_ru,
                "business_relevance": r.business_relevance,
                "coverage_score": r.coverage_score,
            }
            for r in missing_target_clusters[:5]
        ],
    }
    prose_result, _usage = generate_diagnostic_prose(prose_payload)
    if prose_result is None:
        prose = template_diagnostic(prose_payload)
        prose_source = "template"
    else:
        prose = prose_result
        prose_source = "llm"

    return DiagnosticSection(
        available=True,
        root_problem_classification=classification,
        root_problem_ru=prose["root_problem_ru"],
        supporting_symptoms_ru=prose.get("supporting_symptoms_ru", [])[:5],
        recommended_first_actions_ru=prose.get("recommended_first_actions_ru", [])[:5],
        signals=signals,
        brand_demand=brand_demand,
        non_brand_demand=non_brand_demand,
        covered_target_clusters=covered_target_clusters,
        missing_target_clusters=missing_target_clusters,
        low_priority_findings=low_priority_findings,
        prose_source=prose_source,
    )


# ── Helpers ──────────────────────────────────────────────────────────


def _skeleton(message_ru: str) -> DiagnosticSection:
    return DiagnosticSection(
        available=False,
        root_problem_classification="insufficient_data",
        root_problem_ru=message_ru,
        prose_source="skeleton",
    )


async def _brand_impression_split(
    db: AsyncSession, site_id: uuid.UUID
) -> tuple[int, int]:
    """Return (brand_impressions_14d, total_impressions_14d) by joining
    DailyMetric ⋈ QueryIntent on query_id. Uses the same 14-day window
    semantics as CoverageAnalyzer (Webmaster 5-day lag).
    """
    from datetime import date, timedelta

    today = date.today()
    end = today - timedelta(days=5)
    start = end - timedelta(days=13)

    stmt = (
        select(
            func.sum(DailyMetric.impressions).label("imp"),
            QueryIntent.is_brand.label("is_brand"),
        )
        .join(QueryIntent, QueryIntent.query_id == DailyMetric.dimension_id)
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.date.between(start, end),
        )
        .group_by(QueryIntent.is_brand)
    )
    rows = await db.execute(stmt)
    brand_imp = 0
    total_imp = 0
    for r in rows:
        imp = int(r.imp or 0)
        total_imp += imp
        if bool(r.is_brand):
            brand_imp += imp
    return brand_imp, total_imp


async def _low_priority_brand_findings(
    db: AsyncSession, site_id: uuid.UUID
) -> list[str]:
    """Pull category-in-(title,h1_structure) recommendations for the site,
    format as one-line summaries. These are LEGACY top-ranked findings that
    the diagnostic deprioritises when brand_bias fires — surfacing them
    here makes the demotion transparent to the user.
    """
    stmt = (
        select(
            PageReviewRecommendation.category,
            PageReviewRecommendation.priority,
            PageReviewRecommendation.reasoning_ru,
        )
        .where(
            PageReviewRecommendation.site_id == site_id,
            PageReviewRecommendation.category.in_(("title", "h1_structure")),
        )
        .limit(5)
    )
    try:
        rows = await db.execute(stmt)
    except Exception as exc:  # pragma: no cover — defensive only
        logger.warning("diagnostic low-priority findings fetch failed: %s", exc)
        return []

    out: list[str] = []
    for category, priority, reasoning in rows:
        text = (reasoning or "").strip().replace("\n", " ")
        if len(text) > 120:
            text = text[:117] + "…"
        out.append(f"[{category}/{priority}] {text}")
    return out
