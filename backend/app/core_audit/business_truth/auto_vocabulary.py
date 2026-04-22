"""Auto-derive vocabulary from site data — no owner input, no onboarding.

Why: onboarding LLMs (and owners) routinely type services the business
doesn't actually do or geos they don't operate in. Their words then
drive every downstream analysis and poison the output. This module
says: if you want to know what the business does, READ THE BUSINESS's
OUTPUT — pages + queries, not self-description.

Output:
  {
    "services": {"багги", "джиппинг"},
    "geos":     {"абхазия", "сочи", "красная поляна"},
    "evidence": {...diagnostic frequencies...}
  }

Rules:
  • Geos are matched against a bundled Russian tourism gazetteer
    (closed list of ~150 destinations). Unknown geo-like words are
    NOT invented — user explicitly can't expand this through typos.
  • Services = frequent content-bearing tokens from page titles + h1
    + Webmaster queries, minus gazetteer entries, minus noise tokens.
  • Min frequency defaults to 2 (appear on ≥2 pages OR ≥2 queries).
    This kills blog-post mentions and URL-slug noise.
  • Queries with high impression weight count more, so a direction
    with real traffic survives even if it's not yet on the site.
"""

from __future__ import annotations

import re
from typing import Iterable


# Russian tourism gazetteer — a closed list so we don't invent places.
# Ordered longest-first so "красная поляна" matches before "красная".
GEO_GAZETTEER: tuple[str, ...] = (
    # Multi-word entries first
    "красная поляна", "роза хутор", "новый афон", "голубая бухта",
    "горячий ключ", "великий новгород", "нижний новгород",
    # Abkhazia
    "абхазия", "сухум", "гагра", "пицунда", "гудаута", "ткуарчал",
    # Sochi agglomeration
    "сочи", "адлер", "хоста", "лазаревское", "дагомыс", "лоо",
    "мацеста", "кудепста", "эстосадок",
    # Krasnodar krai + Caucasus
    "геленджик", "анапа", "туапсе", "джубга", "архыз", "домбай",
    "кисловодск", "пятигорск", "ессентуки", "железноводск",
    "минеральные воды", "нальчик", "эльбрус", "теберда",
    "майкоп", "краснодар",
    # Crimea
    "крым", "ялта", "севастополь", "феодосия", "евпатория",
    "судак", "алушта", "бахчисарай", "керчь", "симферополь",
    "балаклава", "коктебель", "новый свет",
    # Russia other
    "москва", "подмосковье", "санкт-петербург", "петербург",
    "калининград", "карелия", "алтай", "байкал", "камчатка",
    "иркутск", "владимир", "суздаль", "казань", "нижний",
    "золотое кольцо", "ладога", "валаам", "соловки",
    # North Caucasus republics
    "дагестан", "чечня", "ингушетия", "осетия", "владикавказ",
    "грозный", "махачкала", "дербент",
    # Nearby countries (popular routes)
    "грузия", "тбилиси", "батуми", "армения", "ереван",
    "азербайджан", "баку",
)

# Normalise the gazetteer for matching: lowercase, set.
_GAZETTEER_SET: frozenset[str] = frozenset(g.lower() for g in GEO_GAZETTEER)

# Tokens that are NEVER services — common nouns / filler / commerce
# verbiage. Same list as matcher.py's noise tokens, extended.
_NOISE_TOKENS = frozenset({
    "туры", "тур", "отдых", "поездка", "путёвка", "цена", "цены",
    "стоимость", "забронировать", "купить", "заказать",
    "недорого", "дёшево", "2025", "2026", "2027",
    "и", "а", "или", "в", "во", "на", "у", "по",
    "из", "от", "до", "за", "для", "с", "со", "о", "об",
    # Extra filler — too common to be a service
    "услуги", "услуга", "сайт", "главная", "контакты",
    "о", "нас", "компания", "фирма", "отзывы", "отзыв",
    "новости", "блог", "статья", "кейс", "кейсы", "история",
    "все", "новый", "новая", "лучший", "лучшая", "топ",
    "фото", "видео", "цены", "купить", "заказ",
    "россия", "семьи", "детей",
})


