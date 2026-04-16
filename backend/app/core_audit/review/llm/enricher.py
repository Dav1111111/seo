"""Step 4 orchestrator — merges LLM rewrites into Step 3 ReviewResult.

Contract:
  - Input: ReviewResult (from run_python_checks) + ReviewInput + findings list
  - Pure function — no DB writes.
  - If LLM unavailable or returns garbage → returns Step 3 result unchanged.
  - On success: replaces `reasoning_ru` on matching recs, fills `after`,
    appends NEW recs for h2_drafts + link_proposals + cargo-cult detections.
  - Updates reviewer_model + cost_usd + token counts.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from app.core_audit.review.dto import (
    Recommendation,
    ReviewInput,
    ReviewResult,
)
from app.core_audit.review.enums import RecCategory, RecPriority, ReviewStatus
from app.core_audit.review.findings import CheckFinding, FindingStatus
from app.core_audit.review.llm.base import (
    LLMEnrichment,
    LLMH2Draft,
    LLMLinkProposal,
    finding_id,
)
from app.core_audit.review.llm.runner import run_enrich
from app.core_audit.review.llm.verify import verify

logger = logging.getLogger(__name__)


def enrich_with_llm(
    result: ReviewResult,
    ri: ReviewInput,
    findings: list[CheckFinding],
) -> ReviewResult:
    """Enrich a Python-only ReviewResult with LLM rewrites. Fails open."""
    if result.status != ReviewStatus.completed:
        return result
    if not findings:
        return result

    actionable = [f for f in findings if f.status in (FindingStatus.fail, FindingStatus.warn)]
    if not actionable:
        return result

    sent_ids = {finding_id(f) for f in actionable}

    enrichment, usage = run_enrich(ri, actionable)
    if enrichment is None:
        # LLM failed — keep Python-only result, note cost=0.
        return result

    enrichment = verify(enrichment, ri, sent_ids)
    merged_recs = _merge_recommendations(result.recommendations, enrichment)

    cost = float(usage.get("cost_usd", 0.0) or 0.0)
    model = usage.get("model") or "claude-haiku-4-5"

    return replace(
        result,
        recommendations=merged_recs,
        reviewer_model=f"python+{model}" if cost > 0 else result.reviewer_model,
        cost_usd=result.cost_usd + cost,
        input_tokens=result.input_tokens + int(usage.get("input_tokens", 0) or 0),
        output_tokens=result.output_tokens + int(usage.get("output_tokens", 0) or 0),
    )


def _merge_recommendations(
    recs: list[Recommendation],
    enrichment: LLMEnrichment,
) -> list[Recommendation]:
    """Replace reasoning + fill after on matching recs; append new ones."""
    by_id: dict[str, Recommendation] = {
        r.source_finding_id: r for r in recs if r.source_finding_id
    }
    passthrough: list[Recommendation] = [r for r in recs if not r.source_finding_id]

    updated: list[Recommendation] = []
    consumed: set[str] = set()

    for rw in enrichment.rewrites:
        original = by_id.get(rw.finding_id)
        if original is None:
            continue
        updated.append(replace(
            original,
            reasoning_ru=rw.reasoning_ru or original.reasoning_ru,
            before=original.before or rw.before_text,
            after=rw.after_text or original.after,
        ))
        consumed.add(rw.finding_id)

    # Keep Python recs that LLM didn't touch
    for fid, r in by_id.items():
        if fid not in consumed:
            updated.append(r)

    # Pass-through recs without finding_id (rare — only template-only recs)
    updated.extend(passthrough)

    # H2 drafts → new recs
    for d in enrichment.h2_drafts:
        updated.append(_h2_draft_to_rec(d))

    # Link proposals → new recs
    for lp in enrichment.link_proposals:
        updated.append(_link_to_rec(lp))

    # Cargo-cult detections → one rec per type
    for cargo in enrichment.detected_cargo_cult_schemas:
        updated.append(Recommendation(
            category=RecCategory.schema,
            priority=RecPriority.medium,
            reasoning_ru=(
                f"На странице используется Schema.org тип «{cargo}», который Яндекс "
                f"не парсит в расширенные сниппеты. Замените на Product + Offer для "
                f"туристических продуктов."
            ),
            before=cargo,
            after="Product + Offer + AggregateRating",
            source_finding_id=f"schema_cargo_cult_present:{cargo}",
        ))

    return updated


def _h2_draft_to_rec(d: LLMH2Draft) -> Recommendation:
    return Recommendation(
        category=RecCategory.h1_structure,
        priority=RecPriority.medium,
        reasoning_ru=(
            f"Сгенерирован черновик H2-раздела «{d.block_title}» "
            f"({d.word_count} слов) — проверьте фактуру и добавьте на страницу."
        ),
        before=None,
        after=d.draft_ru,
        source_finding_id=f"llm_h2_draft:{d.block_title}",
    )


def _link_to_rec(lp: LLMLinkProposal) -> Recommendation:
    placement = f" ({lp.placement_hint})" if lp.placement_hint else ""
    return Recommendation(
        category=RecCategory.internal_linking,
        priority=RecPriority.low,
        reasoning_ru=(
            f"Предложена внутренняя ссылка на {lp.target_url}{placement}. "
            f"Обоснование: {lp.reasoning_ru}"
        ),
        before=None,
        after=f'<a href="{lp.target_url}">{lp.anchor_ru}</a>',
        source_finding_id=f"llm_internal_link:{lp.target_url}",
    )
