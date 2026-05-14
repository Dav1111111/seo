"""End-to-end tests for the deterministic Yandex robots.txt auditor.

Pin the stable issue codes, severity levels, and templated Russian
summary/recommendations. Together these tests document the contract
for downstream consumers (studio.py, brain, frontend).
"""

from __future__ import annotations

from app.core_audit.yandex_robots import (
    YandexRobotsAuditResult,
    audit_yandex_robots,
)


def _codes(result: YandexRobotsAuditResult) -> list[str]:
    return [i.code for i in result.issues]


def _severity_of(result: YandexRobotsAuditResult, code: str) -> str | None:
    for i in result.issues:
        if i.code == code:
            return i.severity
    return None


def test_audit_unavailable_when_status_none():
    result = audit_yandex_robots(
        robots_txt=None,
        robots_url="https://example.com/robots.txt",
        http_status=None,
        important_urls=[],
        observed_urls=[],
    )

    assert "robots.unavailable" in _codes(result)
    assert _severity_of(result, "robots.unavailable") == "critical"
    assert result.is_accessible is False
    assert result.valid_for_yandex is False


def test_audit_unavailable_on_http_500():
    result = audit_yandex_robots(
        robots_txt="",
        robots_url="https://example.com/robots.txt",
        http_status=500,
        important_urls=[],
        observed_urls=[],
    )
    assert "robots.unavailable" in _codes(result)
    assert _severity_of(result, "robots.unavailable") == "critical"


def test_audit_root_disallow_yandex_is_critical():
    text = (
        "User-agent: Yandex\n"
        "Disallow: /\n"
    )
    result = audit_yandex_robots(
        robots_txt=text,
        robots_url="https://example.com/robots.txt",
        http_status=200,
        important_urls=[],
        observed_urls=[],
    )

    assert "robots.root_disallowed_yandex" in _codes(result)
    assert _severity_of(result, "robots.root_disallowed_yandex") == "critical"
    # The file parsed cleanly — valid_for_yandex remains True.
    assert result.valid_for_yandex is True


def test_audit_important_url_blocked():
    text = (
        "User-agent: Yandex\n"
        "Disallow: /admin/\n"
        "Sitemap: https://example.com/sitemap.xml\n"
    )
    result = audit_yandex_robots(
        robots_txt=text,
        robots_url="https://example.com/robots.txt",
        http_status=200,
        important_urls=[
            "https://example.com/admin/dashboard",
            "https://example.com/",
        ],
        observed_urls=[],
    )

    assert "robots.important_url_blocked" in _codes(result)
    assert _severity_of(result, "robots.important_url_blocked") == "critical"

    # One blocked check, one ok check.
    risks = [c.risk for c in result.url_checks]
    assert "blocked" in risks
    assert "ok" in risks


def test_audit_clean_param_missing_vs_present():
    # Case A — observed utm_source but no Clean-param → warning
    text_no_clean = (
        "User-agent: Yandex\n"
        "Disallow:\n"
        "Sitemap: https://example.com/sitemap.xml\n"
    )
    result_missing = audit_yandex_robots(
        robots_txt=text_no_clean,
        robots_url="https://example.com/robots.txt",
        http_status=200,
        important_urls=[],
        observed_urls=["https://example.com/page?utm_source=ya&utm_medium=cpc"],
    )
    assert "robots.clean_param_missing" in _codes(result_missing)
    assert _severity_of(result_missing, "robots.clean_param_missing") == "warning"

    # Case B — Clean-param present → info
    text_with_clean = (
        "User-agent: Yandex\n"
        "Disallow:\n"
        "Clean-param: utm_source&utm_medium\n"
        "Sitemap: https://example.com/sitemap.xml\n"
    )
    result_present = audit_yandex_robots(
        robots_txt=text_with_clean,
        robots_url="https://example.com/robots.txt",
        http_status=200,
        important_urls=[],
        observed_urls=["https://example.com/page?utm_source=ya&utm_medium=cpc"],
    )
    assert "robots.clean_param_present" in _codes(result_present)
    assert _severity_of(result_present, "robots.clean_param_present") == "info"
    assert "robots.clean_param_missing" not in _codes(result_present)


