"""Deterministic Schema.org validator.

Pure function — no I/O, no LLM, no DB. Given the crawler's extracted
`schema_blocks` plus page text/url/title/h1, returns a structured
`SchemaAuditResult` with stable issue codes and concrete evidence.

Rule philosophy:
  - JSON-LD only gets content rules. Microdata / RDFa get info-level
    "marker" findings — we know the type, nothing else.
  - Honest wording. "Rich snippet impossible" is never claimed.
  - FAQ DOM mismatch is tolerant: warns only when NONE of the FAQ
    questions appear in `full_text` (accordions break naive checks).
  - Each rule runs in its own try/except — one buggy rule must NOT
    crash the whole audit. On internal error we emit
    `schema.audit_internal_error` and continue.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Iterable

from app.core_audit.schema_audit.dto import (
    SchemaAuditResult,
    SchemaIssue,
    SchemaSource,
)

log = logging.getLogger(__name__)

# Known top-level Schema.org types we apply rules to. Anything outside
# this set is allowed (no error) but flagged info-level via
# `schema.unknown_type` only when it's clearly off (empty/numeric).
KNOWN_TYPES: frozenset[str] = frozenset(
    {
        "Organization",
        "LocalBusiness",
        "TravelAgency",
        "TouristAttraction",
        "TouristTrip",
        "Trip",
        "Product",
        "Service",
        "Offer",
        "AggregateOffer",
        "AggregateRating",
        "Review",
        "WebSite",
        "WebPage",
        "BreadcrumbList",
        "FAQPage",
        "Question",
        "Answer",
        "BlogPosting",
        "Article",
        "NewsArticle",
        "Event",
        "Person",
        "ImageObject",
        "VideoObject",
        "PostalAddress",
        "ContactPoint",
    }
)

# Phrases that mean "price not disclosed" — info only, not a warning.
_PRICE_BY_REQUEST = re.compile(
    r"(по\s*запросу|по\s*договорённости|по\s*договорен[нн]ости|on\s*request|by\s*request)",
    re.IGNORECASE,
)

# Range pattern: "2500-3500", "2500 — 3500", "от 2500 до 3500".
_PRICE_RANGE = re.compile(
    r"""
    ^\s*
    (?:от\s+)?\d[\d\s.,]*       # lower bound
    \s*(?:-|–|—|до)\s*           # separator
    \d[\d\s.,]*                  # upper bound
    \s*(?:руб|₽|rub|р\.)?        # optional currency tail
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Pure numeric (with optional decimal). "2500", "2500.00", "2500,50".
_PRICE_NUMERIC = re.compile(r"^\s*\d+(?:[.,]\d+)?\s*$")

