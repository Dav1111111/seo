"""Decision Tree Q1-Q7 — implements Rule 1 logic.

Input: CoverageAnalyzer report + configuration (volume thresholds, etc.)
Output: CoverageAction (STRENGTHEN / CREATE / MERGE / SPLIT / LEAVE / BLOCK_CREATE)

Logic (from seo-content methodology):

Q1: Есть ли страница со score 2-3?
    YES → Q2
    NO  → Q5

Q2: Можно ли добить до 4+ без каннибализации других intent на странице?
    YES → STRENGTHEN
    NO  → Q3

Q3: Страница уже несёт 2+ конфликтующих intent?
    YES → SPLIT (выделить новую — это НЕ нарушает правило, раздел получит пользу)
    NO  → Q4

Q4: Есть 2+ близких страницы со score 2-3 с каннибализацией?
    YES → MERGE (склеить в одну сильную)
    NO  → STRENGTHEN (fallback)

Q5 (страницы нет): query volume достаточен?
    NO  → LEAVE (мониторить, но не трогать)
    YES → Q6

Q6: Standalone Value Test пройден (≥3/5)?
    NO  → STRENGTHEN ближайший кластер
    YES → Q7 (и safety checks перед финальным CREATE)

Q7: Есть ли родительская категория, куда можно вложить секцию?
    YES → STRENGTHEN parent (лучше чем создавать изолированную страницу)
    NO  → CREATE (если safety layer пропустит)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.intent.coverage import IntentClusterReport
from app.intent.enums import CoverageAction, CoverageStatus, IntentCode
from app.intent.models import PageIntentScore
from app.intent.safety_layer import SafetyVerdict, run_safety_checks
from app.intent.standalone_test import StandaloneTestResult, run_standalone_test
from app.models.page import Page

logger = logging.getLogger(__name__)


# Volume thresholds per intent category (from seo-content spec)
MIN_IMPRESSIONS_COMMERCIAL = 50   # per cluster, 14d
MIN_IMPRESSIONS_INFO = 100        # per cluster, 14d
MIN_QUERIES_COMMERCIAL = 2
MIN_QUERIES_INFO = 5

STRONG_SCORE = 4.0
WEAK_SCORE_MIN = 2.0


@dataclass
class DecisionOutput:
    intent_code: IntentCode
    cluster_key: str                            # e.g. "comm_modified__abkhazia"
    action: CoverageAction
    justification_ru: str
    target_page_id: UUID | None = None
    target_page_url: str | None = None
    proposed_url: str | None = None
    proposed_title: str | None = None

    # Data for follow-up tasks
    queries_count: int = 0
    total_impressions: int = 0
    expected_lift_impressions: int | None = None

    # Diagnostics
    standalone_test: StandaloneTestResult | None = None
    safety_verdict: SafetyVerdict | None = None
    evidence: dict = field(default_factory=dict)


class DecisionTree:
    """Q1-Q7 decision logic over a single IntentClusterReport."""

    async def decide(
        self,
        db: AsyncSession,
        report: IntentClusterReport,
        site_id: UUID,
    ) -> DecisionOutput:
        intent = report.intent_code
        cluster_key = intent.value  # Phase 2C: one cluster per intent; Phase 2D: sub-clusters

        volume_ok = self._volume_threshold_met(report)

        # ── Q1: есть ли страница со score 2-3? ──────────────────────
        has_weak_page = report.pages_with_score_2_3 > 0 or (
            report.best_page_score and WEAK_SCORE_MIN <= report.best_page_score < STRONG_SCORE
        )
        has_strong_page = report.best_page_score and report.best_page_score >= STRONG_SCORE

        # Already well-covered — no action needed
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

        # Over-covered — MERGE
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

        # ── Q1 → Q2 branch: есть слабая страница ─────────────────────
        if has_weak_page:
            # Q2 check: есть ли конфликт intent на странице?
            conflict = await self._check_intent_conflict(
                db, report.best_page_id, intent
            ) if report.best_page_id else False

            if not conflict:
                # Q2 YES → STRENGTHEN
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

            # Q3: конфликт есть — SPLIT
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

        # ── Q5: страницы нет, проверяем объём ────────────────────────
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

        # ── Q6: Standalone Value Test ────────────────────────────────
        proposed_title = self._propose_title(intent, report)
        proposed_url = self._propose_url(intent, report)

        standalone_result = await run_standalone_test(
            db,
            proposed_title=proposed_title,
            proposed_intent=intent,
            site_id=site_id,
            proposed_query=report.top_queries[0] if report.top_queries else None,
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

        # ── Q7 + Safety: standalone passed, теперь safety проверки ──
        safety = await run_safety_checks(
            db,
            proposed_title=proposed_title,
            proposed_url_path=proposed_url,
            proposed_intent=intent,
            site_id=site_id,
            query_volume_14d=report.total_impressions_14d,
            queries_in_cluster=report.queries_count,
        )

        if not safety.safe_to_create:
            # Rule 2 blocked — downgrade to alternative action
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

        # ── All gates passed → CREATE ────────────────────────────────
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
        """Check if the cluster has enough volume to justify investment."""
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
        """Does this page try to serve 2+ intents with conflicting affordances?"""
        rows = await db.execute(
            select(PageIntentScore.intent_code, PageIntentScore.score)
            .where(
                PageIntentScore.page_id == page_id,
                PageIntentScore.score >= WEAK_SCORE_MIN,
            )
        )
        intents_served = [(ic, s) for ic, s in rows if ic != target_intent.value]

        # Simple heuristic: if page has 2+ other intents each ≥2.0, conflict likely
        strong_others = sum(1 for _, s in intents_served if s >= WEAK_SCORE_MIN)

        # And the intents span funnel stages (commercial vs info)
        stages = set()
        for ic, _ in intents_served:
            try:
                ic_enum = IntentCode(ic)
                stages.add(ic_enum.funnel_stage)
            except ValueError:
                continue
        stages.add(target_intent.funnel_stage)

        return strong_others >= 2 and len(stages) >= 2

    def _propose_title(self, intent: IntentCode, report: IntentClusterReport) -> str:
        """Generate a placeholder title for the standalone test."""
        top_q = report.top_queries[0] if report.top_queries else intent.value
        if intent == IntentCode.LOCAL_GEO:
            return f"Экскурсии с бесплатным трансфером — {top_q}"
        if intent == IntentCode.COMM_MODIFIED:
            return f"{top_q.capitalize()} — программа и цены"
        if intent == IntentCode.INFO_DEST:
            return f"Что посмотреть: {top_q}"
        if intent == IntentCode.INFO_LOGISTICS:
            return f"Как добраться: {top_q}"
        if intent == IntentCode.INFO_PREP:
            return f"Советы: {top_q}"
        if intent == IntentCode.COMM_COMPARE:
            return f"ТОП-10 вариантов: {top_q}"
        return top_q

    def _propose_url(self, intent: IntentCode, report: IntentClusterReport) -> str:
        """Generate a placeholder URL path for safety layer."""
        slug_base = {
            IntentCode.LOCAL_GEO: "/pickup/",
            IntentCode.COMM_MODIFIED: "/tours/",
            IntentCode.COMM_CATEGORY: "/tours",
            IntentCode.INFO_DEST: "/guide/",
            IntentCode.INFO_LOGISTICS: "/transport/",
            IntentCode.INFO_PREP: "/blog/",
            IntentCode.COMM_COMPARE: "/top/",
            IntentCode.TRUST_LEGAL: "/reviews",
        }.get(intent, "/")
        # Simple slug from top query
        if report.top_queries:
            top_q = report.top_queries[0]
            slug = "-".join(top_q.lower().split()[:3])
            return f"{slug_base}{slug}" if slug_base.endswith("/") else f"{slug_base}-{slug}"
        return slug_base
