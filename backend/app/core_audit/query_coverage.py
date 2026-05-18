"""Deterministic query strategy and query-to-page coverage helpers.

This module is intentionally LLM-free. It turns stored query relevance,
Wordstat demand, Webmaster position, and lightweight page metadata into
owner-facing actions that can be reused by Studio API and advisor feed.
"""

from __future__ import annotations

import re
from typing import Any


ALTERNATIVE_ACTIVITY_MARKERS: tuple[str, ...] = (
    "джип",
    "квадро",
    "атв",
    "внедорож",
    "оффроад",
)
QUERY_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
QUERY_STOPWORDS: frozenset[str] = frozenset({
    "в", "во", "на", "по", "из", "от", "до", "для", "и", "или", "с", "со",
    "к", "ко", "у", "о", "об", "про", "за", "над", "под", "при", "без",
    "как", "где", "что", "чем", "куда", "это", "не", "ли",
})


def query_strategy_for_row(
    *,
    query_text: str,
    relevance: str,
    wordstat_volume: int | None,
    last_position: float | None,
) -> dict[str, str]:
    """Deterministic owner-facing action for one query.

    This deliberately does not ask an LLM. The point is to make the
    query table actionable without inventing facts: strategy follows
    from the stored relevance class, known demand, and known visibility.
    """
    text = (query_text or "").lower()
    has_alternative_activity = any(
        marker in text for marker in ALTERNATIVE_ACTIVITY_MARKERS
    )
    has_confirmed_demand = wordstat_volume is not None and wordstat_volume > 0

    if relevance == "spam":
        return {
            "strategy_code": "ignore_spam",
            "strategy_label_ru": "не трогать",
            "strategy_reason_ru": "классификатор пометил фразу как мусорную или не про бизнес",
            "strategy_action_ru": "Не создавать контент и не тратить Wordstat/LLM-квоту, пока владелец вручную не поменяет класс.",
        }

    if relevance == "out_of_market":
        return {
            "strategy_code": "ignore_wrong_geo",
            "strategy_label_ru": "чужой регион",
            "strategy_reason_ru": "спрос есть, но он относится не к твоей зоне обслуживания",
            "strategy_action_ru": "Хранить как шум рынка: не создавать посадочную и не оптимизировать сайт под этот город.",
        }

    if (
        has_alternative_activity
        and relevance in {"funnel_warm", "adjacent", "disputed"}
    ):
        return {
            "strategy_code": "mention_as_alternative",
            "strategy_label_ru": "встроить как альтернативу",
            "strategy_reason_ru": "человек ищет похожую активность, но это не ровно твоя услуга",
            "strategy_action_ru": "Не делать отдельную страницу, которая обещает чужую услугу. Добавить честный блок сравнения: чем твой формат отличается и кому он подойдёт лучше.",
        }

    if relevance in {"direct_product", "own"}:
        if last_position is not None and last_position <= 20:
            return {
                "strategy_code": "strengthen_existing_visibility",
                "strategy_label_ru": "усилить видимость",
                "strategy_reason_ru": "по горячему запросу уже есть видимость в поиске",
                "strategy_action_ru": "Найти страницу, которая ранжируется, и усилить её коммерческий ответ: цена, длительность, маршрут, FAQ, schema и CTA.",
            }
        return {
            "strategy_code": "map_to_money_page",
            "strategy_label_ru": "деньги/посадочная",
            "strategy_reason_ru": "горячий запрос про продукт или покупку",
            "strategy_action_ru": "Привязать запрос к основной коммерческой странице. Если такой страницы нет, создать честную посадочную под этот интент.",
        }

    if relevance in {"funnel_warm", "adjacent"}:
        return {
            "strategy_code": "landing_or_section",
            "strategy_label_ru": "посадочная или раздел",
            "strategy_reason_ru": (
                "есть подтверждённый спрос" if has_confirmed_demand
                else "тёплый туристический интент, объём ещё проверяется"
            ),
            "strategy_action_ru": "Проверить, есть ли честная страница под интент. Если услуга не совпадает один-в-один, встроить блок на существующей странице вместо отдельной посадочной.",
        }

    if relevance == "funnel_top":
        return {
            "strategy_code": "editorial_hub",
            "strategy_label_ru": "гайд верхней воронки",
            "strategy_reason_ru": "турист уже в твоём регионе, но ещё выбирает формат отдыха",
            "strategy_action_ru": "Создать или усилить обзорный гайд с подборкой вариантов и мягким переходом к твоему продукту как одному из лучших сценариев.",
        }

    return {
        "strategy_code": "review_manually",
        "strategy_label_ru": "проверить вручную",
        "strategy_reason_ru": "для фразы пока нет уверенной стратегии",
        "strategy_action_ru": "Сначала уточнить класс запроса, затем привязать к странице, разделу или исключить из работы.",
    }


def query_tokens(text: str | None) -> list[str]:
    """Small deterministic tokenizer for query-to-page matching.

    We intentionally keep this light: no LLM, no full-page content scan.
    The coverage hint should be explainable and cheap on large sites.
    """
    if not text:
        return []
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in QUERY_TOKEN_RE.findall(text.lower().replace("ё", "е")):
        if len(raw) < 2 or raw in QUERY_STOPWORDS:
            continue
        if raw in seen:
            continue
        seen.add(raw)
        tokens.append(raw)
    return tokens


