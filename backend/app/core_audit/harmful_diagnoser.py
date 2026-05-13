"""Harmful query diagnosis — Studio v2 etap 5 extension.

Once the classifier flagged a query as spam/disputed AND we know we
rank for it (top-30), the next question is: WHY do we rank, and what
do we change on the site so we stop?

Three steps per query:

  1. Find OUR page that ranks. We probe Yandex Search API for the
     query (without `site:`) and pick the highest-positioned result
     belonging to our domain. If nothing in top-30 → no matched_url
     (Yandex says we DO rank — daily_metrics confirms — but the SERP
     probe today doesn't match. Common reason: position changes by
     hour, or the SERP is region-specific. Surface as «не нашли URL»
     so the owner sees the gap honestly).

  2. Look up the URL in our Page table. We need title / h1 /
     meta_description / first chunk of content_text — these are what
     Yandex used to decide we match the query.

  3. Ask LLM (Haiku) for cause + fixes. Profile narrative_ru is in
     the prompt so the LLM can see the bias of «what this business is»
     vs «what the page text says».

Result is cached on SearchQuery.harmful_diagnosis (JSONB) so a
re-fetch doesn't re-pay LLM. Re-running the diagnoser overwrites
unconditionally (the page content might have changed).

Cost shape (verified empirically):
  Search API: ~5 sec/query, free, but rate-limited (~100/hr)
  Haiku:      ~3 sec/query, ~$0.005 each
  Total:      ~8-10 sec/query, ~5 cents per 10 queries
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.agents.llm_client import call_with_tool
from app.collectors.yandex_serp import fetch_serp


log = logging.getLogger(__name__)


# Limit of SERP positions we consider. Beyond top-30 we wouldn't surface
# this query in the «harmful» report anyway.
SERP_DEPTH = 30


@dataclass
class MatchedPageInfo:
    """Resolved «our URL that ranks for this harmful query»."""
    url: str
    position: int
    title: str
    headline: str


def find_matched_url(
    query_text: str, our_domain: str,
) -> MatchedPageInfo | None:
    """Probe Yandex SERP for `query_text`, find our domain's URL.

    Returns None when:
      - SERP fetch fails (rate-limit, network)
      - our domain doesn't appear in top-SERP_DEPTH

    Domain match is suffix-based (`url.endswith(domain)` after host
    extraction) so `grandtourspirit.ru` matches `www.grandtourspirit.ru`.
    """
    if not query_text or not our_domain:
        return None

    docs, err = fetch_serp(query_text, groups=SERP_DEPTH)
    if err:
        log.info(
            "harmful_diagnoser.serp_failed query=%r err=%s",
            query_text, err,
        )
        return None

    # `lstrip("www.")` was a bug — it treats the arg as a CHAR SET, so
    # «wexample.ru» becomes «example.ru» (real false-match potential
    # against `example.ru` profiles). Use `removeprefix` for true
    # prefix removal.
    norm_domain = our_domain.lower().strip().lstrip(".")
    norm_domain = norm_domain.removeprefix("www.")
    for doc in docs:
        d = (doc.domain or "").lower().strip()
        d = d.removeprefix("www.")
        # Only accept exact match or subdomain-of-our-domain. The reverse
        # branch `norm_domain.endswith("." + d)` admitted any SerpDoc with
        # `domain="ru"` as ours, because "grandtourspirit.ru".endswith(".ru")
        # is True — we never own a parent of our own domain.
        if d == norm_domain or d.endswith("." + norm_domain):
            return MatchedPageInfo(
                url=doc.url,
                position=doc.position,
                title=doc.title,
                headline=doc.headline,
            )
    return None


# ── LLM diagnoser ────────────────────────────────────────────────────


SYSTEM_PROMPT = """\
Ты SEO-аудитор. Твоя задача — объяснить владельцу сайта, почему его
страница ранжируется по нерелевантному запросу, и дать конкретные
правки на странице чтобы это перестало происходить.

