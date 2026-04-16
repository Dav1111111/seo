"""Required H2 blocks + affordances per intent — tourism rubric.

Source: seo-content skill audit 2026-04-17. Consumed by Module 3 (Page
Review via LLM) which grades existing pages and proposes missing sections.

Not currently consumed by Decisioner — adding this data is behavior-neutral.
"""

from __future__ import annotations

from app.core_audit.intent_codes import IntentCode
from app.core_audit.profile_protocol import PageRequirements


TOURISM_PAGE_REQUIREMENTS: dict[IntentCode, PageRequirements] = {
    IntentCode.COMM_MODIFIED: PageRequirements(
        intent=IntentCode.COMM_MODIFIED,
        required_h2_blocks=(
            "Программа по дням",
            "Что входит в стоимость",
            "Что не входит",
            "Даты заездов",
            "Цены",
            "Точка сбора / Как добраться",
            "Отзывы туристов",
            "FAQ",
            "О туроператоре",
        ),
        required_affordances=(
            "price_block_above_fold",
            "booking_form",
            "phone_cta",
            "date_picker_or_schedule",
            "pickup_locations_list",
        ),
        minimum_word_count=800,
    ),
    IntentCode.COMM_CATEGORY: PageRequirements(
        intent=IntentCode.COMM_CATEGORY,
        required_h2_blocks=(
            "Популярные туры",
            "По длительности",
            "По цене",
            "Как выбрать",
        ),
        required_affordances=(
            "catalog_listing",
            "filters_basic",
            "sort_controls",
        ),
        minimum_word_count=400,
    ),
    IntentCode.LOCAL_GEO: PageRequirements(
        intent=IntentCode.LOCAL_GEO,
        required_h2_blocks=(
            "Точки выдачи в городе",
            "Экскурсии с выездом отсюда",
            "Время в пути",
            "Как добраться до точки сбора",
        ),
        required_affordances=("pickup_points_map", "travel_times_table", "local_hotels_list"),
        minimum_word_count=500,
    ),
    IntentCode.INFO_DEST: PageRequirements(
        intent=IntentCode.INFO_DEST,
        required_h2_blocks=(
            "Главные достопримечательности",
            "Адрес, часы работы, цена билета",
            "Как добраться",
            "Где остановиться рядом",
            "Как спланировать маршрут",
            "FAQ",
        ),
        required_affordances=("attractions_list", "map", "gallery"),
        minimum_word_count=1200,
    ),
    IntentCode.INFO_LOGISTICS: PageRequirements(
        intent=IntentCode.INFO_LOGISTICS,
        required_h2_blocks=(
            "На автобусе",
            "На такси / трансфере",
            "На электричке",
            "Время в пути",
            "Стоимость",
        ),
        required_affordances=("transport_table", "map", "travel_time", "cost_breakdown"),
        minimum_word_count=600,
    ),
    IntentCode.INFO_PREP: PageRequirements(
        intent=IntentCode.INFO_PREP,
        required_h2_blocks=(
            "Что взять с собой",
            "Как одеться",
            "Когда лучше ехать",
            "Сезонность",
        ),
        required_affordances=("checklist", "seasonality_table", "tips_blocks"),
        minimum_word_count=500,
    ),
    IntentCode.COMM_COMPARE: PageRequirements(
        intent=IntentCode.COMM_COMPARE,
        required_h2_blocks=(
            "Сравнительная таблица",
            "Плюсы и минусы",
            "Что выбрать",
        ),
        required_affordances=("comparison_table", "listicle", "pros_cons"),
        minimum_word_count=700,
    ),
    IntentCode.TRUST_LEGAL: PageRequirements(
        intent=IntentCode.TRUST_LEGAL,
        required_h2_blocks=(
            "Лицензия и реестр туроператоров",
            "Отзывы",
            "Договор-оферта",
            "Политика возврата",
        ),
        required_affordances=("reviews_section", "legal_docs", "company_info"),
        minimum_word_count=400,
    ),
    IntentCode.TRANS_BRAND: PageRequirements(
        intent=IntentCode.TRANS_BRAND,
        required_h2_blocks=(
            "О компании",
            "Наши туры",
            "Контакты",
            "Отзывы",
        ),
        required_affordances=("brand_homepage", "contact_info"),
        minimum_word_count=300,
    ),
    IntentCode.TRANS_BOOK: PageRequirements(
        intent=IntentCode.TRANS_BOOK,
        required_h2_blocks=(
            "Выбор даты",
            "Стоимость",
            "Что входит",
            "Оформление заявки",
        ),
        required_affordances=("booking_form", "price_visible", "phone_cta"),
        minimum_word_count=300,
    ),
}
