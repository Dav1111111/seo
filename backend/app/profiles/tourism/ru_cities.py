"""Russian cities and regions whitelist for "out of market" detection.

Used by `classify_wordstat_discovery_phrase` to decide that a query like
«прокат багги в москве» — primary product + geo, but the geo is NOT in
the site's `target_config.geos` — is `out_of_market` rather than
`direct_product`. The owner still wants to see those queries (so they
know the demand exists), but priority weight is zero.

Why a whitelist instead of «anything that isn't my geo»: pymorphy3 will
happily lemmatise random words to plausible nominatives. A whitelist
keeps the false-positive rate to zero — we only flag known cities.

Includes:
  * Capitals and million+ cities (Москва, СПб, etc.)
  * Tourism-relevant regional cities OUTSIDE the Sochi/Abkhazia/Crimea
    ecosystem (the pilot site's market). Adjacent Black Sea coast cities
    like Анапа/Геленджик/Туапсе are listed even though they are nearby
    — they ARE a separate competitive market.
  * Caucasus alternatives (Архыз, Домбай, Эльбрус, КМВ) — for sites that
    don't target them, these are out-of-market signals.

Inflection handling: this module enumerates the most common Russian case
forms (nominative + genitive + dative + accusative + locative + prep)
for each entry, because the classifier matches against raw phrase tokens
without pre-lemmatisation. pymorphy3 is used only for fuzzy fallback.
"""

from __future__ import annotations

import re


# ── Core list, nominative singular ──────────────────────────────────
#
# Multi-word entries (e.g. «нижний новгород») are matched as a 2-token
# combo by `is_other_russian_geo`. Hyphenated forms («ростов-на-дону»)
# are normalized to a single token before matching.

_CITIES_NOMINATIVE: tuple[str, ...] = (
    # Capitals and metropolitan regions
    "москва",
    "санкт-петербург",
    "спб",
    "питер",
    "петербург",
    "подмосковье",
    "московская область",
    "ленинградская область",

    # Million+ cities
    "новосибирск",
    "екатеринбург",
    "казань",
    "нижний новгород",
    "челябинск",
    "самара",
    "уфа",
    "ростов-на-дону",
    "ростов",
    "красноярск",
    "пермь",
    "воронеж",
    "волгоград",
    "омск",
    "краснодар",

    # Tourist-relevant Black Sea / Crimea (separate markets from
    # Sochi/Abkhazia)
    "крым",
    "симферополь",
    "ялта",
    "севастополь",
    "евпатория",
    "судак",
    "феодосия",
    "керчь",
    "алушта",

    # Krasnodar Krai but NOT Sochi/Adler
    "анапа",
    "новороссийск",
    "туапсе",
    "геленджик",
    "ейск",

    # Caucasus alternatives
    "архыз",
    "домбай",
    "приэльбрусье",
    "эльбрус",
    "терскол",
    "карачаевск",
    "теберда",
    "минеральные воды",
    "пятигорск",
    "кисловодск",
    "ессентуки",
    "железноводск",
    "нальчик",
    "владикавказ",
    "грозный",
    "махачкала",
    "дербент",

    # Major tourism destinations elsewhere in Russia
    "калининград",
    "светлогорск",
    "зеленоградск",
    "балтийск",

    "карелия",
    "петрозаводск",
    "сортавала",

    "мурманск",
    "териберка",
    "хибины",
    "кировск",

    "алтай",
    "горно-алтайск",
    "белокуриха",
    "телецкое",

    "байкал",
    "иркутск",
    "листвянка",
    "ольхон",
    "улан-удэ",

    "камчатка",
    "петропавловск-камчатский",

    "сахалин",
    "владивосток",
    "хабаровск",

    "татарстан",
    "болгар",
    "свияжск",

    "ярославль",
    "кострома",
    "владимир",
    "суздаль",
    "тула",
    "калуга",
    "рязань",
    "тверь",
    "великий новгород",
    "псков",
    "вологда",
    "архангельск",
    "соловки",
    "соловецкие острова",
    "переславль",
    "переславль-залесский",
    "сергиев посад",

    "уральск",
    "тобольск",
    "тюмень",
)


# ── Hand-crafted common inflections ─────────────────────────────────
#
# Keys: nominative (must appear above). Values: extra forms that
# appear in tourism Wordstat results. We don't bother with every
# theoretical case — just the forms that actually show up («в москве»,
# «из москвы», «в москву», «по москве»).

