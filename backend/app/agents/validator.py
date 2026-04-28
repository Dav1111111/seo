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
from datetime import date, datetime, timedelta, timezone
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
        result_map = await self.validate_indexed(db, site_id, agent_output, agent_name)
        # Preserve original ordering of approved issues.
        return [result_map[i] for i in sorted(result_map)]

    async def validate_indexed(
        self,
        db: AsyncSession,
        site_id: UUID,
        agent_output: AgentOutput,
        agent_name: str,
    ) -> dict[int, IssueDetection]:
        """Validate, returning a `{original_index: kept_issue}` map.

        Callers that need to map kept issues back onto the original DB
        rows (e.g. IssuePipeline) should prefer this over `validate()`,
        because matching by title collapses duplicate titles silently.
        """
        if not agent_output.issues:
            return {}

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
            return {i: issue for i, issue in enumerate(agent_output.issues)}

        return self._apply_decisions(agent_output.issues, raw)

    def _build_system_prompt(self, season_info: dict[str, Any]) -> str:
        return f"""Ты — фильтр качества. Проверяешь найденные проблемы и отсеиваешь ложные.

ПРАВИЛО: Пиши reason ПРОСТЫМ РУССКИМ ЯЗЫКОМ. Без жаргона.

СЕЙЧАС:
  Сезон: {season_info['season']}
  Активность: {season_info['traffic_multiplier']} (1.0 = пик сезона)
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
    ) -> dict[int, IssueDetection]:
        decisions = raw.get("decisions", [])
        summary = raw.get("summary", "")
        logger.info("Validator summary: %s", summary[:200])

        # Primary match: `issue_index`. Fallback: title (only if a
        # decision lacks the index — duplicates collapse silently in
        # that path, but we keep it so old tool outputs don't break).
        decision_by_index: dict[int, dict[str, Any]] = {}
        decision_by_title: dict[str, dict[str, Any]] = {}
        for d in decisions:
            idx = d.get("issue_index")
            if isinstance(idx, int):
                decision_by_index[idx] = d
            else:
                title = d.get("title")
                if isinstance(title, str) and title:
                    decision_by_title.setdefault(title, d)

        approved: dict[int, IssueDetection] = {}

        for i, issue in enumerate(issues):
            decision = decision_by_index.get(i) or decision_by_title.get(issue.title)

            if not decision:
                # No decision → pass through
                approved[i] = issue
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

            approved[i] = issue

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
                # `resolved_at` is timestamptz — compare with a tz-aware
                # datetime so Postgres doesn't silently coerce the date
                # to local-midnight and skew the 90-day window.
                Issue.resolved_at >= datetime.now(timezone.utc) - timedelta(days=90),
            )
            .order_by(Issue.resolved_at.desc())
            .limit(10)
        )
        return [dict(r._mapping) for r in rows]
