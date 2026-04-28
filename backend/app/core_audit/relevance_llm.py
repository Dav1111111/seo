"""LLM half of the query-relevance classifier (Studio v2 etap 4).

Rules (`relevance.py`) catch the obvious `own` cases without paying.
Anything rules deferred (`None` verdict) lands here: «джинсы багги»
needs to be flagged spam, «экскурсии Сочи» needs to be flagged
adjacent — and only LLM with the business narrative has the context
to make those calls.

Promt design choices that matter:

1. **Tool-use** for structured output. The model returns a JSON list
   of {idx, relevance, reason_ru}; we never parse free-form text.
   tool_choice={"type":"tool"} forces the model to call the tool
   exactly once, removing «here's some prose AND a tool call»
   ambiguity.

2. **Index-based output**, not query-text-as-key. Repeating the
   query verbatim back is a token waste AND lets the model
   silently rename the query («багги тур» → «багги-тур») which
   would orphan the row. Index ties the response to our input
   row order.

3. **Batch ~30 queries per call.** Smaller batches = better
   accuracy per query but more model calls (cost adds up). 30 fits
   in Haiku context with room for narrative + reasoning, and 4
   batches × 30 = 120 queries / minute is the right pace for
   one-time site classification.

4. **Brand-aware spam detection.** The narrative_ru tells the model
   what the business actually does, so «джинсы багги» loses against
   «премиальный клуб активного отдыха в Сочи». Without narrative the
   model has only `services` and would over-classify «штаны багги»
   as adjacent (because «багги» is in the profile).

5. **Conservative defaults.** When the model is genuinely unsure it
   returns `disputed`, NOT `own` (don't be optimistic) and NOT `spam`
   (don't reject what might be a real customer). Owner reviews
   disputed manually in the UI.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Sequence

from app.agents.llm_client import call_with_tool
from app.core_audit.relevance import (
    RELEVANCE_VALUES,
    ProfileSlice,
    RelevanceVerdict,
)


log = logging.getLogger(__name__)


CLASSIFY_BATCH_SIZE = 30
LLM_MODEL_TIER = "cheap"  # Haiku — relevance is a classification task,
                          # not generation; cheap tier is plenty.


# ── Prompt ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
Ты классифицируешь поисковые запросы по релевантности конкретному бизнесу.

КЛАССЫ:
  own       — запрос напрямую про продукт бизнеса в его географии.
              Клиент бизнеса гарантированно ищет это.
  adjacent  — формально другая тема, но клиент бизнеса всё равно
              может искать это (соседний интент). Пример: для
              премиум-багги-тура в Сочи запрос «экскурсии Сочи» —
              adjacent, потому что человек, ищущий впечатления в
              Сочи, может попасть на премиум-отдых.
  disputed  — непонятно, может быть и нашим и не нашим. Когда нет
              уверенности — выбирай этот класс.
  spam      — запрос про другую тему (омоним, омограф, не-наш
              рынок). Пример: «джинсы багги» — про одежду, не про
              транспорт. «Багги своими руками» — про DIY-сборку
              машинки, не про туризм.

ПРАВИЛА:
  1. Спорные случаи → disputed, а не own. Лучше владелец бизнеса
     потом скажет «да, это моё», чем мы насчитаем мусор как наш.
  2. Спорные случаи → disputed, а не spam. Лучше пометить
     потенциального клиента как спорного, чем выкинуть.
  3. reason_ru — одно короткое предложение на русском. Объясни ПОЧЕМУ
     ты выбрал именно этот класс — это владелец увидит в UI.
  4. Не оценивай качество запроса (объёмы, перспективы) — только
     релевантность бизнесу.
"""


CLASSIFY_TOOL = {
    "name": "classify_queries",
    "description": (
        "Return relevance classification for each query in the batch. "
        "Output one entry per input query, indexed from 0."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "idx": {
                            "type": "integer",
                            "description": (
                                "Zero-based index matching the input "
                                "query position."
                            ),
                        },
                        "relevance": {
                            "type": "string",
                            "enum": list(RELEVANCE_VALUES[:-1]),  # no 'unclassified' from LLM
                        },
                        "reason_ru": {
                            "type": "string",
                            "description": (
                                "One-sentence Russian justification, "
                                "shown to the site owner."
                            ),
                        },
                    },
                    "required": ["idx", "relevance", "reason_ru"],
                },
            },
        },
        "required": ["results"],
    },
}