_INFLECTIONS: dict[str, tuple[str, ...]] = {
    "москва": ("москвы", "москве", "москву", "москвой"),
    "санкт-петербург": (
        "санкт-петербурга", "санкт-петербурге", "санкт-петербургу",
    ),
    "петербург": ("петербурга", "петербурге", "петербургу"),
    "новосибирск": ("новосибирска", "новосибирске", "новосибирску"),
    "екатеринбург": ("екатеринбурга", "екатеринбурге", "екатеринбургу"),
    "казань": ("казани", "казанью"),
    "челябинск": ("челябинска", "челябинске", "челябинску"),
    "самара": ("самары", "самаре", "самару", "самарой"),
    "уфа": ("уфы", "уфе", "уфу", "уфой"),
    "ростов-на-дону": (
        "ростова-на-дону", "ростове-на-дону", "ростову-на-дону",
    ),
    "ростов": ("ростова", "ростове", "ростову"),
    "красноярск": ("красноярска", "красноярске", "красноярску"),
    "пермь": ("перми", "пермью"),
    "воронеж": ("воронежа", "воронеже", "воронежу"),
    "волгоград": ("волгограда", "волгограде", "волгограду"),
    "омск": ("омска", "омске", "омску"),
    "краснодар": ("краснодара", "краснодаре", "краснодару"),

    "крым": ("крыма", "крыму", "крымом"),
    "симферополь": ("симферополя", "симферополе", "симферополю"),
    "ялта": ("ялты", "ялте", "ялту", "ялтой"),
    "севастополь": ("севастополя", "севастополе", "севастополю"),
    "евпатория": ("евпатории", "евпаторию", "евпаторией"),
    "судак": ("судака", "судаке", "судаку"),
    "феодосия": ("феодосии", "феодосию", "феодосией"),
    "керчь": ("керчи", "керчью"),
    "алушта": ("алушты", "алуште", "алушту", "алуштой"),

    "анапа": ("анапы", "анапе", "анапу", "анапой"),
    "новороссийск": ("новороссийска", "новороссийске", "новороссийску"),
    "туапсе": (),  # indeclinable
    "геленджик": ("геленджика", "геленджике", "геленджику"),
    "ейск": ("ейска", "ейске", "ейску"),

    "архыз": ("архыза", "архызе", "архызу"),
    "домбай": ("домбая", "домбае", "домбаю"),
    "приэльбрусье": ("приэльбрусья", "приэльбрусьи", "приэльбрусью"),
    "эльбрус": ("эльбруса", "эльбрусе", "эльбрусу"),
    "терскол": ("терскола", "терсколе", "терсколу"),
    "карачаевск": ("карачаевска", "карачаевске", "карачаевску"),
    "теберда": ("теберды", "теберде", "теберду", "тебердой"),
    "минеральные воды": ("минеральных вод", "минеральных водах"),
    "пятигорск": ("пятигорска", "пятигорске", "пятигорску"),
    "кисловодск": ("кисловодска", "кисловодске", "кисловодску"),
    "ессентуки": ("ессентуков", "ессентуках"),
    "железноводск": ("железноводска", "железноводске", "железноводску"),
    "нальчик": ("нальчика", "нальчике", "нальчику"),
    "владикавказ": ("владикавказа", "владикавказе", "владикавказу"),
    "грозный": ("грозного", "грозном", "грозному"),
    "махачкала": ("махачкалы", "махачкале", "махачкалу", "махачкалой"),
    "дербент": ("дербента", "дербенте", "дербенту"),

    "калининград": ("калининграда", "калининграде", "калининграду"),
    "светлогорск": ("светлогорска", "светлогорске", "светлогорску"),
    "зеленоградск": ("зеленоградска", "зеленоградске", "зеленоградску"),
    "балтийск": ("балтийска", "балтийске", "балтийску"),

    "карелия": ("карелии", "карелию", "карелией"),
    "петрозаводск": ("петрозаводска", "петрозаводске", "петрозаводску"),
    "сортавала": ("сортавалы", "сортавале", "сортавалу"),

    "мурманск": ("мурманска", "мурманске", "мурманску"),
    "териберка": ("териберки", "териберке", "териберку"),
    "хибины": ("хибин", "хибинах"),
    "кировск": ("кировска", "кировске", "кировску"),

    "алтай": ("алтая", "алтае", "алтаю"),
    "горно-алтайск": (
        "горно-алтайска", "горно-алтайске", "горно-алтайску",
    ),
    "белокуриха": ("белокурихи", "белокурихе", "белокуриху"),
    "телецкое": ("телецкого", "телецком", "телецкому"),

    "байкал": ("байкала", "байкале", "байкалу"),
    "иркутск": ("иркутска", "иркутске", "иркутску"),
    "листвянка": ("листвянки", "листвянке", "листвянку"),
    "ольхон": ("ольхона", "ольхоне", "ольхону"),
    "улан-удэ": (),  # indeclinable

    "камчатка": ("камчатки", "камчатке", "камчатку", "камчаткой"),
    "петропавловск-камчатский": (
        "петропавловска-камчатского", "петропавловске-камчатском",
    ),

    "сахалин": ("сахалина", "сахалине", "сахалину"),
    "владивосток": ("владивостока", "владивостоке", "владивостоку"),
    "хабаровск": ("хабаровска", "хабаровске", "хабаровску"),

    "татарстан": ("татарстана", "татарстане", "татарстану"),
    "болгар": ("болгара", "болгаре", "болгару"),
    "свияжск": ("свияжска", "свияжске", "свияжску"),

    "ярославль": ("ярославля", "ярославле", "ярославлю"),
    "кострома": ("костромы", "костроме", "кострому", "костромой"),
    "владимир": ("владимира", "владимире", "владимиру"),
    "суздаль": ("суздаля", "суздале", "суздалю"),
    "тула": ("тулы", "туле", "тулу", "тулой"),
    "калуга": ("калуги", "калуге", "калугу", "калугой"),
    "рязань": ("рязани", "рязанью"),
    "тверь": ("твери", "тверью"),
    "великий новгород": (
        "великого новгорода", "великом новгороде", "великому новгороду",
    ),
    "псков": ("пскова", "пскове", "пскову"),
    "вологда": ("вологды", "вологде", "вологду", "вологдой"),
    "архангельск": ("архангельска", "архангельске", "архангельску"),
    "соловки": ("соловков", "соловках"),
    "переславль-залесский": (
        "переславля-залесского", "переславле-залесском",
    ),
    "сергиев посад": (
        "сергиева посада", "сергиевом посаде", "сергиеву посаду",
    ),

    "тюмень": ("тюмени", "тюменью"),
    "тобольск": ("тобольска", "тобольске", "тобольску"),
}


