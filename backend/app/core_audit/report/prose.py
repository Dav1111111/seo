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


# ── Phase E: Diagnostic prose ───────────────────────────────────────

SYSTEM_DIAGNOSTIC = """\
Ты — старший SEO-аналитик российского туристического направления. \
Твоя задача — написать ОДНУ корневую проблему сайта (root_problem_ru) \
и связанные с ней симптомы + первоочередные действия.

ПРАВИЛА:
  1. root_problem_ru — 2-3 предложения. Одна корневая причина, \
не список. Обязательно цитируй цифры из сигналов входа.
  2. supporting_symptoms_ru — 3-5 пунктов по 1 предложению. ВСЕ \
симптомы должны логически вытекать из корневой проблемы, а не быть \
независимыми жалобами. Если не вытекают — не включай.
  3. recommended_first_actions_ru — 3-5 конкретных действий, которые \
бьют в корневую проблему (а не лечат симптомы по-отдельности). \
Каждое действие — один императивный глагол.

КЛАССИФИКАЦИЯ:
  * classification == "brand_bias": корневая проблема — сайт виден только \
по бренду, небрендовый спрос не закрыт. Улучшения title/H1 и прочие \
косметические правки НЕ должны попадать в actions.
  * classification == "weak_technical": сайт технически пуст (мало \
страниц). Actions — создать базовые посадочные.
  * classification == "low_coverage": основной спрос небрендовый, но \
покрытие по нему слабое. Actions — усилить/создать посадочные под \
топ-missing кластеры.
  * classification == "none": серьёзных системных проблем нет; \
root_problem_ru лаконично констатирует это, actions — тактические \
улучшения.
  * classification == "insufficient_data": пиши кратко, что сигналов \
мало и рекомендуешь запустить недостающие процессы.

ФОРМАТ: только вызов tool write_diagnostic_prose, без другого текста. \
Весь текст только на русском языке, без англицизмов, без эмодзи.\
"""


DIAGNOSTIC_TOOL: dict = {
    "name": "write_diagnostic_prose",
    "description": "Generate a single root-cause problem, symptoms, and actions for a site diagnostic.",
    "input_schema": {
        "type": "object",
        "required": [
            "root_problem_ru",
            "supporting_symptoms_ru",
            "recommended_first_actions_ru",
        ],
        "properties": {
            "root_problem_ru": {"type": "string", "maxLength": 1500},
            "supporting_symptoms_ru": {
                "type": "array",
                "items": {"type": "string", "maxLength": 400},
                "maxItems": 5,
            },
            "recommended_first_actions_ru": {
                "type": "array",
                "items": {"type": "string", "maxLength": 400},
                "maxItems": 5,
            },
        },
    },
}


def _build_diagnostic_user_message(payload: dict) -> str:
    sig = payload.get("signals") or {}
    brand = payload.get("brand_demand") or {}
    non_brand = payload.get("non_brand_demand") or {}
    missing = payload.get("missing_target_clusters") or []

    missing_lines = "\n".join(
        f"  - {m.get('name_ru','?')} "
        f"(релевантность {m.get('business_relevance',0):.2f}, "
        f"покрытие {m.get('coverage_score') if m.get('coverage_score') is not None else 'нет'})"
        for m in missing[:5]
    ) or "  (нет топ-пробелов)"

    return (
        "<diagnostic_input>\n"
        f"classification: {payload.get('classification','insufficient_data')}\n"
        f"signals:\n"
        f"  blind_spot_score: {sig.get('blind_spot_score')}\n"
        f"  non_brand_coverage_ratio: {sig.get('non_brand_coverage_ratio')}\n"
        f"  brand_imp_ratio: {sig.get('brand_imp_ratio')}\n"
        f"  pages_total: {sig.get('pages_total')}\n"
        f"  trigger_brand_bias: {sig.get('trigger_brand_bias')}\n"
        f"brand_demand: clusters={brand.get('clusters',0)}, "
        f"impressions={brand.get('observed_impressions',0)}\n"
        f"non_brand_demand: clusters={non_brand.get('clusters',0)}, "
        f"impressions={non_brand.get('observed_impressions',0)}, "
        f"covered={non_brand.get('covered',0)}, "
        f"missing={non_brand.get('missing',0)}, "
        f"coverage_ratio={non_brand.get('coverage_ratio',0)}\n"
        f"top_missing_non_brand:\n{missing_lines}\n"
        "</diagnostic_input>\n\n"
        "Сгенерируй корневую проблему + симптомы + действия через "
        "tool write_diagnostic_prose. Используй только цифры из входа."
    )


