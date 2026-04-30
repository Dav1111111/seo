"""Free chat — Phase C.

Separate from `brain.chat` (which is per-action). Owner asks anything
about the site — «почему Webmaster такое показывает», «что такое
индексация», «с какого тура начать», «что мне приоритетнее всего».

The LLM gets a much wider context than per-action chat:
  - business understanding (narrative + observed facts)
  - target_config (primary product, services, regions)
  - the full BrainSnapshot (all 5 module sections)
  - the full plan with all actions in it (so the LLM knows what
    has already been recommended; it can REFER to plan items but
    must NOT invent new ones)
  - the conversation history

Hard rules (system prompt enforces): no fabrication, only data shown
in CONTEXT, refer to the plan / module instead of inventing answers,
trust owner overrides, plain Russian.
"""

from __future__ import annotations

from typing import Any

from app.agents.llm_client import call_plain
from app.core_audit.brain.rules import Plan
from app.core_audit.brain.snapshot import BrainSnapshot


# ── Constants ────────────────────────────────────────────────────────


MAX_HISTORY_MESSAGES = 16
MAX_REPLY_TOKENS = 1200
MAX_USER_MESSAGE_CHARS = 2000
NARRATIVE_TRIM = 1500          # trim narrative_ru at this many chars
FACTS_LIMIT = 8                # how many observed_facts to include
HARMFUL_EXAMPLES_LIMIT = 8     # spam / disputed query examples
URL_EXAMPLES_LIMIT = 5         # not-indexed / unreviewed URL samples


# ── System prompt ────────────────────────────────────────────────────


SYSTEM_PROMPT = """\
Ты — внутренний помощник в SEO-инструменте «Yandex Growth Tower».
Владелец сайта может задать тебе любой вопрос про свой сайт. Твоя
задача — отвечать опираясь ТОЛЬКО на данные в блоке КОНТЕКСТ ниже.

Тон:
  - Простым языком, без жаргона. На «ты», без формальностей.
  - Коротко: 2-5 предложений типичный ответ. Длинные «портянки»
    делай только когда владелец явно просит «расскажи подробно».
  - Не будь чрезмерно бодрым (никаких «Отличный вопрос!»).
  - Не используй маркетинговый язык.

Жёсткие правила:

  1. НЕ ВЫДУМЫВАЙ. Все факты — только из КОНТЕКСТА. Никаких чисел,
     URL, запросов, услуг или дат, которых там нет. Если вопрос
     не покрыт данными — скажи: «Этого я в данных не вижу. Проверить
     можно в [модуль X]» или «Чтобы узнать — запусти [Y]».

  2. ССЫЛАЙСЯ НА ПЛАН. Если владелец спрашивает «что мне делать»,
     «с чего начать», «что приоритетнее» — отсылай к УЖЕ ВЫДАННЫМ
     действиям из секции ПЛАН в КОНТЕКСТЕ, по их title. Не создавай
     новые рекомендации помимо плана.

  3. ОБЪЯСНЯЙ ТЕРМИНЫ. Это твоя единственная свобода — переводить
     с системного на человеческий. «Индексация», «спам в выдаче»,
     «канонический URL», «sitemap», «Webmaster» — объясняй на пальцах.

  4. УВАЖАЙ ВЛАДЕЛЬЦА. Если он говорит «нет, "прокат сочи" — это мой
     запрос», поверь: «понял, тогда поправь руками — открой запрос и
     отметь как мой». Не спорь и не настаивай на классификации.

  5. ЦИТИРУЙ КОНКРЕТНОЕ. «"джинсы багги"» — а не «один из спам-запросов».
     URL — полный, не сокращённый.

  6. КОГДА ОБСУЖДАЕМ ЯНДЕКС / WEBMASTER. Опирайся на реальные данные:
     если в КОНТЕКСТЕ написано «исключено: 0», то говорить «Яндекс
     наверняка многое исключил» нельзя — это противоречит данным.

  7. КОГДА НЕ ЗНАЕШЬ — скажи это прямо. «В данных нет ответа на этот
     вопрос. Проверить можно в [модуль]» — нормальный ответ. Лучше
     честное «не знаю», чем правдоподобная выдумка.

Запрещено:
  - Гарантировать рост позиций / трафика. Только «вероятно по данным».
  - Давать общие SEO-советы из обучения, не привязанные к КОНТЕКСТУ.
  - Писать длинные планы или «вот тебе 10 шагов» сверх плана из
    КОНТЕКСТА.
"""


# ── Context builders ─────────────────────────────────────────────────


