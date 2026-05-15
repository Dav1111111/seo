"""Data contracts for the keyword_match module.

These dataclasses are the public boundary. Other agents (pipeline task,
studio endpoint, AI advisor panel) read these fields by name — do not
rename or repurpose without a coordinated change.

Frozen so consumers can hash / cache safely.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class KeywordGap:
    """A single (query, page) pair where the page lacks query tokens in
    critical SEO surfaces and could realistically rank better.

    Every field is verifiable — no LLM-inferred claims here. The
    `missing_in_*` lists contain *lemmas* (normalized Russian word
    forms), not the surface forms from the query.
    """

    site_id: UUID
    page_id: UUID
    page_url: str
    page_current_title: str | None       # ground truth from latest deep_extract
    page_current_h1: str | None

    query: str                            # exact SearchQuery.query_text
    query_id: UUID
    wordstat_volume: int                  # current month, NULL → skip query
    wordstat_volume_peak_3mo: int | None  # max over next 3 months from trend
    is_off_season: bool                   # current < 0.3 × peak_3mo

    current_position: float | None        # Webmaster avg_position, None if not ranking
    expected_clicks_per_month: int        # CTR(position 5) × volume − CTR(current) × volume

    missing_in_title_lemmas: list[str]    # tokens not in title (and not covered by synonyms)
    missing_in_h1_lemmas: list[str]
    missing_in_h2_lemmas: list[str]
    missing_in_first_para_lemmas: list[str]

    has_synonym_in_title: bool            # if synonym present, missing_in_title may still
                                          #   list tokens but with_synonym flag tells UI
    decision_tree_action: str             # "strengthen" — only this status produces a gap;
                                          #   "create" / "leave" / "block" → not in output


@dataclass(frozen=True)
class KeywordGapsSummary:
    """Site-level aggregation passed to the brain card / global view.

    `top_5_by_uplift` is the same KeywordGap objects already in the full
    list, sliced — consumers don't need to re-sort.
    """

    site_id: UUID
    total_gaps: int
    total_potential_clicks_per_month: int
    pages_with_gaps: int
    top_5_by_uplift: list[KeywordGap]
