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
    assert r.next_available is False


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