# String with a currency or words around the number — warning case.
# e.g. "от 2500", "2500 ₽", "RUB 2500", "starting 2500".
_PRICE_HAS_WORDS_OR_CURRENCY = re.compile(
    r"[a-zа-яёA-ZА-ЯЁ₽$€£¥]",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _unwrap_value(block: Any) -> Any:
    """Crawler sometimes wraps payload as `{"value": {...}}`. Unwrap once."""
    if (
        isinstance(block, dict)
        and "value" in block
        and "@type" not in block
        and isinstance(block.get("value"), (dict, list))
    ):
        return block["value"]
    return block


def _flatten_blocks(schema_blocks: list[dict] | None) -> list[dict]:
    """Normalize raw `schema_blocks` into a flat list of dicts.

    Accepts:
      - None / [] → []
      - dict with @type at top → [dict]
      - dict with @graph → unrolled items
      - list of any of the above (recursively flattened one level)
      - {"value": {...}} → unwrapped
      - blocks with __parse_error / __format markers preserved as-is.
    """
    if not schema_blocks:
        return []
    out: list[dict] = []
    for raw in schema_blocks:
        block = _unwrap_value(raw)
        if not isinstance(block, dict):
            continue
        # Parse error markers pass through untouched.
        if "__parse_error" in block:
            out.append(block)
            continue
        graph = block.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                item = _unwrap_value(item)
                if isinstance(item, dict):
                    out.append(item)
            # If the wrapper has its own @type alongside @graph, keep it too.
            if "@type" in block:
                out.append({k: v for k, v in block.items() if k != "@graph"})
            continue
        out.append(block)
    return out


def _type_names(block: dict) -> list[str]:
    """Return @type as a list (handles string OR list of strings)."""
    t = block.get("@type")
    if isinstance(t, str):
        return [t]
    if isinstance(t, list):
        return [x for x in t if isinstance(x, str)]
    return []


def _has_type(block: dict, name: str) -> bool:
    return name in _type_names(block)


def _format_of(block: dict) -> SchemaSource:
    """Inspect `__format` marker. Default is json-ld."""
    fmt = block.get("__format")
    if fmt in ("microdata", "rdfa", "dom", "json-ld"):
        return fmt  # type: ignore[return-value]
    return "json-ld"


def _truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def _evidence(value: Any, limit: int = 300) -> str:
    """Concrete fact, safely stringified and truncated."""
    try:
        text = value if isinstance(value, str) else repr(value)
    except Exception:
        text = "<unrepr>"
    return _truncate(text, limit)


# ---------------------------------------------------------------------------
# Issue helpers
# ---------------------------------------------------------------------------


def _issue(
    code: str,
    severity: str,
    message_ru: str,
    evidence: str | None,
    fix_ru: str,
    source: SchemaSource = "json-ld",
) -> SchemaIssue:
    return SchemaIssue(
        code=code[:120],
        severity=severity,  # type: ignore[arg-type]
        message_ru=message_ru,
        evidence=_truncate(evidence, 300) if evidence else None,
        fix_ru=fix_ru,
        source=source,
    )


def _safe_run(
    rule_name: str,
    fn: Callable[[], Iterable[SchemaIssue]],
    sink: list[SchemaIssue],
) -> None:
    """Run a rule, swallow exceptions, emit internal-error issue."""
    try:
        for issue in fn():
            if issue is not None:
                sink.append(issue)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("schema_audit rule %s crashed: %s", rule_name, exc)
        sink.append(
            _issue(
                code="schema.audit_internal_error",
                severity="warning",
                message_ru=(
                    f"Внутренняя ошибка проверки разметки ({rule_name}). "
                    "Остальные правила сработали."
                ),
                evidence=_evidence(str(exc)),
                fix_ru="Сообщите команде — это баг проверки, не вашей разметки.",
            )
        )


# ---------------------------------------------------------------------------
# Rules: per-block JSON-LD
# ---------------------------------------------------------------------------


def _rule_breadcrumb(block: dict) -> list[SchemaIssue]:
    if not _has_type(block, "BreadcrumbList"):
        return []
    issues: list[SchemaIssue] = []
    items = block.get("itemListElement")
    if not isinstance(items, list) or not items:
        issues.append(
            _issue(
                code="schema.breadcrumb.missing_items",
                severity="warning",
                message_ru="В BreadcrumbList отсутствует itemListElement или он пуст.",
                evidence=_evidence(items),
                fix_ru="Добавьте список itemListElement c position, name и item для каждого уровня.",
            )
        )
        return issues

    positions: list[int] = []
    for idx, raw_item in enumerate(items):
        item = _unwrap_value(raw_item)
        if not isinstance(item, dict):
            continue
        pos = item.get("position")
        if isinstance(pos, int):
            positions.append(pos)
        elif isinstance(pos, str) and pos.isdigit():
            positions.append(int(pos))
        else:
            issues.append(
                _issue(
                    code="schema.breadcrumb.missing_items",
                    severity="warning",
                    message_ru=(
                        f"У элемента BreadcrumbList #{idx + 1} нет position."
                    ),
                    evidence=_evidence(item),
                    fix_ru="Проставьте числовое поле position у каждого пункта (1, 2, 3…).",
                )
            )
        name = item.get("name") or (
            item.get("item", {}).get("name")
            if isinstance(item.get("item"), dict)
            else None
        )
        if not name:
            issues.append(
                _issue(
                    code="schema.breadcrumb.item_missing_name",
                    severity="warning",
                    message_ru=(
                        f"У элемента BreadcrumbList #{idx + 1} нет name."
                    ),
                    evidence=_evidence(item),
                    fix_ru="Добавьте name (видимая подпись хлебной крошки).",
                )
            )

    if len(positions) >= 2 and positions != sorted(positions):
        issues.append(
            _issue(
                code="schema.breadcrumb.position_disorder",
                severity="warning",
                message_ru="Позиции в BreadcrumbList идут не по порядку.",
                evidence=_evidence(positions),
                fix_ru="Перенумеруйте position последовательно: 1, 2, 3, ...",
            )
        )
    return issues


def _rule_organization(block: dict) -> list[SchemaIssue]:
    org_types = {"Organization", "LocalBusiness", "TravelAgency"}
    if not any(_has_type(block, t) for t in org_types):
        return []
    issues: list[SchemaIssue] = []
    if not block.get("logo"):
        issues.append(
            _issue(
                code="schema.organization.missing_logo",
                severity="warning",
                message_ru="У Organization нет поля logo.",
                evidence=_evidence(block.get("name") or block.get("@type")),
                fix_ru="Добавьте logo (ImageObject или URL картинки) — Яндекс использует для брендового сниппета.",
            )
        )
    same_as = block.get("sameAs")
    if not same_as:
        issues.append(
            _issue(
                code="schema.organization.missing_sameas",
                severity="info",
                message_ru="У Organization нет sameAs — ссылок на профили компании.",
                evidence=None,
                fix_ru="Добавьте sameAs со ссылками на ВКонтакте, Telegram, Яндекс.Карты и т.п.",
            )
        )
    addr = block.get("address")
    if not addr and any(_has_type(block, t) for t in {"LocalBusiness", "TravelAgency"}):
        issues.append(
            _issue(
                code="schema.organization.no_address",
                severity="warning",
                message_ru="У локального бизнеса нет address.",
                evidence=_evidence(block.get("name")),
                fix_ru="Добавьте PostalAddress с streetAddress, addressLocality и postalCode.",
            )
        )
    return issues


def _parse_price_string(price: str) -> str:
    """Classify a raw price string. Returns one of:
    'numeric', 'by_request', 'range', 'with_currency_or_words', 'unknown'.
    """
    p = price.strip()
    if not p:
        return "unknown"
    if _PRICE_BY_REQUEST.search(p):
        return "by_request"
    if _PRICE_RANGE.match(p):
        return "range"
    if _PRICE_NUMERIC.match(p):
        return "numeric"
    if _PRICE_HAS_WORDS_OR_CURRENCY.search(p):
        return "with_currency_or_words"
    return "unknown"


def _rule_offer(block: dict, parent_types: list[str] | None = None) -> list[SchemaIssue]:
    if not (_has_type(block, "Offer") or _has_type(block, "AggregateOffer")):
        return []
    issues: list[SchemaIssue] = []

    price = block.get("price")
    has_price_field = "price" in block or "lowPrice" in block or "highPrice" in block

    if isinstance(price, str):
        kind = _parse_price_string(price)
        if kind == "with_currency_or_words":
            issues.append(
                _issue(
                    code="schema.offer.price_string",
                    severity="warning",
                    message_ru=(
                        "Поле price содержит текст или валюту вместе с числом — "
                        "Яндекс ожидает чистое число, иначе price-сниппет менее вероятен."
                    ),
                    evidence=_evidence(f'price="{price}"'),
                    fix_ru="Вынесите валюту в priceCurrency, а в price оставьте только число (например, 2500).",
                )
            )
        elif kind == "range":
            issues.append(
                _issue(
                    code="schema.offer.price_range",
                    severity="info",
                    message_ru=(
                        "Цена указана диапазоном. Лучше использовать AggregateOffer "
                        "с lowPrice и highPrice — поисковики так чаще показывают вилку."
                    ),
                    evidence=_evidence(f'price="{price}"'),
                    fix_ru="Замените на AggregateOffer с lowPrice/highPrice и priceCurrency.",
                )
            )
        elif kind == "by_request":
            issues.append(
                _issue(
                    code="schema.offer.price_string",
                    severity="info",
                    message_ru=(
                        "Цена не открыта (по запросу). Price-сниппет в Яндексе в этом случае невозможен — "
                        "но это нормально, если бизнес так работает."
                    ),
                    evidence=_evidence(f'price="{price}"'),
                    fix_ru="Если хотите показывать цену в сниппете — раскройте её хотя бы как «от N».",
                )
            )
    # numeric int/float prices → OK, no issue.

    currency = block.get("priceCurrency") or block.get("currency")
    if has_price_field and not currency and not _has_type(block, "AggregateOffer"):
        issues.append(
            _issue(
                code="schema.offer.no_currency",
                severity="warning",
                message_ru="У Offer есть price, но нет priceCurrency.",
                evidence=_evidence(price),
                fix_ru='Добавьте priceCurrency: "RUB".',
            )
        )

    if "availability" not in block:
        issues.append(
            _issue(
                code="schema.offer.no_availability",
                severity="info",
                message_ru="У Offer нет availability — Яндекс не узнает, в наличии ли услуга.",
                evidence=None,
                fix_ru='Добавьте availability: "https://schema.org/InStock" (или другую константу).',
            )
        )

    url = block.get("url")
    if isinstance(url, str) and url and not (url.startswith("http://") or url.startswith("https://")):
        issues.append(
            _issue(
                code="schema.offer.url_not_absolute",
                severity="warning",
                message_ru="Поле url у Offer не абсолютное.",
                evidence=_evidence(f'url="{url}"'),
                fix_ru="Используйте абсолютный URL вида https://example.com/page.",
            )
        )

    return issues


def _rule_product(block: dict) -> list[SchemaIssue]:
    if not _has_type(block, "Product"):
        return []
    issues: list[SchemaIssue] = []
    offers = block.get("offers")
    has_offer = False
    if isinstance(offers, dict):
        has_offer = "Offer" in _type_names(offers) or "AggregateOffer" in _type_names(offers)
    elif isinstance(offers, list):
        for o in offers:
            if isinstance(o, dict) and (_has_type(o, "Offer") or _has_type(o, "AggregateOffer")):
                has_offer = True
                break
    if not has_offer:
        issues.append(
            _issue(
                code="schema.product.no_offer",
                severity="warning",
                message_ru="У Product нет вложенного Offer — цена и валюта не передаются.",
                evidence=_evidence(block.get("name")),
                fix_ru="Добавьте offers: {Offer с price, priceCurrency, availability, url}.",
            )
        )
    return issues


def _rule_tourist_trip(block: dict) -> list[SchemaIssue]:
    if not (_has_type(block, "TouristTrip") or _has_type(block, "Trip")):
        return []
    return [
        _issue(
            code="schema.tourist_trip.suggest_product",
            severity="info",
            message_ru=(
                "TouristTrip описывает тур, но для коммерческого сниппета Яндекса "
                "обычно полезнее Product/Offer или Service/Offer."
            ),
            evidence=_evidence(block.get("name") or block.get("@type")),
            fix_ru="Подумайте про дублирование разметки как Product с вложенным Offer.",
        ),
        _issue(
            code="schema.offer.tourism_type_hint",
            severity="info",
            message_ru="Подсказка: для туризма Product+Offer чаще даёт ценовой сниппет.",
            evidence=None,
            fix_ru="Если есть фиксированная цена тура — оформите Product с offers.",
        ),
    ]


def _rule_faq(
    block: dict,
    full_text: str | None,
) -> list[SchemaIssue]:
    if not _has_type(block, "FAQPage"):
        return []
    issues: list[SchemaIssue] = []
    main = block.get("mainEntity")
    if main is None or (isinstance(main, list) and not main):
        issues.append(
            _issue(
                code="schema.faq.no_main_entity",
                severity="warning",
                message_ru="У FAQPage нет mainEntity с вопросами.",
                evidence=None,
                fix_ru="Добавьте mainEntity: [Question { name, acceptedAnswer: Answer { text } }, ...].",
            )
        )
        return issues

    questions = main if isinstance(main, list) else [main]
    q_texts: list[str] = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        name = q.get("name") or q.get("text")
        if isinstance(name, str) and name.strip():
            q_texts.append(name.strip())

    if q_texts and full_text:
        haystack = full_text.lower()
        # Tolerant: at least ONE question must surface in the rendered text.
        any_present = any(
            _question_in_text(q, haystack) for q in q_texts
        )
        if not any_present:
            issues.append(
                _issue(
                    code="schema.faq.dom_mismatch",
                    severity="warning",
                    message_ru=(
                        "Ни один вопрос из FAQPage не найден в видимом тексте страницы. "
                        "Яндекс может счесть FAQ скрытым — это может мешать."
                    ),
                    evidence=_evidence(q_texts[0] if q_texts else None),
                    fix_ru="Покажите вопросы FAQ на странице (даже под аккордеоном — главное, чтобы текст был в DOM).",
                )
            )
    return issues


def _question_in_text(question: str, lowered_haystack: str) -> bool:
    """Tolerant substring match.

    We lower the question, strip punctuation, and look for either the
    full question or a leading 6-word prefix. This handles accordion
    titles that sometimes drop trailing words.
    """
    q = question.lower().strip()
    if not q:
        return False
    if q in lowered_haystack:
        return True
    # Strip trailing punctuation
    q_clean = re.sub(r"[?!.,;:]+$", "", q).strip()
    if q_clean and q_clean in lowered_haystack:
        return True
    # Try a leading 6-word prefix
    words = q_clean.split()
    if len(words) > 6:
        prefix = " ".join(words[:6])
        if prefix in lowered_haystack:
            return True
    return False


def _rule_blog(block: dict) -> list[SchemaIssue]:
    if not any(_has_type(block, t) for t in {"BlogPosting", "Article", "NewsArticle"}):
        return []
    issues: list[SchemaIssue] = []
    if not block.get("headline"):
        issues.append(
            _issue(
                code="schema.blog.no_headline",
                severity="warning",
                message_ru="У статьи нет headline.",
                evidence=_evidence(block.get("@type")),
                fix_ru="Добавьте headline (обычно = заголовку H1).",
            )
        )
    if not block.get("datePublished") and not block.get("dateModified"):
        issues.append(
            _issue(
                code="schema.blog.no_dates",
                severity="warning",
                message_ru="У статьи нет datePublished / dateModified.",
                evidence=None,
                fix_ru="Добавьте datePublished в ISO 8601 (2026-05-14) и dateModified при изменениях.",
            )
        )
    if not block.get("author"):
        issues.append(
            _issue(
                code="schema.blog.no_author",
                severity="warning",
                message_ru="У статьи нет author.",
                evidence=None,
                fix_ru="Добавьте author: {Person с name} или {Organization с name}.",
            )
        )
    return issues


def _rule_aggregate_rating(block: dict) -> list[SchemaIssue]:
    if not _has_type(block, "AggregateRating"):
        # AggregateRating can also be nested in Product / Service.
        nested = block.get("aggregateRating")
        if not isinstance(nested, dict):
            return []
        block = nested
    if not _has_type(block, "AggregateRating") and not block.get("ratingValue"):
        return []
    issues: list[SchemaIssue] = []
    if not block.get("reviewCount") and not block.get("ratingCount"):
        issues.append(
            _issue(
                code="schema.aggregate_rating.no_review_count",
                severity="warning",
                message_ru="У AggregateRating нет reviewCount / ratingCount.",
                evidence=_evidence(block.get("ratingValue")),
                fix_ru="Добавьте reviewCount (или ratingCount) — без них Яндекс игнорирует рейтинг.",
            )
        )
    return issues


def _rule_unknown_type(block: dict) -> list[SchemaIssue]:
    types = _type_names(block)
    if not types:
        return [
            _issue(
                code="schema.unknown_type",
                severity="warning",
                message_ru="JSON-LD блок без @type.",
                evidence=_evidence(list(block.keys())[:5]),
                fix_ru='Укажите "@type" — например, "Organization", "Product", "FAQPage".',
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Microdata / RDFa markers
# ---------------------------------------------------------------------------


def _format_label(fmt: SchemaSource) -> str:
    return {
        "microdata": "Microdata",
        "rdfa": "RDFa",
        "dom": "DOM",
        "json-ld": "JSON-LD",
    }.get(fmt, fmt)


def _microdata_marker_issue(block: dict, fmt: SchemaSource) -> SchemaIssue:
    types = _type_names(block) or ["?"]
    type_str = ", ".join(types)
    return _issue(
        code="schema.microdata_marker",
        severity="info",
        message_ru=(
            f"На странице есть микроразметка `{type_str}` ({_format_label(fmt)}). "
            "Полный контент проверить нельзя — мы видим только тип."
        ),
        evidence=_evidence(f"@type={type_str}"),
        fix_ru="Если возможно, продублируйте важные сущности в JSON-LD — его проще валидировать.",
        source=fmt,
    )


# ---------------------------------------------------------------------------
# Recommendations builder
# ---------------------------------------------------------------------------


_REC_FROM_CODE: dict[str, str] = {
    "schema.missing": "Добавьте JSON-LD разметку основной сущности страницы (Organization / Product / FAQPage и т.п.).",
    "schema.parse_error": "Исправьте синтаксис JSON-LD — сейчас блок не парсится.",
    "schema.unknown_type": 'Укажите "@type" в каждом JSON-LD блоке.',
    "schema.breadcrumb.missing_items": "Дополните BreadcrumbList: itemListElement с position и name.",
    "schema.breadcrumb.position_disorder": "Перенумеруйте position в хлебных крошках по порядку.",
    "schema.breadcrumb.item_missing_name": "Проставьте name у каждого пункта BreadcrumbList.",
    "schema.organization.missing_logo": "Добавьте logo в Organization — нужен для брендового сниппета.",
    "schema.organization.missing_sameas": "Добавьте sameAs со ссылками на соцсети и Яндекс.Карты.",
    "schema.organization.no_address": "Добавьте PostalAddress для локального бизнеса.",
    "schema.product.no_offer": "Вложите Offer в Product (price, priceCurrency, availability).",
    "schema.offer.price_string": "В price оставьте только число, валюту вынесите в priceCurrency.",
    "schema.offer.price_range": "Используйте AggregateOffer с lowPrice/highPrice вместо диапазона строкой.",
    "schema.offer.no_currency": 'Добавьте priceCurrency: "RUB" к Offer.',
    "schema.offer.no_availability": "Добавьте availability у Offer (InStock / OutOfStock и т.п.).",
    "schema.offer.url_not_absolute": "Сделайте url у Offer абсолютным (с https://).",
    "schema.offer.tourism_type_hint": "Для туров попробуйте Product+Offer — это даёт ценовой сниппет чаще.",
    "schema.faq.no_main_entity": "Заполните mainEntity у FAQPage списком Question/Answer.",
    "schema.faq.dom_mismatch": "Покажите вопросы FAQ в видимом тексте страницы.",
    "schema.blog.no_headline": "Добавьте headline в разметку статьи.",
    "schema.blog.no_dates": "Добавьте datePublished и dateModified в статью.",
    "schema.blog.no_author": "Добавьте author в разметку статьи.",
    "schema.tourist_trip.suggest_product": "Подумайте о Product+Offer как дополнение к TouristTrip.",
    "schema.aggregate_rating.no_review_count": "Добавьте reviewCount к AggregateRating.",
    "schema.microdata_marker": "Если важно — продублируйте разметку в JSON-LD для прозрачности.",
    "schema.audit_internal_error": "Передайте этот случай в команду — это баг проверки.",
}


def _build_recommendations(issues: list[SchemaIssue]) -> list[str]:
    """Flat, deduped, owner-facing list of fixes."""
    seen: set[str] = set()
    out: list[str] = []
    # Critical/warning first, info last.
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    for issue in sorted(issues, key=lambda i: severity_order.get(i.severity, 9)):
        rec = _REC_FROM_CODE.get(issue.code)
        if not rec:
            continue
        if rec in seen:
            continue
        seen.add(rec)
        out.append(rec[:120])
    return out


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------


def _build_summary(
    detected_types: list[str],
    valid_blocks_count: int,
    parse_error_count: int,
    issues: list[SchemaIssue],
    formats: list[str],
) -> str:
    if parse_error_count and valid_blocks_count == 0 and not detected_types:
        return _truncate(
            f"Ошибка парсинга JSON-LD: {parse_error_count} "
            f"{'блок повреждён' if parse_error_count == 1 else 'блока повреждены'}.",
            180,
        )
    if valid_blocks_count == 0 and not formats:
        return _truncate("Schema.org разметка не найдена.", 180)

    fmt_str = ""
    if formats and formats != ["json-ld"]:
        fmt_str = " (" + ", ".join(formats) + ")"

    type_str = ", ".join(detected_types[:8]) if detected_types else "—"
    warnings = sum(1 for i in issues if i.severity == "warning")
    criticals = sum(1 for i in issues if i.severity == "critical")

    parts = [f"Найдено {valid_blocks_count} блок(ов) JSON-LD{fmt_str}"]
    if detected_types:
        parts.append(f"типы: {type_str}")
    tail_bits: list[str] = []
    if criticals:
        tail_bits.append(f"{criticals} critical")
    if warnings:
        tail_bits.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
    if tail_bits:
        parts.append(", ".join(tail_bits) + ".")
    else:
        parts.append("проблем не найдено.")

    summary = ". ".join(parts)
    summary = summary.replace("\n", " ").strip()
    return _truncate(summary, 180)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def audit_schema(
    schema_blocks: list[dict] | None,
    full_text: str | None = None,
    url: str | None = None,
    title: str | None = None,
    h1: str | None = None,
) -> SchemaAuditResult:
    """Deterministic Schema.org audit. Pure function, no DB, no LLM.

    Inputs:
      schema_blocks — list of dicts from the crawler. May include
        `{"__parse_error": "..."}` markers or
        `{"__format": "microdata", "@type": "Product"}` markers.
      full_text     — page visible text for FAQ DOM-mismatch heuristic.
      url, title, h1 — context (not always used; reserved).

    Returns: SchemaAuditResult.
    """
    result = SchemaAuditResult()
    blocks = _flatten_blocks(schema_blocks)

    # Empty / missing markup → warning, not critical.
    if not blocks:
        result.issues.append(
            _issue(
                code="schema.missing",
                severity="warning",
                message_ru=(
                    "На странице не найдено Schema.org разметки. "
                    "Без неё расширенные сниппеты в Яндексе менее вероятны."
                ),
                evidence=None,
                fix_ru="Добавьте JSON-LD блок с основной сущностью страницы.",
            )
        )
        result.recommendations = _build_recommendations(result.issues)
        result.summary_ru = _build_summary([], 0, 0, result.issues, [])
        return result

    formats_set: set[str] = set()
    detected: list[str] = []
    valid_blocks = 0

    for block in blocks:
        # Parse error markers.
        if "__parse_error" in block:
            err = block.get("__parse_error") or "unknown parse error"
            result.parse_error_count += 1
            result.issues.append(
                _issue(
                    code="schema.parse_error",
                    severity="critical",
                    message_ru="JSON-LD блок не парсится.",
                    evidence=_evidence(err),
                    fix_ru="Проверьте JSON на пропущенные запятые, кавычки и завершающие скобки.",
                )
            )
            continue

        fmt = _format_of(block)
        formats_set.add(fmt)

        # Microdata / RDFa: marker only.
        if fmt in ("microdata", "rdfa"):
            _safe_run(
                "microdata_marker",
                lambda b=block, f=fmt: [_microdata_marker_issue(b, f)],
                result.issues,
            )
            for tname in _type_names(block):
                if tname not in detected:
                    detected.append(tname)
            continue

        # JSON-LD content rules.
        valid_blocks += 1
        for tname in _type_names(block):
            if tname not in detected:
                detected.append(tname)

        _safe_run("unknown_type", lambda b=block: _rule_unknown_type(b), result.issues)
        _safe_run("breadcrumb", lambda b=block: _rule_breadcrumb(b), result.issues)
        _safe_run("organization", lambda b=block: _rule_organization(b), result.issues)
        _safe_run("product", lambda b=block: _rule_product(b), result.issues)
        _safe_run("offer_top", lambda b=block: _rule_offer(b), result.issues)
        _safe_run("tourist_trip", lambda b=block: _rule_tourist_trip(b), result.issues)
        _safe_run(
            "faq", lambda b=block, ft=full_text: _rule_faq(b, ft), result.issues
        )
        _safe_run("blog", lambda b=block: _rule_blog(b), result.issues)
        _safe_run(
            "aggregate_rating",
            lambda b=block: _rule_aggregate_rating(b),
            result.issues,
        )

        # Walk one level of nested offers inside Product/Service for currency rules.
        offers = block.get("offers")
        if isinstance(offers, dict):
            _safe_run(
                "nested_offer",
                lambda o=offers: _rule_offer(o, _type_names(block)),
                result.issues,
            )
        elif isinstance(offers, list):
            for nested in offers:
                if isinstance(nested, dict):
                    _safe_run(
                        "nested_offer_list",
                        lambda o=nested: _rule_offer(o, _type_names(block)),
                        result.issues,
                    )

    result.detected_types = detected
    result.formats = sorted(formats_set)
    result.valid_blocks_count = valid_blocks
    result.recommendations = _build_recommendations(result.issues)
    result.summary_ru = _build_summary(
        detected,
        valid_blocks,
        result.parse_error_count,
        result.issues,
        result.formats,
    )
    return result
