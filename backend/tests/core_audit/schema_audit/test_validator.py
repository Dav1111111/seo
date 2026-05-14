"""Tests for the deterministic Schema.org audit validator.

These tests pin the public contract: stable issue codes, severity
levels, tolerant FAQ DOM-match, and JSON-serializable output. Read
together they document the expected behaviour for downstream
consumers (studio.py, brain summary, frontend).
"""

from __future__ import annotations

import json

from app.core_audit.schema_audit import (
    SchemaAuditResult,
    SchemaIssue,
    audit_schema,
)


def _codes(result: SchemaAuditResult) -> list[str]:
    return [i.code for i in result.issues]


def _severities(result: SchemaAuditResult, code: str) -> list[str]:
    return [i.severity for i in result.issues if i.code == code]


# ---------------------------------------------------------------------------
# 1. Happy path: @graph with Product+Offer
# ---------------------------------------------------------------------------


def test_valid_graph_with_product_offer_no_critical():
    blocks = [
        {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Organization",
                    "name": "Гранд Тур Спирит",
                    "logo": "https://example.com/logo.png",
                    "sameAs": ["https://vk.com/grandtourspirit"],
                },
                {
                    "@type": "Product",
                    "name": "Экскурсия в Красную Поляну",
                    "offers": {
                        "@type": "Offer",
                        "price": "2500",
                        "priceCurrency": "RUB",
                        "availability": "https://schema.org/InStock",
                        "url": "https://example.com/krasnaya-polyana",
                    },
                },
            ],
        }
    ]
    result = audit_schema(blocks, full_text="экскурсия")
    assert result.parse_error_count == 0
    assert "Product" in result.detected_types
    assert "Organization" in result.detected_types
    assert not any(i.severity == "critical" for i in result.issues)
    # No price warning for clean numeric price.
    assert "schema.offer.price_string" not in _codes(result)
    assert "schema.offer.no_currency" not in _codes(result)
    assert result.valid_blocks_count >= 2


# ---------------------------------------------------------------------------
# 2. Parse error
# ---------------------------------------------------------------------------


def test_parse_error_emits_critical():
    blocks = [{"__parse_error": "Expecting ',' delimiter at line 5 column 12"}]
    result = audit_schema(blocks)
    assert result.parse_error_count == 1
    criticals = [i for i in result.issues if i.severity == "critical"]
    assert len(criticals) == 1
    assert criticals[0].code == "schema.parse_error"
    assert criticals[0].evidence and "delimiter" in criticals[0].evidence


# ---------------------------------------------------------------------------
# 3 & 4. FAQ DOM mismatch tolerance
# ---------------------------------------------------------------------------


def test_faq_no_dom_mismatch_when_one_question_present():
    blocks = [
        {
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": "Сколько стоит экскурсия?",
                    "acceptedAnswer": {"@type": "Answer", "text": "От 2500 рублей."},
                },
                {
                    "@type": "Question",
                    "name": "Где встреча с гидом?",
                    "acceptedAnswer": {"@type": "Answer", "text": "На вокзале."},
                },
                {
                    "@type": "Question",
                    "name": "Можно ли с детьми?",
                    "acceptedAnswer": {"@type": "Answer", "text": "Конечно."},
                },
            ],
        }
    ]
    full_text = (
        "Часто задаваемые вопросы. Сколько стоит экскурсия? "
        "От 2500 рублей за человека."
    )
    result = audit_schema(blocks, full_text=full_text)
    assert "schema.faq.dom_mismatch" not in _codes(result)


def test_faq_dom_mismatch_when_zero_questions_present():
    blocks = [
        {
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": "Какие документы нужны?",
                    "acceptedAnswer": {"@type": "Answer", "text": "Паспорт."},
                },
            ],
        }
    ]
    full_text = "Описание тура. Маршрут проходит через горы."
    result = audit_schema(blocks, full_text=full_text)
    assert "schema.faq.dom_mismatch" in _codes(result)
    assert _severities(result, "schema.faq.dom_mismatch") == ["warning"]


# ---------------------------------------------------------------------------
# 5 & 6. Breadcrumb structural rules
# ---------------------------------------------------------------------------


