"""Frozen contract for the unified advice center feed.

Every other module (brain rules, health checker, schema audit,
keyword_match, robots audit, funnel coverage gaps) gets reduced to a
single `AdviceCard`. The aggregator returns an ordered `AdviceFeed`
that the frontend renders as one flat list — owner opens /studio home
and sees the same view regardless of which underlying module produced
which signal.

Field names are LOAD-BEARING: Agent 3 (frontend) reads these by name.
Do NOT rename without coordinating across the two agents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Severity = Literal["critical", "high", "medium", "low", "info"]
Category = Literal[
    "technical",    # broken collector, missing data, pipeline failure
    "health",       # service degraded (Metrica counter, etc.)
    "funnel",       # funnel coverage gap, demand pattern advice
    "schema",       # Schema.org markup advice
    "keywords",     # keyword placement advice
    "seo_content",  # title / H1 / content review-pipeline advice
]


# ── Sort score weights ────────────────────────────────────────────────
#
# Severity dominates the order; category only breaks ties. `expected
# clicks uplift` rides on top of that as a 1/10 fractional bump so the
# tie-break is deterministic but never overpowers severity. Critical
# technical (1000 + 200 = 1200) always beats info seo (50 + 30 = 80).

SEVERITY_WEIGHT: dict[Severity, int] = {
    "critical": 1000,
    "high": 700,
    "medium": 400,
    "low": 200,
    "info": 50,
}

CATEGORY_BUMP: dict[Category, int] = {
    "technical": 200,    # broken stuff above SEO advice
    "health": 150,
    "funnel": 100,
    "schema": 50,
    "keywords": 30,
    "seo_content": 30,
}


def compute_sort_score(
    severity: Severity,
    category: Category,
    expected_clicks_uplift: float = 0.0,
) -> float:
    """The single source of truth for the feed ordering.

    Formula: severity_weight + category_bump + expected_clicks/10.

    Used by `formatters.*` (cards build their own score on creation so
    the aggregator can resort cheaply) and by the test suite (so the
    expected ordering is verifiable from one place).
    """
    sev = SEVERITY_WEIGHT.get(severity, 0)
    cat = CATEGORY_BUMP.get(category, 0)
    uplift = max(0.0, float(expected_clicks_uplift or 0.0))
    return float(sev + cat) + uplift / 10.0


@dataclass(frozen=True)
class AdviceCard:
    """One owner-facing item in the advice feed.

    `id` must be stable across runs (e.g. `funnel:top_gap`,
    `robots:critical`) so the frontend can dedupe / persist
    dismissed state across page reloads.

    `sort_score` is precomputed via `compute_sort_score` — the
    aggregator sorts DESC on it.
    """
    id: str
    severity: Severity
    category: Category
    title_ru: str                       # one-sentence headline
    body_ru: str                        # 2-3 sentence explanation
    action_ru: str                      # 1-2 sentence "what to do"
    expected_impact_ru: str | None      # «~25 тыс посетителей/мес» or None
    link: str | None                    # «/studio/queries?layer=funnel_top» etc.
    cta_ru: str | None                  # «Открыть запросы» button label, or None
    sort_score: float                   # aggregator sorts DESC on this
    source_module: str                  # telemetry: «brain», «advisor.health», …


@dataclass(frozen=True)
class AdviceFeed:
    """The full ordered feed returned by the advice endpoint."""
    site_id: str
    computed_at: str                            # ISO 8601 (UTC)
    counts_by_severity: dict[str, int]
    counts_by_category: dict[str, int]
    cards: list[AdviceCard] = field(default_factory=list)


__all__ = [
    "AdviceCard",
    "AdviceFeed",
    "Category",
    "Severity",
    "SEVERITY_WEIGHT",
    "CATEGORY_BUMP",
    "compute_sort_score",
]
