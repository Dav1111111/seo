"""Missing landing pages detector — Studio v2 etap 6.

Owner-facing question: «Я говорю, что у меня есть услуга X (или регион
Y, или формат Z), но Яндекс/Гугл не могут на это ответить, потому что
у меня нет под это отдельной страницы». Классический пример — тур,
который открывается попапом на главной без отдельного URL.

Pipeline (single LLM call per site):

  1. Build INPUT.
       - business signal:    sites.understanding.narrative_ru
                             + observed_facts[].fact (joined)
                             + target_config.services
                             + target_config.secondary_products
                             + target_config.geo_primary/secondary
       - existing pages:     [{path, title, h1, meta, snippet}, …]

  2. Ask Haiku (tool_use, JSON schema). For every gap it must return:
       - service_name        короткая фраза-имя услуги/направления
       - evidence_quote      ТОЧНАЯ цитата из business signal
       - closest_existing_url или null
       - suggested_url_path  /tours/crimea-2026 и т.п.
       - why_it_matters_ru   1-2 предложения для владельца
       - priority            high|medium|low

  3. POST-FILTER (anti-hallucination, KEY guarantee):
       drop any item whose `evidence_quote`, after light normalisation
       (lowercase, collapsed whitespace, stripped punctuation), is NOT
       a substring of the joined business signal. If the LLM invented
       something, this filter never lets it reach the owner.

  4. Sort by priority, cap at MAX_ITEMS.

The result is a JSON-safe list of dicts. The Celery task that calls
this writes them into `sites.target_config.missing_landings` without
disturbing the existing `growth_opportunities` slot owned by the
competitor module.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable

from app.agents.llm_client import call_with_tool


log = logging.getLogger(__name__)


MAX_ITEMS = 12
SNIPPET_CHARS = 400  # per page, fed to LLM


# ── Input shaping ────────────────────────────────────────────────────


def _stringify_geo(target_config: dict[str, Any]) -> str:
    geo_primary = target_config.get("geo_primary") or []
    geo_secondary = target_config.get("geo_secondary") or []
    parts: list[str] = []
    if isinstance(geo_primary, list) and geo_primary:
        parts.append("основные регионы: " + ", ".join(str(g) for g in geo_primary))
    if isinstance(geo_secondary, list) and geo_secondary:
        parts.append(
            "второстепенные регионы: " + ", ".join(str(g) for g in geo_secondary)
        )
    return "; ".join(parts) or "—"


def _stringify_services(target_config: dict[str, Any]) -> str:
    primary_product = target_config.get("primary_product") or "—"
    services = target_config.get("services") or []
    secondary = target_config.get("secondary_products") or []
    parts: list[str] = [f"основной продукт: {primary_product}"]
    if isinstance(services, list) and services:
        parts.append("услуги: " + ", ".join(str(s) for s in services))
    if isinstance(secondary, list) and secondary:
        parts.append("дополнительные продукты: " + ", ".join(str(s) for s in secondary))
    return "; ".join(parts)


def build_business_signal(
    *, understanding: dict[str, Any] | None, target_config: dict[str, Any] | None,
) -> str:
    """Single string passed to the LLM as «what this business does»."""
    understanding = understanding or {}
    target_config = target_config or {}

    narrative = (understanding.get("narrative_ru") or "").strip()
    facts = understanding.get("observed_facts") or []
    fact_lines: list[str] = []
    for f in facts:
        if isinstance(f, dict):
            txt = (f.get("fact") or "").strip()
            if txt:
                page_ref = (f.get("page_ref") or "").strip()
                fact_lines.append(
                    f"- {txt}" + (f"  [{page_ref}]" if page_ref else "")
                )
        elif isinstance(f, str):
            fact_lines.append(f"- {f.strip()}")

    facts_block = "\n".join(fact_lines) if fact_lines else "—"

    return (
        f"ОПИСАНИЕ БИЗНЕСА:\n{narrative or '—'}\n\n"
        f"СЛУЖЕБНЫЕ ПОЛЯ:\n  {_stringify_services(target_config)}\n  "
        f"{_stringify_geo(target_config)}\n\n"
        f"НАБЛЮДАЕМЫЕ ФАКТЫ (что система реально увидела на сайте):\n"
        f"{facts_block}"
    )


def _page_to_card(page: dict[str, Any]) -> str:
    path = page.get("path") or page.get("url") or "—"
    title = (page.get("title") or "").strip()
    h1 = (page.get("h1") or "").strip()
    meta = (page.get("meta_description") or "").strip()
    snippet = ((page.get("content_snippet") or page.get("content") or "")[:SNIPPET_CHARS]).strip()
    bits = [f"  url: {path}"]
    if title:
        bits.append(f"  title: {title}")
    if h1:
        bits.append(f"  h1: {h1}")
    if meta:
        bits.append(f"  meta: {meta}")
    if snippet:
        bits.append(f"  фрагмент: {snippet}")
    return "\n".join(bits)


def build_pages_block(pages: Iterable[dict[str, Any]]) -> str:
    cards = [_page_to_card(p) for p in pages]
    return "\n\n".join(cards) if cards else "—"


# ── LLM contract ─────────────────────────────────────────────────────


SYSTEM_PROMPT = """\
Ты SEO-аудитор русскоязычных сайтов. Твоя задача — найти услуги или
направления, которые **явно описаны в материалах бизнеса** (narrative,
наблюдаемые факты, target_config), но НЕ имеют отдельной посадочной
страницы среди списка URL сайта.

