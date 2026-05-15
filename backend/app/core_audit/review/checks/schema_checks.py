"""Schema.org coverage check — per-type analysis against recommended set.

Compares what the profile recommends for the page's intent
(`profile.schema_rules[intent]`) against what's actually present in the
latest PageDeepExtract (`ri.schema_types`).

Outcome matrix:

  recommended is None        → ONE `schema_missing` not_applicable
                               (intent unknown to profile)
  recommended == ()          → ONE `schema_missing` not_applicable
                               (intent doesn't need schema)

  recommended non-empty AND
  schema_types empty AND
  not ri.has_schema          → ONE `schema_missing` fail/high
                               (BACKWARD-COMPAT — listed recommended_types)

  recommended non-empty AND
  schema_types empty AND
  ri.has_schema=True         → ONE `schema_types_recommended` passed
                               (page has markup but we don't have parsed
                               types — defer to deep-extract refresh)

  recommended non-empty AND
  all recommended present    → ONE `schema_types_complete` passed (0.9 conf)

  recommended non-empty AND
  partial overlap            → N `schema_missing_type` fail findings,
                               one per missing type, each carrying an
                               example JSON-LD snippet + Russian
                               rationale string for the composer.

No DB / LLM access. Set arithmetic only. `ri.schema_types` is populated
by `context_builder` from the latest `PageDeepExtract.schema_blocks`.
"""

from __future__ import annotations

from app.core_audit.intent_codes import IntentCode
from app.core_audit.profile_protocol import SiteProfile
from app.core_audit.review.dto import ReviewInput
from app.core_audit.review.findings import CheckFinding, CheckResult, FindingStatus
from app.profiles.tourism.schema_rules import TOURISM_SCHEMA_EXAMPLES


# Intent buckets for severity assignment. Membership is tested by the
# raw IntentCode.value string so a synthetic intent that hasn't been
# wired into the profile still produces a sensible severity (medium).
_COMMERCIAL_INTENTS: frozenset[str] = frozenset({
    IntentCode.COMM_MODIFIED.value,
    IntentCode.COMM_CATEGORY.value,
    IntentCode.COMM_COMPARE.value,
    IntentCode.TRANS_BOOK.value,
    IntentCode.TRANS_BRAND.value,
})

_INFO_TRUST_LOCAL_INTENTS: frozenset[str] = frozenset({
    IntentCode.INFO_DEST.value,
    IntentCode.INFO_LOGISTICS.value,
    IntentCode.INFO_PREP.value,
    IntentCode.TRUST_LEGAL.value,
    IntentCode.LOCAL_GEO.value,
})

# Intents where missing one of {FAQPage, Offer, TouristTrip, Product} is
# CRITICAL — these schemas directly drive Yandex price + FAQ rich snippets
# on the most commercially valuable pages.
_CRITICAL_COMMERCIAL_INTENTS: frozenset[str] = frozenset({
    IntentCode.COMM_MODIFIED.value,
    IntentCode.TRANS_BOOK.value,
    IntentCode.COMM_CATEGORY.value,
})

_HIGH_VALUE_COMMERCIAL_TYPES: frozenset[str] = frozenset({
    "FAQPage", "Offer", "TouristTrip", "Product",
})

# Short, owner-facing Russian explanation of the SERP effect each type
# gives. Embedded verbatim into evidence so the composer can surface it
# without adding LLM-grade phrasing of its own. Keep one-line answers.
_RATIONALE_RU: dict[str, str] = {
    "TouristTrip": "Размечает страницу как тур — Яндекс понимает контент.",
    "Offer": "Цена в выдаче — без Offer ценовой сниппет невозможен.",
    "Product": (
        "Альтернатива TouristTrip — продаётся как товар, поддерживается "
        "Яндексом для ценовых сниппетов."
    ),
    "FAQPage": (
        "Раскрывающиеся вопросы под результатом — поднимает CTR на 20-40%."
    ),
    "Service": (
        "Тип услуги, дополняет TouristTrip — помогает Яндексу классифицировать."
    ),
    "AggregateOffer": "Диапазон цен «от X до Y» если туров несколько на странице.",
    "BreadcrumbList": "Путь в выдаче вместо URL — пользователь видит структуру.",
    "Article": (
        "Базовый тип статьи — без него Яндекс хуже ранжирует контентные страницы."
    ),
    "HowTo": "Пошаговая раскладушка с шагами в выдаче.",
    "Organization": "Карточка организации с телефоном/адресом.",
    "LocalBusiness": "Карточка организации с телефоном/адресом и геопозицией.",
    "ItemList": "Список туров на категорийной странице.",
}


