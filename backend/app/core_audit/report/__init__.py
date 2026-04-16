"""Module 5 — Weekly Report Synthesis.

Generates a 6-section weekly SEO report per site combining all prior
modules (fingerprint, intent, review, priority) plus DailyMetric
time-series diffs.

Sections (order reflects owner-first reading: action → context):
  1. Executive Summary (LLM prose, template fallback)
  2. This Week's Action Plan (LLM narrative + Module 4 top-10)
  3. Coverage Status (intent audit + open decisions + intent gaps)
  4. Query Performance Trends (WoW impressions, position delta, new/lost)
  5. Page Review Findings (grouped by page, E-E-A-T as subsection)
  6. Technical SEO Snapshot (indexation WoW, non-200, stale fingerprints)

Competitive Gap section from the original 9-section spec is deferred
(no SERP data in v1). Content Opportunities folded into Coverage + Page
Findings.
"""

from app.core_audit.report.dto import (
    ActionPlanSection,
    CoverageSection,
    ExecutiveSection,
    PageFindingsSection,
    QueryTrendsSection,
    ReportMeta,
    TechnicalSection,
    WeeklyReport,
)

__all__ = [
    "ActionPlanSection",
    "CoverageSection",
    "ExecutiveSection",
    "PageFindingsSection",
    "QueryTrendsSection",
    "ReportMeta",
    "TechnicalSection",
    "WeeklyReport",
]
