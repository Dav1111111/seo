"""Title-level detection: length + target-keyword repetition.

Stuffing detection (keyword count >=3 in title) is HIGH severity, not
critical — Yandex Баден-Баден is probabilistic. Critical is reserved for
full-page density breach (see overoptimization.py).

Thresholds (seo-technical audit 2026-04-17):
  len > 90 → fail/high
  len > 70 → warn/medium     (Yandex SERP truncates ~70-75 chars Cyrillic)
  title_keyword_count >= 3 → fail/high
  title_keyword_count == 2 → warn/medium
  missing title → fail/critical
"""

from __future__ import annotations

from app.core_audit.profile_protocol import SiteProfile
from app.core_audit.review.checks._text_utils import lemma_list, lemma_set
from app.core_audit.review.dto import ReviewInput
from app.core_audit.review.findings import CheckFinding, CheckResult, FindingStatus


def check_title(ri: ReviewInput, profile: SiteProfile) -> CheckResult:
    findings: list[CheckFinding] = []
    stats: dict = {}

    if not ri.title or not ri.title.strip():
        findings.append(CheckFinding(
            signal_type="title_missing",
            status=FindingStatus.fail,
            severity="critical",
            confidence=1.0,
            evidence={},
        ))
        stats["title_char_length"] = 0
        stats["title_keyword_count"] = 0
        return CheckResult(findings=findings, stats=stats)

    title = ri.title.strip()
    length = len(title)
    stats["title_char_length"] = length

    if length > 90:
        findings.append(CheckFinding(
            signal_type="title_length",
            status=FindingStatus.fail,
            severity="high",
            confidence=1.0,
            evidence={"length": length, "max_visual": 70, "hard_cap": 90},
        ))
    elif length > 70:
        findings.append(CheckFinding(
            signal_type="title_length",
            status=FindingStatus.warn,
            severity="medium",
            confidence=1.0,
            evidence={"length": length, "max_visual": 70},
        ))
    else:
        findings.append(CheckFinding(
            signal_type="title_length",
            status=FindingStatus.passed,
            confidence=1.0,
            evidence={"length": length},
        ))

    # Target-keyword repetition (only if Russian + have a target query)
    if ri.lang == "ru" and ri.top_queries:
        target = lemma_set(ri.top_queries[0], drop_stopwords=True)
        title_lemmas = lemma_list(title, drop_stopwords=True)
        if target and title_lemmas:
            # Count how many title lemmas are in target set
            keyword_count = sum(1 for t in title_lemmas if t in target)
            stats["title_keyword_count"] = keyword_count

            if keyword_count >= 3:
                findings.append(CheckFinding(
                    signal_type="title_keyword_repetition",
                    status=FindingStatus.fail,
                    severity="high",
                    confidence=0.85,
                    evidence={
                        "keyword_count": keyword_count,
                        "target_lemmas": sorted(target),
                        "title_text": title,
                    },
                ))
            elif keyword_count == 2:
                findings.append(CheckFinding(
                    signal_type="title_keyword_repetition",
                    status=FindingStatus.warn,
                    severity="medium",
                    confidence=0.75,
                    evidence={
                        "keyword_count": keyword_count,
                        "target_lemmas": sorted(target),
                        "title_text": title,
                    },
                ))
            else:
                findings.append(CheckFinding(
                    signal_type="title_keyword_repetition",
                    status=FindingStatus.passed,
                    confidence=0.9,
                    evidence={"keyword_count": keyword_count},
                ))
        else:
            stats["title_keyword_count"] = 0
            findings.append(CheckFinding(
                signal_type="title_keyword_repetition",
                status=FindingStatus.not_applicable,
                confidence=1.0,
                evidence={"reason": "empty_lemmas_or_target"},
            ))
    else:
        stats["title_keyword_count"] = 0
        findings.append(CheckFinding(
            signal_type="title_keyword_repetition",
            status=FindingStatus.not_applicable,
            confidence=1.0,
            evidence={"reason": "non_russian_or_no_target_query", "lang": ri.lang},
        ))

    return CheckResult(findings=findings, stats=stats)
