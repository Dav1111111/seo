"""
ValidatorAgent — Policy gatekeeper.

Runs on Sonnet 4.6 (needs reasoning for judgment calls).
Reviews candidate issues from detection agents and decides:
  - APPROVE (confidence unchanged or raised)
  - ADJUST (confidence lowered with reason)
  - REJECT (issue suppressed entirely)

Checks:
  1. Is there enough data? (minimum 7 days)
  2. Is this a seasonal pattern, not a real anomaly?
  3. Has the same issue been marked false_positive before?
  4. Does the confidence score match the evidence strength?
"""

import json
import logging
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm_client import call_with_tool
from app.agents.schemas import AgentOutput, IssueDetection
from app.agents.seasonality_engine import SeasonalityEngine
from app.models.issue import Issue

logger = logging.getLogger(__name__)

VALIDATION_TOOL = {
    "name": "validate_issues",
    "description": "Review each candidate issue and return validation decisions.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "issue_index": {"type": "integer", "description": "0-based index in the input list"},
                        "verdict": {"type": "string", "enum": ["approve", "adjust", "reject"]},
                        "adjusted_confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "description": "New confidence (only if verdict=adjust)",
                        },
                        "reason": {"type": "string", "description": "Why this decision was made"},
                    },
                    "required": ["issue_index", "verdict", "reason"],
                },
            },
            "summary": {"type": "string", "description": "Overall validation summary"},
        },
        "required": ["decisions", "summary"],
    },
}