def test_breadcrumb_missing_position():
    blocks = [
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "name": "Главная", "item": "https://x.ru/"},
                {"@type": "ListItem", "name": "Туры", "item": "https://x.ru/tours/"},
            ],
        }
    ]
    result = audit_schema(blocks)
    assert "schema.breadcrumb.missing_items" in _codes(result)


def test_breadcrumb_position_disorder():
    blocks = [
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Главная"},
                {"@type": "ListItem", "position": 3, "name": "Туры"},
                {"@type": "ListItem", "position": 2, "name": "Сочи"},
            ],
        }
    ]
    result = audit_schema(blocks)
    assert "schema.breadcrumb.position_disorder" in _codes(result)


# ---------------------------------------------------------------------------
# 7 & 8 & 9. Offer / pricing rules
# ---------------------------------------------------------------------------


def test_offer_price_string_warning():
    blocks = [
        {
            "@type": "Offer",
            "price": "от 2500",
            "priceCurrency": "RUB",
            "availability": "https://schema.org/InStock",
        }
    ]
    result = audit_schema(blocks)
    price_issues = [i for i in result.issues if i.code == "schema.offer.price_string"]
    assert len(price_issues) == 1
    assert price_issues[0].severity == "warning"
    assert price_issues[0].evidence and "от 2500" in price_issues[0].evidence


def test_offer_price_numeric_ok():
    blocks_str = [
        {
            "@type": "Offer",
            "price": "2500",
            "priceCurrency": "RUB",
            "availability": "https://schema.org/InStock",
        }
    ]
    blocks_int = [
        {
            "@type": "Offer",
            "price": 2500,
            "priceCurrency": "RUB",
            "availability": "https://schema.org/InStock",
        }
    ]
    for blocks in (blocks_str, blocks_int):
        result = audit_schema(blocks)
        assert "schema.offer.price_string" not in _codes(result)
        assert "schema.offer.price_range" not in _codes(result)


def test_offer_missing_currency_warning():
    blocks = [
        {
            "@type": "Offer",
            "price": "2500",
            "availability": "https://schema.org/InStock",
        }
    ]
    result = audit_schema(blocks)
    assert "schema.offer.no_currency" in _codes(result)
    assert _severities(result, "schema.offer.no_currency") == ["warning"]


def test_offer_price_range_is_info():
    blocks = [
        {
            "@type": "Offer",
            "price": "2500-3500",
            "priceCurrency": "RUB",
        }
    ]
    result = audit_schema(blocks)
    range_issues = [i for i in result.issues if i.code == "schema.offer.price_range"]
    assert len(range_issues) == 1
    assert range_issues[0].severity == "info"


def test_offer_url_not_absolute():
    blocks = [
        {
            "@type": "Offer",
            "price": "2500",
            "priceCurrency": "RUB",
            "url": "/tour/sochi",
        }
    ]
    result = audit_schema(blocks)
    assert "schema.offer.url_not_absolute" in _codes(result)


# ---------------------------------------------------------------------------
# 10. TouristTrip → info, not warning
# ---------------------------------------------------------------------------


def test_tourist_trip_info_not_critical():
    blocks = [
        {
            "@type": "TouristTrip",
            "name": "Тур в Красную Поляну",
        }
    ]
    result = audit_schema(blocks)
    tt = [i for i in result.issues if i.code == "schema.tourist_trip.suggest_product"]
    assert len(tt) == 1
    assert tt[0].severity == "info"
    assert not any(i.severity == "critical" for i in result.issues)
    # Honest wording — no "невозможно" / "impossible" type claims
    assert "невозможно" not in tt[0].message_ru.lower()


# ---------------------------------------------------------------------------
# 11. Empty schema → warning
# ---------------------------------------------------------------------------


def test_empty_schema_warning_not_critical():
    result = audit_schema([])
    assert len(result.issues) == 1
    assert result.issues[0].code == "schema.missing"
    assert result.issues[0].severity == "warning"
    # Honest wording: no "impossible" / "невозможн" claim about rich snippets.
    msg = result.issues[0].message_ru.lower()
    assert "невозможн" not in msg
    assert "impossible" not in msg
    # Also: None should behave the same.
    result_none = audit_schema(None)
    assert _codes(result_none) == ["schema.missing"]


# ---------------------------------------------------------------------------
# 12. Microdata marker → info, no content rules
# ---------------------------------------------------------------------------