def _build_user_message(
    profile: ProfileSlice,
    narrative_ru: str,
    queries: Sequence[str],
) -> str:
    """Compose the user message — profile context + numbered queries."""
    secondary = (
        ", ".join(profile.secondary_products)
        if profile.secondary_products
        else "—"
    )
    services = ", ".join(profile.services) if profile.services else "—"
    geo_primary = ", ".join(profile.geo_primary) or "—"
    geo_secondary = (
        ", ".join(profile.geo_secondary) if profile.geo_secondary else "—"
    )
    narrative = narrative_ru.strip() or "—"

    lines = [
        "ПРОФИЛЬ БИЗНЕСА:",
        f"  основной продукт: {profile.primary_product or '—'}",
        f"  доп. продукты: {secondary}",
        f"  услуги: {services}",
        f"  основные регионы: {geo_primary}",
        f"  доп. регионы: {geo_secondary}",
        "",
        "ОПИСАНИЕ БИЗНЕСА:",
        narrative,
        "",
        "ЗАПРОСЫ (классифицируй каждый):",
    ]
    for i, q in enumerate(queries):
        lines.append(f"  {i}. {q}")

    lines.append("")
    lines.append("Верни results — один объект на каждый запрос.")
    return "\n".join(lines)


@dataclass(frozen=True)
class LLMClassificationResult:
    verdicts: dict[int, RelevanceVerdict]  # keyed by input index
    cost_usd: float
    input_tokens: int
    output_tokens: int


def classify_by_llm(
    queries: Sequence[str],
    profile: ProfileSlice,
    narrative_ru: str,
) -> LLMClassificationResult:
    """Classify a batch of queries via Haiku.

    Caller is responsible for batching — keep `queries` ≤ CLASSIFY_BATCH_SIZE.
    Returns verdicts dict keyed by input index (NOT query text — see
    module docstring for why). Missing indexes mean the model didn't
    return that entry; caller should treat them as `disputed` so
    nothing falls through silently.
    """
    if not queries:
        return LLMClassificationResult(
            verdicts={}, cost_usd=0.0, input_tokens=0, output_tokens=0,
        )

    user_message = _build_user_message(profile, narrative_ru, queries)

    tool_input, usage = call_with_tool(
        model_tier=LLM_MODEL_TIER,
        system=SYSTEM_PROMPT,
        user_message=user_message,
        tool=CLASSIFY_TOOL,
        max_tokens=2048,
    )

    raw_results = tool_input.get("results") or []
    verdicts: dict[int, RelevanceVerdict] = {}
    seen_idx: set[int] = set()

    for entry in raw_results:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry["idx"])
            relevance = str(entry["relevance"]).strip().lower()
            reason = str(entry.get("reason_ru") or "").strip()
        except (KeyError, TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(queries):
            continue
        if idx in seen_idx:
            continue
        if relevance not in RELEVANCE_VALUES or relevance == "unclassified":
            # Coerce unexpected values to disputed so nothing
            # silently lands in the «own» bucket.
            log.warning(
                "relevance_llm.unexpected_value got=%r idx=%d query=%r",
                relevance, idx, queries[idx],
            )
            relevance = "disputed"
            reason = reason or "LLM вернул неожиданный класс — пометили как спорный"
        seen_idx.add(idx)
        verdicts[idx] = RelevanceVerdict(
            relevance=relevance,
            set_by="llm",
            reason_ru=reason or "—",
        )

    return LLMClassificationResult(
        verdicts=verdicts,
        cost_usd=float(usage.get("cost_usd") or 0.0),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
    )


__all__ = [
    "CLASSIFY_BATCH_SIZE",
    "LLM_MODEL_TIER",
    "LLMClassificationResult",
    "classify_by_llm",
    "_build_user_message",  # exported for prompt-snapshot tests
]
