"""page_intent — вытащить (service, geo) из crawled страницы.

Тонкая обёртка над shared matcher.classify_text: собирает haystack
из title + h1 + URL path + meta + первые ~500 chars контента в один
текст, затем просит matcher классифицировать.

Возвращает список уникальных DirectionKey. Может быть пустым
(about/contact/misc) или несколько (hub-страница, покрывающая много
регионов).
"""

from __future__ import annotations

import re
from typing import Iterable

from app.core_audit.business_truth.dto import DirectionKey
from app.core_audit.business_truth.matcher import classify_text, normalize_text


def _path_text(url: str) -> str:
    if not url:
        return ""
    path = re.sub(r"^https?://[^/]+", "", url)
    return normalize_text(path)


def extract_page_intents(
    page: dict,
    services: Iterable[str],
    geos: Iterable[str],
) -> list[DirectionKey]:
    """Найти все (service × geo) на странице.

    page: dict с ключами url, title, h1, meta_description, content_snippet.
    """
    title_t = normalize_text(page.get("title") or "")
    h1_t = normalize_text(page.get("h1") or "")
    path_t = _path_text(page.get("url") or page.get("path") or "")
    meta_t = normalize_text(page.get("meta_description") or "")
    body_t = normalize_text((page.get("content_snippet") or "")[:500])
    haystack = " ".join([title_t, h1_t, path_t, meta_t, body_t])
    return classify_text(haystack, services, geos)


__all__ = ["extract_page_intents"]