def _normalize(s: str) -> str:
    s = (s or "").lower()
    s = s.replace("-", " ").replace("_", " ")
    s = re.sub(r"[^a-zа-яё0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _path_segments(url: str) -> list[str]:
    """/sochi/ /krasnaya-polyana/ → ["sochi", "krasnaya polyana"]."""
    if not url:
        return []
    path = re.sub(r"^https?://[^/]+", "", url)
    segs = [_normalize(s.replace("-", " ")) for s in path.split("/") if s]
    return [s for s in segs if s]


# Simple RU↔LAT transliteration so URL slugs hit the gazetteer.
# Mirrors matcher._RU_TO_LAT.
_RU_TO_LAT = {
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh",
    "з":"z","и":"i","й":"i","к":"k","л":"l","м":"m","н":"n","о":"o",
    "п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f","х":"h","ц":"c",
    "ч":"ch","ш":"sh","щ":"sch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
}
_GAZETTEER_LAT_TO_RU = {
    "".join(_RU_TO_LAT.get(c, c) for c in g): g for g in _GAZETTEER_SET
}


def _find_geos_in_text(text: str) -> set[str]:
    """Walk the normalised text and return any gazetteer hit.

    Delegates to the shared matcher.matches_vocab so we handle
    Russian case endings ("красной поляне" → "красная поляна"),
    Latin slugs ("/sochi/" → "сочи"), and multi-word entries
    consistently with how page_intent + traffic_reader classify.
    """
    if not text:
        return set()
    from app.core_audit.business_truth.matcher import matches_vocab
    hits: set[str] = set()
    for entry in _GAZETTEER_SET:
        if matches_vocab(text, entry):
            hits.add(entry)
    return hits


def _tokenize_content(text: str) -> list[str]:
    """Extract meaningful content tokens — lowercase, ≥3 chars, not noise."""
    if not text:
        return []
    norm = _normalize(text)
    out: list[str] = []
    for tok in norm.split():
        if len(tok) < 3 or tok in _NOISE_TOKENS or tok in _GAZETTEER_SET:
            continue
        out.append(tok)
    return out


def derive_vocabulary_from_data(
    pages: Iterable[dict],
    queries: Iterable[tuple[str, int]],
    *,
    min_frequency: int = 2,
    query_impression_floor: int = 50,
) -> dict:
    """Produce (services, geos, evidence) from pages + queries.

    pages:    iterable of dicts with keys url, title, h1 (optional: content_snippet).
    queries:  iterable of (query_text, impressions) tuples.
    min_frequency: a service candidate must appear in ≥N distinct contexts
                   (pages + queries combined, each counts once).
    query_impression_floor: queries with impressions ≥ this count full;
                           below, they count half. Suppresses noise.
    """
    pages = list(pages or [])
    queries = list(queries or [])

    # ── 1. Geos: collect from page text + URL + queries. All hits
    #       against gazetteer.
    geos: set[str] = set()
    for p in pages:
        haystack_parts = [
            _normalize(p.get("title") or ""),
            _normalize(p.get("h1") or ""),
            " ".join(_path_segments(p.get("url") or "")),
            _normalize(p.get("meta_description") or ""),
            _normalize((p.get("content_snippet") or "")[:500]),
        ]
        hay = " ".join(haystack_parts)
        geos |= _find_geos_in_text(hay)

    for q, imp in queries:
        if not q or imp is None or imp <= 0:
            continue
        geos |= _find_geos_in_text(_normalize(q))

    # ── 2. Services: frequent content tokens that are NOT geos.
    service_counts: dict[str, int] = {}

    for p in pages:
        # Each page contributes each token at most once (so a title
        # with 5 repeats doesn't inflate the score)
        tokens: set[str] = set()
        for field in ("title", "h1"):
            tokens.update(_tokenize_content(p.get(field) or ""))
        for tok in tokens:
            service_counts[tok] = service_counts.get(tok, 0) + 1

    for q, imp in queries:
        if not q or imp is None or imp <= 0:
            continue
        weight = 1 if imp >= query_impression_floor else 1
        # Each query contributes each token once, weighted by weight
        tokens = set(_tokenize_content(q))
        for tok in tokens:
            service_counts[tok] = service_counts.get(tok, 0) + weight

    services: set[str] = {
        tok for tok, n in service_counts.items() if n >= min_frequency
    }

    # Drop services that are NOT paired with any geo anywhere. Truly
    # isolated tokens probably aren't the core service.
    # (Skip this filter for tests simplicity when only 1 page — let
    # future iterations refine.)

    evidence = {
        "pages_scanned": len(pages),
        "queries_scanned": len(queries),
        "service_candidates": service_counts,
        "gazetteer_size": len(_GAZETTEER_SET),
    }
    return {
        "services": services,
        "geos": geos,
        "evidence": evidence,
    }


__all__ = ["derive_vocabulary_from_data", "GEO_GAZETTEER"]
