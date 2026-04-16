"""Section 3 — Coverage Status. Reuses CoverageAnalyzer output."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.report.dto import CoverageSection, IntentClusterSummary
from app.intent.coverage import CoverageAnalyzer
from app.intent.enums import CoverageStatus
from app.intent.models import CoverageDecision


async def build_coverage(db: AsyncSession, site_id: UUID) -> CoverageSection:
    analyzer = CoverageAnalyzer()
    reports = await analyzer.analyze_site(db, site_id)

    clusters: list[IntentClusterSummary] = []
    strong = weak = missing = over = 0
    for r in reports:
        st = r.status.value
        if r.status is CoverageStatus.strong:
            strong += 1
        elif r.status is CoverageStatus.weak:
            weak += 1
        elif r.status is CoverageStatus.missing:
            missing += 1
        elif r.status is CoverageStatus.over_covered:
            over += 1

        clusters.append(IntentClusterSummary(
            intent_code=r.intent_code.value,
            queries_count=r.queries_count,
            total_impressions_14d=r.total_impressions_14d,
            best_page_url=r.best_page_url,
            best_page_score=round(r.best_page_score, 2),
            status=st,
            ambiguous_queries_count=r.ambiguous_queries_count,
        ))

    total = len(reports) or 1
    distribution = {
        "strong": round(strong / total * 100, 1),
        "weak": round(weak / total * 100, 1),
        "missing": round(missing / total * 100, 1),
        "over_covered": round(over / total * 100, 1),
    }

    open_decisions_row = await db.execute(
        select(CoverageDecision.id).where(
            CoverageDecision.site_id == site_id,
            CoverageDecision.status == "open",
        )
    )
    open_decisions_count = len(open_decisions_row.all())

    intent_gaps: list[str] = []
    for c in clusters:
        if c.status == "missing" and c.queries_count > 0:
            intent_gaps.append(
                f"{c.intent_code}: {c.queries_count} запросов без страницы"
            )
        elif c.status == "weak" and c.best_page_score < 3.0:
            intent_gaps.append(
                f"{c.intent_code}: лучшая страница {c.best_page_score:.1f} — нужно усилить"
            )

    return CoverageSection(
        clusters=clusters,
        strong_count=strong,
        weak_count=weak,
        missing_count=missing,
        over_covered_count=over,
        open_decisions_count=open_decisions_count,
        distribution_pct=distribution,
        intent_gaps=intent_gaps[:10],
    )
