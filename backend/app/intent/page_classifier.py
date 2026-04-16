"""Back-compat shim — forwards page scoring to profile-driven core engine.

Callers that don't yet pass a profile default to tourism/tour_operator.
"""

from __future__ import annotations

from app.core_audit.intent_codes import IntentCode
from app.core_audit.page_classifier import (
    PageIntentScore,
    score_page_all_intents as _score_all_core,
    score_page_for_intent as _score_one_core,
)
from app.profiles.tourism import TOURISM_TOUR_OPERATOR


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
    return _score_one_core(
        intent, TOURISM_TOUR_OPERATOR,
        path=path, title=title, h1=h1,
        content_text=content_text, word_count=word_count,
        has_schema=has_schema, images_count=images_count,
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
    return _score_all_core(
        TOURISM_TOUR_OPERATOR,
        path=path, title=title, h1=h1,
        content_text=content_text, word_count=word_count,
        has_schema=has_schema, images_count=images_count,
    )


__all__ = ["PageIntentScore", "score_page_all_intents", "score_page_for_intent"]
