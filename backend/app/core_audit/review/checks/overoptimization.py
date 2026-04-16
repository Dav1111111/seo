"""Aggregate over-optimization gate.

Reads density + title_keyword_count stats from prior checks and decides
whether the combination crosses the Баден-Баден line (title stuffing AND
body stuffing together = critical). Does NOT re-compute density.

Designed to run AFTER title_checks + density_checks in the aggregator.
"""

from __future__ import annotations

from app.core_audit.profile_protocol import SiteProfile
from app.core_audit.review.dto import ReviewInput
from app.core_audit.review.findings import CheckFinding, CheckResult, FindingStatus


def check_overoptimization(
    ri: ReviewInput,
    profile: SiteProfile,
    *,
    prior_stats: dict,
) -> CheckResult:
    density_body = float(prior_stats.get("density_body", 0.0) or 0.0)
    title_keyword_count = int(prior_stats.get("title_keyword_count", 0) or 0)

    # Critical combination: title stuffing AND body stuffing
    body_over = density_body > 0.03   # 3%+ body density
    title_stuffed = title_keyword_count >= 3

    if body_over and title_stuffed:
        return CheckResult(findings=[CheckFinding(
            signal_type="over_optimization_stuffing",
            status=FindingStatus.fail,
            severity="critical",
            confidence=0.9,
            evidence={
                "density_body": round(density_body, 4),
                "title_keyword_count": title_keyword_count,
                "rule": "body_density>3% AND title_keyword_count>=3",
            },
        )])

    # Body-only critical (already caught by density_checks at 4%, but this
    # catches the 3-4% band when combined with title_keyword_count==2)
    if density_body > 0.03 and title_keyword_count >= 2:
        return CheckResult(findings=[CheckFinding(
            signal_type="over_optimization_stuffing",
            status=FindingStatus.fail,
            severity="high",
            confidence=0.8,
            evidence={
                "density_body": round(density_body, 4),
                "title_keyword_count": title_keyword_count,
                "rule": "body_density>3% AND title_keyword_count>=2",
            },
        )])

    return CheckResult(findings=[CheckFinding(
        signal_type="over_optimization_stuffing",
        status=FindingStatus.passed,
        confidence=0.9,
        evidence={
            "density_body": round(density_body, 4),
            "title_keyword_count": title_keyword_count,
        },
    )])
