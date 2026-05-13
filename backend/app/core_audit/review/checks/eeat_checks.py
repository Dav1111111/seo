"""E-E-A-T signal detection — regex patterns from profile.eeat_signals.

User requirement 6: v1 is SIGNAL DETECTION, not legal validation. A missing
РТО regex match doesn't prove the site is illegal — it says our detector
didn't find the pattern. Wording in composer softens the tone accordingly.

Severity inherits from EEATSignal.priority (critical/high/medium/low).

Audit 2026-05-14 — fan-out collapse: a tourism page can be missing up to
seven EEAT signals (РТО/ИНН/ОГРН/лицензия/автор/отзывы/Яндекс.Карты). Each
gap used to spawn a separate Recommendation card, drowning the owner in
seven near-identical «добавь блок легальности» tasks. Now we emit ONE
aggregate `eeat_signals_missing` finding with `evidence.missing_items` —
composer renders a single card with a bulleted list. The per-signal
`eeat_signal_present` findings stay (they feed PageLevelSummary and the
LLM enricher context); only the failures collapse.
"""

from __future__ import annotations

from app.core_audit.profile_protocol import SiteProfile
from app.core_audit.review.dto import ReviewInput
from app.core_audit.review.findings import CheckFinding, CheckResult, FindingStatus


# Severity ranking — higher means more important. Aggregate finding
# inherits the highest severity among its missing items so the priority
# scorer doesn't downgrade a critical РТО gap into a medium-priority card.
_SEVERITY_RANK: dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


def _max_severity(severities: list[str]) -> str:
    """Pick the strongest severity among missing-signal priorities."""
    if not severities:
        return "medium"
    return max(severities, key=lambda s: _SEVERITY_RANK.get(s, 0))


def check_eeat(ri: ReviewInput, profile: SiteProfile) -> CheckResult:
    findings: list[CheckFinding] = []
    stats: dict = {"missing_eeat_signals": [], "present_eeat_signals": []}

    if not ri.content_text:
        # No content → emit ONE not-applicable aggregate, not N copies.
        findings.append(CheckFinding(
            signal_type="eeat_signals_missing",
            status=FindingStatus.not_applicable,
            confidence=1.0,
            evidence={
                "reason": "no_content_text",
                "missing_items": [s.name for s in profile.eeat_signals],
            },
        ))
        return CheckResult(findings=findings, stats=stats)

    text = ri.content_text
    missing_items: list[str] = []
    missing_priorities: list[str] = []
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
            missing_items.append(signal.name)
            missing_priorities.append(signal.priority)

    # ONE aggregate fail finding — only when at least one signal is missing.
    if missing_items:
        findings.append(CheckFinding(
            signal_type="eeat_signals_missing",
            status=FindingStatus.fail,
            severity=_max_severity(missing_priorities),
            confidence=0.85,
            evidence={
                "missing_items": missing_items,
                "missing_priorities": missing_priorities,
                "count": len(missing_items),
            },
        ))

    return CheckResult(findings=findings, stats=stats)
