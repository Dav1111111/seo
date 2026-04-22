"""Auto-derive vocabulary from site data, no onboarding input.

Principle: platform reads title+h1 of every page + recent queries from
Webmaster, and figures out WHAT the business actually does — based on
what's actually there, not what the owner (or an onboarding LLM)
typed once.

Geos are matched against a bundled Russian tourism gazetteer. Anything
else that co-occurs with a geo and appears frequently enough is a
service candidate.
"""

from __future__ import annotations

import pytest


def test_single_direction_site_derives_it():
    from app.core_audit.business_truth.auto_vocabulary import (
        derive_vocabulary_from_data,
    )
    pages = [
        {"title": "Багги-туры в Абхазии", "h1": "Багги Абхазия", "url": "https://x/a"},
        {"title": "Багги в Абхазии цена",  "h1": "Багги",        "url": "https://x/b"},
    ]
    queries = [("багги абхазия", 500), ("багги абхазия цена", 200)]
    vocab = derive_vocabulary_from_data(pages, queries)
    assert "багги" in vocab["services"]
    assert "абхазия" in vocab["geos"]


def test_blog_mentions_dont_become_services():
    """A word mentioned once in a single blog URL slug shouldn't
    become a declared service."""
    from app.core_audit.business_truth.auto_vocabulary import (
        derive_vocabulary_from_data,
    )
    pages = [
        {"title": "Багги Абхазия",      "h1": "Багги",         "url": "https://x/"},
        {"title": "Багги Абхазия 2",    "h1": "Багги туры",    "url": "https://x/a"},
        {"title": "Кейс клиента",       "h1": "Наш кейс",      "url": "https://x/stories/seo-abkhazia-excursions"},
    ]
    queries = [("багги абхазия", 500)]
    vocab = derive_vocabulary_from_data(pages, queries)
    # Багги — real service (2 pages, query traffic)
    assert "багги" in vocab["services"]
    # "экскурсии" — only in one URL slug, no query traffic → NOT a service
    assert "экскурсии" not in vocab["services"]


def test_multi_geo_site_extracts_all_present_geos():
    from app.core_audit.business_truth.auto_vocabulary import (
        derive_vocabulary_from_data,
    )
    pages = [
        {"title": "Багги в Абхазии",     "h1": "Багги Абхазия", "url": "https://x/a"},
        {"title": "Багги в Сочи",        "h1": "Багги Сочи",    "url": "https://x/b"},
        {"title": "Багги в Красной Поляне","h1": "Багги",        "url": "https://x/c"},
    ]
    queries = [("багги абхазия", 500), ("багги сочи", 100)]
    vocab = derive_vocabulary_from_data(pages, queries)
    assert "абхазия" in vocab["geos"]
    assert "сочи" in vocab["geos"]
    assert "красная поляна" in vocab["geos"]


def test_noise_and_stopwords_excluded():
    """'туры', 'цена', 'отдых' are noise — shouldn't end up as services."""
    from app.core_audit.business_truth.auto_vocabulary import (
        derive_vocabulary_from_data,
    )
    pages = [
        {"title": "Туры и отдых в Абхазии цена", "h1": "Туры", "url": "https://x/"},
        {"title": "Багги Абхазия туры",           "h1": "Багги", "url": "https://x/a"},
    ]
    queries = [("багги абхазия цена", 500), ("туры в абхазии", 200)]
    vocab = derive_vocabulary_from_data(pages, queries)
    assert "багги" in vocab["services"]
    assert "туры" not in vocab["services"]
    assert "отдых" not in vocab["services"]
    assert "цена" not in vocab["services"]


def test_services_require_minimum_frequency():
    """A word appearing on exactly 1 page with 0 traffic isn't a service."""
    from app.core_audit.business_truth.auto_vocabulary import (
        derive_vocabulary_from_data,
    )
    pages = [
        {"title": "Багги в Абхазии", "h1": "Багги", "url": "https://x/a"},
        {"title": "Багги в Абхазии", "h1": "Багги", "url": "https://x/b"},
        {"title": "Кейтеринг на выезде", "h1": "Кейтеринг", "url": "https://x/c"},  # 1 page, 0 queries
    ]
    queries = [("багги абхазия", 500)]
    vocab = derive_vocabulary_from_data(pages, queries, min_frequency=2)
    assert "багги" in vocab["services"]
    assert "кейтеринг" not in vocab["services"]  # doesn't pass threshold


def test_traffic_only_service_still_captured():
    """Word doesn't appear on pages but gets heavy Webmaster queries —
    THAT is the classic 'traffic-only' signal. Include in vocab so
    BusinessTruth can surface it as traffic_only direction."""
    from app.core_audit.business_truth.auto_vocabulary import (
        derive_vocabulary_from_data,
    )
    pages = [
        {"title": "Багги Абхазия", "h1": "Багги", "url": "https://x/"},
    ]
    # Many queries for "джиппинг", none on the site
    queries = [
        ("джиппинг абхазия", 800),
        ("джиппинг абхазия цена", 400),
    ]
    vocab = derive_vocabulary_from_data(pages, queries)
    assert "джиппинг" in vocab["services"]