Что тебе дают:
  - бизнес-профиль (что компания реально делает)
  - запрос по которому сайт случайно ранжируется
  - класс запроса (spam — мусор, disputed — спорный) + причина от
    предыдущего классификатора
  - URL и контент страницы которая ранжируется (title, h1,
    meta_description, начало текста)

Что нужно вернуть (одним вызовом инструмента):

  cause_ru — один абзац (3-5 предложений) на русском. Объясни КОНКРЕТНО,
  какие слова на странице запутали Яндекс. Цитируй фразу/слова из
  title/h1/контента, ссылайся на профиль бизнеса. Без воды,
  без общих фраз про «нерелевантный контент».

  fix_title — новая формулировка title (или null если title не
  проблема). Должен явно содержать продукт + туристический контекст.
  Регион указывай ТОЛЬКО если он встречается в `narrative_ru` бизнеса
  или в `services`/`geo_primary` — иначе пиши `[укажи регион]` как
  плейсхолдер (по аналогии с `[уточнить цену]` в стандартных правилах
  переписывания). Длина 50-65 символов.

  fix_h1 — новый H1 (или null). Менее формальный чем title. То же
  правило с регионом: только из профиля или плейсхолдер.

  fix_meta_description — новый meta description (или null). Регион
  только из профиля или плейсхолдер.

  fix_content_change_ru — что переписать в самом тексте страницы.
  Конкретно: «убрать абзац про X», «добавить упоминание Y»,
  «переименовать раздел Z». Не «улучшить контент» — не помогает.

  schema_recommendation — рекомендация по schema.org разметке.
  Правила СТРОГО:
    • Если в данных (поле `Schema.org блоки на странице`) явно видно
      cargo-cult типы TouristTrip / TouristAttraction /
      TouristDestination / Event / TravelAction / Trip — напиши:
      «Заменить {обнаруженный_тип} на Product + Offer + AggregateRating».
    • Если этих типов в данных НЕ видно — верни null (или строку
      «Не требуется (нет данных)»). НЕ переписывай deny-list
      обратно в ответ. Если на странице вообще нет JSON-LD —
      это не повод придумывать «заменить TouristTrip» — верни null.
    • Не ссылайся на типы, которых не было в `Schema.org блоки на
      странице` — даже как «убедись, что нет TouristTrip».
  Яндекс игнорирует cargo-cult типы в rich snippets, поэтому ТОЛЬКО
  Product + Offer + AggregateRating имеет смысл, и ТОЛЬКО если на
  странице сейчас стоит мусорный тип.

  cause_ru — null если данные не позволяют объяснить КОНКРЕТНО (нет
  ни title/h1/контента, ни schema-блоков). Иначе — один абзац
  (3-5 предложений) на русском с цитатой фразы из страницы. Не
  выдумывай причину, если данных мало.

  noindex_recommended — true ТОЛЬКО если страница вообще не нужна
  на сайте (служебная, технический мусор, дубль). Иначе false.

Стиль:
  - короткими предложениями
  - без маркетингового пуха
  - всё на русском
  - конкретные фразы/абзацы, не «расширьте описание»
