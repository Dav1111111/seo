"""Dataclasses + finding-id helper for the LLM enrichment layer."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core_audit.review.findings import CheckFinding


@dataclass(frozen=True)
class LLMRewrite:
    """LLM-generated rewrite for a specific Python finding."""
    finding_id: str
    before_text: str | None
    after_text: str
    reasoning_ru: str


@dataclass(frozen=True)
class LLMH2Draft:
    """Draft content (200-300 words) for a missing required H2 block."""
    block_title: str                       # matches profile.page_requirements[intent] entry
    draft_ru: str
    word_count: int


@dataclass(frozen=True)
class LLMLinkProposal:
    """Internal link suggestion — target_url MUST be from ri.link_candidates."""
    anchor_ru: str
    target_url: str
    reasoning_ru: str
    placement_hint: str | None = None       # "intro"|"body"|"faq"|"footer"|None


@dataclass(frozen=True)
class LLMEnrichment:
    """Structured LLM response after validation."""
    rewrites: tuple[LLMRewrite, ...] = ()
    h2_drafts: tuple[LLMH2Draft, ...] = ()
    link_proposals: tuple[LLMLinkProposal, ...] = ()
    detected_cargo_cult_schemas: tuple[str, ...] = ()


def finding_id(f: CheckFinding) -> str:
    """Deterministic identity for a finding — used to merge LLM rewrites back.

    Rules:
      h2 findings       → 'missing_critical_h2:Цены'
      eeat findings     → 'eeat_signal_missing:rto_number'
      commercial        → 'commercial_factor_missing:phone_in_header'
      everything else   → signal_type alone
    """
    evidence = f.evidence or {}
    if f.signal_type in ("missing_critical_h2", "missing_recommended_h2"):
        return f"{f.signal_type}:{evidence.get('block', '')}"
    if f.signal_type in ("eeat_signal_missing", "eeat_signal_present"):
        return f"{f.signal_type}:{evidence.get('signal_name', '')}"
    if f.signal_type in (
        "commercial_factor_missing",
        "commercial_factor_present",
        "commercial_factor_deferred_to_llm",
    ):
        return f"{f.signal_type}:{evidence.get('factor_name', '')}"
    return f.signal_type