def generate_diagnostic_prose(
    payload: dict,
) -> tuple[dict, dict] | tuple[None, dict]:
    """Run a single Haiku call; return ({root_problem_ru, supporting_symptoms_ru,
    recommended_first_actions_ru}, usage) on success, (None, usage) on any
    failure — caller falls back to template_diagnostic.
    """
    if call_with_tool is None:
        return None, {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "model": None}

    try:
        tool_input, usage = call_with_tool(
            model_tier="cheap",
            system=SYSTEM_DIAGNOSTIC,
            user_message=_build_diagnostic_user_message(payload),
            tool=DIAGNOSTIC_TOOL,
            max_tokens=1200,
        )
    except Exception as exc:
        logger.warning("diagnostic prose call failed: %s — template fallback", exc)
        return None, {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "model": None}

    if not isinstance(tool_input, dict) or not tool_input.get("root_problem_ru"):
        logger.warning("diagnostic prose tool_input malformed — template fallback")
        return None, usage

    return {
        "root_problem_ru": str(tool_input.get("root_problem_ru", "")),
        "supporting_symptoms_ru": [
            str(s) for s in (tool_input.get("supporting_symptoms_ru") or [])
        ],
        "recommended_first_actions_ru": [
            str(a) for a in (tool_input.get("recommended_first_actions_ru") or [])
        ],
    }, usage


def template_diagnostic(payload: dict) -> dict:
    """Deterministic fallback when the LLM is unavailable. Interpolates
    signal numbers into a canned template per classification.
    """
    cls = payload.get("classification") or "insufficient_data"
    sig = payload.get("signals") or {}
    brand = payload.get("brand_demand") or {}
    non_brand = payload.get("non_brand_demand") or {}

    bs = sig.get("blind_spot_score", 0) or 0
    nbc = sig.get("non_brand_coverage_ratio", 0) or 0
    bi = sig.get("brand_imp_ratio", 0) or 0
    pt = sig.get("pages_total", 0) or 0

    if cls == "brand_bias":
        root = (
            f"Сайт виден преимущественно по бренду: {bi * 100:.0f}% показов приходится "
            f"на брендовые запросы, а небрендовое покрытие — {nbc * 100:.0f}% "
            f"при blind-spot индексе {bs:.2f}. "
            f"Технически сайт не пустой ({pt} страниц), значит проблема не в количестве "
            f"URL, а в том, что ни одна из них не ранжируется по целевому спросу."
        )
        symptoms = [
            f"Небрендовое покрытие только {non_brand.get('covered', 0)} из "
            f"{non_brand.get('clusters', 0)} целевых кластеров.",
            f"Брендовые показы — {brand.get('observed_impressions', 0)}, "
            f"небрендовые — {non_brand.get('observed_impressions', 0)}.",
            f"Blind-spot индекс {bs:.2f} означает, что сайт получает существенно "
            f"меньше небрендовых показов, чем ожидается от рынка.",
        ]
        actions = [
            "Создать посадочные под топ-5 небрендовых целевых кластеров с высокой релевантностью.",
            "Проверить, что существующие страницы не каннибализируют друг друга по брендовым запросам.",
            "Добавить в меню и внутреннюю перелинковку ссылки на небрендовые разделы.",
        ]
    elif cls == "weak_technical":
        root = (
            f"Сайт технически слишком мал: всего {pt} страниц — этого недостаточно, "
            f"чтобы закрыть целевой спрос, даже если все существующие URL сильные."
        )
        symptoms = [
            f"Всего {pt} страниц на сайте.",
            f"Небрендовое покрытие {nbc * 100:.0f}% — большинство целевых интентов без посадочных.",
        ]
        actions = [
            "Развернуть базовую структуру посадочных под core-кластеры.",
            "Сделать выгрузку sitemap.xml и убедиться, что все существующие URL индексируются.",
        ]
    elif cls == "low_coverage":
        root = (
            f"Покрытие небрендового спроса слабое: {nbc * 100:.0f}% при blind-spot "
            f"индексе {bs:.2f}. Основная точка роста — усилить ранжирование по "
            f"небрендовым целевым кластерам."
        )
        symptoms = [
            f"Покрыто {non_brand.get('covered', 0)} из {non_brand.get('clusters', 0)} небрендовых кластеров.",
            f"Blind-spot индекс {bs:.2f}.",
        ]
        actions = [
            "Выбрать топ-10 missing-кластеров по business_relevance и назначить им посадочные.",
            "Для weak-кластеров (coverage 0.4–0.6) — усилить контент и внутренние ссылки.",
        ]
    elif cls == "none":
        root = (
            "Системных корневых проблем не обнаружено: покрытие и видимость в норме. "
            "Дальнейшая работа — тактические улучшения по приоритетному списку."
        )
        symptoms = []
        actions = [
            "Продолжайте работу по приоритетному плану.",
            "Отслеживайте новые missing-кластеры в следующих отчётах.",
        ]
    else:  # insufficient_data
        root = (
            "Сигналов пока недостаточно для точной классификации корневой проблемы. "
            "Построение целевого спроса и сбор 14 дней Webmaster-метрик позволит "
            "запустить диагностику на полной базе."
        )
        symptoms = []
        actions = [
            "Убедитесь, что целевой спрос построен (demand-map expand).",
            "Дождитесь накопления 14 дней данных Яндекс.Webmaster.",
        ]

    return {
        "root_problem_ru": root,
        "supporting_symptoms_ru": symptoms,
        "recommended_first_actions_ru": actions,
    }


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
