"""Tourism intent regex rules — mirrored from original app.intent.taxonomy.

Preserves order and weights exactly. Engines that consume this iterate all
rules and pick the highest weight match.
"""

from __future__ import annotations

import re

from app.core_audit.intent_codes import IntentCode
from app.core_audit.profile_protocol import IntentRule

_F = re.IGNORECASE | re.UNICODE


TOURISM_INTENT_RULES: tuple[IntentRule, ...] = (
    # TRANS_BOOK
    IntentRule(
        intent=IntentCode.TRANS_BOOK,
        pattern=re.compile(r"\b(забронир|заказать|оформить|купить\s*тур|купить\s*экскурс)\w*", _F),
        weight=0.95,
    ),
    IntentRule(IntentCode.TRANS_BOOK, re.compile(r"\bстоимост\w*|сколько\s+стоит", _F), 0.80),
    IntentRule(IntentCode.TRANS_BOOK, re.compile(r"\bкупить\b", _F), 0.80),
    IntentRule(IntentCode.TRANS_BOOK, re.compile(r"\bзаказ(ать|а|ы)?\b", _F), 0.75),
    IntentRule(IntentCode.TRANS_BOOK, re.compile(r"\b(цен[аы]|прайс)\b", _F), 0.75),

    # COMM_COMPARE
    IntentRule(IntentCode.COMM_COMPARE, re.compile(r"\b(лучш|топ|топ-\d+|рейтинг|сравнение|vs)\b", _F), 0.85),
    IntentRule(IntentCode.COMM_COMPARE, re.compile(r"\b\w+\s+или\s+\w+", _F), 0.70),

    # INFO_LOGISTICS
    IntentRule(IntentCode.INFO_LOGISTICS, re.compile(r"\bкак\s+добраться|как\s+доехать", _F), 0.95),
    IntentRule(IntentCode.INFO_LOGISTICS, re.compile(r"\bсколько\s+ехать|время\s+в\s+пути", _F), 0.90),
    IntentRule(IntentCode.INFO_LOGISTICS, re.compile(r"\bрасписан\w+|схема\s+проезда", _F), 0.80),
    IntentRule(IntentCode.INFO_LOGISTICS, re.compile(r"\b(автобус|электричк|такси)\s+(из|в|до)\s+\w+", _F), 0.75),

    # INFO_PREP
    IntentRule(IntentCode.INFO_PREP, re.compile(r"\bчто\s+(взять|одеть|надеть)", _F), 0.90),
    IntentRule(IntentCode.INFO_PREP, re.compile(r"\bкогда\s+(лучше|ехать|поехать)", _F), 0.85),
    IntentRule(IntentCode.INFO_PREP, re.compile(r"\bсовет\w*\s+туристам|лучшее\s+время", _F), 0.80),
    IntentRule(IntentCode.INFO_PREP, re.compile(r"\bпогода\s+в\s+\w+", _F), 0.70),

    # TRUST_LEGAL
    IntentRule(IntentCode.TRUST_LEGAL, re.compile(r"\bотзыв\w*\s+о\b", _F), 0.85),
    IntentRule(IntentCode.TRUST_LEGAL, re.compile(r"\bлицензи\w*|договор|оферт\w+|возврат", _F), 0.80),
    IntentRule(IntentCode.TRUST_LEGAL, re.compile(r"\bбезопасно\s+ли|обман\w*|развод\w*", _F), 0.80),

    # COMM_MODIFIED
    IntentRule(
        IntentCode.COMM_MODIFIED,
        re.compile(r"\b(экскурс|тур|джиппинг)\w*\s+(из|в|по)\s+\w+\s+(в|на|до|из)\s+\w+", _F),
        0.90,
    ),
    IntentRule(
        IntentCode.COMM_MODIFIED,
        re.compile(
            r"\b(экскурс|тур)\w*.{0,40}\b(с\s+детьми|недорог\w*|на\s+\d+\s+(день|дня|дней)|групп\w*|индивидуальн\w*|с\s+трансфером)\b",
            _F,
        ),
        0.85,
    ),
    IntentRule(IntentCode.COMM_MODIFIED, re.compile(r"\b(скидк\w*|акци[яию]|промокод\w*)\b", _F), 0.70),

    # LOCAL_GEO
    IntentRule(
        IntentCode.LOCAL_GEO,
        re.compile(
            r"\b(экскурс|тур|джиппинг)\w*\s+(из\s+)?(лоо|адлер|хост|кудепст|лазаревск|дагомыс|мацест|красная\s+поляна|эсто-садок|сочи\s+центр)\w*",
            _F,
        ),
        0.90,
    ),
    IntentRule(
        IntentCode.LOCAL_GEO,
        re.compile(
            r"\b(куда|что\s+посмотреть).{0,20}\b(из\s+)?(лоо|адлер|хост|кудепст|лазаревск|дагомыс|мацест)",
            _F,
        ),
        0.80,
    ),

    # INFO_DEST
    IntentRule(IntentCode.INFO_DEST, re.compile(r"\bчто\s+посмотреть", _F), 0.90),
    IntentRule(IntentCode.INFO_DEST, re.compile(r"\bдостопримечательност\w+", _F), 0.85),
    IntentRule(IntentCode.INFO_DEST, re.compile(r"\bкуда\s+сходить", _F), 0.80),
    IntentRule(IntentCode.INFO_DEST, re.compile(r"\bинтересные\s+места", _F), 0.75),

    # COMM_CATEGORY
    IntentRule(
        IntentCode.COMM_CATEGORY,
        re.compile(
            r"\b(экскурс|тур|джиппинг|морские\s+прогулки)\w*\s+(в|по|из)?\s*(сочи|абхази|красная\s+поляна|адлер)\w*",
            _F,
        ),
        0.85,
    ),

    # TRANS_BRAND (regex-based; token-based detection is separate)
    IntentRule(IntentCode.TRANS_BRAND, re.compile(r"\bофициальн\w+\s+сайт\w*", _F), 0.85),
)


# Fallback commercial pattern — when no intent rule matches, if query mentions
# a tourism service + geo, classify as COMM_CATEGORY with low confidence.
TOURISM_FALLBACK_COMMERCIAL_PATTERN = re.compile(
    r"(экскурс|тур|джиппинг).*(сочи|абхази|красная\s+поляна|адлер)",
    _F,
)


# Doorway URL spam triggers. Geo-swap pattern (/excursii-loo etc.) is NOT
# included because legitimate pickup pages follow that exact shape in tourism.
TOURISM_DOORWAY_SPAM_URL_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"-\d{4}/?$"),                                       # year spam
    re.compile(r"-(deshevo|nedorogo|luchshie)\b", re.I),            # cheap-words spam
)
