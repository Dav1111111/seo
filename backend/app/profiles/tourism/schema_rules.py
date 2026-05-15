"""Schema.org guidance for Russian tourism pages.

Keep this conservative: Yandex documents Schema.org support and product/price
snippets, but we must not claim that tourism entities are "bad" only because
they are not Product. For tours, prefer a factual tourism entity plus explicit
offers; Product is optional only when the page really behaves like a packaged
commercial product and the validator accepts it.

Data consumed by Module 3 when generating schema recommendations.
"""

from __future__ import annotations

from app.core_audit.intent_codes import IntentCode


TOURISM_SCHEMA_RULES: dict[IntentCode, tuple[str, ...]] = {
    IntentCode.COMM_MODIFIED: (
        "TouristTrip",
        "Service",
        "Offer",
        "AggregateOffer",
        "FAQPage",
        "BreadcrumbList",
    ),
    IntentCode.COMM_CATEGORY: ("BreadcrumbList", "ItemList", "Organization"),
    IntentCode.TRANS_BOOK: ("TouristTrip", "Service", "Offer", "BreadcrumbList"),
    IntentCode.TRANS_BRAND: ("Organization", "LocalBusiness", "BreadcrumbList"),
    IntentCode.INFO_DEST: ("Article", "BreadcrumbList", "FAQPage"),
    IntentCode.INFO_LOGISTICS: ("Article", "FAQPage"),
    IntentCode.INFO_PREP: ("Article", "FAQPage", "HowTo"),
    IntentCode.COMM_COMPARE: ("Article", "ItemList", "BreadcrumbList"),
    IntentCode.TRUST_LEGAL: ("Organization", "LocalBusiness"),
    IntentCode.LOCAL_GEO: ("LocalBusiness", "BreadcrumbList"),
}


# Explicitly discouraged schema types. Leave empty unless we have deterministic
# validator evidence that a type is both present and harmful for this page.
TOURISM_SCHEMA_CARGO_CULT: frozenset[str] = frozenset()


# Example JSON-LD blocks per recommended type. Used by the per-type
# recommendation engine to give owner a ready-to-paste template.
# Keep these MINIMAL — owner customizes; we just show the shape.
# All examples use Russian-friendly defaults (priceCurrency=RUB).
# Owner must replace ALL CAPS placeholders.
SCHEMA_EXAMPLES: dict[str, str] = {
    "TouristTrip": """\
{
  "@context": "https://schema.org",
  "@type": "TouristTrip",
  "name": "НАЗВАНИЕ_ТУРА",
  "description": "КОРОТКОЕ_ОПИСАНИЕ",
  "touristType": ["активный отдых", "приключения"],
  "itinerary": {
    "@type": "ItemList",
    "itemListElement": [
      {"@type": "TouristAttraction", "name": "ТОЧКА_МАРШРУТА_1"},
      {"@type": "TouristAttraction", "name": "ТОЧКА_МАРШРУТА_2"}
    ]
  },
  "offers": {
    "@type": "Offer",
    "price": "24900",
    "priceCurrency": "RUB",
    "availability": "https://schema.org/InStock",
    "url": "https://ваш-сайт/страница-тура"
  }
}""",
    "Offer": """\
{
  "@context": "https://schema.org",
  "@type": "Offer",
  "price": "24900",
  "priceCurrency": "RUB",
  "availability": "https://schema.org/InStock",
  "url": "https://ваш-сайт/страница-тура",
  "validFrom": "2026-05-01"
}""",
    "Product": """\
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "НАЗВАНИЕ_ТУРА",
  "description": "КОРОТКОЕ_ОПИСАНИЕ",
  "image": "https://ваш-сайт/img/photo.jpg",
  "brand": {"@type": "Brand", "name": "ВАШ_БРЕНД"},
  "offers": {
    "@type": "Offer",
    "price": "24900",
    "priceCurrency": "RUB",
    "availability": "https://schema.org/InStock"
  }
}""",
    "FAQPage": """\
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "Какие документы нужны?",
      "acceptedAnswer": {"@type": "Answer", "text": "Только паспорт РФ."}
    },
    {
      "@type": "Question",
      "name": "Включена ли страховка?",
      "acceptedAnswer": {"@type": "Answer", "text": "Да, базовая страховка включена."}
    }
  ]
}""",
    "HowTo": """\
{
  "@context": "https://schema.org",
  "@type": "HowTo",
  "name": "Как доехать до НАЗВАНИЕ_МЕСТА",
  "step": [
    {"@type": "HowToStep", "name": "Шаг 1", "text": "ТЕКСТ_ШАГА"},
    {"@type": "HowToStep", "name": "Шаг 2", "text": "ТЕКСТ_ШАГА"}
  ]
}""",
    "Service": """\
{
  "@context": "https://schema.org",
  "@type": "Service",
  "name": "НАЗВАНИЕ_УСЛУГИ",
  "provider": {"@type": "Organization", "name": "ВАША_КОМПАНИЯ"},
  "areaServed": "Сочи, Краснодарский край",
  "offers": {
    "@type": "Offer",
    "price": "24900",
    "priceCurrency": "RUB"
  }
}""",
    "AggregateOffer": """\
{
  "@context": "https://schema.org",
  "@type": "AggregateOffer",
  "lowPrice": "12000",
  "highPrice": "45000",
  "priceCurrency": "RUB",
  "offerCount": 8
}""",
    "BreadcrumbList": """\
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {"@type": "ListItem", "position": 1, "name": "Главная", "item": "https://ваш-сайт/"},
    {"@type": "ListItem", "position": 2, "name": "Туры", "item": "https://ваш-сайт/tours"}
  ]
}""",
    "LocalBusiness": """\
{
  "@context": "https://schema.org",
  "@type": "LocalBusiness",
  "name": "ВАША_КОМПАНИЯ",
  "telephone": "+7-XXX-XXX-XX-XX",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "АДРЕС",
    "addressLocality": "Сочи",
    "addressCountry": "RU"
  },
  "openingHours": "Mo-Su 09:00-21:00"
}""",
    "Organization": """\
{
  "@context": "https://schema.org",
  "@type": "Organization",
  "name": "ВАША_КОМПАНИЯ",
  "url": "https://ваш-сайт/",
  "logo": "https://ваш-сайт/logo.png",
  "sameAs": ["https://t.me/...", "https://vk.com/..."]
}""",
    "Article": """\
{
  "@context": "https://schema.org",
  "@type": "Article",
  "headline": "ЗАГОЛОВОК",
  "author": {"@type": "Person", "name": "АВТОР"},
  "datePublished": "2026-05-01",
  "dateModified": "2026-05-15"
}""",
    "ItemList": """\
{
  "@context": "https://schema.org",
  "@type": "ItemList",
  "itemListElement": [
    {"@type": "ListItem", "position": 1, "url": "https://ваш-сайт/тур-1"},
    {"@type": "ListItem", "position": 2, "url": "https://ваш-сайт/тур-2"}
  ]
}""",
}


# Canonical name expected by tests + per-type schema check.
# Kept as a direct alias of SCHEMA_EXAMPLES so existing callers that already
# imported the shorter name continue to work.
TOURISM_SCHEMA_EXAMPLES: dict[str, str] = SCHEMA_EXAMPLES


def example_for_type(type_name: str) -> str | None:
    """Public helper used by checks/composer to attach a paste-in template."""
    return SCHEMA_EXAMPLES.get(type_name)
