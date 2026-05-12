"""Plain data shapes for Lateral Query Expansion."""

from __future__ import annotations

from dataclasses import dataclass, field

RELATION_VALUES = ("direct", "related", "info", "weak")
SOURCE_SIGNALS = (
    "business_truth", "competitor_serp", "wordstat_related", "composite",
)


@dataclass
class LateralCandidate:
    """One LLM-proposed query idea, post-validation."""

    query: str
    relation: str
    confidence: float
    rationale: str
    source_signal: str = "composite"

    @property
    def query_norm(self) -> str:
        return normalize_query(self.query)


@dataclass
class LateralContext:
    """The input snapshot we hand to the LLM.

    Built by `context.build_context()`; small and JSON-serialisable so
    the prompt stays compact (Haiku context is precious).
    """

    site_id: str
    domain: str
    business_summary: str
    services: list[str] = field(default_factory=list)
    geo: list[str] = field(default_factory=list)
    competitor_brands: list[str] = field(default_factory=list)
    top_observed_queries: list[dict] = field(default_factory=list)
    existing_lateral_norms: set[str] = field(default_factory=set)
    strategic_focus: str | None = None
    # Lateral v2 (2026-05-13): anti-cannibalization + own-brand guards.
    # own_pages: list of {url, title, h1, intent_code} for the site's own
    # crawled pages — passed to the LLM so it doesn't propose a query
    # that an existing page already targets. Cap ~50.
    own_pages: list[dict] = field(default_factory=list)
    # brand_strings: tokens identifying THIS site's brand (e.g. domain
    # root, target_config.brand_name, display_name). LLM must not propose
    # queries containing any of these — Python post-filter enforces it.
    brand_strings: list[str] = field(default_factory=list)


def normalize_query(q: str) -> str:
    """Lowercase + collapse whitespace. Stable across runs.

    Kept intentionally permissive — we do NOT strip punctuation or
    Russian stop words, because "тур в сочи" and "тур по сочи" should
    stay separate ideas for the owner to triage.
    """
    return " ".join((q or "").lower().split())
