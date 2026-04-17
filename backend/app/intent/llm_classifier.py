"""LLM fallback for ambiguous queries (confidence < 0.5 from regex).

Uses Claude Haiku with few-shot prompt. One batched call per ~20 queries.
Cost: ~$0.001 per 100 queries classified.
"""

from __future__ import annotations

import logging

from app.agents.llm_client import call_with_tool
from app.intent.classifier import ClassificationResult
from app.intent.enums import IntentCode

logger = logging.getLogger(__name__)

CLASSIFIER_LLM_VERSION = "1.0.0"

# Structured output tool — LLM must fill this
CLASSIFY_TOOL = {
    "name": "classify_queries",
    "description": "Classify Russian tourism queries by user intent.",
    "input_schema": {
        "type": "object",
        "properties": {
            "classifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": "0-based index in input list"},
                        "intent_code": {
                            "type": "string",
                            "enum": [e.value for e in IntentCode],
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                        "reasoning_ru": {
                            "type": "string",
                            "description": "One-sentence reason in Russian",
                        },
                    },
                    "required": ["index", "intent_code", "confidence"],
                },
            },
        },
        "required": ["classifications"],
    },
}

SYSTEM_PROMPT = """Ты классификатор поисковых запросов для русскоязычного туристического сайта.

Определи intent (намерение пользователя) из 10 категорий:

TOFU (информационные, турист изучает):
- info_dest: "что посмотреть", "достопримечательности", "куда сходить"
- info_logistics: "как добраться", "сколько ехать", "расписание"
- info_prep: "что взять", "когда ехать", "советы", "погода"

MOFU (коммерческие, турист сравнивает):
- comm_compare: "лучшие", "топ-10", "рейтинг", "или X или Y"
- comm_category: общий коммерческий БЕЗ модификатора ("экскурсии в Сочи")
- comm_modified: коммерческий С модификатором ("экскурсии из Сочи в Абхазию на 1 день")

BOFU (готов купить):
- trans_book: "забронировать", "купить", "стоимость"
- trans_brand: содержит название компании
- local_geo: геомодификатор места ОТКУДА турист ("экскурсии из Лоо", "туры Хоста")
- trust_legal: отзывы, лицензия, возврат, "безопасно ли"

ВАЖНЫЕ РАЗЛИЧИЯ:
- local_geo vs comm_category: "экскурсии Сочи" = comm_category (направление),
  "экскурсии из Лоо" = local_geo (место забора туриста)
- comm_modified vs comm_category: наличие модификатора типа "на 1 день", "с детьми", "из ... в ..."
- info_dest vs info_prep: "что посмотреть" = dest, "что взять" = prep

Верни classifications. confidence 0.5-1.0."""


def classify_ambiguous_batch(queries: list[str], known_brands: list[str] | None = None) -> list[dict]:
    """Classify a batch of ambiguous queries via LLM.

    Returns list of dicts with {intent_code, confidence, reasoning_ru}
    in same order as input. If LLM fails, returns empty list.
    """
    if not queries:
        return []

    lines = ["Запросы для классификации:"]
    for i, q in enumerate(queries):
        lines.append(f"[{i}] {q}")
    if known_brands:
        lines.append(f"\nБренд сайта: {', '.join(known_brands)}")
    lines.append("\nВерни classifications через classify_queries.")

    user_msg = "\n".join(lines)

    try:
        raw, usage = call_with_tool(
            model_tier="cheap",
            system=SYSTEM_PROMPT,
            user_message=user_msg,
            tool=CLASSIFY_TOOL,
            max_tokens=2000,
        )
        logger.info(
            "LLM classify_queries: %d items, cost=$%.5f tokens=%d+%d",
            len(queries), usage["cost_usd"], usage["input_tokens"], usage["output_tokens"],
        )
    except Exception as exc:
        logger.error("LLM classify failed: %s", exc)
        return []

    # Parse response
    classifications = raw.get("classifications", [])
    # Index → result
    by_idx: dict[int, dict] = {}
    for c in classifications:
        idx = c.get("index")
        if idx is None or idx >= len(queries):
            continue
        by_idx[idx] = {
            "intent_code": c.get("intent_code", "info_dest"),
            "confidence": float(c.get("confidence", 0.5)),
            "reasoning_ru": c.get("reasoning_ru", ""),
        }

    # Fill missing with fallback
    results = []
    for i, q in enumerate(queries):
        if i in by_idx:
            results.append(by_idx[i])
        else:
            results.append({
                "intent_code": "info_dest",
                "confidence": 0.3,
                "reasoning_ru": "LLM не вернул classification для этого запроса",
            })
    return results
