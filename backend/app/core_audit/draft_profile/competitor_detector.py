"""LLM proposal for competitor brands (Phase F).

One Haiku call per site. Fail-open: any error (network, JSON, schema)
returns an empty list and the caller logs a warning. Estimated cost
~$0.002 per site with prompt caching.

Input signal: observed queries where is_branded=True but that do NOT
match the site's own display_name / brand tokens, plus a slice of the
top short observed queries. The LLM filters to those it believes are
real competitor brands with high confidence.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Sequence

from app.core_audit.draft_profile.dto import CompetitorBrand


log = logging.getLogger(__name__)


MAX_INPUT_QUERIES = 40
MAX_RESULTS = 15


SYSTEM_PROMPT = (
    "Ты SEO-аналитик. Из списка брендовых запросов выдели бренды "
    "КОНКУРЕНТОВ (не сам сайт). Верни только те, в которых уверен. "
    "Не включай родовые слова (туры, экскурсии, отель) и не "
    "возвращай собственный бренд сайта."
)


PROPOSE_TOOL: dict[str, Any] = {
    "name": "propose_competitor_brands",
    "description": (
        "Предложить бренды конкурентов, извлечённые из брендовых "
        "запросов. Каждый бренд сопровождается оценкой уверенности "
        "0..1 (confidence_ru)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "competitor_brands": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "confidence_ru": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["competitor_brands"],
    },
}


def _build_user_message(
    site_name: str,
    site_domain: str,
    queries: Sequence[str],
) -> str:
    sample = list(queries)[:MAX_INPUT_QUERIES]
    lines = [
        f"Сайт: {site_name or '(без названия)'} ({site_domain or '—'})",
        "",
        "Список наблюдаемых запросов (в том числе брендовых):",
    ]
    for q in sample:
        lines.append(f"  - {q}")
    lines.extend([
        "",
        "Задача: выделить бренды КОНКУРЕНТОВ (без учёта собственного "
        "бренда сайта). Верни результат через tool "
        "propose_competitor_brands с оценкой confidence_ru 0..1.",
    ])
    return "\n".join(lines)


def propose_competitor_brands(
    site_name: str,
    site_domain: str,
    candidate_queries: Iterable[str],
    *,
    caller: Any = None,
) -> list[CompetitorBrand]:
    """Return a list of CompetitorBrand, or [] on any error.

    Parameters
    ----------
    site_name, site_domain:
        Passed to the LLM so it can exclude the site's own brand.
    candidate_queries:
        Iterable of observed brand-like / short queries.
    caller:
        Optional injected callable matching `call_with_tool(...)`.
        Tests pass a fake; production lazy-imports from
        `app.agents.llm_client`.
    """
    queries = [q for q in (candidate_queries or []) if q]
    if not queries:
        return []

    if caller is None:
        try:
            from app.agents.llm_client import call_with_tool as caller_fn
        except Exception as exc:  # noqa: BLE001 — fail-open
            log.warning("draft_profile.competitor.llm_import_failed err=%s", exc)
            return []
        caller = caller_fn

    user_message = _build_user_message(site_name, site_domain, queries)

    try:
        tool_input, _usage = caller(
            model_tier="cheap",
            system=SYSTEM_PROMPT,
            user_message=user_message,
            tool=PROPOSE_TOOL,
            max_tokens=1024,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.warning("draft_profile.competitor.llm_call_failed err=%s", exc)
        return []

    if not isinstance(tool_input, dict):
        return []

    raw = tool_input.get("competitor_brands") or []
    if not isinstance(raw, list):
        return []

    own_tokens = {
        (site_name or "").strip().lower(),
        (site_domain or "").strip().lower(),
    } - {""}

    out: list[CompetitorBrand] = []
    seen: set[str] = set()
    for item in raw[: MAX_RESULTS * 2]:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        nl = name.lower()
        if any(t and t in nl for t in own_tokens):
            continue
        if nl in seen:
            continue
        seen.add(nl)
        try:
            conf = float(item.get("confidence_ru", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        out.append(CompetitorBrand(name=name, confidence_ru=conf))
        if len(out) >= MAX_RESULTS:
            break
    return out


__all__ = [
    "SYSTEM_PROMPT",
    "PROPOSE_TOOL",
    "propose_competitor_brands",
]
