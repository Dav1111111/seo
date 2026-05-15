from __future__ import annotations

from app.collectors.metrica import _bounce_decimal, _metric_value, _url_keys


def test_url_keys_normalise_idn_www_query_and_trailing_slash() -> None:
    host_path, path = _url_keys(
        "https://www.южный-континент.рф/catalog/?utm=1#top",
        "xn----jtbbjdhsdbbg3ce9iub.xn--p1ai",
    )

    assert host_path == "xn----jtbbjdhsdbbg3ce9iub.xn--p1ai/catalog"
    assert path == "/catalog"


def test_url_keys_map_relative_paths_to_site_domain() -> None:
    host_path, path = _url_keys("/tours/abhazia/", "grandtourspirit.ru")

    assert host_path == "grandtourspirit.ru/tours/abhazia"
    assert path == "/tours/abhazia"


def test_metric_helpers_are_fail_soft() -> None:
    assert _metric_value([1, "2.5"], 1) == 2.5
    assert _metric_value([], 2, default=7) == 7
    assert _bounce_decimal(42.25) == 0.4225
    assert _bounce_decimal(None) is None
