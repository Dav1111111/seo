"""H2 block completeness — split critical vs recommended tiers.

User requirement 5: don't treat all missing H2s equally. `critical_h2_blocks`
missing → fail/high severity. `recommended_h2_blocks` missing → warn/medium.

Matching is lenient: we lemmatize both sides and check if the required
block's lemma set is a subset of any existing H2's lemma set. Rationale:
a page may name "Цены на туры" while requirement is "Цены" — they match.

If crawler hasn't emitted h2_blocks yet (`ri.h2_blocks == ()`), we emit
one `not_applicable` finding per tier plus a single stat
`{"h2_extraction": "unavailable"}` — reviewer UI softens wording.
"""

from __future__ import annotations

from app.core_audit.profile_protocol import SiteProfile
from app.core_audit.review.checks._text_utils import lemma_set
from app.core_audit.review.dto import ReviewInput
from app.core_audit.review.findings import CheckFinding, CheckResult, FindingStatus


def _matches(required_lemmas: frozenset[str], h2_lemma_sets: list[frozenset[str]]) -> bool:
    if not required_lemmas:
        return True
    return any(required_lemmas.issubset(existing) for existing in h2_lemma_sets)


def check_h2_completeness(ri: ReviewInput, profile: SiteProfile) -> CheckResult:
    findings: list[CheckFinding] = []
    stats: dict = {}

    req = profile.page_requirements.get(ri.target_intent)
    if req is None:
        return CheckResult(
            findings=[CheckFinding(
                signal_type="missing_critical_h2",
                status=FindingStatus.not_applicable,
                confidence=1.0,
                evidence={"reason": "no_profile_requirements"},
            )],
        )

    critical = req.critical_h2_blocks
    recommended = req.recommended_h2_blocks
    h2_blocks = ri.h2_blocks

    if not h2_blocks:
        stats["h2_extraction"] = "unavailable"
        if critical:
            findings.append(CheckFinding(
                signal_type="missing_critical_h2",
                status=FindingStatus.not_applicable,
                confidence=0.5,
                evidence={"reason": "h2_extraction_unavailable", "expected": list(critical)},
            ))
        if recommended:
            findings.append(CheckFinding(
                signal_type="missing_recommended_h2",
                status=FindingStatus.not_applicable,
                confidence=0.5,
                evidence={"reason": "h2_extraction_unavailable", "expected": list(recommended)},
            ))
        return CheckResult(findings=findings, stats=stats)

    existing_sets = [lemma_set(h) for h in h2_blocks]

    missing_critical: list[str] = []
    for block in critical:
        if not _matches(lemma_set(block), existing_sets):
            missing_critical.append(block)
    missing_recommended: list[str] = []
    for block in recommended:
        if not _matches(lemma_set(block), existing_sets):
            missing_recommended.append(block)

    stats["missing_critical_h2"] = missing_critical
    stats["missing_recommended_h2"] = missing_recommended

    # Emit one finding per missing block (UI can render distinct cards).
    for block in missing_critical:
        findings.append(CheckFinding(
            signal_type="missing_critical_h2",
            status=FindingStatus.fail,
            severity="high",
            confidence=0.85,
            evidence={"block": block, "tier": "critical", "existing_h2": list(h2_blocks)},
        ))
    for block in missing_recommended:
        findings.append(CheckFinding(
            signal_type="missing_recommended_h2",
            status=FindingStatus.warn,
            severity="medium",
            confidence=0.8,
            evidence={"block": block, "tier": "recommended", "existing_h2": list(h2_blocks)},
        ))
    if not missing_critical and critical:
        findings.append(CheckFinding(
            signal_type="missing_critical_h2",
            status=FindingStatus.passed,
            confidence=0.9,
            evidence={"all_present": list(critical)},
        ))
    if not missing_recommended and recommended:
        findings.append(CheckFinding(
            signal_type="missing_recommended_h2",
            status=FindingStatus.passed,
            confidence=0.9,
            evidence={"all_present": list(recommended)},
        ))

    return CheckResult(findings=findings, stats=stats)
