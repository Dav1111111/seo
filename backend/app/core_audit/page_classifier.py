"""Profile-driven page scoring — 6-signal rubric (S1-S6).

  S1 Heading/H1 match (0.20) — searches profile.intent_rules filtered by intent
  S2 Content body coverage (0.25) — profile.content_signals or rule fallback
  S3 Structural (0.15) — profile.url_patterns; images bonus for visual intents
  S4 CTA alignment (0.15) — profile.cta_patterns_booking / _info
  S5 Schema.org (0.10) — universal schema-bool × intent-weight map
  S6 E-E-A-T (0.15) — universal word-count + schema heuristic

Hard rule: S3 < 0.4 caps total at 3.0 (Yandex commercial factors gate).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core_audit.intent_codes import IntentCode
from app.core_audit.profile_protocol import SiteProfile


@dataclass(frozen=True)
class PageIntentScore:
    intent: IntentCode
    score: float
    s1_heading: float
    s2_content: float
    s3_structure: float
    s4_cta: float
    s5_schema: float
    s6_eeat: float


# Intents that benefit from multi-image galleries (universal: visual intents).
_VISUAL_INTENTS = frozenset({IntentCode.INFO_DEST, IntentCode.COMM_MODIFIED})


def _score_heading(intent: IntentCode, title: str, h1: str, profile: SiteProfile) -> float:
    combined = f"{title or ''} {h1 or ''}".lower()
    if not combined.strip():
        return 0.0
    best = 0.0
    for rule in profile.intent_rules:
        if rule.intent is intent and rule.pattern.search(combined):
            if rule.weight > best:
                best = rule.weight
    return best


def _score_content(
    intent: IntentCode, content_text: str, word_count: int, profile: SiteProfile,
) -> float:
    if not content_text or word_count < 50:
        return 0.0
    patterns = profile.content_signals.get(intent, ())
    if patterns:
        hits = sum(1 for p in patterns if p.search(content_text))
        return min(1.0, hits / max(len(patterns), 1))
    # Fallback: any main taxonomy rule for this intent matches body
    for rule in profile.intent_rules:
        if rule.intent is intent and rule.pattern.search(content_text):
            return 0.6
    return 0.0


def _score_structure(
    intent: IntentCode, path: str, has_schema: bool, images_count: int, profile: SiteProfile,
) -> float:
    patterns = profile.url_patterns.get(intent, ())
    for p in patterns:
        if p.search(path):
            if intent in _VISUAL_INTENTS and images_count >= 3:
                return 1.0
            return 0.8
    return 0.0


def _score_cta(intent: IntentCode, content_text: str, profile: SiteProfile) -> float:
    if not content_text:
        return 0.0
    text_lower = content_text.lower()

    booking_cta = any(p.search(text_lower) for p in profile.cta_patterns_booking)
    info_cta = any(p.search(text_lower) for p in profile.cta_patterns_info)

    if intent in (IntentCode.TRANS_BOOK, IntentCode.COMM_MODIFIED, IntentCode.LOCAL_GEO):
        return 1.0 if booking_cta else 0.3
    if intent in (IntentCode.INFO_DEST, IntentCode.INFO_LOGISTICS, IntentCode.INFO_PREP):
        return 0.8 if info_cta else 0.3
    if intent is IntentCode.COMM_CATEGORY:
        return 0.9 if booking_cta else 0.5
    return 0.4


def _score_schema(intent: IntentCode, has_schema: bool) -> float:
    if not has_schema:
        return 0.0
    if intent in (IntentCode.COMM_MODIFIED, IntentCode.TRANS_BOOK, IntentCode.COMM_CATEGORY):
        return 1.0
    return 0.5


def _score_eeat(word_count: int, has_schema: bool) -> float:
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
    profile: SiteProfile,
    *,
    path: str,
    title: str | None,
    h1: str | None,
    content_text: str | None,
    word_count: int | None,
    has_schema: bool,
    images_count: int | None,
) -> PageIntentScore:
    title = title or ""
    h1 = h1 or ""
    content_text = content_text or ""
    word_count = word_count or 0
    images_count = images_count or 0

    s1 = _score_heading(intent, title, h1, profile)
    s2 = _score_content(intent, content_text, word_count, profile)
    s3 = _score_structure(intent, path or "", has_schema, images_count, profile)
    s4 = _score_cta(intent, content_text, profile)
    s5 = _score_schema(intent, has_schema)
    s6 = _score_eeat(word_count, has_schema)

    weighted = (
        s1 * 0.20
        + s2 * 0.25
        + s3 * 0.15
        + s4 * 0.15
        + s5 * 0.10
        + s6 * 0.15
    )

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
    profile: SiteProfile,
    *,
    path: str,
    title: str | None,
    h1: str | None,
    content_text: str | None,
    word_count: int | None,
    has_schema: bool,
    images_count: int | None,
) -> dict[IntentCode, PageIntentScore]:
    return {
        intent: score_page_for_intent(
            intent, profile,
            path=path, title=title, h1=h1,
            content_text=content_text, word_count=word_count,
            has_schema=has_schema, images_count=images_count,
        )
        for intent in IntentCode
    }
