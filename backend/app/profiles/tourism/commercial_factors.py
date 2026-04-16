"""Yandex commercial ranking factors for tourism pages.

Official classifier from Садовский (Optimization conf 2019, updated 2023).
Priority order reflects Yandex's weighting; `critical` items are blockers
for commercial ranking.
"""

from __future__ import annotations

import re

from app.core_audit.profile_protocol import CommercialFactor


TOURISM_COMMERCIAL_FACTORS: tuple[CommercialFactor, ...] = (
    CommercialFactor(
        name="price_above_fold",
        detection_pattern=None,     # requires DOM positioning — LLM-checked
        priority="critical",
        description_ru="Цена видна на первом экране без скролла",
    ),
    CommercialFactor(
        name="phone_in_header",
        detection_pattern=re.compile(r"\+7\s*\(?\d{3}\)?\s*\d{3}[\s-]?\d{2}[\s-]?\d{2}", re.I),
        priority="critical",
        description_ru="Телефон в формате +7 (XXX) в шапке сайта",
    ),
    CommercialFactor(
        name="callback_form",
        detection_pattern=re.compile(r"обратн(?:ый|ого)\s+звон|заказать\s+звонок", re.I),
        priority="high",
        description_ru="Форма обратного звонка",
    ),
    CommercialFactor(
        name="rto_in_footer",
        detection_pattern=re.compile(r"\b(РТО|реестр(?:овый)?\s+номер\s+туроператора)\b", re.I),
        priority="critical",
        description_ru="Номер в реестре туроператоров (РТО) в футере",
    ),
    CommercialFactor(
        name="schedule_block",
        detection_pattern=re.compile(r"(часы|график|время)\s+работы|работаем\s+(?:ежедневно|с)", re.I),
        priority="medium",
        description_ru="График работы указан",
    ),
    CommercialFactor(
        name="payment_icons",
        detection_pattern=re.compile(r"\b(мир|visa|mastercard|сбп|т-банк|тинькофф)\b", re.I),
        priority="medium",
        description_ru="Иконки способов оплаты (Мир, СБП, Т-Банк)",
    ),
    CommercialFactor(
        name="reviews_with_schema",
        detection_pattern=None,
        priority="high",
        description_ru="Отзывы с микроразметкой AggregateRating и ссылкой на Яндекс.Карты",
    ),
    CommercialFactor(
        name="yandex_maps_address",
        detection_pattern=re.compile(r"yandex\.ru/maps|api-maps\.yandex\.ru", re.I),
        priority="medium",
        description_ru="Адрес офиса с картой Яндекса (не Google Maps)",
    ),
    CommercialFactor(
        name="contract_offer",
        detection_pattern=re.compile(r"\bдоговор[-\s]?оферт|публичн(ая|ого)\s+оферт", re.I),
        priority="high",
        description_ru="Договор-оферта и политика возврата",
    ),
)
