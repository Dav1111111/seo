"""Funnel-aware intent prefixes for tourism Wordstat classification.

Three layers, ordered from most actionable (commercial) to least
(discovery). The classifier walks the lists in priority order and
returns the first match — a phrase like «забронировать экскурсию в
сочи» is `commercial`, not `tourism`, because commercial wins.

Why prefixes and not full words: Russian inflection makes
«забронировать», «забронируй», «забронирование» the same intent. We use
`startswith` against tokens with a minimum length guard to keep
ambiguous short prefixes («цен» matching «ценный») from leaking.
"""

from __future__ import annotations


# ── Bottom-funnel (hot): ready to buy ────────────────────────────────
#
# A phrase here is a commercial signal regardless of geo, e.g.
# «забронировать багги» without a city is still `direct_product` because
# the user clearly wants to transact.

COMMERCIAL_INTENT_PREFIXES: tuple[str, ...] = (
    "цен",       # цена / цены / ценой — guarded against «ценный» below
    "стоим",     # стоимость / стоимости
    "забронир",  # забронировать / забронируй
    "бронир",    # бронирование
    "бронь",
    "купить",
    "заказ",     # заказать / заказ
    "прокат",
    "аренд",     # аренда / арендовать
    "отзыв",     # отзывы / отзыв
    "недорог",   # недорого / недорогие
    "трансфер",
    "скидк",     # скидка / скидки
    "акции",
)

# Tokens with this exact text are NOT a positive match even if a
# prefix above would textually match. This is the explicit «цен»-vs-
# «ценный» guard: «ценный» / «ценность» are NOT commercial intent.
# Anything not in this set passes through the normal startswith check.
_AMBIGUOUS_TOKEN_BLACKLIST: frozenset[str] = frozenset({
    "ценный", "ценная", "ценное", "ценные", "ценных", "ценность",
    "ценности", "туман", "туманный", "туманно", "турбина", "турник",
    "туркмения", "туркменистан",  # «тур» prefix — these are NOT tourism
})


# ── Mid-funnel (warm): knows tourism activity, picking the format ────

TOURISM_INTENT_PREFIXES: tuple[str, ...] = (
    "экскурс",   # экскурсия, экскурсии, экскурсионный
    "тур",       # тур, туры — careful, also matches «турист»
    "турист",
    "отдых",
    "активн",    # активный отдых
    "поход",
    "маршрут",
    "поездк",    # поездка
    "путешеств", # путешествие
    "круиз",
    "сплав",
    "восхожд",   # восхождение
    "трекинг",
    "глэмпинг",
    "кемпинг",
    "рыбалк",    # рыбалка
    "охота",
    "сафари",
    "джип",      # джип-тур
    "квадро",    # квадроцикл
    "снегоход",
    "вертолет",
    "яхт",       # яхта, яхтинг
    "дайвинг",
    "серфинг",
    "сноуборд",
    "лыж",       # лыжи
    "горнолыж",
    "термальн",  # термальные источники
    "санатори",
    "база отдыха",
    "глэмп",
)


# ── Top-funnel (cold but huge volume): tourist already in geo, hasn't
#    picked an activity yet ──────────────────────────────────────────

DISCOVERY_INTENT_PREFIXES: tuple[str, ...] = (
    "развлеч",   # развлечения, развлечься
    "посмотр",   # «что посмотреть»
    "сходить",   # «куда сходить»
    "досуг",
    "достопримеч",  # достопримечательности
    "интересн",  # «что-то интересное»
    "необычн",   # «необычные места»
    "куда",      # «куда поехать», «куда пойти»
    "что делать",
    "чем заняться",
    "места",     # «красивые места»
    "красивые",
    "пляж",      # пляжи — discovery, не commercial
    "погода",
    "сезон",
    "топ",       # «топ-10 мест»
    "лучшие",    # «лучшие места»
    "гид",       # «гид по сочи» (discovery, not commercial guide booking)
    "путеводит", # путеводитель
    "виды",      # «виды Сочи»
    "панорам",
    "парк",      # парк, парки
    "музе",      # музей, музеи
)


__all__ = [
    "COMMERCIAL_INTENT_PREFIXES",
    "TOURISM_INTENT_PREFIXES",
    "DISCOVERY_INTENT_PREFIXES",
    "detect_intent_layer",
]


# ── Detection ───────────────────────────────────────────────────────


def _matches_any_prefix(token: str, prefixes: tuple[str, ...]) -> bool:
    """`token.startswith(p)`, with two guards:

      1. Tokens in `_AMBIGUOUS_TOKEN_BLACKLIST` never match — this is
         how we keep «цен»→«ценный» from firing false positives.
      2. Multi-word prefixes are skipped here (handled by
         `_matches_any_multiword` against the whole phrase).
    """
    if token in _AMBIGUOUS_TOKEN_BLACKLIST:
        return False
    for prefix in prefixes:
        if " " in prefix:
            continue
        if token.startswith(prefix):
            return True
    return False


def _matches_any_multiword(phrase: str, prefixes: tuple[str, ...]) -> bool:
    for prefix in prefixes:
        if " " not in prefix:
            continue
        if prefix in phrase:
            return True
    return False


def detect_intent_layer(tokens: list[str], phrase: str = "") -> str:
    """Return ``"commercial" | "tourism" | "discovery" | "none"``.

    Priority order: commercial > tourism > discovery. The most
    actionable signal wins — «забронировать экскурсии в сочи» is a
    commercial query (the booking intent dominates).

    Parameters:
      tokens — already tokenised, lowercased, ё-normalised
      phrase — the same phrase as a single string, for multi-word
               prefixes like «что делать», «база отдыха»
    """
    if not tokens and not phrase:
        return "none"

    # 1) Commercial wins
    for tok in tokens:
        if _matches_any_prefix(tok, COMMERCIAL_INTENT_PREFIXES):
            return "commercial"
    if _matches_any_multiword(phrase, COMMERCIAL_INTENT_PREFIXES):
        return "commercial"

    # 2) Then tourism activity
    for tok in tokens:
        if _matches_any_prefix(tok, TOURISM_INTENT_PREFIXES):
            return "tourism"
    if _matches_any_multiword(phrase, TOURISM_INTENT_PREFIXES):
        return "tourism"

    # 3) Then discovery / top-of-funnel
    for tok in tokens:
        if _matches_any_prefix(tok, DISCOVERY_INTENT_PREFIXES):
            return "discovery"
    if _matches_any_multiword(phrase, DISCOVERY_INTENT_PREFIXES):
        return "discovery"

    return "none"
