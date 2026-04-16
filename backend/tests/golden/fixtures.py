"""Canonical fixtures for snapshot-parity tests.

These inputs feed the classifier/scorer/standalone/safety functions during
refactor-parity verification. Post-refactor outputs MUST match baseline.json
byte-for-byte.

Add fixtures conservatively — every addition forces a baseline rebuild.
"""

from __future__ import annotations

# 20 real Russian tourism queries covering all 10 intents + regression cases.
# Tuple shape: (query_text, site_brand_tokens or None)
SAMPLE_QUERIES: list[tuple[str, list[str] | None]] = [
    ("экскурсии в сочи недорого", None),
    ("туры абхазия", None),
    ("как добраться до красной поляны", None),
    ("что взять на экскурсию в горы", None),
    ("что посмотреть в сочи", None),
    ("отзывы туроператор", None),
    ("забронировать тур рица", None),
    ("официальный сайт южный континент", ["южный континент"]),
    ("рица или гагра", None),
    ("экскурсии из лоо", None),
    ("рука массаж", ["южный континент"]),          # regression: "ук" must NOT brand-match
    ("цена канатка газпром лаура", None),
    ("купить тур в абхазию", None),
    ("скидки на туры", None),
    ("джиппинг сочи", None),
    ("сколько стоит экскурсия на рицу", None),
    ("индивидуальные экскурсии в красную поляну", None),
    ("погода в абхазии", None),
    ("морские прогулки адлер", None),
    ("лицензия туроператора", None),
]


# Page fixtures — simplified HTML-free signals fed into score_page_all_intents.
SAMPLE_PAGES: list[dict] = [
    {
        "name": "tour_detail_rica",
        "path": "/tours/tur-na-ricu",
        "title": "Тур на озеро Рица из Сочи — цены от 2500₽ | Южный Континент",
        "h1": "Экскурсия на озеро Рица",
        "content_text": (
            "Программа тура рассчитана на один день. Забираем из отеля в Адлере, "
            "Лоо, Хосте. Что включено: трансфер, экскурсовод, обед. Что не входит: "
            "входные билеты. Забронировать можно онлайн или по телефону."
        ),
        "word_count": 480,
        "has_schema": True,
        "images_count": 8,
    },
    {
        "name": "category_tours",
        "path": "/tours/",
        "title": "Все туры и экскурсии из Сочи — Южный Континент",
        "h1": "Экскурсии из Сочи",
        "content_text": (
            "Каталог туров. Программа тура, цена, что включено. Забронировать онлайн."
        ),
        "word_count": 120,
        "has_schema": False,
        "images_count": 12,
    },
    {
        "name": "info_guide_abkhazia",
        "path": "/gids/chto-posmotret-v-abkhazii/",
        "title": "20 достопримечательностей Абхазии — что посмотреть в 2026",
        "h1": "Главные места Абхазии",
        "content_text": (
            "Главные достопримечательности Абхазии: Рица, Гагра, Новый Афон. "
            "Как добраться из Сочи. Время в пути около 2 часов. Узнать больше "
            "об экскурсиях можно на странице туров."
        ),
        "word_count": 1600,
        "has_schema": True,
        "images_count": 15,
    },
    {
        "name": "thin_about",
        "path": "/o-nas",
        "title": "О нас",
        "h1": "О компании",
        "content_text": "Мы туроператор в Сочи.",
        "word_count": 20,
        "has_schema": False,
        "images_count": 0,
    },
]


# Standalone-test inputs (C1-C4 only; C5 needs DB — verified separately on prod).
# Tuple: (name, proposed_title, proposed_query, proposed_intent_value, parent_intent_value)
SAMPLE_STANDALONE: list[dict] = [
    {
        "name": "tour_to_rica_from_loo",
        "proposed_title": "Тур на Рицу из Лоо",
        "proposed_query": "экскурсия из лоо на рицу",
        "proposed_intent": "local_geo",
        "parent_intent": "comm_category",
    },
    {
        "name": "generic_modifier_only",
        "proposed_title": "Экскурсии недорого",
        "proposed_query": "экскурсии недорого",
        "proposed_intent": "comm_modified",
        "parent_intent": "comm_category",
    },
    {
        "name": "info_guide",
        "proposed_title": "Что посмотреть в Гагре",
        "proposed_query": "гагра достопримечательности",
        "proposed_intent": "info_dest",
        "parent_intent": None,
    },
    {
        "name": "trust_page",
        "proposed_title": "Лицензия туроператора",
        "proposed_query": "лицензия туроператора",
        "proposed_intent": "trust_legal",
        "parent_intent": None,
    },
]