def test_audit_cyrillic_warning():
    text = (
        "User-agent: Yandex\n"
        "Disallow: /экскурсии/\n"
        "Sitemap: https://example.com/sitemap.xml\n"
    )
    result = audit_yandex_robots(
        robots_txt=text,
        robots_url="https://example.com/robots.txt",
        http_status=200,
        important_urls=[],
        observed_urls=[],
    )
    assert "robots.cyrillic_found" in _codes(result)
    assert _severity_of(result, "robots.cyrillic_found") == "warning"


def test_audit_too_large_warning():
    body = "User-agent: Yandex\n" + ("Disallow: /a\n" * 60000)
    result = audit_yandex_robots(
        robots_txt=body,
        robots_url="https://example.com/robots.txt",
        http_status=200,
        important_urls=[],
        observed_urls=[],
    )
    assert "robots.too_large" in _codes(result)
    assert _severity_of(result, "robots.too_large") == "warning"


def test_audit_no_sitemap_warning():
    text = (
        "User-agent: Yandex\n"
        "Disallow: /admin/\n"
    )
    result = audit_yandex_robots(
        robots_txt=text,
        robots_url="https://example.com/robots.txt",
        http_status=200,
        important_urls=[],
        observed_urls=[],
    )
    assert "robots.no_sitemap" in _codes(result)
    assert _severity_of(result, "robots.no_sitemap") == "warning"


def test_audit_clean_summary_when_all_ok():
    # Admin-like Disallow shouldn't trigger noindex-info, Sitemap present,
    # nothing critical, ASCII only.
    text = (
        "User-agent: Yandex\n"
        "Disallow: /admin/\n"
        "Disallow: /api/\n"
        "Allow: /\n"
        "Clean-param: utm_source&utm_medium\n"
        "Sitemap: https://example.com/sitemap.xml\n"
    )
    result = audit_yandex_robots(
        robots_txt=text,
        robots_url="https://example.com/robots.txt",
        http_status=200,
        important_urls=["https://example.com/", "https://example.com/products"],
        observed_urls=["https://example.com/page?utm_source=ya"],
    )

    crit = [i for i in result.issues if i.severity == "critical"]
    warn = [i for i in result.issues if i.severity == "warning"]
    assert crit == []
    assert warn == []
    assert result.summary_ru == "Файл robots.txt в порядке для Яндекса."
    # All url_checks ok.
    assert all(c.risk == "ok" for c in result.url_checks)


def test_audit_disallow_used_for_noindex_info():
    # A "content-looking" disallow path (no admin/api markers) should
    # surface the info-level hint to prefer meta noindex.
    text = (
        "User-agent: Yandex\n"
        "Disallow: /catalog/old-products/\n"
        "Sitemap: https://example.com/sitemap.xml\n"
    )
    result = audit_yandex_robots(
        robots_txt=text,
        robots_url="https://example.com/robots.txt",
        http_status=200,
        important_urls=[],
        observed_urls=[],
    )
    assert "robots.disallow_used_for_noindex" in _codes(result)
    assert _severity_of(result, "robots.disallow_used_for_noindex") == "info"


def test_audit_summary_lists_critical_codes():
    text = (
        "User-agent: Yandex\n"
        "Disallow: /\n"
    )
    result = audit_yandex_robots(
        robots_txt=text,
        robots_url="https://example.com/robots.txt",
        http_status=200,
        important_urls=[],
        observed_urls=[],
    )
    assert "Найдены критические проблемы" in result.summary_ru
    assert "robots.root_disallowed_yandex" in result.summary_ru


def test_audit_result_is_json_serializable():
    import json

    text = (
        "User-agent: Yandex\n"
        "Disallow: /admin/\n"
        "Sitemap: https://example.com/sitemap.xml\n"
    )
    result = audit_yandex_robots(
        robots_txt=text,
        robots_url="https://example.com/robots.txt",
        http_status=200,
        important_urls=["https://example.com/admin/dashboard"],
        observed_urls=[],
    )
    payload = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "robots.important_url_blocked" in payload
