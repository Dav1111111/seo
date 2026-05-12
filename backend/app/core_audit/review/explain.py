"""Translate a single page-review recommendation into «человеческий русский».

The Python checks + the enricher speak SEO. They describe «незаполненный
meta description», «отсутствие Schema.org/Product», «дубль canonical» —
все термины, которые владелец туристического агентства даже не обязан
понимать. This module produces a 2-3 sentence plain-Russian explanation
of *what to do and why*, keyed off the rec's category + before/after +
reasoning_ru.

Design notes:

  * **One LLM call per rec.** Cheap tier (Haiku / gpt-5.4-mini through
    the provider routing in `llm_client`). Schema/JSON-LD/canonical
    terms get paraphrased; numeric promises are forbidden — owners get
    burned by "+30% trafика" hand-waving, so we tell the model to
    refuse those.
  * **No DB access.** This is a pure function: rec in, (text, usage)
    out. The caller persists `plain_ru` and writes to `agent_runs` as
    needed.
  * **Strict 600-char output cap.** The chat UI inlines this text into
    a tooltip / drawer — anything longer would defeat the «коротко и
    без жаргона» promise.
"""

from __future__ import annotations

from typing import Any

from app.agents.llm_client import call_with_tool
from app.core_audit.review.models import PageReviewRecommendation


_SYSTEM_PROMPT = (
    "Ты переводишь техническую SEO-рекомендацию на простой человеческий "
    "русский. 2-3 предложения. Без жаргона (Schema, JSON-LD, canonical, "
    "hreflang, sitemap — паразразируй как «невидимый ярлык для "
    "поисковика»). Объясни: что владелец/посетитель увидит после правки, "
    "почему это важно. Не выдумывай цифр процент-роста."
)


_TOOL: dict[str, Any] = {
    "name": "translate",
    "description": (
        "Верни одно поле plain_ru — простое объяснение рекомендации "
        "для владельца сайта, который не знает SEO-терминологии."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "plain_ru": {
                "type": "string",
                "maxLength": 600,
                "description": (
                    "2-3 предложения на простом русском. Без терминов "
                    "Schema/JSON-LD/canonical/hreflang/sitemap. Без "
                    "выдуманных процентов роста."
                ),
            },
        },
        "required": ["plain_ru"],
    },
}


def _serialize_rec(rec: PageReviewRecommendation | dict[str, Any]) -> str:
    """Pack the rec into a single user-message payload.

    We hand the model the same four fields owners see in the UI:
    category (the technical bucket), before_text / after_text (the
    actual edit), and reasoning_ru (the Python check's justification).
    Anything beyond that — page URL, site context, priority score —
    would invite the model to hallucinate domain claims it can't back
    up from this payload alone.

    Accepts both ORM row and plain dict — see translate_to_plain_ru.
    """
    if isinstance(rec, dict):
        category = rec.get("category") or "—"
        reasoning = rec.get("reasoning_ru") or "—"
        before = rec.get("before_text") or ""
        after = rec.get("after_text") or ""
    else:
        category = rec.category
        reasoning = rec.reasoning_ru or "—"
        before = rec.before_text or ""
        after = rec.after_text or ""

    parts: list[str] = [
        f"Категория: {category}",
        f"Обоснование от системы: {reasoning}",
    ]
    if before:
        # Cap to 1500 chars per field — keeps the call cheap and a
        # huge before/after never dominates the context.
        parts.append(f"Сейчас на странице: «{before[:1500]}»")
    if after:
        parts.append(f"Предлагаемая правка: «{after[:1500]}»")
    parts.append(
        "Объясни эту рекомендацию владельцу турагентства на простом "
        "русском (2-3 предложения)."
    )
    return "\n".join(parts)


def translate_to_plain_ru(
    rec: PageReviewRecommendation | dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Run one cheap-tier LLM call to produce `plain_ru` for `rec`.

    Returns ``(plain_ru_text, usage_stats)``. Usage stats follow the
    shape returned by :func:`app.agents.llm_client.call_with_tool` —
    in particular ``cost_usd`` is present so the caller can roll it
    up into `agent_runs` / a backfill total.

    Accepts either the ORM row (on-demand endpoint, same event loop)
    or a plain dict with `category` / `reasoning_ru` / `before_text` /
    `after_text` keys (backfill — strings are pre-extracted so the
    async-engine attributes aren't touched from a worker thread).

    Raises whatever `call_with_tool` raises (network errors, balance
    exhaustion after fallback, etc.) — the on-demand endpoint catches
    these and surfaces 502; the backfill records the rec id in an
    `errors` list and moves on.
    """
    user_msg = _serialize_rec(rec)
    tool_input, usage_stats = call_with_tool(
        model_tier="cheap",
        system=_SYSTEM_PROMPT,
        user_message=user_msg,
        tool=_TOOL,
        max_tokens=800,
    )
    plain_ru = (tool_input or {}).get("plain_ru") or ""
    plain_ru = plain_ru.strip()
    # Defensive truncation — the schema sets maxLength=600 but the
    # provider may not enforce it on every backend.
    if len(plain_ru) > 600:
        plain_ru = plain_ru[:600].rstrip()
    return plain_ru, usage_stats


__all__ = ["translate_to_plain_ru"]
