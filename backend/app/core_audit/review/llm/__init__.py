"""LLM enrichment layer (Step 4).

Takes Step 3 ReviewResult (reviewer_model='python-only') and enriches it
with Haiku-generated rewrites + better reasoning_ru. Does NOT detect new
issues — the LLM rewrites against findings Python already produced.

Cost budget: ~$0.003 per page via prompt caching.
Fails open: LLM failure → keep Python-only result, set cost_usd=0.
"""

from app.core_audit.review.llm.base import (
    LLMEnrichment,
    LLMH2Draft,
    LLMLinkProposal,
    LLMRewrite,
    finding_id,
)
from app.core_audit.review.llm.enricher import enrich_with_llm

__all__ = [
    "LLMEnrichment",
    "LLMH2Draft",
    "LLMLinkProposal",
    "LLMRewrite",
    "enrich_with_llm",
    "finding_id",
]