def _format_business_block(
    *, domain: str, target_config: dict[str, Any], understanding: dict[str, Any],
) -> str:
    parts: list[str] = [f"САЙТ: {domain}"]

    primary_product = (target_config or {}).get("primary_product")
    services = (target_config or {}).get("services") or []
    secondary = (target_config or {}).get("secondary_products") or []
    geo_primary = (target_config or {}).get("geo_primary") or []
    geo_secondary = (target_config or {}).get("geo_secondary") or []
    if primary_product:
        parts.append(f"  основной продукт: {primary_product}")
    if isinstance(services, list) and services:
        parts.append(f"  услуги: {', '.join(map(str, services))}")
    if isinstance(secondary, list) and secondary:
        parts.append(
            f"  дополнительные продукты: {', '.join(map(str, secondary))}",
        )
    if isinstance(geo_primary, list) and geo_primary:
        parts.append(
            f"  основные регионы: {', '.join(map(str, geo_primary))}",
        )
    if isinstance(geo_secondary, list) and geo_secondary:
        parts.append(
            f"  второстепенные регионы: {', '.join(map(str, geo_secondary))}",
        )

    narrative = (understanding or {}).get("narrative_ru") or ""
    narrative = narrative.strip()
    if narrative:
        if len(narrative) > NARRATIVE_TRIM:
            narrative = narrative[:NARRATIVE_TRIM] + " […]"
        parts.append("")
        parts.append("ОПИСАНИЕ БИЗНЕСА:")
        parts.append(narrative)

    facts = (understanding or {}).get("observed_facts") or []
    if isinstance(facts, list) and facts:
        rendered: list[str] = []
        for f in facts[:FACTS_LIMIT]:
            if isinstance(f, dict):
                txt = (f.get("fact") or "").strip()
                if not txt:
                    continue
                ref = (f.get("page_ref") or "").strip()
                rendered.append(f"  - {txt}" + (f"  [{ref}]" if ref else ""))
            elif isinstance(f, str):
                rendered.append(f"  - {f.strip()}")
        if rendered:
            parts.append("")
            parts.append(
                "ЧТО МЫ САМИ УВИДЕЛИ НА САЙТЕ (объективные факты):",
            )
            parts.extend(rendered)

    return "\n".join(parts)


def _format_full_snapshot(snap: BrainSnapshot) -> str:
    """Whole snapshot — all 5 sections — in compact bullet form. The
    free chat doesn't slice; the LLM may need any of these to answer
    a free-form question."""
    parts: list[str] = ["СОСТОЯНИЕ САЙТА (на момент запроса):"]

    # Indexation
    idx = snap.indexation
    parts.append("  Индексация:")
    parts.append(f"    всего страниц: {idx.pages_total}")
    parts.append(f"    в индексе Яндекса: {idx.pages_in_index}")
    parts.append(f"    исключено: {idx.pages_excluded}")
    parts.append(f"    статус неизвестен: {idx.pages_unknown}")
    if idx.sample_not_indexed_urls:
        parts.append("    примеры не в индексе:")
        for u in idx.sample_not_indexed_urls[:URL_EXAMPLES_LIMIT]:
            parts.append(f"      - {u}")
    if idx.sample_excluded:
        parts.append("    примеры исключённых:")
        for ex in idx.sample_excluded[:URL_EXAMPLES_LIMIT]:
            url = ex.get("url", "")
            reason = ex.get("reason", "")
            parts.append(f"      - {url} (причина: {reason or '—'})")

    # Queries
    q = snap.queries
    parts.append("  Запросы:")
    parts.append(f"    всего: {q.total}")
    parts.append(
        f"    мои: {q.own}, смежные: {q.adjacent}, "
        f"спорные: {q.disputed}, спам: {q.spam}, "
        f"не разобраны: {q.unclassified}",
    )
    if q.with_volume:
        parts.append(f"    с известным объёмом Wordstat: {q.with_volume}")
    if q.sample_own:
        parts.append("    примеры «моих»:")
        for w in q.sample_own[:5]:
            parts.append(f"      - «{w}»")
    if q.sample_harmful:
        parts.append("    примеры вредных:")
        for h in q.sample_harmful[:HARMFUL_EXAMPLES_LIMIT]:
            qt = h.get("query_text", "") if isinstance(h, dict) else str(h)
            rel = h.get("relevance", "") if isinstance(h, dict) else ""
            reason = h.get("reason_ru", "") if isinstance(h, dict) else ""
            line = f"      - «{qt}» [{rel}]"
            if reason:
                line += f" — {reason}"
            parts.append(line)

    # Review
    r = snap.review
    parts.append("  Ревью страниц:")
    parts.append(f"    с ревью: {r.pages_with_review}")
    parts.append(f"    без ревью: {r.pages_without_review}")
    parts.append(
        f"    рекомендаций ждут решения: {r.recs_pending} "
        f"(из них высокого приоритета: {r.recs_high_priority_pending})",
    )
    if r.sample_unreviewed_urls:
        parts.append("    примеры без ревью:")
        for u in r.sample_unreviewed_urls[:URL_EXAMPLES_LIMIT]:
            parts.append(f"      - {u}")

    # Missing landings
    m = snap.missing_landings
    parts.append("  Услуги без отдельной страницы:")
    parts.append(
        f"    всего: {m.total} (важных: {m.high_priority}, "
        f"средних: {m.medium_priority}, несрочных: {m.low_priority})",
    )
    if m.items:
        parts.append("    примеры:")
        for it in m.items[:5]:
            name = (it.get("service_name") or "").strip()
            prio = it.get("priority", "")
            quote = (it.get("evidence_quote") or "").strip()
            line = f"      - {name} [{prio}]"
            if quote:
                line += f" — цитата из описания: «{quote}»"
            parts.append(line)

    # Outcomes
    o = snap.outcomes
    parts.append("  Применённые правки и замеры:")
    parts.append(f"    всего применено: {o.applied_total}")
    parts.append(f"    за последние 14 дней: {o.applied_last_14d}")
    parts.append(f"    ждут замера через 14 дней: {o.pending_followup}")

    return "\n".join(parts)