# ── Build a flat frozenset of all known forms ───────────────────────


def _normalise(token: str) -> str:
    """Normalise to lower + ё→е + collapsed whitespace.

    Matches `_clean_wordstat_seed` from `collectors/tasks.py` so the
    classifier and this module stay consistent on edge tokens.
    """
    text = (token or "").strip().lower().replace("ё", "е")
    return " ".join(text.split())


def _build_all_forms() -> frozenset[str]:
    out: set[str] = set()
    for canonical in _CITIES_NOMINATIVE:
        out.add(_normalise(canonical))
        for form in _INFLECTIONS.get(canonical, ()):  # type: ignore[arg-type]
            out.add(_normalise(form))
    return frozenset(out)


RU_CITIES_AND_REGIONS: frozenset[str] = _build_all_forms()
"""Flat set of every recognised geo form (nominative + inflections)."""


# ── Two-word combos so we can match «в нижнем новгороде» ────────────


def _multiword_canonicals() -> frozenset[str]:
    out: set[str] = set()
    for canonical in _CITIES_NOMINATIVE:
        if " " in canonical:
            out.add(_normalise(canonical))
    for forms in _INFLECTIONS.values():
        for form in forms:
            if " " in form:
                out.add(_normalise(form))
    return frozenset(out)


_MULTIWORD_FORMS: frozenset[str] = _multiword_canonicals()


# ── Lemma map back to canonical for the reason string ───────────────


def _build_form_to_canonical() -> dict[str, str]:
    out: dict[str, str] = {}
    for canonical in _CITIES_NOMINATIVE:
        canon_norm = _normalise(canonical)
        out[canon_norm] = canon_norm
        for form in _INFLECTIONS.get(canonical, ()):  # type: ignore[arg-type]
            out[_normalise(form)] = canon_norm
    return out


_FORM_TO_CANONICAL: dict[str, str] = _build_form_to_canonical()


# ── Public API ──────────────────────────────────────────────────────


_WORD_RE = re.compile(r"[\w-]+", re.UNICODE)


def _tokenise(phrase: str) -> list[str]:
    """Tokenise the phrase the same way the classifier does.

    Strip punctuation, lowercase, ё→е. Keep hyphens (Ростов-на-Дону).
    """
    text = _normalise(phrase)
    return _WORD_RE.findall(text)


def is_other_russian_geo(
    tokens_or_phrase: list[str] | str,
    my_geos: set[str] | frozenset[str],
) -> tuple[bool, str | None]:
    """Detect a Russian city/region in the phrase that is NOT in
    `my_geos`.

    Returns `(True, canonical_name)` on hit, `(False, None)` otherwise.

    Matches against:
      1. Single tokens, against every known case form.
      2. Two-token combos, for multi-word geos like «нижний новгород»,
         «ростов на дону» (without hyphens), «великий новгород».
    """
    if isinstance(tokens_or_phrase, str):
        tokens = _tokenise(tokens_or_phrase)
    else:
        tokens = [_normalise(t) for t in tokens_or_phrase if t]

    if not tokens:
        return False, None

    my_norm = {_normalise(g) for g in my_geos if g}

    # 2-word combos first (greedy: «нижний новгород» beats «нижний» alone)
    for i in range(len(tokens) - 1):
        combo = f"{tokens[i]} {tokens[i + 1]}"
        if combo in _MULTIWORD_FORMS:
            canon = _FORM_TO_CANONICAL.get(combo, combo)
            if canon not in my_norm and combo not in my_norm:
                return True, canon

    # Single tokens
    for tok in tokens:
        if tok in RU_CITIES_AND_REGIONS:
            canon = _FORM_TO_CANONICAL.get(tok, tok)
            # If the owner's profile already lists this geo as primary
            # or secondary, it is NOT «other»: skip and keep looking.
            if canon in my_norm or tok in my_norm:
                continue
            return True, canon

    return False, None


__all__ = [
    "RU_CITIES_AND_REGIONS",
    "is_other_russian_geo",
]
