"""Commercial-factor detection — regex patterns from profile.commercial_factors.

Factors with `detection_pattern=None` (e.g. price_above_fold needs DOM
position) are DEFERRED — emitted as `commercial_factor_deferred_to_llm`
not_applicable findings. Step 4 LLM will handle them.

Severity inherits from CommercialFactor.priority.

Audit 2026-05-14 — fan-out collapse: a typical tourism page can be missing
up to nine commercial factors (телефон, цена, callback-форма, график,
оплата, РТО в футере, отзывы со схемой, Яндекс.Карты, договор-оферта).
We used to emit nine separate findings → nine cards. Now we emit ONE
`commercial_factors_missing` finding with `evidence.missing_items`; the
composer turns it into a single bulleted card. Per-factor `_present`
and `_deferred_to_llm` findings are kept (summary and LLM context still
want the breakdown).
"""

from __future__ import annotations

from app.core_audit.profile_protocol import SiteProfile
from app.core_audit.review.dto import ReviewInput
from app.core_audit.review.findings import CheckFinding, CheckResult, FindingStatus


_SEVERITY_RANK: dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


def _max_severity(severities: list[str]) -> str:
    if not severities:
        return "medium"
    return max(severities, key=lambda s: _SEVERITY_RANK.get(s, 0))


def check_commercial(ri: ReviewInput, profile: SiteProfile) -> CheckResult:
    findings: list[CheckFinding] = []
    stats: dict = {
        "missing_commercial_factors": [],
        "present_commercial_factors": [],
        "deferred_commercial_factors": [],
    }

    if not ri.content_text:
        # ONE aggregate not-applicable instead of N — keep the deny-list
        # so the summary can still render «no content_text — N factors
        # not checked» without faking findings.
        findings.append(CheckFinding(
            signal_type="commercial_factors_missing",
            status=FindingStatus.not_applicable,
            confidence=1.0,
            evidence={
                "reason": "no_content_text",
                "missing_items": [cf.name for cf in profile.commercial_factors],
            },
        ))
        return CheckResult(findings=findings, stats=stats)

    text = ri.content_text
    missing_items: list[str] = []
    missing_descriptions: list[str] = []
    missing_priorities: list[str] = []
    for cf in profile.commercial_factors:
        if cf.detection_pattern is None:
            stats["deferred_commercial_factors"].append(cf.name)
            findings.append(CheckFinding(
                signal_type="commercial_factor_deferred_to_llm",
                status=FindingStatus.not_applicable,
                confidence=1.0,
                evidence={
                    "factor_name": cf.name,
                    "priority": cf.priority,
                    "description_ru": cf.description_ru,
                    "reason": "needs_dom_position_or_jsonld",
                },
            ))
            continue

        hit = cf.detection_pattern.search(text)
        if hit:
            stats["present_commercial_factors"].append(cf.name)
            findings.append(CheckFinding(
                signal_type="commercial_factor_present",
                status=FindingStatus.passed,
                confidence=0.85,
                evidence={
                    "factor_name": cf.name,
                    "matched_text": hit.group(0)[:80],
                    "priority": cf.priority,
                },
            ))
        else:
            stats["missing_commercial_factors"].append(cf.name)
            missing_items.append(cf.name)
            missing_descriptions.append(cf.description_ru)
            missing_priorities.append(cf.priority)

    if missing_items:
        findings.append(CheckFinding(
            signal_type="commercial_factors_missing",
            status=FindingStatus.fail,
            severity=_max_severity(missing_priorities),
            confidence=0.85,
            evidence={
                "missing_items": missing_items,
                "missing_descriptions": missing_descriptions,
                "missing_priorities": missing_priorities,
                "count": len(missing_items),
            },
        ))

    return CheckResult(findings=findings, stats=stats)
