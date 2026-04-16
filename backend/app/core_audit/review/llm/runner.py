"""Thin wrapper around llm_client.call_with_tool — fails open.

Returns (LLMEnrichment | None, usage_stats). None means LLM unavailable
or response malformed — caller keeps Python-only result.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core_audit.review.dto import ReviewInput
from app.core_audit.review.findings import CheckFinding
from app.core_audit.review.llm.base import (
    LLMEnrichment,
    LLMH2Draft,
    LLMLinkProposal,
    LLMRewrite,
)
from app.core_audit.review.llm.prompts import (
    ENRICH_TOOL,
    SYSTEM_ENRICH,
    build_user_prompt,
)

logger = logging.getLogger(__name__)

# Import at module level so tests can patch this name; None if anthropic
# SDK unavailable in the process (e.g. dev shell without dependencies).
try:
    from app.agents.llm_client import call_with_tool
    _LLM_AVAILABLE = True
except Exception as _exc:
    call_with_tool = None        # type: ignore[assignment]
    _LLM_AVAILABLE = False
    logger.warning("llm_client unavailable: %s — Step 4 will fall back to Python-only", _exc)


def _parse_response(tool_input: dict[str, Any]) -> LLMEnrichment:
    rewrites = tuple(
        LLMRewrite(
            finding_id=str(r.get("finding_id", "")),
            before_text=r.get("before_text"),
            after_text=str(r.get("after_text", "")),
            reasoning_ru=str(r.get("reasoning_ru", "")),
        )
        for r in (tool_input.get("rewrites") or [])
        if r.get("finding_id") and r.get("after_text")
    )
    h2_drafts = tuple(
        LLMH2Draft(
            block_title=str(d.get("block_title", "")),
            draft_ru=str(d.get("draft_ru", "")),
            word_count=len(str(d.get("draft_ru", "")).split()),
        )
        for d in (tool_input.get("h2_drafts") or [])
        if d.get("block_title") and d.get("draft_ru")
    )
    links = tuple(
        LLMLinkProposal(
            anchor_ru=str(l.get("anchor_ru", "")),
            target_url=str(l.get("target_url", "")),
            reasoning_ru=str(l.get("reasoning_ru", "")),
            placement_hint=l.get("placement_hint"),
        )
        for l in (tool_input.get("link_proposals") or [])
        if l.get("anchor_ru") and l.get("target_url")
    )
    cargo = tuple(
        str(s) for s in (tool_input.get("detected_cargo_cult_schemas") or [])
        if s
    )
    return LLMEnrichment(
        rewrites=rewrites,
        h2_drafts=h2_drafts,
        link_proposals=links,
        detected_cargo_cult_schemas=cargo,
    )


def run_enrich(
    ri: ReviewInput,
    actionable: list[CheckFinding],
) -> tuple[LLMEnrichment | None, dict[str, Any]]:
    """Invoke LLM, parse, return (enrichment or None, usage stats)."""
    if not actionable:
        return LLMEnrichment(), {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "model": None}

    if call_with_tool is None:
        return None, {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "model": None}

    user_prompt = build_user_prompt(ri, actionable)

    try:
        tool_input, usage = call_with_tool(
            model_tier="cheap",
            system=SYSTEM_ENRICH,
            user_message=user_prompt,
            tool=ENRICH_TOOL,
            max_tokens=2048,
        )
    except Exception as exc:
        logger.warning("llm enrichment call failed page=%s: %s", ri.page_id, exc)
        return None, {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "model": None}

    if not isinstance(tool_input, dict):
        logger.warning("llm returned non-dict tool_input for page=%s", ri.page_id)
        return None, {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "model": None}

    try:
        enrichment = _parse_response(tool_input)
    except Exception as exc:
        logger.warning("llm response parse failed page=%s: %s", ri.page_id, exc)
        return None, usage

    return enrichment, usage
