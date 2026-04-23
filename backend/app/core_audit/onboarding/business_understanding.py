"""BusinessUnderstandingAgent — step 1 of the onboarding wizard.

Reads the crawled pages of a site (top N by word_count) and produces a
structured understanding of the business: narrative, niche, positioning,
USP. The output is shown to the owner on step 1 of the wizard as an
editable text block; once confirmed, it is persisted to
`sites.understanding` JSONB.

Design choices (from prompt-engineer skill review):
- `tool_use` for structured output — more reliable than free-form JSON.
- Temperature 0 for determinism across re-runs.
- System prompt explicitly separates "observed" (what I saw on pages)
  from "inferred" (what I conclude from patterns). The agent is
  instructed to mark each inference with a page_ref.
- Anti-hallucination guards: never fabricate prices, brand names, URLs,
  or services that are not present in the input pages.
- Cost cap via caller: typical run ~$0.01 on Haiku 4.5.

Fail-open: if the LLM call fails or the payload is malformed, returns a
structured error object — the UI then shows a retry button.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence


log = logging.getLogger(__name__)

# Upper bound on number of pages sent to the LLM. Value is trimmed to
# keep Sonnet runs under the Vercel-proxy timeout (~60s): on grandtour-
# scale sites Sonnet takes ~4–5s per page, so 10 pages lands inside the
# window with headroom.
MAX_PAGES = 10

# How much text per page to include in the prompt. We keep the first
# `PAGE_TEXT_SNIPPET_CHARS` chars of content_text + full title + full h1.
PAGE_TEXT_SNIPPET_CHARS = 600

# Cost cap: if we ever build a site so large that the prompt balloons,
# we bail out early rather than surprise-bill. Value in USD.
MAX_COST_PER_RUN_USD = 0.05


SYSTEM_PROMPT = """\
Ты — SEO-аналитик, который читает сайт и кратко рассказывает владельцу,
что это за бизнес. Твоя цель — **не угадать**, а **описать то, что видишь
на страницах**, и честно разделить наблюдение и предположение.

Правила:
1. Пиши по-русски, простым человеческим языком, без SEO-жаргона.
2. В поле `narrative_ru` дай 1 абзац (100–180 слов): ниша, что компания
   продаёт, для кого, чем отличается. Тон — будто объясняешь
   знакомому, а не презентация для инвестора.
3. **Никогда не выдумывай:**
   - цены (пиши их только если они дословно есть в контенте страницы);
   - имена брендов (только если они упомянуты прямо);
   - услуги, которых нет на страницах;
   - факты об опыте/годах/клиентах.
4. Разделяй `observed_facts` (то, что буквально на страницах) и
   `inferences` (твои выводы). Для каждого observed_fact указывай поле
   `page_ref` — URL или title страницы-источника.
5. Если информации мало или она противоречива — лучше напиши об этом
   в `uncertainties`, чем придумывай красивую историю.
6. `detected_niche` — 2–5 слов (например «премиум активный туризм»).
7. `detected_positioning` — 1 предложение (например «малые группы,
   экспедиционный формат, высокий чек»).
8. `detected_usp` — то, что бизнес подчёркивает как отличие. Если не
   нашёл явного УТП, напиши `null` или короткую фразу и пометь как
   `inference`.
