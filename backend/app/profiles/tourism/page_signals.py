"""URL/content/CTA patterns for page scoring (S1-S4).

Mirrored from app.intent.page_classifier. Consumed by the universal page
scorer once Step 4 threads profile through the function.
"""

from __future__ import annotations

import re

from app.core_audit.intent_codes import IntentCode


TOURISM_URL_PATTERNS: dict[IntentCode, tuple[re.Pattern, ...]] = {
    IntentCode.TRANS_BRAND: (re.compile(r"^/$|^/index", re.I),),
    IntentCode.COMM_CATEGORY: (
        re.compile(r"/tours/?$", re.I),
        re.compile(r"/excursii?/?$", re.I),
        re.compile(r"/catalog", re.I),
    ),
    IntentCode.COMM_MODIFIED: (
        re.compile(r"/tours/[\w-]+$", re.I),
        re.compile(r"/excursii?/[\w-]+$", re.I),
    ),
    IntentCode.INFO_DEST: (
        re.compile(r"/(guide|gids?|chto-posmotret)/", re.I),
        re.compile(r"/destination", re.I),
    ),
    IntentCode.LOCAL_GEO: (
        re.compile(r"/(pickup|from-\w+|iz-\w+)/", re.I),
    ),
    IntentCode.TRUST_LEGAL: (
        re.compile(r"/(otzyvy|reviews|about|o-nas|privacy|terms)", re.I),
    ),
    IntentCode.INFO_LOGISTICS: (
        re.compile(r"/(kak-dobratsya|transport|how-to-get)", re.I),
    ),
    IntentCode.INFO_PREP: (
        re.compile(r"/(blog|stati|news|stories)/", re.I),
        re.compile(r"/(faq|voprosy)", re.I),
    ),
}


TOURISM_CONTENT_SIGNALS: dict[IntentCode, tuple[re.Pattern, ...]] = {
    IntentCode.TRANS_BOOK: (
        re.compile(r"\bзабронировать|оформить\s+заявку|оставить\s+заявку", re.I),
    ),
    IntentCode.COMM_MODIFIED: (
        re.compile(r"\bпрограмма\s+тура|что\s+включено|что\s+не\s+входит", re.I),
    ),
    IntentCode.INFO_DEST: (
        re.compile(r"\bдостопримечательност|главные\s+места", re.I),
    ),
    IntentCode.LOCAL_GEO: (
        re.compile(r"\b(трансфер\s+от\s+отеля|забираем\s+из)", re.I),
    ),
    IntentCode.INFO_LOGISTICS: (
        re.compile(r"\bкак\s+добраться|время\s+в\s+пути", re.I),
    ),
    IntentCode.INFO_PREP: (
        re.compile(r"\bчто\s+взять|как\s+одеться", re.I),
    ),
    IntentCode.TRUST_LEGAL: (
        re.compile(r"\bотзыв|оферт|лицензи|ИНН", re.I),
    ),
}


# CTA patterns — consumed by S4 scoring once profile is threaded through.
TOURISM_BOOKING_CTA_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"забронировать|оформить|купить|заказать", re.I),
)

TOURISM_INFO_CTA_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"узнать\s+больше|подробнее|читать\s+дальше", re.I),
)
