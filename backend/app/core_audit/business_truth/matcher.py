"""Shared text → (service, geo) classifier.

Used by:
  - page_intent: classify crawled pages into business directions
  - traffic_reader: classify Webmaster queries into same directions

Pure rules-based: finds service and geo tokens from the owner's
confirmed vocabulary inside arbitrary Russian text, tolerates
Russian case endings and RU→LAT transliteration of URL slugs.

If text has >=1 service token AND >=1 geo token, each cartesian
(service, geo) pair is a matched direction. Otherwise empty result
(not enough evidence to assign).
"""

from __future__ import annotations

import re
from typing import Iterable

from app.core_audit.business_truth.dto import DirectionKey


_NOISE_TOKENS = frozenset({
    "туры", "тур", "отдых", "поездка", "путёвка", "цена", "цены",
    "стоимость", "забронировать", "купить", "заказать",
    "недорого", "дёшево", "2025", "2026", "2027",
    "и", "а", "или", "в", "во", "на", "у", "по",
    "из", "от", "до", "за", "для", "с", "со", "о", "об",
})


_RU_TO_LAT = {
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh",
    "з":"z","и":"i","й":"i","к":"k","л":"l","м":"m","н":"n","о":"o",
    "п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f","х":"h","ц":"c",
    "ч":"ch","ш":"sh","щ":"sch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
}


def _translit_ru_to_lat(s: str) -> str:
    return "".join(_RU_TO_LAT.get(ch, ch) for ch in s)


def normalize_text(s: str) -> str:
    """Lowercase, strip hyphens/underscores/punct, collapse whitespace."""
    s = (s or "").lower()
    s = s.replace("-", " ").replace("_", " ")
    s = re.sub(r"[^a-zа-яё0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _token_stems(text: str) -> set[str]:
    """Token + trailing truncations for Russian inflections.

    Minimum stem length is 4 — shorter prefixes collide across unrelated
    words (e.g. "судно" and "судак" both reduce to "суд").
    """
    out: set[str] = set()
    for tok in text.split():
        if len(tok) < 3 or tok in _NOISE_TOKENS:
            continue
        out.add(tok)
        if len(tok) >= 5:
            out.add(tok[:-1])
        if len(tok) >= 6:
            out.add(tok[:-2])
    return out


def matches_vocab(haystack: str, vocab_entry: str) -> bool:
    """True if `vocab_entry` appears in `haystack`.

    `haystack` expected pre-normalized (via normalize_text). Tries
    both Russian and transliterated Latin forms.

    Single-word entries: stem intersection (handles "абхазии"/"абхазию"
    → "абхазия").
    Multi-word entries: two-pass. First try exact substring (handles
    URL slug "krasnaya polyana"). If that misses, check each word's
    stems separately — covers inflected forms where "красная поляна"
    appears as "красной поляне" in the page text.
    """
    entry_ru = normalize_text(vocab_entry)
    if not entry_ru:
        return False
    entry_lat = _translit_ru_to_lat(entry_ru)
    candidates = [entry_ru]
    if entry_lat != entry_ru:
        candidates.append(entry_lat)

    page_stems = _token_stems(haystack)
    for entry in candidates:
        if " " in entry:
            # Try exact substring first (fast path, catches slugs)
            if entry in haystack:
                return True
            # Fallback: every word's stems must appear in haystack.
            # Adjacency isn't required — adjacency rarely survives
            # Russian inflection anyway ("в красной поляне зимой").
            words = entry.split()
            all_match = True
            for w in words:
                w_stems = _token_stems(w) | {w}
                if not (page_stems & w_stems):
                    all_match = False
                    break
            if all_match:
                return True
        else:
            entry_stems = _token_stems(entry) | {entry}
            if page_stems & entry_stems:
                return True
    return False


def classify_text(
    text: str,
    services: Iterable[str],
    geos: Iterable[str],
) -> list[DirectionKey]:
    """Return all (service × geo) directions evidenced by `text`.

    `text` is any string — page content, a query, a headline. If ≥1
    service matched AND ≥1 geo matched, the cartesian product is
    returned. Otherwise empty list.
    """
    services_list = [s for s in (services or []) if s and str(s).strip()]
    geos_list = [g for g in (geos or []) if g and str(g).strip()]
    if not services_list or not geos_list or not text:
        return []

    haystack = normalize_text(text)
    if not haystack:
        return []

    matched_s = [s for s in services_list if matches_vocab(haystack, s)]
    matched_g = [g for g in geos_list if matches_vocab(haystack, g)]
    if not matched_s or not matched_g:
        return []

    out: list[DirectionKey] = []
    seen: set[tuple[str, str]] = set()
    for s in matched_s:
        for g in matched_g:
            k = DirectionKey.of(s, g)
            tup = (k.service, k.geo)
            if tup in seen:
                continue
            seen.add(tup)
            out.append(k)
    out.sort(key=lambda k: (k.service, k.geo))
    return out


__all__ = ["classify_text", "normalize_text", "matches_vocab"]