def _severity_for_missing_type(missing_type: str, intent_value: str) -> str:
    """Three-tier severity per the schema-fix audit spec.

    Order of checks:
      1. CRITICAL — high-value commercial type missing on the most
         commercial intents (COMM_MODIFIED, TRANS_BOOK, COMM_CATEGORY).
      2. HIGH    — any type missing on any other COMM_*/TRANS_* intent.
      3. MEDIUM  — anything missing on INFO_*/TRUST_*/LOCAL_* intents.
      4. MEDIUM  — fallback when intent is in neither bucket (defensive).
    """
    if (
        intent_value in _CRITICAL_COMMERCIAL_INTENTS
        and missing_type in _HIGH_VALUE_COMMERCIAL_TYPES
    ):
        return "critical"
    if intent_value in _COMMERCIAL_INTENTS:
        return "high"
    if intent_value in _INFO_TRUST_LOCAL_INTENTS:
        return "medium"
    return "medium"


def check_schema(ri: ReviewInput, profile: SiteProfile) -> CheckResult:
    recommended = profile.schema_rules.get(ri.target_intent)
    present = tuple(ri.schema_types or ())
    stats: dict = {
        "has_schema": ri.has_schema,
        "schema_types_present": list(present),
    }

    # ── Branch 1: intent not in profile at all ──────────────────────
    if recommended is None:
        return CheckResult(findings=[CheckFinding(
            signal_type="schema_missing",
            status=FindingStatus.not_applicable,
            confidence=1.0,
            evidence={"reason": "unknown_intent_in_profile"},
        )], stats=stats)

    # ── Branch 2: intent declared but no schema needed ──────────────
    if not recommended:
        return CheckResult(findings=[CheckFinding(
            signal_type="schema_missing",
            status=FindingStatus.not_applicable,
            confidence=1.0,
            evidence={"reason": "schema_not_applicable_for_intent"},
        )], stats=stats)

    # ── Branch 3: BACKWARD-COMPAT — no schema at all on a page that
    # needs schema. Emit the legacy `schema_missing` umbrella card so
    # Studio + existing tests keep rendering exactly the same way for
    # the «add any schema» path.
    if not present and not ri.has_schema:
        return CheckResult(findings=[CheckFinding(
            signal_type="schema_missing",
            status=FindingStatus.fail,
            severity="high",
            confidence=1.0,
            evidence={
                "has_schema": False,
                "recommended_types": list(recommended),
                "intent": ri.target_intent.value,
            },
        )], stats=stats)

    # ── Branch 4: page has markup but we don't have parsed types ────
    # (older fingerprint, deep-extract not run yet). Emit the
    # informational passed finding rather than spamming N missing-type
    # cards from no data.
    if not present:
        return CheckResult(findings=[CheckFinding(
            signal_type="schema_types_recommended",
            status=FindingStatus.passed,
            confidence=0.7,
            evidence={
                "has_schema": True,
                "recommended_types": list(recommended),
                "note": "type_level_check_deferred_to_deep_extract",
            },
        )], stats=stats)

    # ── Branches 5+6: we have parsed types — diff against recommended.
    present_set = set(present)
    missing = [t for t in recommended if t not in present_set]

    # Full coverage → single passed finding.
    if not missing:
        return CheckResult(findings=[CheckFinding(
            signal_type="schema_types_complete",
            status=FindingStatus.passed,
            confidence=0.9,
            evidence={
                "has_schema": True,
                "recommended_types": list(recommended),
                "present_types": list(present),
                "reason": "all_recommended_types_present",
            },
        )], stats=stats)

    # Partial coverage → ONE fail finding per missing recommended type.
    findings: list[CheckFinding] = []
    intent_value = ri.target_intent.value
    for missing_type in missing:
        severity = _severity_for_missing_type(missing_type, intent_value)
        rationale = _RATIONALE_RU.get(
            missing_type,
            f"Дополнительный тип Schema.org для интента «{intent_value}».",
        )
        evidence: dict = {
            "missing_type": missing_type,
            "intent": intent_value,
            "present_types": list(present),
            # Composer uses `schema_types_present` to render «найдено: …»;
            # keep both keys (`present_types` for callers reading evidence
            # directly, `schema_types_present` for the composer template).
            "schema_types_present": list(present),
            "recommended_types": list(recommended),
            "rationale_ru": rationale,
        }
        example = TOURISM_SCHEMA_EXAMPLES.get(missing_type)
        if example is not None:
            evidence["example_jsonld"] = example
        findings.append(CheckFinding(
            signal_type="schema_missing_type",
            status=FindingStatus.fail,
            severity=severity,
            confidence=0.95,
            evidence=evidence,
        ))

    stats["missing_schema_types"] = list(missing)
    return CheckResult(findings=findings, stats=stats)