"""


DIAGNOSE_TOOL = {
    "name": "diagnose_harmful_visibility",
    "description": (
        "Return one structured diagnosis: cause + concrete page edits."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "cause_ru": {
                "type": ["string", "null"],
                "description": (
                    "Один абзац: какие слова страницы спутали Яндекс. "
                    "Цитируй конкретное. null если данные не показывают "
                    "это явно (нет title/h1/контента — лучше промолчать, "
                    "чем выдумать причину)."
                ),
            },
            "fix_title": {
                "type": ["string", "null"],
                "description": (
                    "Новый title 50-65 символов или null. Регион "
                    "указывай ТОЛЬКО если он встречается в профиле "
                    "бизнеса (narrative_ru / services / geo); иначе "
                    "используй плейсхолдер `[укажи регион]`."
                ),
            },
            "fix_h1": {
                "type": ["string", "null"],
                "description": (
                    "Новый H1 или null. Регион только из профиля или "
                    "плейсхолдер `[укажи регион]`."
                ),
            },
            "fix_meta_description": {
                "type": ["string", "null"],
                "description": (
                    "Новый meta description или null. Регион только "
                    "из профиля или плейсхолдер `[укажи регион]`."
                ),
            },
            "fix_content_change_ru": {
                "type": ["string", "null"],
                "description": "Что переписать в тексте страницы",
            },
            "schema_recommendation": {
                "type": ["string", "null"],
                "description": (
                    "Schema.org рекомендация. Заполняй ТОЛЬКО если в "
                    "поле `Schema.org блоки на странице` видно cargo-"
                    "cult тип (TouristTrip / TouristAttraction / "
                    "TouristDestination / Event / TravelAction / Trip)"
                    " — тогда напиши «Заменить {тип} на Product + "
                    "Offer + AggregateRating». Иначе null. Не "
                    "переписывай deny-list если этих типов на "
                    "странице нет."
                ),
            },
            "noindex_recommended": {
                "type": "boolean",
                "description": (
                    "true только если страница не нужна вообще "
                    "(служебная, дубль, мусор)"
                ),
            },
        },
        # Note: `cause_ru` and `schema_recommendation` are nullable —
        # NOT in `required`. The LLM is allowed (and encouraged) to
        # return null when the input data doesn't support a confident
        # answer. See the system prompt rules and post-filter below.
        "required": [
            "fix_title",
            "fix_h1",
            "fix_meta_description",
            "fix_content_change_ru",
            "noindex_recommended",
        ],
    },
}


def _format_schema_blocks_for_prompt(
    schema_blocks: list[dict[str, Any]] | None,
) -> str:
    """Render the JSON-LD `@type` values on the page for the prompt.

    The LLM only needs to know WHICH Schema.org types are present —
    not the full JSON. We surface a comma-separated, deduped list of
    `@type` values. When the list is None or empty, return a marker
    string so the LLM (and the post-filter) can distinguish «we don't
    know» from «we know there are zero blocks».

    `@type` per schema.org may be either a string or an array of
    strings; both shapes are accepted.
    """
    if schema_blocks is None:
        return "(нет данных — JSON-LD не извлекался)"
    if not schema_blocks:
        return "(JSON-LD блоков не найдено)"
    types: list[str] = []
    seen: set[str] = set()
    for block in schema_blocks:
        if not isinstance(block, dict):
            continue
        raw_type = block.get("@type")
        if raw_type is None:
            continue
        if isinstance(raw_type, list):
            for t in raw_type:
                if isinstance(t, str) and t and t.casefold() not in seen:
                    seen.add(t.casefold())
                    types.append(t)
        elif isinstance(raw_type, str) and raw_type:
            if raw_type.casefold() not in seen:
                seen.add(raw_type.casefold())
                types.append(raw_type)
    if not types:
        return "(JSON-LD блоков не найдено)"
    return ", ".join(types)


def _build_user_message(
    query: str,
    relevance: str,
    relevance_reason: str | None,
    business_narrative: str,
    business_primary: str,
    business_geo: list[str],
    matched: MatchedPageInfo,
    page_title: str | None,
    page_h1: str | None,
    page_meta: str | None,
    page_content_excerpt: str,
    page_schema_blocks: list[dict[str, Any]] | None = None,
) -> str:
    geo_str = ", ".join(business_geo) if business_geo else "—"
    schema_line = _format_schema_blocks_for_prompt(page_schema_blocks)
    return (
        f"БИЗНЕС:\n"
        f"  основной продукт: {business_primary or '—'}\n"
        f"  регионы: {geo_str}\n"
        f"  описание: {business_narrative or '—'}\n"
        f"\n"
        f"ВРЕДНЫЙ ЗАПРОС: {query}\n"
        f"  класс: {relevance}\n"
        f"  причина классификации: {relevance_reason or '—'}\n"
        f"\n"
        f"СТРАНИЦА КОТОРАЯ РАНЖИРУЕТСЯ:\n"
        f"  url: {matched.url}\n"
        f"  позиция: {matched.position}\n"
        f"  title: {page_title or '—'}\n"
        f"  H1: {page_h1 or '—'}\n"
        f"  meta description: {page_meta or '—'}\n"
        f"  Schema.org блоки на странице: {schema_line}\n"
        f"  фрагмент контента (первые 1200 символов):\n"
        f"  {page_content_excerpt}\n"
        f"\n"
        f"Объясни почему страница ранжируется по этому запросу и что "
        f"переписать. Используй инструмент diagnose_harmful_visibility."
    )


# Schema types the LLM is most likely to hallucinate back at us by
# echoing the deny-list verbatim. Kept in sync with
# `review.llm.verify.CARGO_CULT_SCHEMA_TYPES`; we deliberately do NOT
# import that set to avoid pulling review/ internals into this module.
_CARGO_CULT_TYPES = frozenset({
    "touristtrip",
    "touristattraction",
    "touristdestination",
    "event",
    "travelaction",
    "trip",
})

# Casefold-stable label for owners when we blank a hallucinated rec.
_SCHEMA_REC_BLANKED = "Не требуется (нет данных)"


def _present_types_casefold(
    schema_blocks: list[dict[str, Any]] | None,
) -> set[str]:
    """Casefolded `@type` values actually present in the page snapshot.

    Mirrors review.llm.verify.filter_hallucinated_cargo_cult's intake
    shape (single string or list of strings).
    """
    if not schema_blocks:
        return set()
    present: set[str] = set()
    for block in schema_blocks:
        if not isinstance(block, dict):
            continue
        raw_type = block.get("@type")
        if raw_type is None:
            continue
        if isinstance(raw_type, list):
            for t in raw_type:
                if isinstance(t, str) and t:
                    present.add(t.casefold())
        elif isinstance(raw_type, str) and raw_type:
            present.add(raw_type.casefold())
    return present


# Word-boundary regex compiled once per cargo-cult type. Substring
# matching alone would falsely flag «Trip» inside «TouristTrip»,
# making the sanitizer over-eager.
_CARGO_CULT_PATTERNS: dict[str, re.Pattern[str]] = {
    t: re.compile(r"\b" + re.escape(t) + r"\b", re.IGNORECASE)
    for t in _CARGO_CULT_TYPES
}


def _sanitize_schema_recommendation(
    raw: str | None,
    schema_blocks: list[dict[str, Any]] | None,
) -> str | None:
    """Drop cargo-cult mentions the page snapshot doesn't actually have.

    If the LLM names any of the cargo-cult types in `raw` that are NOT
    present in `schema_blocks`, the recommendation is replaced with a
    short «no data» marker. If the LLM cited a type that IS on the
    page, the recommendation passes through unchanged. If `raw` is
    null/empty, returns None.

    Matching is word-boundary regex (case-insensitive) so `Trip`
    inside `TouristTrip` is not double-counted.
    """
    if not raw:
        return None
    mentioned: set[str] = set()
    for t, pat in _CARGO_CULT_PATTERNS.items():
        if pat.search(raw):
            mentioned.add(t)
    if not mentioned:
        # No cargo-cult vocabulary in the text → assume the LLM is
        # genuinely recommending Product+Offer or similar. Pass through.
        return raw
    present = _present_types_casefold(schema_blocks)
    # Every mentioned cargo-cult type must actually be on the page.
    if mentioned.issubset(present):
        return raw
    log.info(
        "harmful_diagnoser.schema_rec_blanked "
        "mentioned=%r present=%r raw=%r",
        sorted(mentioned), sorted(present), raw,
    )
    return _SCHEMA_REC_BLANKED


def diagnose_one(
    *,
    query: str,
    relevance: str,
    relevance_reason: str | None,
    business_narrative: str,
    business_primary: str,
    business_geo: list[str],
    matched: MatchedPageInfo,
    page_title: str | None,
    page_h1: str | None,
    page_meta: str | None,
    page_content: str | None,
    page_schema_blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """LLM-call wrapper. Returns the JSONB-shaped diagnosis dict.

    `page_schema_blocks` — optional list of parsed JSON-LD blocks from
    the page's PageDeepExtract snapshot. When passed, the prompt
    surfaces the `@type` values to the LLM, and the post-filter blanks
    any cargo-cult schema mention the LLM echoed back without the
    page actually having that type. Pass None when the caller doesn't
    have a snapshot — the post-filter then assumes «no JSON-LD
    extracted» and conservatively drops cargo-cult recommendations.
    """
    excerpt = (page_content or "").strip()[:1200] or "—"

    user_msg = _build_user_message(
        query=query,
        relevance=relevance,
        relevance_reason=relevance_reason,
        business_narrative=business_narrative,
        business_primary=business_primary,
        business_geo=business_geo,
        matched=matched,
        page_title=page_title,
        page_h1=page_h1,
        page_meta=page_meta,
        page_content_excerpt=excerpt,
        page_schema_blocks=page_schema_blocks,
    )

    tool_input, usage = call_with_tool(
        model_tier="cheap",
        system=SYSTEM_PROMPT,
        user_message=user_msg,
        tool=DIAGNOSE_TOOL,
        max_tokens=1500,
    )

    raw_schema_rec = tool_input.get("schema_recommendation")
    cleaned_schema_rec = _sanitize_schema_recommendation(
        raw_schema_rec, page_schema_blocks,
    )

    return {
        "matched_url": matched.url,
        "matched_position": matched.position,
        # cause_ru is now nullable in the tool schema. When the LLM
        # returns null (or omits it entirely) we coalesce to "" so the
        # JSONB shape stays stable for existing UI consumers — the
        # empty string is treated as «no cause» in the frontend the
        # same as null. No KeyError on missing field.
        "cause_ru": tool_input.get("cause_ru") or "",
        "fixes": {
            "title_change": tool_input.get("fix_title"),
            "h1_change": tool_input.get("fix_h1"),
            "meta_description_change": tool_input.get("fix_meta_description"),
            "content_change_ru": tool_input.get("fix_content_change_ru"),
            "schema_recommendation": cleaned_schema_rec,
            "noindex_recommended": bool(
                tool_input.get("noindex_recommended") or False,
            ),
        },
        "model": usage.get("model") or "",
        "cost_usd": float(usage.get("cost_usd") or 0.0),
        "diagnosed_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Fallback page matcher (when SERP probe finds nothing) ───────────


# Russian + Latin word characters; everything else is a separator.
_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    """Lowercase token set, length ≥ 3 to avoid stop-word noise."""
    if not text:
        return set()
    return {
        m.group(0).lower()
        for m in _TOKEN_RE.finditer(text)
        if len(m.group(0)) >= 3
    }


def score_page_for_query(query: str, page) -> int:
    """Token-overlap score of a Page against a query.

    Looks at title + h1 + meta_description + first 800 chars of content.
    No tf-idf — for a 7-query batch the simplest approach wins.
    Score is the number of query tokens present anywhere on the page.
    """
    q_toks = _tokens(query)
    if not q_toks:
        return 0
    page_text = " ".join([
        page.title or "",
        page.h1 or "",
        page.meta_description or "",
        (page.content_text or "")[:800],
    ])
    p_toks = _tokens(page_text)
    return len(q_toks & p_toks)


__all__ = [
    "MatchedPageInfo",
    "find_matched_url",
    "diagnose_one",
    "score_page_for_query",
    "SERP_DEPTH",
]