class ValidatorAgent:
    """Reviews issues before they are published. Not a BaseAgent subclass — different flow."""

    model_tier = "smart"  # Sonnet 4.6 for reasoning

    def __init__(self):
        self.engine = SeasonalityEngine()

    async def validate(
        self,
        db: AsyncSession,
        site_id: UUID,
        agent_output: AgentOutput,
        agent_name: str,
    ) -> list[IssueDetection]:
        """
        Validate a batch of issues. Returns filtered list with adjusted confidence.
        """
        if not agent_output.issues:
            return []

        # Gather context for validator
        season_info = self.engine.to_context_dict(date.today())
        past_false_positives = await self._get_recent_false_positives(db, site_id, agent_name)

        system_prompt = self._build_system_prompt(season_info)
        user_message = self._build_user_message(agent_output, past_false_positives, season_info)

        try:
            raw, usage = call_with_tool(
                model_tier=self.model_tier,
                system=system_prompt,
                user_message=user_message,
                tool=VALIDATION_TOOL,
                max_tokens=4096,
            )
            logger.info(
                "Validator: model=%s cost=$%.5f tokens=%d+%d",
                usage["model"], usage["cost_usd"],
                usage["input_tokens"], usage["output_tokens"],
            )
        except Exception as exc:
            logger.error("Validator LLM call failed: %s — passing all issues through", exc)
            return agent_output.issues

        return self._apply_decisions(agent_output.issues, raw)

    def _build_system_prompt(self, season_info: dict[str, Any]) -> str:
        return f"""Ты — Агент-Валидатор, контролёр качества системы SEO-мониторинга.

ВАЖНО: Все reason и summary — ТОЛЬКО на русском языке.

ТЕКУЩИЙ СЕЗОН:
  Сезон: {season_info['season']}
  Множитель трафика: {season_info['traffic_multiplier']}
  Праздник: {season_info['holiday_name'] or 'Нет'}
  Примечание: {season_info['note']}

ТВОЯ РОЛЬ:
Ты получаешь кандидатов-проблемы от агентов-детекторов. По КАЖДОЙ проблеме реши:
  - APPROVE: проблема реальная, доказательства убедительные → оставить как есть
  - ADJUST: проблема возможна, но уверенность завышена/занижена → скорректировать confidence
  - REJECT: проблема — шум, сезонность или ранее опровергнута → скрыть

ПРАВИЛА ВАЛИДАЦИИ:
1. ДОСТАТОЧНОСТЬ ДАННЫХ: Если проблема основана на <7 днях данных, снизь confidence на 0.2
2. СЕЗОННЫЙ ПАТТЕРН: Падение в межсезонье → REJECT или снизить confidence на 0.3
3. МАЛЫЕ АБСОЛЮТНЫЕ ЗНАЧЕНИЯ: Падение с 2 до 1 показа — это шум (confidence ≤0.3)
4. ПРАЗДНИЧНЫЕ ЭФФЕКТЫ: Изменения трафика в праздники ожидаемы — повысь порог
5. ПРОШЛЫЕ ЛОЖНЫЕ СРАБАТЫВАНИЯ: Если аналогичная проблема была false_positive → REJECT
6. ШУМ ПОЗИЦИЙ: Колебания позиции ±2 — нормальная флуктуация → REJECT или confidence ≤0.3

БУДЬ КОНСЕРВАТИВЕН. Лучше пропустить реальную проблему, чем завалить пользователя шумом.
Доверенная система генерирует меньше, но более качественных сигналов.

Выводи решения через инструмент validate_issues. Все reason — НА РУССКОМ."""

    def _build_user_message(
        self,
        agent_output: AgentOutput,
        past_fps: list[dict[str, Any]],
        season_info: dict[str, Any],
    ) -> str:
        lines = [
            f"Agent summary: {agent_output.summary}",
            f"Analysis period: {agent_output.analysis_period}",
            "",
            "CANDIDATE ISSUES TO VALIDATE:",
        ]

        for i, issue in enumerate(agent_output.issues):
            lines.append(
                f"\n[{i}] {issue.severity.upper()} (confidence={issue.confidence}) — {issue.title}\n"
                f"    Type: {issue.issue_type}\n"
                f"    Description: {issue.description}\n"
                f"    Affected: {', '.join(issue.affected_urls_or_queries[:5])}\n"
                f"    Recommendation: {issue.recommendation}"
            )

        if past_fps:
            lines.append("\nPAST FALSE POSITIVES (similar issues previously rejected by human):")
            for fp in past_fps[:5]:
                lines.append(f"  - [{fp['agent_name']}] {fp['title']} (rejected {fp['resolved_at']})")

        lines.append(f"\nCurrent season: {season_info['season']} (multiplier={season_info['traffic_multiplier']})")
        lines.append("\nValidate each issue and call validate_issues.")

        return "\n".join(lines)

    def _apply_decisions(
        self,
        issues: list[IssueDetection],
        raw: dict[str, Any],
    ) -> list[IssueDetection]:
        decisions = raw.get("decisions", [])
        summary = raw.get("summary", "")
        logger.info("Validator summary: %s", summary[:200])

        approved: list[IssueDetection] = []

        decision_map = {d["issue_index"]: d for d in decisions}

        for i, issue in enumerate(issues):
            decision = decision_map.get(i)

            if not decision:
                # No decision → pass through
                approved.append(issue)
                continue

            verdict = decision.get("verdict", "approve")
            reason = decision.get("reason", "")

            if verdict == "reject":
                logger.info("  REJECTED [%d] %s — %s", i, issue.title[:50], reason[:80])
                continue

            if verdict == "adjust":
                new_conf = decision.get("adjusted_confidence", issue.confidence)
                logger.info(
                    "  ADJUSTED [%d] %s — %.2f → %.2f — %s",
                    i, issue.title[:50], issue.confidence, new_conf, reason[:80],
                )
                issue = issue.model_copy(update={"confidence": new_conf})

            approved.append(issue)

        logger.info(
            "Validator: %d in → %d approved (%d rejected)",
            len(issues), len(approved), len(issues) - len(approved),
        )
        return approved

    async def _get_recent_false_positives(
        self,
        db: AsyncSession,
        site_id: UUID,
        agent_name: str,
    ) -> list[dict[str, Any]]:
        rows = await db.execute(
            select(Issue.agent_name, Issue.title, Issue.issue_type, Issue.resolved_at)
            .where(
                Issue.site_id == site_id,
                Issue.status == "false_positive",
                Issue.resolved_at >= date.today() - timedelta(days=90),
            )
            .order_by(Issue.resolved_at.desc())
            .limit(10)
        )
        return [dict(r._mapping) for r in rows]
