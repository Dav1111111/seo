"""Required H2 blocks + affordances per intent — tourism rubric.

Source: seo-content skill audit 2026-04-17. Consumed by Module 3 (Page
Review) which grades existing pages and proposes missing sections.

H2 blocks split into critical (hard gap, commercial blocker) and
recommended (improves coverage + E-E-A-T). Review pipeline emits different
severities per tier.

Not currently consumed by Decisioner — adding this data is behavior-neutral.
"""

from __future__ import annotations

from app.core_audit.intent_codes import IntentCode
from app.core_audit.profile_protocol import PageRequirements


TOURISM_PAGE_REQUIREMENTS: dict[IntentCode, PageRequirements] = {
    IntentCode.COMM_MODIFIED: PageRequirements(
        intent=IntentCode.COMM_MODIFIED,
        critical_h2_blocks=(
            "Цены",
            "Точка сбора / Как добраться",
            "Даты заездов",
        ),
        recommended_h2_blocks=(
            "Программа по дням",
            "Что входит в стоимость",
            "Что не входит",
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
        critical_h2_blocks=(
            "Популярные туры",
        ),
        recommended_h2_blocks=(
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
        critical_h2_blocks=(
            "Точки выдачи в городе",
            "Как добраться до точки сбора",
        ),
        recommended_h2_blocks=(
            "Экскурсии с выездом отсюда",
            "Время в пути",
        ),
        required_affordances=("pickup_points_map", "travel_times_table", "local_hotels_list"),
        minimum_word_count=500,
    ),
    IntentCode.INFO_DEST: PageRequirements(
        intent=IntentCode.INFO_DEST,
        critical_h2_blocks=(
            "Главные достопримечательности",
            "Как добраться",
        ),
        recommended_h2_blocks=(
            "Адрес, часы работы, цена билета",
            "Где остановиться рядом",
            "Как спланировать маршрут",
            "FAQ",
        ),
        required_affordances=("attractions_list", "map", "gallery"),
        minimum_word_count=1200,
    ),
    IntentCode.INFO_LOGISTICS: PageRequirements(
        intent=IntentCode.INFO_LOGISTICS,
        critical_h2_blocks=(
            "Время в пути",
            "Стоимость",
        ),
        recommended_h2_blocks=(
            "На автобусе",
            "На такси / трансфере",
            "На электричке",
        ),
        required_affordances=("transport_table", "map", "travel_time", "cost_breakdown"),
        minimum_word_count=600,
    ),
    IntentCode.INFO_PREP: PageRequirements(
        intent=IntentCode.INFO_PREP,
        critical_h2_blocks=(
            "Что взять с собой",
        ),
        recommended_h2_blocks=(
            "Как одеться",
            "Когда лучше ехать",
            "Сезонность",
        ),
        required_affordances=("checklist", "seasonality_table", "tips_blocks"),
        minimum_word_count=500,
    ),
    IntentCode.COMM_COMPARE: PageRequirements(
        intent=IntentCode.COMM_COMPARE,
        critical_h2_blocks=(
            "Сравнительная таблица",
        ),
        recommended_h2_blocks=(
            "Плюсы и минусы",
            "Что выбрать",
        ),
        required_affordances=("comparison_table", "listicle", "pros_cons"),
        minimum_word_count=700,
    ),
    IntentCode.TRUST_LEGAL: PageRequirements(
        intent=IntentCode.TRUST_LEGAL,
        critical_h2_blocks=(
            "Лицензия и реестр туроператоров",
        ),
        recommended_h2_blocks=(
            "Отзывы",
            "Договор-оферта",
            "Политика возврата",
        ),
        required_affordances=("reviews_section", "legal_docs", "company_info"),
        minimum_word_count=400,
    ),
    IntentCode.TRANS_BRAND: PageRequirements(
        intent=IntentCode.TRANS_BRAND,
        critical_h2_blocks=(
            "Контакты",
        ),
        recommended_h2_blocks=(
            "О компании",
            "Наши туры",
            "Отзывы",
        ),
        required_affordances=("brand_homepage", "contact_info"),
        minimum_word_count=300,
    ),
    IntentCode.TRANS_BOOK: PageRequirements(
        intent=IntentCode.TRANS_BOOK,
        critical_h2_blocks=(
            "Стоимость",
            "Оформление заявки",
        ),
        recommended_h2_blocks=(
            "Выбор даты",
            "Что входит",
        ),
        required_affordances=("booking_form", "price_visible", "phone_cta"),
        minimum_word_count=300,
    ),
}
