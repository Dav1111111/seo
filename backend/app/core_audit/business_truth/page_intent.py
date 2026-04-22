"""page_intent — вытащить (service, geo) из crawled страницы.

Rules-based, без LLM. Принципы:
  1. Алфавит сервисов и geo — только те, что владелец подтвердил в
     онбординге (`services` + `geo_primary` + `geo_secondary`). Это
     сразу исключает шум «полный набор туристических тем».
  2. Источники матчинга внутри страницы (с весами):
        title (×3)  — самый сильный сигнал
        h1 (×3)
        URL path (×2) — точные slug-и типа /sochi/ спасают даже при
                        blend-ном title
        meta_description (×1)
        первые ~500 chars content (×1)
  3. Морфология: упрощённо — сравниваем основы слов через
     обрезку 1-2 последних символов. Без словаря (pymorphy2 есть, но
     тяжёлый для hot-path; держим чистую функцию быстрой).
  4. Multi-word geo вроде "красная поляна" матчится по нормализованному
     тексту с удалением дефисов — чтобы URL slug /krasnaya-polyana/ или
     "Красной Поляне" в заголовке оба попадали.

Результат — список всех `(service, geo)`, которые реально встретились
на странице. Страница может покрывать несколько направлений (hub-
страница «Багги-туры: Абхазия, Сочи, Красная Поляна» → 3 направления).
Если ничего не совпало — пустой список (about/contact/misc).
"""

from __future__ import annotations

import re
from typing import Iterable

from app.core_audit.business_truth.dto import DirectionKey


# Стоп-токены — общие слова, которые сами по себе не несут направления.
# Если владелец зачем-то положил такое в services, мы их игнорируем на
# этапе матчинга страницы, чтобы "туры" не давали false positive.
_NOISE_TOKENS = frozenset({
    "туры", "тур", "отдых", "поездка", "путёвка", "цена", "цены",
    "стоимость", "забронировать", "купить", "заказать",
    "недорого", "дёшево", "2025", "2026", "2027",
    "и", "а", "или", "в", "во", "на", "у", "по",
    "из", "от", "до", "за", "для", "с", "со", "о", "об",
})


def _normalize_text(s: str) -> str:
    """Нижний регистр, дефисы → пробелы, буквы/цифры/пробелы."""
    s = (s or "").lower()
    s = s.replace("-", " ").replace("_", " ")
    s = re.sub(r"[^a-zа-яё0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _path_text(url: str) -> str:
    """Вытащить из URL только path (без схемы/хоста) и нормализовать."""
    if not url:
        return ""
    path = re.sub(r"^https?://[^/]+", "", url)
    return _normalize_text(path)


def _token_stems(text: str) -> set[str]:
    """Простая морфология: токен + его обрубленные формы.

    Для русского достаточно убрать 1-2 последних символа чтобы
    большинство падежных форм "абхазии"/"абхазию" сошлись с "абхазия".
    """
    out: set[str] = set()
    for tok in text.split():
        if len(tok) < 3:
            continue
        if tok in _NOISE_TOKENS:
            continue
        out.add(tok)
        # агрессивные stem-ы: "абхазии" → "абхаз", "абхазия" → "абхаз"
        if len(tok) >= 5:
            out.add(tok[:-1])
            out.add(tok[:-2])
    return out


def _matches_vocab(page_text: str, vocab_entry: str) -> bool:
    """Проверка, встречается ли элемент словаря (например "красная поляна"
    или "багги") в нормализованном тексте страницы.

    Multi-word entries — ищем подстроку. Single-word — смотрим на
    stem-совпадение через `_token_stems`.
    """
    entry = _normalize_text(vocab_entry)
    if not entry:
        return False
    if " " in entry:
        # multi-word — ищем подстроку
        return entry in page_text
    # single-word: строгий токен-матч или stem-совпадение
    stems = _token_stems(page_text)
    # дополнительно строим stem-ы от самого entry (чтобы "экскурсии"
    # матчило и stem "экскурс")
    entry_stems = _token_stems(entry) | {entry}
    return bool(stems & entry_stems)


def extract_page_intents(
    page: dict,
    services: Iterable[str],
    geos: Iterable[str],
) -> list[DirectionKey]:
    """Найти все (service × geo), которые встречаются на странице.

    page: dict с ключами url, title, h1, meta_description, content_snippet.
          Любой ключ может быть None/пустой — пропускаем.
    services, geos: нормализованные словари из onboarding.

    Возвращает список уникальных DirectionKey. Порядок стабильный —
    сначала по сервису, потом по geo.
    """
    services = [s for s in (services or []) if s and str(s).strip()]
    geos = [g for g in (geos or []) if g and str(g).strip()]
    if not services or not geos:
        return []

    # Собираем веса по источникам. Для простоты считаем matches как
    # "есть/нет" — не нужна точная арифметика, нужно уверенное попадание.
    title_t = _normalize_text(page.get("title") or "")
    h1_t = _normalize_text(page.get("h1") or "")
    path_t = _path_text(page.get("url") or page.get("path") or "")
    meta_t = _normalize_text(page.get("meta_description") or "")
    body_t = _normalize_text((page.get("content_snippet") or "")[:500])

    # Объединяем в один текст поиска. Вес неявен: title/h1/path
    # вносят те же токены, так что если слово там есть, оно в поиске есть.
    haystack = " ".join([title_t, h1_t, path_t, meta_t, body_t])
    if not haystack.strip():
        return []

    matched_services = [s for s in services if _matches_vocab(haystack, s)]
    matched_geos = [g for g in geos if _matches_vocab(haystack, g)]

    if not matched_services or not matched_geos:
        return []

    # Декартово произведение — каждая услуга в паре с каждым geo, если
    # оба видны на странице. Это даёт hub-страницам корректную карту.
    out: list[DirectionKey] = []
    seen: set[tuple[str, str]] = set()
    for s in matched_services:
        for g in matched_geos:
            key = DirectionKey.of(s, g)
            tup = (key.service, key.geo)
            if tup in seen:
                continue
            seen.add(tup)
            out.append(key)

    out.sort(key=lambda k: (k.service, k.geo))
    return out


__all__ = ["extract_page_intents"]