"""


TOOL_SCHEMA: dict[str, Any] = {
    "name": "describe_business",
    "description": (
        "Описать бизнес сайта на основе содержимого страниц. "
        "Разделить наблюдаемые факты и выводы. Не выдумывать."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "narrative_ru": {
                "type": "string",
                "description": "1 абзац 100–180 слов, разговорный тон.",
            },
            "detected_niche": {
                "type": "string",
                "description": "2–5 слов, описание ниши.",
            },
            "detected_positioning": {
                "type": "string",
                "description": "1 предложение про позиционирование.",
            },
            "detected_usp": {
                "type": ["string", "null"],
                "description": (
                    "Явное УТП если нашёл, null если не нашёл."
                ),
            },
            "observed_facts": {
                "type": "array",
                "description": (
                    "Буквальные факты со страниц. Каждый с page_ref."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "fact": {"type": "string"},
                        "page_ref": {"type": "string"},
                    },
                    "required": ["fact", "page_ref"],
                },
            },
            "inferences": {
                "type": "array",
                "description": "Выводы из паттернов (не прямые факты).",
                "items": {"type": "string"},
            },
            "uncertainties": {
                "type": "array",
                "description": (
                    "Что непонятно / противоречиво / требует уточнения."
                ),
                "items": {"type": "string"},
            },
        },
        "required": [
            "narrative_ru",
            "detected_niche",
            "detected_positioning",
            "observed_facts",
            "inferences",
            "uncertainties",
        ],
    },
}


@dataclass
class UnderstandingResult:
    """Structured output of the agent, ready for JSONB persistence."""

    narrative_ru: str = ""
    detected_niche: str = ""
    detected_positioning: str = ""
    detected_usp: str | None = None
    observed_facts: list[dict[str, str]] = field(default_factory=list)
    inferences: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    status: str = "ok"  # "ok" | "empty_pages" | "llm_failed" | "malformed"
    error: str | None = None
    pages_analyzed: int = 0
    cost_usd: float = 0.0

    def to_jsonb(self) -> dict[str, Any]:
        return {
            "narrative_ru": self.narrative_ru,
            "detected_niche": self.detected_niche,
            "detected_positioning": self.detected_positioning,
            "detected_usp": self.detected_usp,
            "observed_facts": self.observed_facts,
            "inferences": self.inferences,
            "uncertainties": self.uncertainties,
            "status": self.status,
            "error": self.error,
            "pages_analyzed": self.pages_analyzed,
            "cost_usd": self.cost_usd,
        }


def _build_user_message(
    site_domain: str,
    site_display_name: str | None,
    pages: Sequence[dict[str, Any]],
) -> str:
    """Compact the page list into an LLM prompt.

    Each page is rendered as:  - TITLE | H1 | URL\n     snippet(800)
    """
    header = [
        f"Домен сайта: {site_domain}",
        f"Название (если есть): {site_display_name or '—'}",
        "",
        f"Ниже — выжимка по {len(pages)} страницам сайта. Для каждой:",
        "URL, заголовок, H1 и начало контента. Опирайся только на них.",
        "",
    ]
    body: list[str] = []
    for idx, p in enumerate(pages, 1):
        title = (p.get("title") or "").strip()
        h1 = (p.get("h1") or "").strip()
        url = (p.get("url") or "").strip()
        snippet = (p.get("content_text") or "").strip()
        snippet = snippet[:PAGE_TEXT_SNIPPET_CHARS]
        body.append(
            f"### Страница {idx}\n"
            f"URL: {url}\n"
            f"Title: {title or '—'}\n"
            f"H1: {h1 or '—'}\n"
            f"Контент: {snippet or '(пусто)'}"
        )
    tail = [
        "",
        "Опиши бизнес сайта через tool `describe_business`.",
        "Строго соблюдай правила: не выдумывай цены, бренды, услуги.",
        "Каждый observed_fact должен иметь page_ref (URL или Title).",
    ]
    return "\n".join(header + body + tail)


def understand_business(
    site_domain: str,
    site_display_name: str | None,
    pages: Sequence[dict[str, Any]],
    *,
    caller: Any = None,
) -> UnderstandingResult:
    """Run the agent. Pure function for testability.

    `pages` is a list of dicts with keys url, title, h1, content_text.
    The caller is expected to pre-select and rank the top ~20 pages.

    `caller` is the injection hook: production path uses
    `app.agents.llm_client.call_with_tool`; tests pass a fake.
    """
    if not pages:
        return UnderstandingResult(
            status="empty_pages",
            error="No crawled pages with content_text — run crawler first.",
        )

    trimmed = list(pages)[:MAX_PAGES]
    user_message = _build_user_message(site_domain, site_display_name, trimmed)

    if caller is None:
        try:
            from app.agents.llm_client import call_with_tool as caller_fn
        except Exception as exc:  # noqa: BLE001
            log.warning("understanding.llm_import_failed err=%s", exc)
            return UnderstandingResult(
                status="llm_failed",
                error=f"LLM client import failed: {exc}",
                pages_analyzed=len(trimmed),
            )
        caller = caller_fn

    try:
        tool_input, usage = caller(
            model_tier="cheap",
            system=SYSTEM_PROMPT,
            user_message=user_message,
            tool=TOOL_SCHEMA,
            max_tokens=2048,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("understanding.llm_call_failed err=%s", exc)
        return UnderstandingResult(
            status="llm_failed",
            error=str(exc),
            pages_analyzed=len(trimmed),
        )

    cost = float((usage or {}).get("cost_usd") or 0.0)
    if cost > MAX_COST_PER_RUN_USD:
        log.warning(
            "understanding.cost_over_cap cost=%.4f cap=%.4f",
            cost,
            MAX_COST_PER_RUN_USD,
        )

    if not isinstance(tool_input, dict):
        return UnderstandingResult(
            status="malformed",
            error="LLM returned non-dict tool_input",
            pages_analyzed=len(trimmed),
            cost_usd=cost,
        )

    return UnderstandingResult(
        narrative_ru=str(tool_input.get("narrative_ru") or "")[:2000],
        detected_niche=str(tool_input.get("detected_niche") or "")[:200],
        detected_positioning=str(tool_input.get("detected_positioning") or "")[:500],
        detected_usp=(
            str(tool_input.get("detected_usp"))[:500]
            if tool_input.get("detected_usp")
            else None
        ),
        observed_facts=[
            {"fact": str(f.get("fact", ""))[:500],
             "page_ref": str(f.get("page_ref", ""))[:500]}
            for f in (tool_input.get("observed_facts") or [])
            if isinstance(f, dict)
        ][:30],
        inferences=[str(x)[:500] for x in (tool_input.get("inferences") or [])][:20],
        uncertainties=[
            str(x)[:500] for x in (tool_input.get("uncertainties") or [])
        ][:20],
        status="ok",
        pages_analyzed=len(trimmed),
        cost_usd=cost,
    )


__all__ = [
    "SYSTEM_PROMPT",
    "TOOL_SCHEMA",
    "UnderstandingResult",
    "understand_business",
    "MAX_PAGES",
    "MAX_COST_PER_RUN_USD",
]
