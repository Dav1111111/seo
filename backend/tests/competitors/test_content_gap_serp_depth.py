from app.core_audit.competitors.content_gap import analyze_gaps


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
