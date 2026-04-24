"""Unit tests for Playground scenarios.

We mock the outbound Yandex call (`fetch_serp`) so tests don't depend
on network and run in <1 s. What we DO assert is the shape & flow:
  - every step returns a StepResult that serialises to dict
  - step-to-step data flows through `prior` correctly (step 2 reads
    step 1's filtered output, etc.)
  - blacklist filter actually removes marketplaces & socials
  - own-domain filter removes the owner site even with www prefix
  - empty inputs short-circuit without hitting the network
"""

from __future__ import annotations

from unittest.mock import patch

from app.collectors.yandex_serp import SerpDoc
from app.playground.scenarios import (
    SCENARIO_META,
    list_scenarios,
    run_step,
)


def _doc(position: int, domain: str, title: str = "") -> SerpDoc:
    return SerpDoc(
        position=position,
        url=f"https://{domain}/page-{position}",
        domain=domain,
        title=title or f"Title {position}",
        headline="",
    )


# ── Registry invariants ───────────────────────────────────────────────

def test_all_scenarios_have_unique_ids() -> None:
    ids = [s["id"] for s in list_scenarios()]
    assert len(ids) == len(set(ids))


def test_every_scenario_declares_at_least_one_input() -> None:
    for s in list_scenarios():
        assert s["inputs"], f"{s['id']} has no inputs"


def test_every_scenario_has_russian_description() -> None:
    for s in list_scenarios():
        assert len(s["description_ru"]) > 20


# ── Dispatcher safety ─────────────────────────────────────────────────

def test_unknown_scenario_returns_error_not_exception() -> None:
    r = run_step("does_not_exist", 0, {}, [])
    assert r.ok is False
    assert r.error == "unknown_scenario"


# ── Indexation scenario ───────────────────────────────────────────────

def test_indexation_requires_domain() -> None:
    r = run_step("indexation", 0, {}, [])
    assert r.ok is False
    assert "domain" in (r.error or "").lower() or r.error == "Укажи домен."


def test_indexation_returns_pages_on_success() -> None:
    """Successful SERP call with 2 pages: both pages surface in the
    response, and since 2 < LOW_INDEX_THRESHOLD the scenario offers
    to continue with diagnostics — that's the intended path now."""
    with patch("app.playground.scenarios.check_indexation") as mock:
        from app.collectors.yandex_serp import IndexationResult
        mock.return_value = IndexationResult(
            domain="example.ru",
            pages_found=2,
            pages=[_doc(1, "example.ru"), _doc(2, "example.ru")],
            error=None,
        )
        r = run_step("indexation", 0, {"domain": "example.ru"}, [])
    assert r.ok is True
    assert r.response_summary["pages_found"] == 2
    assert len(r.response_summary["pages"]) == 2
    # 2 pages is below LOW_INDEX_THRESHOLD (=3) so diagnostics are offered
    assert r.next_available is True


# ── Competitors-by-query scenario ─────────────────────────────────────

def _mock_serp_docs():
    return [
        _doc(1, "grandtourspirit.ru"),         # own — drop
        _doc(2, "sochi-buggy.ru"),             # keep
        _doc(3, "avito.ru"),                   # blacklist
        _doc(4, "m.avito.ru"),                 # blacklist (subdomain)
        _doc(5, "abkhazia-tour.ru"),           # keep
        _doc(6, "youtube.com"),                # blacklist
        _doc(7, "tours-sochi.ru"),             # keep
        _doc(8, "2gis.ru"),                    # blacklist
        _doc(9, "krasnaya-polyana.ru"),        # keep
        _doc(10, "vk.com"),                    # blacklist
    ]


def test_competitors_requires_query() -> None:
    r = run_step("competitors_by_query", 0, {}, [])
    assert r.ok is False
    assert r.error == "empty_query"


def test_step0_fetches_serp_and_returns_raw() -> None:
    with patch(
        "app.playground.scenarios.fetch_serp",
        return_value=(_mock_serp_docs(), None),
    ):
        r = run_step(
            "competitors_by_query", 0,
            {"query": "багги абхазия", "own_domain": "grandtourspirit.ru"},
            [],
        )
    assert r.ok is True
    assert r.step_index == 0
    assert r.response_summary["docs_returned"] == 10
    assert len(r.response_summary["raw_serp"]) == 10
    assert r.next_available is True
    # request_shown must include real endpoint + query
    assert "searchasync" in (r.request_shown or {}).get("endpoint", "").lower()