def test_empty_input_returns_empty_vocab():
    from app.core_audit.business_truth.auto_vocabulary import (
        derive_vocabulary_from_data,
    )
    vocab = derive_vocabulary_from_data([], [])
    assert vocab["services"] == set()
    assert vocab["geos"] == set()


def test_url_slug_geo_detected_without_title_mention():
    """/sochi/ path → sochi is a geo even if title is generic."""
    from app.core_audit.business_truth.auto_vocabulary import (
        derive_vocabulary_from_data,
    )
    pages = [
        {"title": "Активный отдых", "h1": "Активный отдых",
         "url": "https://x/sochi/"},
        {"title": "Активный отдых", "h1": "Активный отдых",
         "url": "https://x/sochi/2"},
    ]
    queries = [("багги сочи", 200)]
    vocab = derive_vocabulary_from_data(pages, queries)
    assert "сочи" in vocab["geos"]


def test_low_impression_queries_count_less_than_strong_ones():
    """Regression: query_impression_floor actually weighs queries now.

    Pre-fix, both branches of the weight ternary returned 1 — so a
    1-impression fluke carried the same signal as a 1000-impression
    workhorse. After fix, below-floor queries count 0.5.
    """
    from app.core_audit.business_truth.auto_vocabulary import (
        derive_vocabulary_from_data,
    )
    pages = [
        {"title": "Багги Абхазия", "h1": "Багги", "url": "https://x/a"},
    ]
    # 'фигня' appears in 3 different rare queries (1 impression each).
    # Without weighting fix, that'd bank 3 points and pass min_frequency=2.
    # With weighting, 3 × 0.5 = 1.5, correctly below threshold.
    queries = [
        ("багги абхазия",       500),  # real service
        ("фигня абхазия",         1),  # rare noise 1
        ("фигня абхазия 2",       1),  # rare noise 2
        ("фигня абхазия 3",       1),  # rare noise 3
    ]
    vocab = derive_vocabulary_from_data(
        pages, queries,
        min_frequency=2,
        query_impression_floor=50,
    )
    assert "багги" in vocab["services"]
    assert "фигня" not in vocab["services"]


def test_brand_tokens_from_domain_blocked_as_services():
    """grandtourspirit.ru → 'grand', 'tour', 'spirit' NOT in services."""
    from app.core_audit.business_truth.auto_vocabulary import (
        derive_vocabulary_from_data,
    )
    pages = [
        {"title": "Grand Tour Spirit — багги Абхазия", "h1": "Grand",
         "url": "https://grandtourspirit.ru/"},
        {"title": "Grand Tour Spirit — багги Сочи",    "h1": "Tour",
         "url": "https://grandtourspirit.ru/sochi/"},
    ]
    vocab = derive_vocabulary_from_data(
        pages, [("багги абхазия", 500)],
        site_domain="grandtourspirit.ru",
    )
    assert "grand" not in vocab["services"]
    assert "tour" not in vocab["services"]
    assert "spirit" not in vocab["services"]
    # But real service still passes
    assert "багги" in vocab["services"]


def test_morphological_forms_of_gazetteer_not_services():
    """'Абхазии' appears in a query but shouldn't spawn a fake
    service token — its stems match gazetteer entry 'абхазия'."""
    from app.core_audit.business_truth.auto_vocabulary import (
        derive_vocabulary_from_data,
    )
    pages = [
        {"title": "Багги в Абхазии", "h1": "Багги Абхазия", "url": "https://x/"},
        {"title": "Багги Абхазия",   "h1": "Багги",        "url": "https://x/a"},
    ]
    queries = [("багги в абхазии цена", 500), ("багги в абхазию туры", 200)]
    vocab = derive_vocabulary_from_data(pages, queries)
    assert "абхазия" in vocab["geos"]
    assert "абхазии" not in vocab["services"]
    assert "абхазию" not in vocab["services"]


def test_question_words_not_services():
    """'как', 'что', 'какая' etc. blocked."""
    from app.core_audit.business_truth.auto_vocabulary import (
        derive_vocabulary_from_data,
    )
    pages = [
        {"title": "Багги в Абхазии", "h1": "Багги",
         "url": "https://x/"},
        {"title": "Багги в Абхазии 2", "h1": "Багги",
         "url": "https://x/a"},
    ]
    queries = [
        ("какая нужна категория для багги", 100),
        ("как забронировать багги", 200),
        ("что такое багги тур", 150),
    ]
    vocab = derive_vocabulary_from_data(pages, queries)
    assert "какая" not in vocab["services"]
    assert "как" not in vocab["services"]
    assert "что" not in vocab["services"]
    assert "багги" in vocab["services"]


def test_only_gazetteer_geos_returned():
    """Random non-gazetteer word shouldn't leak into geos even if
    it looks like a location."""
    from app.core_audit.business_truth.auto_vocabulary import (
        derive_vocabulary_from_data,
    )
    pages = [
        {"title": "Багги в Бомбее", "h1": "Бомбей", "url": "https://x/"},
    ]
    queries = [("багги бомбей", 100)]
    vocab = derive_vocabulary_from_data(pages, queries)
    # Bombay isn't in the Russian tourism gazetteer
    assert "бомбей" not in vocab["geos"]
