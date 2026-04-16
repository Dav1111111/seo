"""DTOs for the weekly report — Pydantic v2 models, JSONB-serializable."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ── Meta ──────────────────────────────────────────────────────────────

class ReportMeta(BaseModel):
    site_id: UUID
    site_host: str
    week_start: date               # inclusive Monday
    week_end: date                 # inclusive Sunday
    generated_at: datetime
    builder_version: str
    llm_cost_usd: float = 0.0
    generation_ms: int = 0
    status: str = "completed"      # completed | draft | failed


# ── 1. Executive ──────────────────────────────────────────────────────

class ExecutiveSection(BaseModel):
    health_score: int                              # 0-100
    health_score_delta: int | None = None          # WoW diff
    wow_impressions_pct: float | None = None
    wow_clicks_pct: float | None = None
    top_wins: list[str] = Field(default_factory=list)
    top_losses: list[str] = Field(default_factory=list)
    prose_ru: str                                  # LLM or template
    prose_source: str = "template"                 # "llm" | "template"


# ── 2. Action Plan ────────────────────────────────────────────────────

class ActionPlanItem(BaseModel):
    recommendation_id: UUID
    page_url: str | None
    target_intent_code: str
    category: str
    priority: str                                  # critical|high|medium|low
    priority_score: float
    expected_lift_impressions: int | None = None
    suggested_owner: str                           # copywriter|dev|legal|seo
    eta_ru: str                                    # "сегодня" | "эта неделя" | "2 недели"
    reasoning_ru: str
    before_text: str | None = None
    after_text: str | None = None


class ActionPlanSection(BaseModel):
    items: list[ActionPlanItem] = Field(default_factory=list)
    narrative_ru: str = ""
    narrative_source: str = "template"             # "llm" | "template"
    pages_represented: int = 0
    total_in_backlog: int = 0


# ── 3. Coverage ───────────────────────────────────────────────────────

class IntentClusterSummary(BaseModel):
    intent_code: str
    queries_count: int
    total_impressions_14d: int
    best_page_url: str | None = None
    best_page_score: float
    status: str                                    # strong|weak|missing|over_covered
    ambiguous_queries_count: int = 0


class CoverageSection(BaseModel):
    clusters: list[IntentClusterSummary] = Field(default_factory=list)
    strong_count: int = 0
    weak_count: int = 0
    missing_count: int = 0
    over_covered_count: int = 0
    open_decisions_count: int = 0
    distribution_pct: dict[str, float] = Field(default_factory=dict)
    intent_gaps: list[str] = Field(default_factory=list)   # e.g. "COMM_CATEGORY has 0 pages ≥ 4.0"


# ── 4. Query Trends ───────────────────────────────────────────────────

class QueryMove(BaseModel):
    query_text: str
    impressions_this_week: int
    impressions_prev_week: int
    impressions_diff: int
    avg_position_this_week: float | None = None
    avg_position_prev_week: float | None = None


class QueryTrendsSection(BaseModel):
    data_available: bool = True
    totals_this_week: dict[str, float | int] = Field(default_factory=dict)   # imp/clk/pos
    totals_prev_week: dict[str, float | int] = Field(default_factory=dict)
    wow_diff: dict[str, float] = Field(default_factory=dict)                 # % diffs
    top_movers_up: list[QueryMove] = Field(default_factory=list)
    top_movers_down: list[QueryMove] = Field(default_factory=list)
    new_queries: list[str] = Field(default_factory=list)
    lost_queries: list[str] = Field(default_factory=list)
    note_ru: str | None = None                     # e.g. "Webmaster нет данных за 2 дня"


# ── 5. Page Findings ──────────────────────────────────────────────────

class PageFindingSummary(BaseModel):
    page_id: UUID
    page_url: str | None
    target_intent_code: str
    reviewed_at: datetime
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    top_issues: list[str] = Field(default_factory=list)   # one-liner per top category
    missing_eeat_signals: list[str] = Field(default_factory=list)
    missing_commercial_factors: list[str] = Field(default_factory=list)


class PageFindingsSection(BaseModel):
    reviews_run_count: int = 0
    pages_reviewed: int = 0
    by_category_count: dict[str, int] = Field(default_factory=dict)
    by_priority_count: dict[str, int] = Field(default_factory=dict)
    pages: list[PageFindingSummary] = Field(default_factory=list)
    warning_ru: str | None = None                  # shown when 0 reviews


# ── 6. Technical ──────────────────────────────────────────────────────

class TechnicalSection(BaseModel):
    pages_total: int = 0
    pages_indexed: int = 0
    pages_non_200: int = 0
    indexation_rate: float = 0.0                   # 0-1
    indexation_rate_prev_week: float | None = None
    duplicates_suspected: int = 0                  # content_hash groups count>1
    fingerprint_stale_count: int = 0               # >30d unchanged
    warning_ru: str | None = None


# ── Composite ─────────────────────────────────────────────────────────

class WeeklyReport(BaseModel):
    meta: ReportMeta
    executive: ExecutiveSection
    action_plan: ActionPlanSection
    coverage: CoverageSection
    query_trends: QueryTrendsSection
    page_findings: PageFindingsSection
    technical: TechnicalSection

    model_config = {
        "arbitrary_types_allowed": True,
    }

    def to_jsonb(self) -> dict[str, Any]:
        """Serialize to JSONB-safe dict (str timestamps, str UUIDs)."""
        return self.model_dump(mode="json")
