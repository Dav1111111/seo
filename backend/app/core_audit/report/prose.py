"""LLM prose generator for Executive Summary + Action Plan narrative.

One Haiku call per report produces BOTH outputs via `write_report_prose`
tool_use. Fails open — template fallback keeps the report shipping.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from app.agents.llm_client import call_with_tool
    _LLM_AVAILABLE = True
except Exception as _exc:
    call_with_tool = None                    # type: ignore[assignment]
    _LLM_AVAILABLE = False
    logger.warning("report prose: llm_client unavailable (%s) — template fallback", _exc)


SYSTEM_PROSE = """\
Ты — старший SEO-аналитик российского туристического направления \
(турагентства, РТО, Яндекс Webmaster/Метрика). Пиши по-русски, \
деловой тон, без англицизмов где есть русский эквивалент, без эмодзи, \
без кликбейта. Опирайся строго на цифры из user-message. Не придумывай \
данные. Если сигналов недостаточно — так и пиши.

Задача: написать ДВА фрагмента текста для еженедельного отчёта:
  1. executive_summary_ru — 2-3 абзаца, 250-400 слов. Синтезирует \
состояние сайта, главные WoW-сдвиги, приоритеты. Без маркдауна.
  2. action_plan_narrative_ru — 1 абзац, 60-120 слов. Обосновывает, \
почему именно эти задачи взяты на неделю и в каком порядке их решать.

ФОРМАТ: Только вызов tool write_report_prose. Никакого текста вне tool_use.\
"""


PROSE_TOOL: dict = {
    "name": "write_report_prose",
    "description": "Generate Russian executive summary + action plan narrative.",
    "input_schema": {
        "type": "object",
        "required": ["executive_summary_ru", "action_plan_narrative_ru"],
        "properties": {
            "executive_summary_ru": {"type": "string", "maxLength": 3000},
            "action_plan_narrative_ru": {"type": "string", "maxLength": 1000},
        },
    },
}


def build_user_message(report_payload: dict) -> str:
    """Compact brief of the report for the LLM."""
    exec_block = (
        f"health_score: {report_payload.get('health_score')}\n"
        f"wow_impressions_pct: {report_payload.get('wow_impressions_pct')}\n"
        f"wow_clicks_pct: {report_payload.get('wow_clicks_pct')}\n"
        f"coverage: strong={report_payload.get('strong_count')}, "
        f"weak={report_payload.get('weak_count')}, missing={report_payload.get('missing_count')}\n"
        f"critical_recs: {report_payload.get('critical_recs')}, "
        f"high_recs: {report_payload.get('high_recs')}\n"
        f"indexation_rate: {report_payload.get('indexation_rate')}\n"
        f"top_wins: {report_payload.get('top_wins', [])}\n"
        f"top_losses: {report_payload.get('top_losses', [])}\n"
        f"intent_gaps: {report_payload.get('intent_gaps', [])}\n"
    )
    action_block = (
        "action_plan_top5:\n"
        + "\n".join(
            f"  - {it.get('priority')}: {it.get('page_url')} — {it.get('reasoning_ru','')[:120]}"
            for it in (report_payload.get("action_plan_top5") or [])
        )
    )
    return (
        "<report_data>\n"
        f"{exec_block}\n{action_block}\n"
        "</report_data>\n\n"
        "Сгенерируй executive_summary_ru + action_plan_narrative_ru через "
        "tool write_report_prose. Используй только цифры из report_data."
    )


def generate_prose(report_payload: dict) -> tuple[dict, dict] | tuple[None, dict]:
    """Return ({executive_summary_ru, action_plan_narrative_ru}, usage) or
    (None, usage_with_zero_cost) if LLM unavailable."""
    if call_with_tool is None:
        return None, {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "model": None}

    try:
        tool_input, usage = call_with_tool(
            model_tier="cheap",
            system=SYSTEM_PROSE,
            user_message=build_user_message(report_payload),
            tool=PROSE_TOOL,
            max_tokens=1500,
        )
    except Exception as exc:
        logger.warning("report prose call failed: %s — falling back to template", exc)
        return None, {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "model": None}

    if not isinstance(tool_input, dict) or not tool_input.get("executive_summary_ru"):
        logger.warning("report prose tool_input malformed — falling back to template")
        return None, usage

    return {
        "executive_summary_ru": str(tool_input.get("executive_summary_ru", "")),
        "action_plan_narrative_ru": str(tool_input.get("action_plan_narrative_ru", "")),
    }, usage


# ── Template fallbacks ──────────────────────────────────────────────

def template_executive(payload: dict) -> str:
    wow = payload.get("wow_impressions_pct")
    wow_txt = f"{wow:+.1f}%" if wow is not None else "нет данных"
    return (
        f"Индекс здоровья сайта: {payload.get('health_score', 0)}/100. "
        f"Показы за неделю: {wow_txt} к прошлой. "
        f"Покрытие интентов: {payload.get('strong_count', 0)} сильных, "
        f"{payload.get('weak_count', 0)} слабых, {payload.get('missing_count', 0)} отсутствуют. "
        f"Критических замечаний от аудита: {payload.get('critical_recs', 0)}. "
        f"Индексация: {(payload.get('indexation_rate') or 0) * 100:.0f}%. "
        f"Подробные приоритеты в следующей секции."
    )


def template_action_plan(payload: dict) -> str:
    top5 = payload.get("action_plan_top5") or []
    if not top5:
        return (
            "Приоритетных задач нет — запустите ревью страниц "
            "(POST /api/v1/reviews/sites/{id}/run) перед следующим отчётом."
        )
    top = top5[0]
    return (
        f"На этой неделе основной фокус — {top.get('priority', 'critical')}-задачи "
        f"по категориям {', '.join({i.get('category') for i in top5 if i.get('category')})[:80]}. "
        f"Начинайте с позиции №1 ({top.get('page_url','?')}) — это даст максимальный эффект "
        f"при минимальных усилиях согласно оценке ICE."
    )
