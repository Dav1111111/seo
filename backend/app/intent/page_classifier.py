"""Page intent classifier — heuristic based on URL, title, H1, content patterns.

Unlike query classifier (single intent per query), a page CAN serve multiple intents.
We compute a score 0-5 per (page, intent) pair — the 6-signal rubric from seo-content:

  S1. Title/H1 intent match (regex on heading, weight 0.20)
  S2. Content body coverage (keywords presence, weight 0.25)
  S3. Structural affordance (DOM patterns, weight 0.15)
  S4. CTA alignment (button texts, weight 0.15)
  S5. Schema.org match (page type, weight 0.10)
  S6. E-E-A-T evidence (author, date, reviews, weight 0.15)

Phase 2A uses simplified heuristics — Phase 2B adds LLM review for ambiguous pages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.intent.enums import IntentCode
from app.intent.taxonomy import TAXONOMY

# ── Patterns for URL-based page type detection ────────────────────────

_URL_PATTERNS: dict[IntentCode, list[re.Pattern]] = {
    IntentCode.TRANS_BRAND: [re.compile(r"^/$|^/index", re.I)],
    IntentCode.COMM_CATEGORY: [
        re.compile(r"/tours/?$", re.I),
        re.compile(r"/excursii?/?$", re.I),
        re.compile(r"/catalog", re.I),
    ],
    IntentCode.COMM_MODIFIED: [
        re.compile(r"/tours/[\w-]+$", re.I),          # tour detail page
        re.compile(r"/excursii?/[\w-]+$", re.I),
    ],
    IntentCode.INFO_DEST: [
        re.compile(r"/(guide|gids?|chto-posmotret)/", re.I),
        re.compile(r"/destination", re.I),
    ],
    IntentCode.LOCAL_GEO: [
        re.compile(r"/(pickup|from-\w+|iz-\w+)/", re.I),
    ],
    IntentCode.TRUST_LEGAL: [
        re.compile(r"/(otzyvy|reviews|about|o-nas|privacy|terms)", re.I),
    ],
    IntentCode.INFO_LOGISTICS: [
        re.compile(r"/(kak-dobratsya|transport|how-to-get)", re.I),
    ],
    IntentCode.INFO_PREP: [
        re.compile(r"/(blog|stati|news|stories)/", re.I),
        re.compile(r"/(faq|voprosy)", re.I),
    ],
}

# ── Content keyword signals ───────────────────────────────────────────

_CONTENT_SIGNALS: dict[IntentCode, list[re.Pattern]] = {
    IntentCode.TRANS_BOOK: [
        re.compile(r"\bзабронировать|оформить\s+заявку|оставить\s+заявку", re.I),
    ],
    IntentCode.COMM_MODIFIED: [
        re.compile(r"\bпрограмма\s+тура|что\s+включено|что\s+не\s+входит", re.I),
    ],
    IntentCode.INFO_DEST: [
        re.compile(r"\bдостопримечательност|главные\s+места", re.I),
    ],
    IntentCode.LOCAL_GEO: [
        re.compile(r"\b(трансфер\s+от\s+отеля|забираем\s+из)", re.I),
    ],
    IntentCode.INFO_LOGISTICS: [
        re.compile(r"\bкак\s+добраться|время\s+в\s+пути", re.I),
    ],
    IntentCode.INFO_PREP: [
        re.compile(r"\bчто\s+взять|как\s+одеться", re.I),
    ],
    IntentCode.TRUST_LEGAL: [
        re.compile(r"\bотзыв|оферт|лицензи|ИНН", re.I),
    ],
}


@dataclass(frozen=True)
class PageIntentScore:
    intent: IntentCode
    score: float              # 0.0-5.0
    s1_heading: float         # 0-1 contribution
    s2_content: float
    s3_structure: float
    s4_cta: float
    s5_schema: float
    s6_eeat: float


def _score_heading(intent: IntentCode, title: str, h1: str) -> float:
    """S1: Does the title/H1 lexically match the intent?"""
    combined = f"{title or ''} {h1 or ''}".lower()
    if not combined.strip():
        return 0.0

    definition = TAXONOMY[intent]
    best = 0.0
    for rule in definition.rules:
        if rule.pattern.search(combined):
            best = max(best, rule.weight)
    return best


def _score_content(intent: IntentCode, content_text: str, word_count: int) -> float:
    """S2: Does the body mention keywords/phrases for this intent?"""
    if not content_text or word_count < 50:
        return 0.0
    patterns = _CONTENT_SIGNALS.get(intent, [])
    hits = sum(1 for p in patterns if p.search(content_text))
    if patterns:
        return min(1.0, hits / max(len(patterns), 1))
    # Fallback: check main taxonomy rules (less reliable on body)
    definition = TAXONOMY[intent]
    for rule in definition.rules:
        if rule.pattern.search(content_text):
            return 0.6
    return 0.0


def _score_structure(intent: IntentCode, path: str, has_schema: bool, images_count: int) -> float:
    """S3: URL pattern + basic structural signals."""
    patterns = _URL_PATTERNS.get(intent, [])
    for p in patterns:
        if p.search(path):
            # Bonus for multi-image pages (tourism needs gallery)
            if intent in (IntentCode.INFO_DEST, IntentCode.COMM_MODIFIED) and images_count >= 3:
                return 1.0
            return 0.8
    return 0.0


def _score_cta(intent: IntentCode, content_text: str) -> float:
    """S4: CTA alignment with funnel stage."""
    if not content_text:
        return 0.0
    text_lower = content_text.lower()

    booking_cta = bool(re.search(r"забронировать|оформить|купить|заказать", text_lower))
    info_cta = bool(re.search(r"узнать\s+больше|подробнее|читать\s+дальше", text_lower))

    # Map intent → expected CTA
    if intent in (IntentCode.TRANS_BOOK, IntentCode.COMM_MODIFIED, IntentCode.LOCAL_GEO):
        return 1.0 if booking_cta else 0.3
    if intent in (IntentCode.INFO_DEST, IntentCode.INFO_LOGISTICS, IntentCode.INFO_PREP):
        return 0.8 if info_cta else 0.3
    if intent == IntentCode.COMM_CATEGORY:
        return 0.9 if booking_cta else 0.5
    return 0.4


def _score_schema(intent: IntentCode, has_schema: bool) -> float:
    """S5: Schema.org presence (simplified — any schema = partial credit)."""
    if not has_schema:
        return 0.0
    # Tourism commercial intents benefit most from TouristTrip/Product schema
    if intent in (IntentCode.COMM_MODIFIED, IntentCode.TRANS_BOOK, IntentCode.COMM_CATEGORY):
        return 1.0
    return 0.5


def _score_eeat(word_count: int, has_schema: bool) -> float:
    """S6: Trust signals — rough heuristic based on content depth + schema."""
    score = 0.0
    if word_count and word_count > 500:
        score += 0.4
    if word_count and word_count > 1500:
        score += 0.3
    if has_schema:
        score += 0.3
    return min(1.0, score)


def score_page_for_intent(
    intent: IntentCode,
    *,
    path: str,
    title: str | None,
    h1: str | None,
    content_text: str | None,
    word_count: int | None,
    has_schema: bool,
    images_count: int | None,
) -> PageIntentScore:
    """Compute 6-signal weighted score for (page, intent) pair. Returns 0.0-5.0."""
    title = title or ""
    h1 = h1 or ""
    content_text = content_text or ""
    word_count = word_count or 0
    images_count = images_count or 0

    s1 = _score_heading(intent, title, h1)
    s2 = _score_content(intent, content_text, word_count)
    s3 = _score_structure(intent, path or "", has_schema, images_count)
    s4 = _score_cta(intent, content_text)
    s5 = _score_schema(intent, has_schema)
    s6 = _score_eeat(word_count, has_schema)

    # Weighted 0-1 → multiply by 5 to get 0-5
    weighted = (
        s1 * 0.20
        + s2 * 0.25
        + s3 * 0.15
        + s4 * 0.15
        + s5 * 0.10
        + s6 * 0.15
    )

    # Hard rule from seo-content spec: if s3 < 0.4, cap total at 3.0
    # (structure matters critically for Yandex commercial factors)
    total_5 = weighted * 5.0
    if s3 < 0.4 and total_5 > 3.0:
        total_5 = 3.0

    return PageIntentScore(
        intent=intent,
        score=round(total_5, 2),
        s1_heading=round(s1, 2),
        s2_content=round(s2, 2),
        s3_structure=round(s3, 2),
        s4_cta=round(s4, 2),
        s5_schema=round(s5, 2),
        s6_eeat=round(s6, 2),
    )


def score_page_all_intents(
    *,
    path: str,
    title: str | None,
    h1: str | None,
    content_text: str | None,
    word_count: int | None,
    has_schema: bool,
    images_count: int | None,
) -> dict[IntentCode, PageIntentScore]:
    """Score a page against all 10 intents."""
    return {
        intent: score_page_for_intent(
            intent,
            path=path, title=title, h1=h1,
            content_text=content_text, word_count=word_count,
            has_schema=has_schema, images_count=images_count,
        )
        for intent in IntentCode
    }