Очень строгие правила:

  1. Ничего не выдумывай. Если услуга не упомянута в materialах
     бизнеса — её нет. Не предлагай «добавить корпоративные туры»
     если про корпоративные туры в narrative ни слова.

  2. evidence_quote — ОБЯЗАТЕЛЬНО. Это **точная цитата** из текста
     бизнеса (narrative или observed_facts), доказывающая что услуга
     там упоминается. Цитата должна быть подстрокой исходного текста,
     дословно. Без перефраза.

  3. Если для упомянутой услуги уже есть посадочная страница среди
     URL списка (даже если её можно усилить) — **не возвращай её**.
     Этот модуль ищет ПРОПУСКИ, а не докрутку существующих страниц.

  4. Группируй варианты одной услуги. Если упомянуты «Крым» и
     «экспедиции в Крым 12-15 мая» — это одна missing landing
     «Крым», а не две.

  5. closest_existing_url — указывай только если на сайте есть
     близкая по теме страница (например, для пропущенной «вертолёты»
     ближе всего /experiences/). Если ничего близкого нет — null.

  6. suggested_url_path — короткий, латиница, по существующему
     паттерну сайта если он угадывается (например, если все туры на
     /experiences/exp-NAME, то для Крыма /experiences/exp-crimea).

  7. priority:
       - high   — есть в narrative + это коммерческая услуга +
                  есть конкретные привязки (даты, регион, цена)
       - medium — упомянута, но без сильных коммерческих сигналов
       - low    — упомянута мимолётом

Стиль why_it_matters_ru: 1-2 коротких предложения для владельца сайта
без маркетинговой воды. Объясни, чем отдельная страница лучше
текущего покрытия.
"""


DETECT_TOOL = {
    "name": "detect_missing_landings",
    "description": (
        "Return a list of services/directions mentioned in the business "
        "narrative that lack a dedicated landing page on the site."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "missing": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "service_name": {"type": "string"},
                        "evidence_quote": {
                            "type": "string",
                            "description": (
                                "Точная цитата (подстрока) из materialов "
                                "бизнеса. Без перефраза."
                            ),
                        },
                        "closest_existing_url": {
                            "type": ["string", "null"],
                            "description": "URL ближайшей существующей страницы или null",
                        },
                        "suggested_url_path": {"type": "string"},
                        "why_it_matters_ru": {"type": "string"},
                        "priority": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                    },
                    "required": [
                        "service_name",
                        "evidence_quote",
                        "closest_existing_url",
                        "suggested_url_path",
                        "why_it_matters_ru",
                        "priority",
                    ],
                },
            },
            "summary_ru": {
                "type": "string",
                "description": (
                    "Одно короткое предложение для владельца: общий вердикт. "
                    "Например «Покрытие Абхазии полное, но Крым и доп. "
                    "форматы (яхты, вертолёты) пока без отдельных страниц». "
                    "Если ничего не найдено — «Все услуги покрыты страницами»."
                ),
            },
        },
        "required": ["missing", "summary_ru"],
    },
}


# ── Anti-hallucination post-filter ────────────────────────────────────


_NORMALISE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[«»\"'`(),.;:!?\-—–\[\]{}/]")


def _normalise(text: str) -> str:
    """Lowercase, NFKC, drop punctuation, collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _NORMALISE_RE.sub(" ", text).strip()
    return text


