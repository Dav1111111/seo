"""Profile-driven Decision Tree — Q1-Q7 logic over an IntentClusterReport.

Branches: LEAVE, STRENGTHEN, MERGE, SPLIT, CREATE, BLOCK_CREATE.
Profile supplies URL/title proposers; engine supplies the decision logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.intent_codes import CoverageAction, CoverageStatus, IntentCode
from app.core_audit.profile_protocol import SiteProfile
from app.core_audit.safety_layer import SafetyVerdict, run_safety_checks
from app.core_audit.standalone_test import StandaloneTestResult, run_standalone_test
from app.intent.coverage import IntentClusterReport
from app.intent.models import PageIntentScore
from app.models.page import Page

logger = logging.getLogger(__name__)

MIN_IMPRESSIONS_COMMERCIAL = 50
MIN_IMPRESSIONS_INFO = 100
MIN_QUERIES_COMMERCIAL = 2
MIN_QUERIES_INFO = 5

STRONG_SCORE = 4.0
WEAK_SCORE_MIN = 2.0


@dataclass
class DecisionOutput:
    intent_code: IntentCode
    cluster_key: str
    action: CoverageAction
    justification_ru: str
    target_page_id: UUID | None = None
    target_page_url: str | None = None
    proposed_url: str | None = None
    proposed_title: str | None = None

    queries_count: int = 0
    total_impressions: int = 0
    expected_lift_impressions: int | None = None

    standalone_test: StandaloneTestResult | None = None
    safety_verdict: SafetyVerdict | None = None
    evidence: dict = field(default_factory=dict)


class DecisionTree:
    """Q1-Q7 decision logic over a single IntentClusterReport, driven by profile."""

    async def decide(
        self,
        db: AsyncSession,
        report: IntentClusterReport,
        site_id: UUID,
        profile: SiteProfile,
    ) -> DecisionOutput:
        intent = report.intent_code
        cluster_key = intent.value

        volume_ok = self._volume_threshold_met(report)

        has_weak_page = report.pages_with_score_2_3 > 0 or (
            report.best_page_score and WEAK_SCORE_MIN <= report.best_page_score < STRONG_SCORE
        )
        has_strong_page = report.best_page_score and report.best_page_score >= STRONG_SCORE

        if has_strong_page and report.status == CoverageStatus.strong:
            return DecisionOutput(
                intent_code=intent,
                cluster_key=cluster_key,
                action=CoverageAction.leave,
                justification_ru=(
                    f"Интент «{intent.value}» уже хорошо покрыт страницей "
                    f"{report.best_page_url} (score {report.best_page_score:.1f}). "
                    f"Действий не требуется."
                ),
                target_page_id=report.best_page_id,
                target_page_url=report.best_page_url,
                queries_count=report.queries_count,
                total_impressions=report.total_impressions_14d,
            )

        if report.status == CoverageStatus.over_covered:
            return DecisionOutput(
                intent_code=intent,
                cluster_key=cluster_key,
                action=CoverageAction.merge,
                justification_ru=(
                    f"По интенту «{intent.value}» {report.pages_with_score_gte_4}+ "
                    f"страниц борются за одни запросы — они каннибализируют друг друга. "
                    f"Объедините в одну сильную страницу."
                ),
                target_page_url=report.best_page_url,
                queries_count=report.queries_count,
                total_impressions=report.total_impressions_14d,
                evidence={
                    "strong_pages_count": report.pages_with_score_gte_4,
                    "best_score": report.best_page_score,
                },
            )

        if has_weak_page:
            conflict = await self._check_intent_conflict(
                db, report.best_page_id, intent
            ) if report.best_page_id else False

            if not conflict:
                return DecisionOutput(
                    intent_code=intent,
                    cluster_key=cluster_key,
                    action=CoverageAction.strengthen,
                    justification_ru=(
                        f"Страница {report.best_page_url} частично покрывает интент "
                        f"«{intent.value}» (score {report.best_page_score:.1f}). "
                        f"Усиление существующей страницы эффективнее создания новой."
                    ),
                    target_page_id=report.best_page_id,
                    target_page_url=report.best_page_url,
                    queries_count=report.queries_count,
                    total_impressions=report.total_impressions_14d,
                )

            return DecisionOutput(
                intent_code=intent,
                cluster_key=cluster_key,
                action=CoverageAction.split,
                justification_ru=(
                    f"Страница {report.best_page_url} пытается обслуживать несколько "
                    f"разных интентов одновременно. Разделите — каждый intent заслуживает "
                    f"своей страницы с нужной структурой."
                ),
                target_page_id=report.best_page_id,
                target_page_url=report.best_page_url,
                queries_count=report.queries_count,
                total_impressions=report.total_impressions_14d,
            )

        if not volume_ok:
            return DecisionOutput(
                intent_code=intent,
                cluster_key=cluster_key,
                action=CoverageAction.leave,
                justification_ru=(
                    f"По интенту «{intent.value}» недостаточно данных: "
                    f"{report.queries_count} запросов, {report.total_impressions_14d} "
                    f"показов за 14 дней. Слишком мало чтобы инвестировать в создание "
                    f"отдельной страницы. Мониторим."
                ),
                queries_count=report.queries_count,
                total_impressions=report.total_impressions_14d,
            )

        top_q = report.top_queries[0] if report.top_queries else intent.value
        proposed_title = profile.propose_title(intent, top_q)
        proposed_url = profile.propose_url(intent, top_q)

        standalone_result = await run_standalone_test(
            db,
            profile,
            proposed_title=proposed_title,
            proposed_intent=intent,
            site_id=site_id,
            proposed_query=top_q if report.top_queries else None,
        )

        if not standalone_result.verdict_pass:
            return DecisionOutput(
                intent_code=intent,
                cluster_key=cluster_key,
                action=CoverageAction.strengthen,
                justification_ru=(
                    f"Интент «{intent.value}» не прошёл Standalone Value Test "
                    f"({standalone_result.passed_count}/{standalone_result.applicable_count} критериев). "
                    f"Лучше усилить существующий кластер, а не создавать отдельную страницу."
                ),
                proposed_title=proposed_title,
                queries_count=report.queries_count,
                total_impressions=report.total_impressions_14d,
                standalone_test=standalone_result,
            )

        safety = await run_safety_checks(
            db,
            profile,
            proposed_title=proposed_title,
            proposed_url_path=proposed_url,
            proposed_intent=intent,
            site_id=site_id,
            query_volume_14d=report.total_impressions_14d,
            queries_in_cluster=report.queries_count,
        )

        if not safety.safe_to_create:
            alt = safety.alternative_action or "STRENGTHEN"
            if alt == "STRENGTHEN":
                action = CoverageAction.strengthen
            elif alt == "LEAVE":
                action = CoverageAction.leave
            else:
                action = CoverageAction.block_create

            block_reasons = "; ".join(b.reason for b in safety.blocks)
            return DecisionOutput(
                intent_code=intent,
                cluster_key=cluster_key,
                action=action,
                justification_ru=(
                    f"Safety Layer заблокировал создание страницы для «{intent.value}»: "
                    f"{block_reasons}. Рекомендация: {alt}."
                ),
                target_page_url=safety.alternative_page_url,
                proposed_title=proposed_title,
                proposed_url=proposed_url,
                queries_count=report.queries_count,
                total_impressions=report.total_impressions_14d,
                standalone_test=standalone_result,
                safety_verdict=safety,
            )

        if intent is IntentCode.COMM_MODIFIED:
            parent = await self._find_parent_category(db, site_id)
            if parent is not None:
                parent_page_id, parent_url, parent_score = parent
                base_justification = (
                    f"Интент «{intent.value}» не покрыт и прошёл все проверки: "
                    f"Standalone Value Test {standalone_result.passed_count}"
                    f"/{standalone_result.applicable_count}, safety layer пропустил."
                )
                return DecisionOutput(
                    intent_code=intent,
                    cluster_key=cluster_key,
                    action=CoverageAction.strengthen,
                    justification_ru=(
                        f"{base_justification} parent category found: {parent_url}"
                    ),
                    target_page_id=parent_page_id,
                    target_page_url=parent_url,
                    proposed_title=proposed_title,
                    proposed_url=proposed_url,
                    queries_count=report.queries_count,
                    total_impressions=report.total_impressions_14d,
                    standalone_test=standalone_result,
                    safety_verdict=safety,
                    evidence={
                        "q7_parent_override": True,
                        "parent_page_id": str(parent_page_id),
                        "parent_url": parent_url,
                        "parent_score": round(parent_score, 2),
                        "reasoning": (
                            "Q7: родительская COMM_CATEGORY страница со score "
                            f"{parent_score:.2f} способна поглотить подраздел — "
                            "усиление родителя эффективнее создания дочерней."
                        ),
                    },
                )

        return DecisionOutput(
            intent_code=intent,
            cluster_key=cluster_key,
            action=CoverageAction.create,
            justification_ru=(
                f"Интент «{intent.value}» не покрыт и прошёл все проверки: "
                f"Standalone Value Test {standalone_result.passed_count}"
                f"/{standalone_result.applicable_count}, safety layer пропустил. "
                f"Рекомендуется создать страницу {proposed_url}."
            ),
            proposed_title=proposed_title,
            proposed_url=proposed_url,
            queries_count=report.queries_count,
            total_impressions=report.total_impressions_14d,
            standalone_test=standalone_result,
            safety_verdict=safety,
        )

    # ── Helpers ────────────────────────────────────────────────────────

    def _volume_threshold_met(self, report: IntentClusterReport) -> bool:
        intent = report.intent_code
        if intent.funnel_stage == "tofu":
            return (
                report.queries_count >= MIN_QUERIES_INFO
                or report.total_impressions_14d >= MIN_IMPRESSIONS_INFO
            )
        return (
            report.queries_count >= MIN_QUERIES_COMMERCIAL
            or report.total_impressions_14d >= MIN_IMPRESSIONS_COMMERCIAL
        )

    async def _check_intent_conflict(
        self, db: AsyncSession, page_id: UUID, target_intent: IntentCode
    ) -> bool:
        rows = await db.execute(
            select(PageIntentScore.intent_code, PageIntentScore.score)
            .where(
                PageIntentScore.page_id == page_id,
                PageIntentScore.score >= WEAK_SCORE_MIN,
            )
        )
        intents_served = [(ic, s) for ic, s in rows if ic != target_intent.value]
        strong_others = sum(1 for _, s in intents_served if s >= WEAK_SCORE_MIN)

        stages = set()
        for ic, _ in intents_served:
            try:
                ic_enum = IntentCode(ic)
                stages.add(ic_enum.funnel_stage)
            except ValueError:
                continue
        stages.add(target_intent.funnel_stage)

        return strong_others >= 2 and len(stages) >= 2

    async def _find_parent_category(
        self, db: AsyncSession, site_id: UUID
    ) -> tuple[UUID, str, float] | None:
        rows = await db.execute(
            select(PageIntentScore.page_id, PageIntentScore.score, Page.url)
            .join(Page, Page.id == PageIntentScore.page_id)
            .where(
                PageIntentScore.site_id == site_id,
                PageIntentScore.intent_code == IntentCode.COMM_CATEGORY.value,
                PageIntentScore.score >= WEAK_SCORE_MIN,
            )
            .order_by(PageIntentScore.score.desc())
            .limit(1)
        )
        row = rows.first()
        if not row:
            return None
        page_id, score, url = row
        return page_id, url, score
