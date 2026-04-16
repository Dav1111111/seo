"""E-E-A-T signal detection — regex patterns from profile.eeat_signals.

User requirement 6: v1 is SIGNAL DETECTION, not legal validation. A missing
РТО regex match doesn't prove the site is illegal — it says our detector
didn't find the pattern. Wording in composer softens the tone accordingly.

Severity inherits from EEATSignal.priority (critical/high/medium/low).
"""

from __future__ import annotations

from app.core_audit.profile_protocol import SiteProfile
from app.core_audit.review.dto import ReviewInput
from app.core_audit.review.findings import CheckFinding, CheckResult, FindingStatus


def check_eeat(ri: ReviewInput, profile: SiteProfile) -> CheckResult:
    findings: list[CheckFinding] = []
    stats: dict = {"missing_eeat_signals": [], "present_eeat_signals": []}

    if not ri.content_text:
        for signal in profile.eeat_signals:
            findings.append(CheckFinding(
                signal_type="eeat_signal_missing",
                status=FindingStatus.not_applicable,
                confidence=1.0,
                evidence={"reason": "no_content_text", "signal_name": signal.name},
            ))
        return CheckResult(findings=findings, stats=stats)

    text = ri.content_text
    for signal in profile.eeat_signals:
        hit = signal.pattern.search(text) if signal.pattern is not None else None
        if hit:
            stats["present_eeat_signals"].append(signal.name)
            findings.append(CheckFinding(
                signal_type="eeat_signal_present",
                status=FindingStatus.passed,
                confidence=0.9,
                evidence={
                    "signal_name": signal.name,
                    "matched_text": hit.group(0)[:80],
                    "priority": signal.priority,
                },
            ))
        else:
            stats["missing_eeat_signals"].append(signal.name)
            findings.append(CheckFinding(
                signal_type="eeat_signal_missing",
                status=FindingStatus.fail,
                severity=signal.priority,
                confidence=0.85,
                evidence={
                    "signal_name": signal.name,
                    "priority": signal.priority,
                    "pattern": signal.pattern.pattern if signal.pattern else None,
                },
            ))

    return CheckResult(findings=findings, stats=stats)