def evidence_in_signal(evidence: str, signal: str) -> bool:
    """True iff `evidence` appears as a substring of `signal` after
    light normalisation. This is the gate that drops anything the LLM
    fabricated — there is no other safety net.

    We keep the bar realistic: short evidence (under 8 normalised chars)
    is too prone to spurious matches and is rejected outright.
    """
    n_ev = _normalise(evidence)
    if len(n_ev) < 8:
        return False
    n_sig = _normalise(signal)
    return n_ev in n_sig


# ── Top-level entry point ─────────────────────────────────────────────


def find_missing_landings(
    *,
    understanding: dict[str, Any] | None,
    target_config: dict[str, Any] | None,
    pages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run the LLM detector and return a JSONB-safe result envelope.

    Shape:
        {
          "items": [...],
          "summary_ru": "...",
          "model": "claude-haiku-4-5",
          "cost_usd": 0.012,
          "input_pages": 22,
          "rejected_no_evidence": 1,
          "computed_at": "2026-04-29T17:00:00+00:00",
        }
    """
    business_signal = build_business_signal(
        understanding=understanding, target_config=target_config,
    )
    pages_block = build_pages_block(pages)

    user_message = (
        f"{business_signal}\n\n"
        f"СТРАНИЦЫ САЙТА (всего {len(pages)}):\n\n"
        f"{pages_block}\n\n"
        f"Используй detect_missing_landings и верни список услуг, "
        f"которые упомянуты в материалах бизнеса, но не имеют отдельной "
        f"посадочной страницы среди URL выше."
    )

    tool_input, usage = call_with_tool(
        model_tier="cheap",
        system=SYSTEM_PROMPT,
        user_message=user_message,
        tool=DETECT_TOOL,
        max_tokens=2500,
    )

    raw_items = tool_input.get("missing") or []
    summary_ru = (tool_input.get("summary_ru") or "").strip()

    accepted: list[dict[str, Any]] = []
    rejected = 0
    for it in raw_items:
        if not isinstance(it, dict):
            rejected += 1
            continue
        evidence = (it.get("evidence_quote") or "").strip()
        if not evidence_in_signal(evidence, business_signal):
            rejected += 1
            log.info(
                "missing_landings.rejected_no_evidence service=%r quote=%r",
                it.get("service_name"), evidence[:80],
            )
            continue
        accepted.append({
            "service_name": (it.get("service_name") or "").strip(),
            "evidence_quote": evidence,
            "closest_existing_url": it.get("closest_existing_url") or None,
            "suggested_url_path": (it.get("suggested_url_path") or "").strip(),
            "why_it_matters_ru": (it.get("why_it_matters_ru") or "").strip(),
            "priority": (it.get("priority") or "medium").strip().lower(),
        })

    # Sort: high → medium → low
    rank = {"high": 0, "medium": 1, "low": 2}
    accepted.sort(key=lambda i: rank.get(i["priority"], 9))
    accepted = accepted[:MAX_ITEMS]

    return {
        "items": accepted,
        "summary_ru": summary_ru,
        "model": usage.get("model") or "",
        "cost_usd": float(usage.get("cost_usd") or 0.0),
        "input_pages": len(pages),
        "rejected_no_evidence": rejected,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


__all__ = [
    "build_business_signal",
    "build_pages_block",
    "evidence_in_signal",
    "find_missing_landings",
    "MAX_ITEMS",
]
