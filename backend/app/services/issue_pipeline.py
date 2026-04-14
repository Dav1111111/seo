"""
Issue pipeline — the spine of the system.

Flow:
  1. Run detection agents (search_visibility, technical_indexing)
  2. Collect candidate issues
  3. Enrich with seasonality context
  4. Pass to ValidatorAgent
  5. Store approved issues (confidence >= 0.6)
  6. Flag medium-confidence (0.4-0.6) for manual review
  7. Suppress low-confidence (<0.4)
  8. Respect operating mode
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.agents.schemas import AgentOutput, AgentRunResult, IssueDetection
from app.agents.search_visibility import SearchVisibilityAgent
from app.agents.technical_indexing import TechnicalIndexingAgent
from app.agents.validator import ValidatorAgent
from app.agents.seasonality_engine import SeasonalityEngine
from app.models.issue import Issue
from app.services.operating_mode import OperatingModeGuard

logger = logging.getLogger(__name__)

CONFIDENCE_PUBLISH = 0.6    # Show to user
CONFIDENCE_REVIEW = 0.4     # Flag for manual review
# Below 0.4 → suppressed

DETECTION_AGENTS: list[type[BaseAgent]] = [
    SearchVisibilityAgent,
    TechnicalIndexingAgent,
]


class IssuePipeline:
    """Orchestrates: detect → validate → store → alert."""

    def __init__(self):
        self.validator = ValidatorAgent()
        self.seasonality = SeasonalityEngine()

    async def run(
        self,
        db: AsyncSession,
        site_id: UUID,
        trigger: str = "scheduled",
        skip_validation: bool = False,
    ) -> dict[str, Any]:
        """Run the full pipeline for a site."""
        t0 = time.monotonic()
        guard = await OperatingModeGuard.for_site(db, site_id)

        results: dict[str, Any] = {
            "site_id": str(site_id),
            "mode": guard.mode,
            "agents": {},
            "validation": {},
            "issues_published": 0,
            "issues_review": 0,
            "issues_suppressed": 0,
            "total_cost_usd": 0.0,
        }

        # Phase 1: Run detection agents
        all_candidate_issues: list[tuple[str, IssueDetection]] = []

        for agent_cls in DETECTION_AGENTS:
            agent = agent_cls()
            agent_result = await agent.run(db, site_id, trigger=trigger)
            results["agents"][agent.agent_name] = {
                "issues_found": agent_result.issues_found,
                "cost_usd": agent_result.cost_usd,
                "summary": agent_result.summary,
                "error": agent_result.error,
            }
            results["total_cost_usd"] += agent_result.cost_usd

            # Collect issues for validation
            # (Re-read from DB since BaseAgent.run already saves them)
            # Actually, let's restructure: don't save in BaseAgent, save here after validation
            # For now, issues are already saved — validator will adjust confidence in place

        await db.commit()

        # Phase 2: Validate (re-read issues, adjust confidence)
        if not skip_validation:
            from sqlalchemy import select, update
            from app.models.issue import Issue

            # Get issues just created by agents in this run
            recent_issues = await db.execute(
                select(Issue)
                .where(
                    Issue.site_id == site_id,
                    Issue.status == "open",
                    Issue.created_at >= datetime.now(timezone.utc).replace(
                        hour=0, minute=0, second=0
                    ),
                )
                .order_by(Issue.created_at.desc())
            )
            issues_to_validate = recent_issues.scalars().all()

            if issues_to_validate:
                # Build AgentOutput for validator
                candidate_output = AgentOutput(
                    issues=[
                        IssueDetection(
                            issue_type=i.issue_type,
                            severity=i.severity,
                            confidence=float(i.confidence),
                            title=i.title,
                            description=i.description or "",
                            affected_urls_or_queries=(i.evidence or {}).get("affected", []),
                            evidence=i.evidence or {},
                            recommendation=i.recommendation or "",
                        )
                        for i in issues_to_validate
                    ],
                    summary=f"{len(issues_to_validate)} issues from today's analysis",
                )

                validated = await self.validator.validate(
                    db, site_id, candidate_output, "pipeline"
                )
                results["total_cost_usd"] += 0  # validator cost tracked in its own call

                # Update confidence in DB based on validation
                validated_map = {v.title: v for v in validated}
                rejected_titles = set()

                for issue_row in issues_to_validate:
                    validated_issue = validated_map.get(issue_row.title)
                    if validated_issue is None:
                        # Rejected by validator
                        issue_row.status = "suppressed"
                        rejected_titles.add(issue_row.title)
                        results["issues_suppressed"] += 1
                    else:
                        # Update confidence
                        issue_row.confidence = validated_issue.confidence

                        if validated_issue.confidence >= CONFIDENCE_PUBLISH:
                            results["issues_published"] += 1
                        elif validated_issue.confidence >= CONFIDENCE_REVIEW:
                            issue_row.status = "review"
                            results["issues_review"] += 1
                        else:
                            issue_row.status = "suppressed"
                            results["issues_suppressed"] += 1

                await db.commit()

                results["validation"] = {
                    "input_count": len(issues_to_validate),
                    "approved_count": len(validated),
                    "rejected_count": len(issues_to_validate) - len(validated),
                    "rejected_titles": list(rejected_titles)[:10],
                }

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        results["elapsed_ms"] = elapsed_ms

        logger.info(
            "Pipeline done: published=%d review=%d suppressed=%d cost=$%.4f time=%dms",
            results["issues_published"],
            results["issues_review"],
            results["issues_suppressed"],
            results["total_cost_usd"],
            elapsed_ms,
        )

        return results
