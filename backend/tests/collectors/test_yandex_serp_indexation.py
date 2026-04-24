"""Unit tests for `check_indexation` — the site:domain probe that
answers "is the site in Yandex index?" when Webmaster is silent.

We mock `fetch_serp` directly; the transport (submit → poll → base64 →
XML) is already exercised in production and covered by monitoring.
What matters in these tests is the domain-matching logic that filters
the SERP down to rows that actually belong to the owned domain,
because a raw `site:` SERP can include cached copies, subdomain
variants, or third-party aggregators mentioning the domain.
"""

from __future__ import annotations

from unittest.mock import patch

from app.collectors.yandex_serp import (
    IndexationResult,
    SerpDoc,
    check_indexation,
)


def _doc(position: int, domain: str, url: str, title: str = "") -> SerpDoc:
    return SerpDoc(
        position=position,
        url=url,
        domain=domain,
        title=title or url,
        headline="",
    )


def test_empty_domain_returns_error_without_api_call() -> None:
    with patch("app.collectors.yandex_serp.fetch_serp") as mock:
        out = check_indexation("")
    assert out.error == "empty_domain"
    assert out.pages_found == 0
    mock.assert_not_called()


def test_api_error_bubbles_up_with_empty_pages() -> None:
    with patch(
        "app.collectors.yandex_serp.fetch_serp",
        return_value=([], "http_401_on_submit"),
    ):
        out = check_indexation("example.ru")
    assert out.error == "http_401_on_submit"
    assert out.pages == []
    assert out.pages_found == 0


def test_zero_pages_is_a_legit_finding_not_an_error() -> None:
    """Domain absent from index is *data*, not a failure."""
    with patch("app.collectors.yandex_serp.fetch_serp", return_value=([], None)):
        out = check_indexation("ghost-site.ru")
    assert out.error is None
    assert out.pages_found == 0
    assert out.pages == []


def test_filters_out_foreign_domains_from_site_query() -> None:
    """`site:example.ru` can surface non-owned rows (aggregators, cached
    mirrors) — keep only rows whose domain matches or is a subdomain."""
    docs = [
        _doc(1, "example.ru", "https://example.ru/page-a"),
        _doc(2, "cached.google.com", "https://cached.google.com/example.ru"),
        _doc(3, "blog.example.ru", "https://blog.example.ru/post"),
        _doc(4, "spam-site.com", "https://spam-site.com/example.ru"),
    ]
    with patch("app.collectors.yandex_serp.fetch_serp", return_value=(docs, None)):
        out = check_indexation("example.ru")
    assert out.pages_found == 2
    urls = {p.url for p in out.pages}
    assert urls == {"https://example.ru/page-a", "https://blog.example.ru/post"}


def test_normalises_domain_with_www_and_trailing_slash() -> None:
    with patch("app.collectors.yandex_serp.fetch_serp", return_value=([], None)) as mock:
        check_indexation("WWW.Example.RU/")
    # domain passed to fetch_serp was normalised inside query
    query_arg = mock.call_args.args[0]
    assert query_arg == "site:example.ru"


def test_strips_scheme_and_path_from_user_pasted_url() -> None:
    """Owner pastes full URL from browser bar — we must still send
    Yandex `site:bare-host`, not `site:https://...`.

    The silent failure this test pins down: the old code stripped only
    `www.` and trailing slash, so `https://www.x.ru/page` became
    `https://www.x.ru/page` (www prefix doesn't match, starts with
    https://). We sent `site:https://...` → Yandex returned 0 hits
    every time → looked like "site not indexed". This regression must
    not come back.
    """
    cases = {
        "https://www.grandtourspirit.ru/": "site:grandtourspirit.ru",
        "https://grandtourspirit.ru/abkhazia": "site:grandtourspirit.ru",
        "http://example.ru": "site:example.ru",
        "HTTPS://WWW.EXAMPLE.RU/Path?q=1": "site:example.ru",
        "  grandtourspirit.ru  ": "site:grandtourspirit.ru",
    }
    for raw, expected in cases.items():
        with patch("app.collectors.yandex_serp.fetch_serp", return_value=([], None)) as mock:
            check_indexation(raw)
        assert mock.call_args.args[0] == expected, (
            f"input {raw!r} -> sent {mock.call_args.args[0]!r}, expected {expected!r}"
        )


def test_to_dict_is_json_serialisable() -> None:
    import json

    docs = [_doc(1, "example.ru", "https://example.ru/a", "Title A")]
    with patch("app.collectors.yandex_serp.fetch_serp", return_value=(docs, None)):
        out = check_indexation("example.ru")
    payload = out.to_dict()
    # Must round-trip through json without errors
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "https://example.ru/a" in dumped
    assert payload["pages_found"] == 1
    assert payload["error"] is None


def test_result_is_immutable_dataclass_instance() -> None:
    with patch("app.collectors.yandex_serp.fetch_serp", return_value=([], None)):
        out = check_indexation("example.ru")
    assert isinstance(out, IndexationResult)
