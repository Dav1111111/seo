"""Commercial-factor detection — regex patterns from profile.commercial_factors.

Factors with `detection_pattern=None` (e.g. price_above_fold needs DOM
position) are DEFERRED — emitted as `commercial_factor_deferred_to_llm`
not_applicable findings. Step 4 LLM will handle them.

Severity inherits from CommercialFactor.priority.
"""

from __future__ import annotations

from app.core_audit.profile_protocol import SiteProfile
from app.core_audit.review.dto import ReviewInput
from app.core_audit.review.findings import CheckFinding, CheckResult, FindingStatus


def check_commercial(ri: ReviewInput, profile: SiteProfile) -> CheckResult:
    findings: list[CheckFinding] = []
    stats: dict = {
        "missing_commercial_factors": [],
        "present_commercial_factors": [],
        "deferred_commercial_factors": [],
    }

    if not ri.content_text:
        for cf in profile.commercial_factors:
            findings.append(CheckFinding(
                signal_type="commercial_factor_missing",
                status=FindingStatus.not_applicable,
                confidence=1.0,
                evidence={"reason": "no_content_text", "factor_name": cf.name},
            ))
        return CheckResult(findings=findings, stats=stats)

    text = ri.content_text
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
            findings.append(CheckFinding(
                signal_type="commercial_factor_missing",
                status=FindingStatus.fail,
                severity=cf.priority,
                confidence=0.85,
                evidence={
                    "factor_name": cf.name,
                    "priority": cf.priority,
                    "description_ru": cf.description_ru,
                    "pattern": cf.detection_pattern.pattern,
                },
            ))

    return CheckResult(findings=findings, stats=stats)
