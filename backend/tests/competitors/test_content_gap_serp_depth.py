from app.core_audit.competitors.content_gap import analyze_gaps
from app.core_audit.competitors.opportunities import build_growth_opportunities


def test_gap_reports_checked_serp_depth_not_top_100_claim() -> None:
    gaps = analyze_gaps(
        own_domain="example.ru",
        competitor_domains=["rival.ru"],
        query_to_serp={
            "туры сочи": [
                {
                    "position": 1,
                    "domain": "rival.ru",
                    "url": "https://rival.ru/tours",
                    "title": "Туры Сочи",
                },
                {
                    "position": 10,
                    "domain": "other.ru",
                    "url": "https://other.ru/tours",
                    "title": "Other",
                },
            ],
        },
    )

    assert len(gaps) == 1
    assert gaps[0].site_position is None
    assert gaps[0].serp_depth == 10
    assert gaps[0].to_dict()["serp_depth"] == 10


def test_gap_skips_when_own_site_is_visible_in_checked_sample() -> None:
    gaps = analyze_gaps(
        own_domain="example.ru",
        competitor_domains=["rival.ru"],
        query_to_serp={
            "туры сочи": [
                {
                    "position": 1,
                    "domain": "rival.ru",
                    "url": "https://rival.ru/tours",
                    "title": "Туры Сочи",
                },
                {
                    "position": 9,
                    "domain": "example.ru",
                    "url": "https://example.ru/tours",
                    "title": "Наши туры",
                },
            ],
        },
    )

    assert gaps == []


def test_opportunity_reasoning_uses_honest_serp_copy() -> None:
    """Regression pin: the opportunity reasoning text must describe the
    site as missing from the *checked SERP sample*, not from "the index"
    or "top-100". Yandex Search API requests top-10 by default and we
    have no signal about pages beyond that — claiming otherwise misleads
    the owner. If a future refactor reverts to «не в индексе» / «не в
    топ-100», this test must fail."""
    gap_rows = [
        {
            "query": "туры сочи",
            "site_position": None,
            "serp_depth": 10,
            "competitor_domain": "rival.ru",
            "competitor_position": 2,
            "competitor_url": "https://rival.ru/tours",
            "competitor_title": "Туры Сочи",
            "other_competitors": [],
        },
    ]

    opps = build_growth_opportunities(
        content_gaps=gap_rows,
        deep_dive_self=None,
        deep_dive_competitors=None,
        own_pages=None,
    )

    assert opps, "expected at least one content-gap opportunity"
    reasoning = opps[0]["reasoning_ru"]

    # Canonical honest copy — at least one of the approved phrasings
    # must be present.
    honest_phrasings = (
        "не в проверенном топ-",
        "не видим в проверенном топ-",
        "не найдено в проверенной SERP-выборке",
    )
    assert any(p in reasoning for p in honest_phrasings), (
        f"Opportunity reasoning lost honest SERP-depth copy. "
        f"Got: {reasoning!r}"
    )

    # And the dishonest reverted forms must NOT come back.
    forbidden = ("не в индексе", "не в топ-100", "отсутствует в индексе")
    for bad in forbidden:
        assert bad not in reasoning, (
            f"Opportunity reasoning regressed to dishonest copy "
            f"{bad!r}. Full text: {reasoning!r}"
        )
