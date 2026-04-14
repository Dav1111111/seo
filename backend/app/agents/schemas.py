"""
Pydantic v2 schemas for agent input/output.
IssueDetection is the universal structured output format for all agents.
"""

from datetime import date
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict


class IssueSeverity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class IssueType(str, Enum):
    # Search visibility
    position_drop = "position_drop"
    impression_drop = "impression_drop"
    ctr_anomaly = "ctr_anomaly"
    cannibalization = "cannibalization"
    new_opportunity = "new_opportunity"
    # Technical/indexing
    index_drop = "index_drop"
    crawl_error_spike = "crawl_error_spike"
    sitemap_error = "sitemap_error"
    # Content
    thin_content = "thin_content"
    missing_meta = "missing_meta"
    # Traffic
    traffic_drop = "traffic_drop"
    # Generic
    other = "other"


class IssueDetection(BaseModel):
    """Single issue found by an agent."""
    model_config = ConfigDict(use_enum_values=True)

    issue_type: IssueType
    severity: IssueSeverity
    confidence: float = Field(ge=0.0, le=1.0)
    title: str = Field(max_length=500)
    description: str
    affected_urls_or_queries: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    recommendation: str


class AgentOutput(BaseModel):
    """Full structured output from an agent's analysis."""
    issues: list[IssueDetection] = Field(default_factory=list)
    summary: str
    analysis_period: str = ""  # e.g. "2026-03-10 to 2026-04-09"


class AgentContext(BaseModel):
    """Input context loaded from DB for an agent."""
    site_id: UUID
    site_domain: str
    analysis_date: date
    operating_mode: str = "readonly"
    season_info: dict[str, Any] = Field(default_factory=dict)


class AgentRunResult(BaseModel):
    """What an agent run returns to the orchestrator."""
    agent_name: str
    site_id: UUID
    issues_found: int
    issues_saved: int
    summary: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    error: str | None = None


# ── Claude tool schema (used in llm_client.call_with_tool) ─────────────────

ISSUE_DETECTION_TOOL: dict[str, Any] = {
    "name": "report_issues",
    "description": (
        "Report all detected SEO issues with supporting evidence. "
        "Call this tool even if no issues are found — pass an empty issues array."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "issues": {
                "type": "array",
                "description": "List of detected issues (can be empty)",
                "items": {
                    "type": "object",
                    "properties": {
                        "issue_type": {
                            "type": "string",
                            "enum": [e.value for e in IssueType],
                        },
                        "severity": {
                            "type": "string",
                            "enum": [e.value for e in IssueSeverity],
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "description": "0.0 = uncertain, 1.0 = certain",
                        },
                        "title": {
                            "type": "string",
                            "description": "Short issue title (max 500 chars)",
                        },
                        "description": {
                            "type": "string",
                            "description": "Detailed explanation of the issue",
                        },
                        "affected_urls_or_queries": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "URLs or queries affected",
                        },
                        "evidence": {
                            "type": "object",
                            "description": "Raw data supporting this issue",
                        },
                        "recommendation": {
                            "type": "string",
                            "description": "Concrete action to fix this issue",
                        },
                    },
                    "required": [
                        "issue_type", "severity", "confidence",
                        "title", "description", "recommendation",
                    ],
                },
            },
            "summary": {
                "type": "string",
                "description": "2-3 sentence executive summary of findings",
            },
        },
        "required": ["issues", "summary"],
    },
}