def _field_token_score(tokens: list[str], field: str | None) -> float:
    if not tokens or not field:
        return 0.0
    haystack = field.lower().replace("ё", "е")
    hits = sum(1 for token in tokens if token_matches_field(token, haystack))
    return hits / max(1, len(tokens))


def token_matches_field(token: str, haystack: str) -> bool:
    if token in haystack:
        return True
    # Cheap Russian inflection tolerance: «абхазия» should match
    # «абхазии», «экскурсия» → «экскурсии». This is only a coverage
    # hint, so a conservative prefix is enough and avoids a morphology
    # dependency on the hot API path.
    if len(token) >= 6 and token[:5] in haystack:
        return True
    return False


def score_query_page(query_text: str, page: dict[str, Any]) -> tuple[int, list[str]]:
    """Return 0..100 score + evidence fields for a query/page pair."""
    tokens = query_tokens(query_text)
    if not tokens:
        return 0, []

    title_score = _field_token_score(tokens, page.get("title"))
    h1_score = _field_token_score(tokens, page.get("h1"))
    path_score = _field_token_score(tokens, page.get("path"))
    meta_score = _field_token_score(tokens, page.get("meta_description"))
    combined_score = _field_token_score(
        tokens,
        " ".join(
            str(page.get(field) or "")
            for field in ("title", "h1", "path", "meta_description")
        ),
    )

    raw = (
        title_score * 32
        + h1_score * 28
        + path_score * 16
        + meta_score * 12
        + combined_score * 12
    )
    phrase = " ".join(tokens)
    exact_bonus = 0
    if phrase:
        for field, bonus in (("title", 10), ("h1", 10), ("path", 6)):
            value = str(page.get(field) or "").lower().replace("ё", "е")
            if phrase in value:
                exact_bonus += bonus
                break

    evidence: list[str] = []
    if title_score:
        evidence.append("title")
    if h1_score:
        evidence.append("h1")
    if path_score:
        evidence.append("url")
    if meta_score:
        evidence.append("description")

    return min(100, int(round(raw + exact_bonus))), evidence


def best_page_for_query(
    query_text: str,
    pages: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, int, list[str]]:
    best_page: dict[str, Any] | None = None
    best_score = 0
    best_evidence: list[str] = []

    for page in pages:
        score, evidence = score_query_page(query_text, page)
        if score > best_score:
            best_page = page
            best_score = score
            best_evidence = evidence

    return best_page, best_score, best_evidence


def coverage_for_query(
    *,
    query_text: str,
    relevance: str,
    strategy_code: str,
    last_position: float | None,
    pages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Map one query to the best current page and next coverage action."""
    if relevance in {"spam", "out_of_market"}:
        return {
            "coverage_status": "ignored",
            "coverage_score": 0,
            "coverage_reason_ru": "запрос исключён из SEO-работы по классу",
            "coverage_action_ru": "Не привязывать к странице, пока класс запроса не изменён вручную.",
            "best_page_id": None,
            "best_page_url": None,
            "best_page_title": None,
            "best_page_match_source": [],
        }

    if not pages:
        return {
            "coverage_status": "unknown",
            "coverage_score": 0,
            "coverage_reason_ru": "в базе нет просканированных страниц для сопоставления",
            "coverage_action_ru": "Сначала запусти краулер/полный анализ, затем вернись к покрытию запросов.",
            "best_page_id": None,
            "best_page_url": None,
            "best_page_title": None,
            "best_page_match_source": [],
        }

    best_page, score, evidence = best_page_for_query(query_text, pages)
    if best_page is None or score < 25:
        reason = "не нашёл страницу, которая явно отвечает на этот запрос"
        if last_position is not None:
            reason = (
                "страница ранжируется в поиске, но по title/h1/url я не могу "
                "честно определить, какая именно закрывает запрос"
            )
        return {
            "coverage_status": "missing",
            "coverage_score": score,
            "coverage_reason_ru": reason,
            "coverage_action_ru": "Решить: создать отдельную посадочную, добавить раздел на существующую страницу или исключить запрос.",
            "best_page_id": None,
            "best_page_url": None,
            "best_page_title": None,
            "best_page_match_source": evidence,
        }

    is_alternative = strategy_code == "mention_as_alternative"
    if score >= 70 and not is_alternative:
        status = "covered"
        action = "Страница уже похожа на целевую. Дальше усиливать её фактами, сниппетом, FAQ/schema и внутренними ссылками."
    elif score >= 45:
        status = "weak"
        action = "Использовать найденную страницу как кандидат: добавить точный блок под запрос и проверить, не обещаем ли чужую услугу."
    else:
        status = "weak"
        action = "Совпадение слабое. Страница может быть кандидатом для вставки блока, но отдельное решение ещё нужно проверить."

    if is_alternative:
        status = "weak"
        action = "Не создавать отдельную страницу под чужую услугу. На найденной странице добавить честный блок сравнения/альтернативы."

    title = best_page.get("title") or best_page.get("h1") or best_page.get("path")
    return {
        "coverage_status": status,
        "coverage_score": score,
        "coverage_reason_ru": (
            f"лучший кандидат совпал по: {', '.join(evidence)}"
            if evidence else "найден слабый кандидат без сильного совпадения"
        ),
        "coverage_action_ru": action,
        "best_page_id": best_page.get("id"),
        "best_page_url": best_page.get("url"),
        "best_page_title": title,
        "best_page_match_source": evidence,
    }


__all__ = [
    "best_page_for_query",
    "coverage_for_query",
    "query_strategy_for_row",
    "query_tokens",
    "score_query_page",
    "token_matches_field",
]
