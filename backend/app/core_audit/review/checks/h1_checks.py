"""H1 structural checks: presence + duplicate-vs-title.

H1 = Title exact match is LOW severity (SEO best practice miss, not a
Yandex penalty — clarified by seo-technical audit).
"""

from __future__ import annotations

import unicodedata

from app.core_audit.profile_protocol import SiteProfile
from app.core_audit.review.dto import ReviewInput
from app.core_audit.review.findings import CheckFinding, CheckResult, FindingStatus


def _normalize(s: str | None) -> str:
    if not s:
        return ""
    return " ".join(unicodedata.normalize("NFKC", s).casefold().split()).strip()


def check_h1(ri: ReviewInput, profile: SiteProfile) -> CheckResult:
    findings: list[CheckFinding] = []
    stats: dict = {}

    if not ri.h1 or not ri.h1.strip():
        findings.append(CheckFinding(
            signal_type="h1_missing",
            status=FindingStatus.fail,
            severity="high",
            confidence=1.0,
            evidence={},
        ))
        stats["h1_equals_title"] = False
        return CheckResult(findings=findings, stats=stats)

    h1_norm = _normalize(ri.h1)
    title_norm = _normalize(ri.title)
    h1_equals_title = bool(h1_norm and title_norm and h1_norm == title_norm)
    stats["h1_equals_title"] = h1_equals_title

    if h1_equals_title:
        findings.append(CheckFinding(
            signal_type="h1_equals_title",
            status=FindingStatus.warn,
            severity="low",
            confidence=1.0,
            evidence={"h1": ri.h1, "title": ri.title},
        ))
    else:
        findings.append(CheckFinding(
            signal_type="h1_equals_title",
            status=FindingStatus.passed,
            confidence=1.0,
            evidence={},
        ))

    return CheckResult(findings=findings, stats=stats)
