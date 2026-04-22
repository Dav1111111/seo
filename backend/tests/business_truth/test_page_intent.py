"""Page intent extractor — classify a crawled page into (service, geo).

Input: page dict (url, title, h1, meta_description, content_snippet)
     + token universe from understanding (services + geos).
Output: list of DirectionKey the page covers (may be empty for about/
        contact/misc pages, or multiple for hub pages).

Tests first — these are the concrete behaviours we need:
  1. Simple match: single service + single geo in title
  2. URL path match (even when title is generic)
  3. Multi-geo page: "/buggy-tours/" covers all declared geos
  4. Miss: page about company — empty result
  5. Case/whitespace tolerance
  6. Stop-words don't pollute matching (e.g. "цена", "туры")
"""

from __future__ import annotations


def test_title_match_service_and_geo():
    from app.core_audit.business_truth.page_intent import extract_page_intents
    page = {
        "url": "https://grandtourspirit.ru/abkhazia/",
        "title": "Багги-туры в Абхазии от 5000₽ — Гранд Тур",
        "h1": "Багги Абхазия",
        "meta_description": "",
        "content_snippet": "",
    }
    intents = extract_page_intents(
        page,
        services={"багги", "экскурсии", "трансфер"},
        geos={"абхазия", "сочи", "крым"},
    )
    keys = [(k.service, k.geo) for k in intents]
    assert ("багги", "абхазия") in keys


def test_url_path_catches_geo_when_title_is_generic():
    """/sochi/ path should be enough to declare geo even if title is bland."""
    from app.core_audit.business_truth.page_intent import extract_page_intents
    page = {
        "url": "https://grandtourspirit.ru/sochi/",
        "title": "Активный отдых",  # no geo here
        "h1": "Активный отдых для всей семьи",
        "meta_description": "",
        "content_snippet": "Багги, квадроциклы, джиппинг — всё включено.",
    }
    intents = extract_page_intents(
        page,
        services={"багги", "квадроциклы", "джиппинг"},
        geos={"абхазия", "сочи", "крым"},
    )
    keys = [(k.service, k.geo) for k in intents]
    # Multiple services mentioned in content, geo from URL
    assert ("багги", "сочи") in keys or ("квадроциклы", "сочи") in keys


def test_hub_page_covers_multiple_geos():
    """/buggy-tours/ lists all regions → each (buggy, geo) is a valid intent."""
    from app.core_audit.business_truth.page_intent import extract_page_intents
    page = {
        "url": "https://grandtourspirit.ru/buggy-tours/",
        "title": "Багги-туры: Абхазия, Сочи, Красная Поляна",
        "h1": "Багги туры",
        "meta_description": "",
        "content_snippet": "",
    }
    intents = extract_page_intents(
        page,
        services={"багги"},
        geos={"абхазия", "сочи", "красная поляна", "крым"},
    )
    keys = [(k.service, k.geo) for k in intents]
    assert ("багги", "абхазия") in keys
    assert ("багги", "сочи") in keys
    assert ("багги", "красная поляна") in keys
    assert ("багги", "крым") not in keys  # not mentioned


def test_about_page_has_no_service_geo_intent():
    from app.core_audit.business_truth.page_intent import extract_page_intents
    page = {
        "url": "https://grandtourspirit.ru/about/",
        "title": "О компании — Гранд Тур",
        "h1": "О нас",
        "meta_description": "Премиум-туризм",
        "content_snippet": "Мы работаем с 2015 года...",
    }
    intents = extract_page_intents(
        page,
        services={"багги", "экскурсии"},
        geos={"абхазия", "сочи"},
    )
    assert intents == []


def test_case_insensitive_matching():
    from app.core_audit.business_truth.page_intent import extract_page_intents
    page = {
        "url": "https://example.com/tours",
        "title": "БАГГИ ТУРЫ В АБХАЗИИ",
        "h1": "", "meta_description": "", "content_snippet": "",
    }
    intents = extract_page_intents(
        page, services={"багги"}, geos={"абхазия"},
    )
    assert any(
        k.service == "багги" and k.geo == "абхазия" for k in intents
    )


def test_stop_words_in_tokens_dont_produce_fake_matches():
    """'туры' alone shouldn't match a service token — it's a stop word."""
    from app.core_audit.business_truth.page_intent import extract_page_intents
    page = {
        "url": "https://example.com/tours/",
        "title": "Туры по России",  # generic, no real service/geo
        "h1": "", "meta_description": "", "content_snippet": "",
    }
    intents = extract_page_intents(
        page,
        services={"багги", "экскурсии"},
        geos={"абхазия"},
    )
    assert intents == []


def test_morphology_tolerant_endings():
    """'Абхазии' / 'Абхазию' should match token 'абхазия'.

    Simple rule: strip the last 1-2 chars from page tokens when checking,
    so inflected forms hit the base token.
    """
    from app.core_audit.business_truth.page_intent import extract_page_intents
    page = {
        "url": "https://example.com/x",
        "title": "Отдых в Абхазии зимой",  # note: "Абхазии" not "Абхазия"
        "h1": "Экскурсии по Абхазию",      # another form
        "meta_description": "", "content_snippet": "",
    }
    intents = extract_page_intents(
        page,
        services={"экскурсии"},
        geos={"абхазия"},
    )
    keys = [(k.service, k.geo) for k in intents]
    assert ("экскурсии", "абхазия") in keys


def test_multi_word_geo_matches_in_hyphenated_url_slug():
    """/krasnaya-polyana/ should resolve to geo 'красная поляна'."""
    from app.core_audit.business_truth.page_intent import extract_page_intents
    page = {
        "url": "https://example.com/krasnaya-polyana/",
        "title": "Багги в Красной Поляне",
        "h1": "", "meta_description": "", "content_snippet": "",
    }
    intents = extract_page_intents(
        page,
        services={"багги"},
        geos={"красная поляна"},
    )
    keys = [(k.service, k.geo) for k in intents]
    assert ("багги", "красная поляна") in keys
