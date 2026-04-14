"""
BaseAgent — abstract class all agents inherit from.

Pattern:
  1. load_context(db, site_id)  → reads DB, builds AgentContext
  2. build_prompt(context, data) → formats user message for Claude
  3. call_llm(system, user_msg) → calls Anthropic via tool_use
  4. parse_output(raw)          → validates with Pydantic
  5. save_issues(db, issues)    → upserts to issues table
  6. record_run(db, result)     → saves agent_runs audit row
"""

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm_client import call_with_tool
from app.agents.schemas import (
    AgentContext,
    AgentOutput,
    AgentRunResult,
    IssueDetection,
    ISSUE_DETECTION_TOOL,
)
from app.models.agent_run import AgentRun
from app.models.issue import Issue
from app.models.site import Site

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    agent_name: str = "base"
    model_tier: str = "cheap"      # "cheap" = Haiku, "smart" = Sonnet

    # ── Abstract interface ─────────────────────────────────────────────────

    @abstractmethod
    def get_system_prompt(self, context: AgentContext) -> str:
        """Return the stable system prompt for this agent (cached by Claude)."""

    @abstractmethod
    async def load_data(self, db: AsyncSession, context: AgentContext) -> dict:
        """Load raw data from DB for this agent's analysis."""

    @abstractmethod
    def format_user_message(self, context: AgentContext, data: dict) -> str:
        """Format the data as a user message for Claude."""

    # ── Main entry point ───────────────────────────────────────────────────

    async def run(
        self,
        db: AsyncSession,
        site_id: UUID,
        trigger: str = "scheduled",
    ) -> AgentRunResult:
        """Run the full agent pipeline."""
        t0 = time.monotonic()

        # 1. Load site info
        site_row = await db.execute(select(Site).where(Site.id == site_id))
        site = site_row.scalar_one_or_none()
        if not site:
            return AgentRunResult(
                agent_name=self.agent_name,
                site_id=site_id,
                issues_found=0,
                issues_saved=0,
                summary="Site not found.",
                cost_usd=0.0,
                input_tokens=0,
                output_tokens=0,
                error="Site not found",
            )

        from datetime import date
        context = AgentContext(
            site_id=site_id,
            site_domain=site.domain,
            analysis_date=date.today(),
            operating_mode=site.operating_mode,
        )

        # Create an in-progress agent_run record
        run_record = AgentRun(
            site_id=site_id,
            agent_name=self.agent_name,
            model_used="pending",
            trigger=trigger,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        db.add(run_record)
        await db.flush()

        try:
            # 2. Load data
            data = await self.load_data(db, context)

            if not data:
                result = AgentRunResult(
                    agent_name=self.agent_name,
                    site_id=site_id,
                    issues_found=0,
                    issues_saved=0,
                    summary="No data available for analysis.",
                    cost_usd=0.0,
                    input_tokens=0,
                    output_tokens=0,
                )
                await self._complete_run(db, run_record, result, int((time.monotonic() - t0) * 1000))
                return result

            # 3. Build prompts
            system = self.get_system_prompt(context)
            user_msg = self.format_user_message(context, data)

            # 4. Call Claude with structured tool_use output
            raw_output, usage = call_with_tool(
                model_tier=self.model_tier,
                system=system,
                user_message=user_msg,
                tool=ISSUE_DETECTION_TOOL,
                max_tokens=4096,
            )

            # 5. Parse + validate
            agent_output = self._parse_output(raw_output)

            # 6. Save issues
            issues_saved = await self._save_issues(db, site_id, agent_output.issues)

            # 7. Build result
            result = AgentRunResult(
                agent_name=self.agent_name,
                site_id=site_id,
                issues_found=len(agent_output.issues),
                issues_saved=issues_saved,
                summary=agent_output.summary,
                cost_usd=usage["cost_usd"],
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
            )

            await self._complete_run(db, run_record, result, int((time.monotonic() - t0) * 1000), usage)
            logger.info(
                "Agent %s done: %d issues, $%.5f, %dms",
                self.agent_name, result.issues_found,
                result.cost_usd, int((time.monotonic() - t0) * 1000),
            )
            return result

        except Exception as exc:
            logger.error("Agent %s failed: %s", self.agent_name, exc, exc_info=True)
            result = AgentRunResult(
                agent_name=self.agent_name,
                site_id=site_id,
                issues_found=0,
                issues_saved=0,
                summary=f"Agent failed: {exc}",
                cost_usd=0.0,
                input_tokens=0,
                output_tokens=0,
                error=str(exc),
            )
            run_record.status = "failed"
            run_record.error_message = str(exc)[:2000]
            run_record.completed_at = datetime.now(timezone.utc)
            run_record.duration_ms = int((time.monotonic() - t0) * 1000)
            await db.flush()
            return result

    # ── Helpers ────────────────────────────────────────────────────────────

    def _parse_output(self, raw: dict) -> AgentOutput:
        try:
            return AgentOutput(**raw)
        except (ValidationError, TypeError) as exc:
            logger.warning("Output parse error: %s — raw: %s", exc, str(raw)[:200])
            return AgentOutput(issues=[], summary="Parse error — no issues extracted.")

    async def _save_issues(
        self,
        db: AsyncSession,
        site_id: UUID,
        issues: list[IssueDetection],
    ) -> int:
        saved = 0
        for issue in issues:
            # Only save if confidence >= 0.4 (below that: noise)
            if issue.confidence < 0.4:
                continue
            new_issue = Issue(
                site_id=site_id,
                agent_name=self.agent_name,
                issue_type=issue.issue_type,
                severity=issue.severity,
                confidence=issue.confidence,
                title=issue.title,
                description=issue.description,
                affected_entity_type="query" if issue.affected_urls_or_queries and "/" not in issue.affected_urls_or_queries[0] else "page",
                evidence={
                    "affected": issue.affected_urls_or_queries[:10],
                    **issue.evidence,
                },
                recommendation=issue.recommendation,
                status="open",
            )
            db.add(new_issue)
            saved += 1

        await db.flush()
        return saved

    async def _complete_run(
        self,
        db: AsyncSession,
        run_record: AgentRun,
        result: AgentRunResult,
        duration_ms: int,
        usage: dict | None = None,
    ) -> None:
        from app.config import settings
        run_record.status = "failed" if result.error else "completed"
        run_record.completed_at = datetime.now(timezone.utc)
        run_record.duration_ms = duration_ms
        run_record.output_summary = {
            "issues_found": result.issues_found,
            "issues_saved": result.issues_saved,
            "summary": result.summary[:500],
        }
        if usage:
            run_record.model_used = usage.get("model", settings.AI_DAILY_MODEL)
            run_record.input_tokens = usage.get("input_tokens", 0)
            run_record.output_tokens = usage.get("output_tokens", 0)
            run_record.cost_usd = usage.get("cost_usd", 0.0)
            run_record.prompt_hash = usage.get("prompt_hash")
        await db.flush()