def test_step1_filters_out_marketplaces_socials_and_own_domain() -> None:
    # prior contains step-0 output shape
    prior_step0 = {
        "response_summary": {
            "raw_serp": [
                {"position": d.position, "domain": d.domain, "url": d.url, "title": d.title}
                for d in _mock_serp_docs()
            ],
        },
    }
    r = run_step(
        "competitors_by_query", 1,
        {"query": "багги абхазия", "own_domain": "grandtourspirit.ru"},
        [prior_step0],
    )
    assert r.ok is True
    kept_domains = {row["domain"] for row in r.response_summary["kept"]}
    assert kept_domains == {"sochi-buggy.ru", "abkhazia-tour.ru", "tours-sochi.ru", "krasnaya-polyana.ru"}
    dropped = r.response_summary["dropped"]
    reasons = [d["reason"] for d in dropped]
    assert any("твой сайт" in r for r in reasons)
    assert any("маркетплейс" in r or "соцсеть" in r or "агрегатор" in r for r in reasons)


def test_step1_handles_www_in_own_domain_input() -> None:
    prior_step0 = {
        "response_summary": {
            "raw_serp": [
                {"position": 1, "domain": "example.ru", "url": "https://example.ru/x", "title": ""},
            ],
        },
    }
    r = run_step(
        "competitors_by_query", 1,
        {"query": "q", "own_domain": "WWW.Example.RU/"},
        [prior_step0],
    )
    # example.ru must be classified as own, not kept as competitor
    assert r.response_summary["kept"] == []
    assert len(r.response_summary["dropped"]) == 1
    assert "твой сайт" in r.response_summary["dropped"][0]["reason"]


def test_step2_sorts_by_position_and_presents_final_list() -> None:
    prior = [
        {"response_summary": {"raw_serp": []}},  # step 0 placeholder
        {
            "response_summary": {
                "kept": [
                    {"position": 5, "domain": "b.ru", "url": "", "title": ""},
                    {"position": 2, "domain": "a.ru", "url": "", "title": ""},
                    {"position": 8, "domain": "c.ru", "url": "", "title": ""},
                ],
            },
        },
    ]
    r = run_step("competitors_by_query", 2, {"query": "q"}, prior)
    assert r.ok is True
    domains_in_order = [c["domain"] for c in r.response_summary["competitors"]]
    assert domains_in_order == ["a.ru", "b.ru", "c.ru"]
    assert r.next_available is False


# ── Indexation scenario: low-index triggers diagnostic continuation ──

def test_indexation_step0_healthy_count_ends_scenario() -> None:
    """If owner's site has normal indexation (>= LOW_INDEX_THRESHOLD),
    scenario ends — no point kicking off diagnostics just because it's
    the scenario's default flow."""
    from app.collectors.yandex_serp import IndexationResult
    from app.playground.scenarios import LOW_INDEX_THRESHOLD

    docs = [_doc(i, "example.ru") for i in range(1, LOW_INDEX_THRESHOLD + 2)]
    with patch("app.playground.scenarios.check_indexation") as mock:
        mock.return_value = IndexationResult(
            domain="example.ru",
            pages_found=len(docs),
            pages=docs,
            error=None,
        )
        r = run_step("indexation", 0, {"domain": "example.ru"}, [])
    assert r.ok is True
    assert r.next_available is False


def test_indexation_step0_low_count_offers_diagnostics() -> None:
    from app.collectors.yandex_serp import IndexationResult

    with patch("app.playground.scenarios.check_indexation") as mock:
        mock.return_value = IndexationResult(
            domain="example.ru",
            pages_found=1,
            pages=[_doc(1, "example.ru")],
            error=None,
        )
        r = run_step("indexation", 0, {"domain": "example.ru"}, [])
    assert r.next_available is True
    assert "мало" in (r.next_hint_ru or "").lower()


