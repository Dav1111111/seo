"""ReportBuilder — orchestrates 6 sections into a WeeklyReport.

Single LLM call for Executive + Action Plan narrative prose; all other
sections are deterministic. Fail-open on LLM → template prose + status="draft".
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.report.diffs import default_week_end, week_range
from app.core_audit.report.dto import (
    ExecutiveSection,
    ReportMeta,
    WeeklyReport,
)
from app.core_audit.report.health_score import compute_health_score
from app.core_audit.report.prose import (
    generate_prose,
    template_action_plan,
    template_executive,
)
from app.core_audit.report.sections.action_plan import build_action_plan
from app.core_audit.report.sections.coverage import build_coverage
from app.core_audit.report.sections.page_findings import build_page_findings
from app.core_audit.report.sections.query_trends import build_query_trends
from app.core_audit.report.sections.technical import build_technical
from app.models.site import Site

logger = logging.getLogger(__name__)

BUILDER_VERSION = "1.0.0"


class ReportBuilder:
    def __init__(self, builder_version: str = BUILDER_VERSION) -> None:
        self.version = builder_version

    async def build_weekly_report(
        self,
        db: AsyncSession,
        site_id: UUID,
        *,
        week_end: date | None = None,
    ) -> WeeklyReport:
        t0 = time.monotonic()
        end = week_end or default_week_end()
        start, _ = week_range(end)

        site = (await db.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
        host = site.domain if site else str(site_id)

        # ── Data sections (in parallel is fine; keep serial for clarity + debug)
        coverage = await build_coverage(db, site_id)
        trends = await build_query_trends(db, site_id, end)
        findings = await build_page_findings(db, site_id)
        technical = await build_technical(db, site_id, end)
        action_plan = await build_action_plan(db, site_id, top_n=10)

        # ── Health score
        total_intents = max(coverage.strong_count + coverage.weak_count
                            + coverage.missing_count + coverage.over_covered_count, 1)
        coverage_strong_pct = (coverage.strong_count + coverage.over_covered_count) / total_intents
        critical_count = findings.by_priority_count.get("critical", 0)
        wow_imp_pct = trends.wow_diff.get("impressions_pct") if trends.data_available else None

        health = compute_health_score(
            coverage_strong_pct=coverage_strong_pct,
            critical_recs_count=critical_count,
            indexation_rate=technical.indexation_rate,
            wow_impressions_pct=wow_imp_pct,
        )

        # ── Wins / losses narrative bullets
        top_wins = _top_wins(coverage, trends, findings)
        top_losses = _top_losses(coverage, trends, findings)

        # ── LLM prose (executive + action plan narrative)
        prose_payload = {
            "health_score": health,
            "wow_impressions_pct": wow_imp_pct,
            "wow_clicks_pct": trends.wow_diff.get("clicks_pct") if trends.data_available else None,
            "strong_count": coverage.strong_count,
            "weak_count": coverage.weak_count,
            "missing_count": coverage.missing_count,
            "critical_recs": critical_count,
            "high_recs": findings.by_priority_count.get("high", 0),
            "indexation_rate": technical.indexation_rate,
            "top_wins": top_wins,
            "top_losses": top_losses,
            "intent_gaps": coverage.intent_gaps[:5],
            "action_plan_top5": [
                {
                    "priority": it.priority,
                    "page_url": it.page_url,
                    "category": it.category,
                    "reasoning_ru": it.reasoning_ru,
                }
                for it in action_plan.items[:5]
            ],
        }

        prose_result, prose_usage = generate_prose(prose_payload)
        llm_cost = float(prose_usage.get("cost_usd", 0.0) or 0.0)

        if prose_result is None:
            exec_prose = template_executive(prose_payload)
            action_narrative = template_action_plan(prose_payload)
            prose_source = "template"
            status = "draft" if llm_cost == 0.0 else "completed"
        else:
            exec_prose = prose_result["executive_summary_ru"]
            action_narrative = prose_result["action_plan_narrative_ru"]
            prose_source = "llm"
            status = "completed"

        executive = ExecutiveSection(
            health_score=health,
            wow_impressions_pct=wow_imp_pct,
            wow_clicks_pct=trends.wow_diff.get("clicks_pct") if trends.data_available else None,
            top_wins=top_wins,
            top_losses=top_losses,
            prose_ru=exec_prose,
            prose_source=prose_source,
        )
        action_plan = action_plan.model_copy(update={
            "narrative_ru": action_narrative,
            "narrative_source": prose_source,
        })

        meta = ReportMeta(
            site_id=site_id,
            site_host=host,
            week_start=start,
            week_end=end,
            generated_at=datetime.now(timezone.utc),
            builder_version=self.version,
            llm_cost_usd=round(llm_cost, 6),
            generation_ms=int((time.monotonic() - t0) * 1000),
            status=status,
        )

        return WeeklyReport(
            meta=meta,
            executive=executive,
            action_plan=action_plan,
            coverage=coverage,
            query_trends=trends,
            page_findings=findings,
            technical=technical,
        )


def _top_wins(coverage, trends, findings) -> list[str]:
    wins: list[str] = []
    if coverage.strong_count > 0:
        wins.append(f"{coverage.strong_count} интентов покрыты сильной страницей")
    if trends.data_available and (trends.wow_diff.get("impressions_pct") or 0) > 0:
        wins.append(f"Показы: +{trends.wow_diff['impressions_pct']:.1f}% WoW")
    if trends.new_queries:
        wins.append(f"Новых запросов в топ-50: {len(trends.new_queries)}")
    return wins[:3]


def _top_losses(coverage, trends, findings) -> list[str]:
    losses: list[str] = []
    if coverage.missing_count > 0:
        losses.append(f"{coverage.missing_count} интентов без страниц")
    if trends.data_available and (trends.wow_diff.get("impressions_pct") or 0) < 0:
        losses.append(f"Показы: {trends.wow_diff['impressions_pct']:.1f}% WoW")
    if findings.by_priority_count.get("critical", 0) > 0:
        losses.append(f"Критических замечаний: {findings.by_priority_count['critical']}")
    if trends.lost_queries:
        losses.append(f"Потеряно запросов из топ-50: {len(trends.lost_queries)}")
    return losses[:3]