def test_microdata_marker_info_only():
    blocks = [{"__format": "microdata", "@type": "Product"}]
    result = audit_schema(blocks)
    codes = _codes(result)
    assert "schema.microdata_marker" in codes
    micro = [i for i in result.issues if i.code == "schema.microdata_marker"][0]
    assert micro.severity == "info"
    assert micro.source == "microdata"
    # Crucially: no Product content rules applied (no schema.product.no_offer).
    assert "schema.product.no_offer" not in codes
    assert "Product" in result.detected_types
    assert "microdata" in result.formats


def test_rdfa_marker_info_only():
    blocks = [{"__format": "rdfa", "@type": "Organization"}]
    result = audit_schema(blocks)
    codes = _codes(result)
    assert "schema.microdata_marker" in codes
    assert "schema.organization.missing_logo" not in codes


# ---------------------------------------------------------------------------
# 13 & 14. Summary + JSON serializability
# ---------------------------------------------------------------------------


def test_summary_ru_one_line():
    blocks = [
        {
            "@type": "Organization",
            "name": "X",
            "logo": "https://x.ru/logo.png",
        },
        {
            "@type": "WebSite",
            "url": "https://x.ru/",
            "name": "X",
        },
    ]
    result = audit_schema(blocks)
    assert len(result.summary_ru) <= 200
    assert "\n" not in result.summary_ru
    assert result.summary_ru  # non-empty


def test_to_dict_json_serializable():
    blocks = [
        {
            "@type": "Product",
            "name": "Тур",
            "offers": {
                "@type": "Offer",
                "price": "от 2500",
                "priceCurrency": "RUB",
            },
        },
        {"__parse_error": "boom"},
        {"__format": "microdata", "@type": "Organization"},
    ]
    result = audit_schema(blocks, full_text="что-то про тур")
    payload = result.to_dict()
    raw = json.dumps(payload, ensure_ascii=False)
    # Round-trip: structure preserved.
    parsed = json.loads(raw)
    assert isinstance(parsed["issues"], list)
    assert isinstance(parsed["recommendations"], list)
    assert parsed["parse_error_count"] == 1


# ---------------------------------------------------------------------------
# Extras — robustness
# ---------------------------------------------------------------------------


def test_at_type_as_list():
    blocks = [
        {
            "@type": ["LocalBusiness", "TravelAgency"],
            "name": "Гранд",
            "logo": "https://x.ru/l.png",
            "address": {"@type": "PostalAddress", "streetAddress": "ул. Ленина 1"},
        }
    ]
    result = audit_schema(blocks)
    assert "LocalBusiness" in result.detected_types
    # No missing_logo / no_address since both present.
    assert "schema.organization.missing_logo" not in _codes(result)
    assert "schema.organization.no_address" not in _codes(result)


def test_value_wrapper_unwraps():
    blocks = [
        {
            "value": {
                "@type": "Organization",
                "name": "X",
                "logo": "https://x.ru/logo.png",
            }
        }
    ]
    result = audit_schema(blocks)
    assert "Organization" in result.detected_types


def test_recommendations_deduped_and_short():
    # Two products both missing offers → recommendation appears once.
    blocks = [
        {"@type": "Product", "name": "A"},
        {"@type": "Product", "name": "B"},
    ]
    result = audit_schema(blocks)
    rec_hits = [
        r for r in result.recommendations if "Offer" in r or "offer" in r.lower()
    ]
    assert len(rec_hits) == 1
    for rec in result.recommendations:
        assert len(rec) <= 120


def test_issue_dataclass_frozen_and_typed():
    issue = SchemaIssue(
        code="schema.missing",
        severity="warning",
        message_ru="x",
        evidence=None,
        fix_ru="y",
        source="json-ld",
    )
    # Frozen — assignment must fail.
    try:
        issue.code = "other"  # type: ignore[misc]
    except Exception:
        pass
    else:
        raise AssertionError("SchemaIssue must be frozen")


def test_blog_missing_fields():
    blocks = [
        {
            "@type": "BlogPosting",
            # no headline, no dates, no author
        }
    ]
    result = audit_schema(blocks)
    codes = _codes(result)
    assert "schema.blog.no_headline" in codes
    assert "schema.blog.no_dates" in codes
    assert "schema.blog.no_author" in codes