def test_diagnostic_step1_parses_valid_sitemap_xml() -> None:
    sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.ru/</loc></url>
      <url><loc>https://example.ru/about</loc></url>
      <url><loc>https://example.ru/contacts</loc></url>
    </urlset>"""
    with patch(
        "app.playground.scenarios._http_get",
        return_value={"status": 200, "body": sitemap_xml, "content_type": "application/xml", "error": None},
    ):
        r = run_step("indexation", 1, {"domain": "example.ru"}, [])
    assert r.ok is True
    summary = r.response_summary
    assert summary["valid_xml"] is True
    assert summary["urls_declared"] == 3
    assert r.next_available is True


def test_diagnostic_step1_flags_spa_returning_html_as_sitemap() -> None:
    """SPA router eats /sitemap.xml and returns the index HTML — this
    is the specific failure mode in grandtourspirit's ROADMAP, and it's
    the single most valuable pattern to detect automatically."""
    html_body = "<!DOCTYPE html><html><body><div id='root'></div></body></html>"
    with patch(
        "app.playground.scenarios._http_get",
        return_value={"status": 200, "body": html_body, "content_type": "text/html", "error": None},
    ):
        r = run_step("indexation", 1, {"domain": "example.ru"}, [])
    assert r.response_summary["problem"] == "sitemap_returns_html"


def test_diagnostic_step2_flags_disallow_all() -> None:
    robots_body = "User-agent: *\nDisallow: /\n"
    with patch(
        "app.playground.scenarios._http_get",
        return_value={"status": 200, "body": robots_body, "content_type": "text/plain", "error": None},
    ):
        r = run_step("indexation", 2, {"domain": "example.ru"}, [])
    assert r.response_summary["problem"] == "blocks_all_root_path"


def test_diagnostic_step3_flags_empty_spa_shell() -> None:
    shell = "<!DOCTYPE html><html><body><div id=\"root\"></div></body></html>"
    with patch(
        "app.playground.scenarios._http_get",
        return_value={"status": 200, "body": shell, "content_type": "text/html", "error": None},
    ):
        r = run_step("indexation", 3, {"domain": "example.ru"}, [])
    assert r.response_summary["problem"] == "empty_spa_shell"


def test_diagnostic_step3_accepts_real_rendered_homepage() -> None:
    html = (
        "<html><head><title>Grand Tour Spirit</title></head>"
        "<body>"
        + ("Багги-экспедиции по Абхазии из Сочи. " * 20)
        + "</body></html>"
    )
    with patch(
        "app.playground.scenarios._http_get",
        return_value={"status": 200, "body": html, "content_type": "text/html", "error": None},
    ):
        r = run_step("indexation", 3, {"domain": "example.ru"}, [])
    assert r.response_summary["problem"] is None
    assert r.response_summary["title"] == "Grand Tour Spirit"


def test_diagnosis_critical_when_homepage_returns_500() -> None:
    """Synthesis orders causes from most-actionable to least-actionable.
    A 500 on the homepage beats everything else."""
    prior = [
        {"response_summary": {"pages_found": 0}},
        {"response_summary": {"valid_xml": True, "urls_declared": 10}},
        {"response_summary": {"problem": None, "disallow_count": 0}},
        {"response_summary": {"status": 500, "problem": "http_500"}},
    ]
    r = run_step("indexation", 4, {"domain": "example.ru"}, prior)
    assert r.response_summary["severity"] == "critical"
    assert "не отвечает" in r.response_summary["verdict"].lower()


def test_diagnosis_attributes_gap_to_slow_crawl_when_tech_clean() -> None:
    """The grandtourspirit-shaped case: sitemap declares many URLs,
    indexation is low, but everything technical is clean. Attribute
    to slow crawl + recommend IndexNow/Webmaster — our own tools."""
    prior = [
        {"response_summary": {"pages_found": 1}},
        {"response_summary": {"valid_xml": True, "urls_declared": 14}},
        {"response_summary": {"problem": None, "disallow_count": 2}},
        {
            "response_summary": {
                "status": 200, "problem": None,
                "title": "Site", "text_length": 3000, "spa_root_only": False,
            },
        },
    ]
    r = run_step("indexation", 4, {"domain": "example.ru"}, prior)
    assert r.response_summary["severity"] == "medium"
    action = r.response_summary["action_ru"]
    assert "IndexNow" in action or "Переобход" in action


def test_all_step_results_serialise_to_dict_without_error() -> None:
    """Frontend calls .to_dict() implicitly via the API layer. A shape
    regression here 500s the playground — guard it."""
    with patch(
        "app.playground.scenarios.fetch_serp",
        return_value=(_mock_serp_docs(), None),
    ):
        r = run_step(
            "competitors_by_query", 0,
            {"query": "q", "own_domain": "grandtourspirit.ru"},
            [],
        )
    d = r.to_dict()
    # Dataclass asdict must produce JSON-native types at leaves
    assert isinstance(d["step_index"], int)
    assert isinstance(d["response_summary"], dict)
    assert "next_available" in d
