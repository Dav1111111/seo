"""Schema.org types that Yandex actually uses in Russian tourism SERP.

Source: seo-content audit 2026-04-17. Critical finding — Yandex does NOT
parse TouristTrip / TouristAttraction into rich snippets, so profile
recommends Product for tours (with Offer + AggregateRating) instead.

Data consumed by Module 3 when generating schema recommendations.
"""

from __future__ import annotations

from app.core_audit.intent_codes import IntentCode


TOURISM_SCHEMA_RULES: dict[IntentCode, tuple[str, ...]] = {
    IntentCode.COMM_MODIFIED: ("Product", "Offer", "AggregateRating", "FAQPage", "BreadcrumbList"),
    IntentCode.COMM_CATEGORY: ("BreadcrumbList", "ItemList", "Organization"),
    IntentCode.TRANS_BOOK: ("Product", "Offer", "BreadcrumbList"),
    IntentCode.TRANS_BRAND: ("Organization", "LocalBusiness", "BreadcrumbList"),
    IntentCode.INFO_DEST: ("Article", "BreadcrumbList", "FAQPage"),
    IntentCode.INFO_LOGISTICS: ("Article", "FAQPage"),
    IntentCode.INFO_PREP: ("Article", "FAQPage", "HowTo"),
    IntentCode.COMM_COMPARE: ("Article", "ItemList", "BreadcrumbList"),
    IntentCode.TRUST_LEGAL: ("Organization", "LocalBusiness"),
    IntentCode.LOCAL_GEO: ("LocalBusiness", "BreadcrumbList"),
}


# Explicitly discouraged — Yandex does not parse these into rich snippets.
TOURISM_SCHEMA_CARGO_CULT: frozenset[str] = frozenset({
    "TouristTrip",
    "TouristAttraction",
    "TouristDestination",
})
