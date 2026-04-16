"""Schema.org presence check — boolean only in v1.

We only know `ri.has_schema: bool` without parsing raw HTML. Type-level
and cargo-cult-type detection (TouristTrip etc.) defer to Step 4 LLM.

Emits:
  - `schema_missing` fail/high  — has_schema=False AND profile has recommended types for intent
  - `schema_types_recommended` passed (low) — has_schema=True AND profile has recommended types (informational)
  - not_applicable — profile.schema_rules.get(intent) is None (unknown intent)
                     or == () (no schema recommended for this intent)
"""

from __future__ import annotations

from app.core_audit.profile_protocol import SiteProfile
from app.core_audit.review.dto import ReviewInput
from app.core_audit.review.findings import CheckFinding, CheckResult, FindingStatus


def check_schema(ri: ReviewInput, profile: SiteProfile) -> CheckResult:
    recommended = profile.schema_rules.get(ri.target_intent)
    stats = {"has_schema": ri.has_schema}

    if recommended is None:
        return CheckResult(findings=[CheckFinding(
            signal_type="schema_missing",
            status=FindingStatus.not_applicable,
            confidence=1.0,
            evidence={"reason": "unknown_intent_in_profile"},
        )], stats=stats)

    if not recommended:  # empty tuple = schema not applicable for this intent
        return CheckResult(findings=[CheckFinding(
            signal_type="schema_missing",
            status=FindingStatus.not_applicable,
            confidence=1.0,
            evidence={"reason": "schema_not_applicable_for_intent"},
        )], stats=stats)

    if not ri.has_schema:
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

    return CheckResult(findings=[CheckFinding(
        signal_type="schema_types_recommended",
        status=FindingStatus.passed,
        confidence=0.7,  # schema present but we can't verify types — low confidence of full pass
        evidence={
            "has_schema": True,
            "recommended_types": list(recommended),
            "note": "type_level_check_deferred_to_llm",
        },
    )], stats=stats)
