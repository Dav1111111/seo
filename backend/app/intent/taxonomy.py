"""Intent taxonomy — regex rules + structural affordances.

Each intent has:
  - code: IntentCode enum
  - patterns: list of (regex, weight) — highest weight match wins
  - required_affordances: what structure the page should have to serve this intent
  - modifier_signals: secondary axis (pickup, price, seasonal, audience)
"""

import re
from dataclasses import dataclass, field

from app.intent.enums import IntentCode

_RE_FLAGS = re.IGNORECASE | re.UNICODE


@dataclass(frozen=True)
class IntentRule:
    pattern: re.Pattern
    weight: float  # 0.0-1.0, higher = more confident match


@dataclass(frozen=True)
class IntentDefinition:
    code: IntentCode
    description_ru: str
    rules: list[IntentRule] = field(default_factory=list)
    # Structural affordances — what pages serving this intent must have
    required_affordances: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)


# ── Regex patterns (ordered by specificity) ───────────────────────────
# More specific patterns should have higher weight

INTENT_DEFINITIONS: list[IntentDefinition] = [
    # TRANS_BOOK — highest specificity, checked first
    IntentDefinition(
        code=IntentCode.TRANS_BOOK,
        description_ru="Пользователь готов купить/забронировать",
        rules=[
            IntentRule(re.compile(r"\b(забронир|заказать|оформить|купить\s*тур|купить\s*экскурс)\w*", _RE_FLAGS), 0.95),
            IntentRule(re.compile(r"\bстоимост\w*|сколько\s+стоит", _RE_FLAGS), 0.80),
            # Standalone "купить" — commercial intent even without "тур"/"экскурсию"
            IntentRule(re.compile(r"\bкупить\b", _RE_FLAGS), 0.80),
            # "заказ" / "заказать"
            IntentRule(re.compile(r"\bзаказ(ать|а|ы)?\b", _RE_FLAGS), 0.75),
            # Price signals: "цена", "цены", "прайс"
            IntentRule(re.compile(r"\b(цен[аы]|прайс)\b", _RE_FLAGS), 0.75),
        ],
        required_affordances=["booking_form", "price_visible", "phone_cta"],
        examples=["забронировать тур в абхазию", "купить экскурсию на 33 водопада", "сколько стоит красная поляна"],
    ),

    # COMM_COMPARE
    IntentDefinition(
        code=IntentCode.COMM_COMPARE,
        description_ru="Сравнение вариантов",
        rules=[
            IntentRule(re.compile(r"\b(лучш|топ|топ-\d+|рейтинг|сравнение|vs)\b", _RE_FLAGS), 0.85),
            IntentRule(re.compile(r"\b\w+\s+или\s+\w+", _RE_FLAGS), 0.70),  # "Роза Хутор или Красная Поляна"
        ],
        required_affordances=["comparison_table", "listicle", "pros_cons"],
        examples=["лучшие экскурсии в сочи", "топ 10 туров абхазия", "роза хутор или красная поляна"],
    ),

    # INFO_LOGISTICS
    IntentDefinition(
        code=IntentCode.INFO_LOGISTICS,
        description_ru="Как добраться / логистика",
        rules=[
            IntentRule(re.compile(r"\bкак\s+добраться|как\s+доехать", _RE_FLAGS), 0.95),
            IntentRule(re.compile(r"\bсколько\s+ехать|время\s+в\s+пути", _RE_FLAGS), 0.90),
            IntentRule(re.compile(r"\bрасписан\w+|схема\s+проезда", _RE_FLAGS), 0.80),
            IntentRule(re.compile(r"\b(автобус|электричк|такси)\s+(из|в|до)\s+\w+", _RE_FLAGS), 0.75),
        ],
        required_affordances=["transport_table", "map", "travel_time", "cost_breakdown"],
        examples=["как добраться до красной поляны", "сколько ехать из сочи в абхазию"],
    ),

    # INFO_PREP
    IntentDefinition(
        code=IntentCode.INFO_PREP,
        description_ru="Подготовка к поездке",
        rules=[
            IntentRule(re.compile(r"\bчто\s+(взять|одеть|надеть)", _RE_FLAGS), 0.90),
            IntentRule(re.compile(r"\bкогда\s+(лучше|ехать|поехать)", _RE_FLAGS), 0.85),
            IntentRule(re.compile(r"\bсовет\w*\s+туристам|лучшее\s+время", _RE_FLAGS), 0.80),
            IntentRule(re.compile(r"\bпогода\s+в\s+\w+", _RE_FLAGS), 0.70),
        ],
        required_affordances=["checklist", "seasonality_table", "tips_blocks"],
        examples=["что взять на экскурсию в горы", "когда лучше ехать в сочи", "погода в абхазии"],
    ),

    # TRUST_LEGAL
    IntentDefinition(
        code=IntentCode.TRUST_LEGAL,
        description_ru="Доверие, отзывы, юридические вопросы",
        rules=[
            IntentRule(re.compile(r"\bотзыв\w*\s+о\b", _RE_FLAGS), 0.85),
            IntentRule(re.compile(r"\bлицензи\w*|договор|оферт\w+|возврат", _RE_FLAGS), 0.80),
            IntentRule(re.compile(r"\bбезопасно\s+ли|обман\w*|развод\w*", _RE_FLAGS), 0.80),
        ],
        required_affordances=["reviews_section", "legal_docs", "company_info"],
        examples=["отзывы о туроператоре сочи", "лицензия южный континент", "безопасно ли в абхазии"],
    ),

    # COMM_MODIFIED — commercial с явным модификатором
    IntentDefinition(
        code=IntentCode.COMM_MODIFIED,
        description_ru="Коммерческий запрос с модификатором (направление/формат/длительность)",
        rules=[
            # "экскурсии из сочи в абхазию"
            IntentRule(
                re.compile(r"\b(экскурс|тур|джиппинг)\w*\s+(из|в|по)\s+\w+\s+(в|на|до|из)\s+\w+", _RE_FLAGS),
                0.90,
            ),
            # "экскурсии с детьми", "туры недорого", "на 1 день"
            IntentRule(
                re.compile(r"\b(экскурс|тур)\w*.{0,40}\b(с\s+детьми|недорог\w*|на\s+\d+\s+(день|дня|дней)|групп\w*|индивидуальн\w*|с\s+трансфером)\b", _RE_FLAGS),
                0.85,
            ),
            # Discounts / promo signals — "скидка", "акция", "промокод"
            IntentRule(
                re.compile(r"\b(скидк\w*|акци[яию]|промокод\w*)\b", _RE_FLAGS),
                0.70,
            ),
        ],
        required_affordances=["filtered_listing", "modifier_in_h1"],
        examples=[
            "экскурсии из сочи в абхазию на 1 день",
            "туры недорого с детьми",
            "индивидуальные экскурсии в красную поляну",
        ],
    ),

    # LOCAL_GEO — pickup-location queries (наша специфика!)
    IntentDefinition(
        code=IntentCode.LOCAL_GEO,
        description_ru="Локальный запрос с геомодификатором места (откуда турист)",
        rules=[
            # "экскурсии Лоо", "туры из Адлера", "экскурсии в Хосте"
            IntentRule(
                re.compile(
                    r"\b(экскурс|тур|джиппинг)\w*\s+(из\s+)?(лоо|адлер|хост|кудепст|лазаревск|дагомыс|мацест|красная\s+поляна|эсто-садок|сочи\s+центр)\w*",
                    _RE_FLAGS,
                ),
                0.90,
            ),
            # "куда съездить из Лоо"
            IntentRule(
                re.compile(
                    r"\b(куда|что\s+посмотреть).{0,20}\b(из\s+)?(лоо|адлер|хост|кудепст|лазаревск|дагомыс|мацест)",
                    _RE_FLAGS,
                ),
                0.80,
            ),
        ],
        required_affordances=["pickup_points", "local_hotels_list", "travel_times_table"],
        examples=["экскурсии из лоо", "куда съездить из адлера", "туры из хосты"],
    ),

    # INFO_DEST — "что посмотреть"
    IntentDefinition(
        code=IntentCode.INFO_DEST,
        description_ru="Что посмотреть в месте, достопримечательности",
        rules=[
            IntentRule(re.compile(r"\bчто\s+посмотреть", _RE_FLAGS), 0.90),
            IntentRule(re.compile(r"\bдостопримечательност\w+", _RE_FLAGS), 0.85),
            IntentRule(re.compile(r"\bкуда\s+сходить", _RE_FLAGS), 0.80),
            IntentRule(re.compile(r"\bинтересные\s+места", _RE_FLAGS), 0.75),
        ],
        required_affordances=["attractions_list", "map", "gallery"],
        examples=["что посмотреть в сочи", "достопримечательности абхазии", "куда сходить в адлере"],
    ),

    # COMM_CATEGORY — общие коммерческие без модификатора
    IntentDefinition(
        code=IntentCode.COMM_CATEGORY,
        description_ru="Общий коммерческий запрос: «{услуга} {место}» без модификатора",
        rules=[
            # "экскурсии в сочи", "туры абхазия" — простые (substring match, no full-line anchors)
            IntentRule(
                re.compile(
                    r"\b(экскурс|тур|джиппинг|морские\s+прогулки)\w*\s+(в|по|из)?\s*(сочи|абхази|красная\s+поляна|адлер)\w*",
                    _RE_FLAGS,
                ),
                0.85,
            ),
        ],
        required_affordances=["catalog_listing", "filters_basic"],
        examples=["экскурсии в сочи", "туры абхазия", "джиппинг сочи"],
    ),

    # TRANS_BRAND — проверяется через site_domain/brand в classifier
    # Regex rules catch explicit nav signals; brand token detection is separate.
    IntentDefinition(
        code=IntentCode.TRANS_BRAND,
        description_ru="Брендовый запрос (содержит имя компании)",
        rules=[
            # "официальный сайт" — явный навигационный сигнал к бренду
            IntentRule(re.compile(r"\bофициальн\w+\s+сайт\w*", _RE_FLAGS), 0.85),
        ],
        required_affordances=["brand_homepage", "contact_info"],
        examples=["южный континент сочи", "grand tour spirit", "южный континент официальный сайт"],
    ),
]

# Quick lookup by code
TAXONOMY: dict[IntentCode, IntentDefinition] = {d.code: d for d in INTENT_DEFINITIONS}
