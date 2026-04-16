"""Yandex Proksima trust signals for Russian tourism sites.

РТО (Реестр Туроператоров) is mandatory for tour operators; travel agencies
that only resell have softer requirements — see models/travel_agency.py.
"""

from __future__ import annotations

import re

from app.core_audit.profile_protocol import EEATSignal


TOURISM_EEAT_SIGNALS: tuple[EEATSignal, ...] = (
    EEATSignal(
        name="rto_number",
        pattern=re.compile(r"\b(РТО|ТТ)\s*[-:]?\s*\d{6,}", re.I),
        weight=0.40,
        priority="critical",
    ),
    EEATSignal(
        name="inn",
        pattern=re.compile(r"\bИНН\s*:?\s*\d{10,12}\b", re.I),
        weight=0.25,
        priority="high",
    ),
    EEATSignal(
        name="ogrn",
        pattern=re.compile(r"\bОГРН\s*:?\s*\d{13}\b", re.I),
        weight=0.15,
        priority="high",
    ),
    EEATSignal(
        name="license_section",
        pattern=re.compile(r"\bлицензи[яи]|свидетельств\w+\s+(?:о\s+)?(?:государственной\s+)?регистрац", re.I),
        weight=0.10,
        priority="medium",
    ),
    EEATSignal(
        name="author_byline",
        pattern=re.compile(r"\b(автор[а:]|написал|подготовил)\b", re.I),
        weight=0.10,
        priority="medium",
    ),
    EEATSignal(
        name="reviews_block",
        pattern=re.compile(r"\bотзыв\w*\s+(наших\s+)?(клиентов|туристов|путешественников)", re.I),
        weight=0.15,
        priority="high",
    ),
    EEATSignal(
        name="yandex_maps_reviews",
        pattern=re.compile(r"yandex\.(?:ru|com)/maps/|yandex\.ru/profile/", re.I),
        weight=0.20,
        priority="high",
    ),
)