def _format_plan_block(plan: Plan) -> str:
    """The current plan, by title + severity. The LLM should refer
    owners to these rather than invent new actions."""
    if not plan.actions:
        return (
            "ТЕКУЩИЙ ПЛАН: пусто (срочных действий не найдено или модули "
            "пока не запущены)."
        )
    parts = ["ТЕКУЩИЙ ПЛАН (направляй к этим действиям, не выдумывай новые):"]
    for a in plan.actions:
        parts.append(
            f"  - [{a.severity}] {a.title} → {a.link_to}",
        )
    return "\n".join(parts)


# ── Public API ───────────────────────────────────────────────────────


def build_user_message(
    *,
    domain: str,
    target_config: dict[str, Any],
    understanding: dict[str, Any],
    snap: BrainSnapshot,
    plan: Plan,
    history: list[dict[str, str]],
    new_message: str,
) -> str:
    """Compose the single user-message string for `call_plain`."""
    # Strategic focus, if owner has set one, takes the top-of-prompt
    # slot — every answer must be subordinated to it (the prompt
    # itself spells out the rule).
    from app.core_audit.strategic_focus import (
        from_target_config,
        render_for_prompt,
    )
    focus = from_target_config(target_config or {})

    blocks = [
        "КОНТЕКСТ — это всё, что ты знаешь про сайт. Все ответы должны "
        "опираться только на этот блок. Если факта тут нет — его нет.",
        "",
        render_for_prompt(focus),
        "",
        _format_business_block(
            domain=domain,
            target_config=target_config,
            understanding=understanding,
        ),
        "",
        _format_full_snapshot(snap),
        "",
        _format_plan_block(plan),
        "",
    ]
    if history:
        blocks.append("ИСТОРИЯ РАЗГОВОРА:")
        for turn in history[-MAX_HISTORY_MESSAGES:]:
            role = turn.get("role") or "user"
            content = (turn.get("content") or "").strip()
            if not content:
                continue
            tag = "ВЛАДЕЛЕЦ" if role == "user" else "ТЫ"
            blocks.append(f"{tag}: {content}")
        blocks.append("")
    blocks.append(f"ВЛАДЕЛЕЦ СЕЙЧАС СПРАШИВАЕТ: {new_message.strip()}")
    blocks.append("")
    blocks.append(
        "Ответь по существу, опираясь только на КОНТЕКСТ. "
        "Если данных не хватает — скажи об этом честно.",
    )
    return "\n".join(blocks)


def free_chat(
    *,
    domain: str,
    target_config: dict[str, Any],
    understanding: dict[str, Any],
    snap: BrainSnapshot,
    plan: Plan,
    history: list[dict[str, str]],
    new_message: str,
) -> dict[str, Any]:
    """One-turn chat. Returns `{reply, cost_usd, model, ...}`."""
    new_message = (new_message or "").strip()
    if not new_message:
        raise ValueError("empty message")
    if len(new_message) > MAX_USER_MESSAGE_CHARS:
        new_message = new_message[:MAX_USER_MESSAGE_CHARS] + " […обрезано]"

    user_msg = build_user_message(
        domain=domain,
        target_config=target_config,
        understanding=understanding,
        snap=snap,
        plan=plan,
        history=history or [],
        new_message=new_message,
    )

    reply, usage = call_plain(
        model_tier="cheap",
        system=SYSTEM_PROMPT,
        user_message=user_msg,
        max_tokens=MAX_REPLY_TOKENS,
    )

    return {
        "reply": (reply or "").strip(),
        "cost_usd": float(usage.get("cost_usd") or 0.0),
        "model": usage.get("model") or "",
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
    }


__all__ = [
    "MAX_HISTORY_MESSAGES",
    "MAX_REPLY_TOKENS",
    "MAX_USER_MESSAGE_CHARS",
    "SYSTEM_PROMPT",
    "build_user_message",
    "free_chat",
]
