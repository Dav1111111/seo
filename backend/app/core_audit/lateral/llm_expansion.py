"""Single Haiku call → 15–20 lateral query candidates.

Forced tool_use output, so we don't fight JSON parsing.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.llm_client import call_with_tool
from app.core_audit.lateral.dto import (
    LateralCandidate,
    LateralContext,
    RELATION_VALUES,
    normalize_query,
)

logger = logging.getLogger(__name__)

# We ask for 18 in the prompt — gives Haiku breathing room while staying
# in the 15–20 band the owner agreed to. We clip to 20 on parse.
TARGET_COUNT = 18
MAX_CANDIDATES_KEPT = 20


LATERAL_TOOL: dict[str, Any] = {
    "name": "propose_lateral_queries",
    "description": (
        "Return a list of search-query ideas the site should plausibly "
        "rank for in Yandex 2026, that it isn't tracking yet. Each idea "
        "must be in Russian, lowercase, no quotes, real-search-engine "
        "phrasing (4-7 words typical)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "minItems": 10,
                "maxItems": 22,
                "items": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The query text in Russian, "
                                           "lowercase, 2-9 words.",
                        },
                        "relation": {
                            "type": "string",
                            "enum": list(RELATION_VALUES),
                            "description": (
                                "direct = sells the same product to the "
                                "same buyer; related = same audience, "
                                "different product; info = the buyer "
                                "researches this before buying; weak = "
                                "loose adjacency, only chase if cheap."
                            ),
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "description": (
                                "Your honest belief that pursuing this "
                                "query is worth the owner's time."
                            ),
                        },
                        "rationale": {
                            "type": "string",
                            "description": (
                                "One short Russian sentence — why this "
                                "query is relevant to THIS business. "
                                "No generic SEO talk."
                            ),
                        },
                    },
                    "required": [
                        "query", "relation", "confidence", "rationale",
                    ],
                },
            }
        },
        "required": ["queries"],
    },
}


_SYSTEM = """\
Ты Yandex SEO-стратег, помогающий русскому туристическому бизнесу
расширить охват поиска. На входе — бизнес-контекст и список запросов,
по которым сайт уже отслеживается. На выходе — 15-20 НОВЫХ идей
запросов, на которые сайт мог бы плавно расшириться.

ПРАВИЛА:

1. **На русском, в нижнем регистре**, без кавычек. Длина 2-9 слов.
2. **Не повторяй** запросы из «existing_lateral_norms» и не дублируй
   уже наблюдаемые в списке observed_queries.
3. **Не предлагай брендовые запросы** (бренд сайта или конкурентов).
4. **Не выдумывай услуги/гео, которых нет у бизнеса** — будь привязан
   к services / geo / strategic_focus.
5. **Не повторяй услуги один-в-один** — задача расширить, а не
   парафразировать. «Багги Абхазия» → не «купить багги Абхазия», а
   «однодневные туры из Адлера», «активный отдых Гагра», «джип-тур
   Рицу» — рядом, но шире.
6. **Каждой идее — relation**:
   - direct: явная продажа того же продукта тому же покупателю.
   - related: тот же покупатель, смежный продукт.
   - info: покупатель ищет перед покупкой (без транзакции).
   - weak: дальняя смежность, только если дешёво занять.
7. **Confidence** 0.0-1.0 — честная оценка, не средне-теплая. Цифры
   ниже 0.4 ставь только когда сам сомневаешься.
8. **Rationale** — одно предложение по-русски, почему это В ТОЧКУ для
   ЭТОГО бизнеса. Без общих фраз «увеличит трафик», «повысит видимость».

Если бизнес-контекст пустой или непонятный — лучше верни меньше идей
с высоким confidence, чем нагнать 20 общих.
"""


def _format_user_message(ctx: LateralContext) -> str:
    """Compact user payload — kept under ~1.5KB for cheap Haiku calls."""
    lines: list[str] = []
    lines.append(f"domain: {ctx.domain}")
    lines.append(f"business_summary: {ctx.business_summary}")
    if ctx.services:
        lines.append("services: " + ", ".join(ctx.services))
    if ctx.geo:
        lines.append("geo: " + ", ".join(ctx.geo))
    if ctx.strategic_focus:
        lines.append("strategic_focus: " + ctx.strategic_focus)
    if ctx.competitor_brands:
        lines.append(
            "competitor_brands (не предлагай эти бренды): "
            + ", ".join(ctx.competitor_brands)
        )

    if ctx.top_observed_queries:
        obs = []
        for row in ctx.top_observed_queries[:25]:
            vol = row.get("volume")
            tag = f" (~{vol}/mo)" if vol else ""
            obs.append(f"{row.get('query', '')}{tag}")
        lines.append("observed_queries (не дублируй):\n  - " + "\n  - ".join(obs))

    if ctx.existing_lateral_norms:
        ex = sorted(ctx.existing_lateral_norms)[:40]
        lines.append("existing_lateral_norms (не повторяй):\n  - " + "\n  - ".join(ex))

    lines.append(
        f"\nВерни ровно {TARGET_COUNT} идей через tool propose_lateral_queries."
    )
    return "\n".join(lines)


def expand_with_llm(
    ctx: LateralContext,
) -> tuple[list[LateralCandidate], dict[str, Any]]:
    """Sync — for the Celery worker. Returns (candidates, usage_stats)."""

    user_msg = _format_user_message(ctx)
    tool_input, usage = call_with_tool(
        model_tier="cheap",
        system=_SYSTEM,
        user_message=user_msg,
        tool=LATERAL_TOOL,
        max_tokens=2500,
    )

    raw_items = tool_input.get("queries") or []
    candidates: list[LateralCandidate] = []
    seen_norms: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        q = (item.get("query") or "").strip()
        if not q:
            continue
        norm = normalize_query(q)
        if norm in seen_norms:
            continue
        if norm in ctx.existing_lateral_norms:
            # LLM ignored our hint — drop silently to save a DB roundtrip.
            continue

        relation = (item.get("relation") or "").strip().lower()
        if relation not in RELATION_VALUES:
            relation = "related"

        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        rationale = (item.get("rationale") or "").strip()[:500]

        seen_norms.add(norm)
        candidates.append(
            LateralCandidate(
                query=q[:500],
                relation=relation,
                confidence=confidence,
                rationale=rationale,
                source_signal="composite",
            )
        )

        if len(candidates) >= MAX_CANDIDATES_KEPT:
            break

    logger.info(
        "lateral.llm_done domain=%s raw=%d kept=%d cost=$%.5f",
        ctx.domain, len(raw_items), len(candidates), usage.get("cost_usd", 0.0),
    )
    return candidates, usage


__all__ = ["expand_with_llm", "LATERAL_TOOL"]
