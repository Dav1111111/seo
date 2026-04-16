"""Keyword density check — computed SEPARATELY for title / h1 / body.

User requirement 4 (2026-04-17): density must be per-scope, not one global
number — a short title with one repeat has different meaning from a body
paragraph with the same ratio.

Thresholds (seo-technical audit):
  body:  > 4%  → fail/critical (Баден-Баден stuffing)
         3-4%  → fail/high
         2-3%  → warn/medium
         < 0.3% → warn/medium  (under-optimization, only on commercial intents)
  title: > 33% → fail/high  (e.g. 2 of 6 tokens is a repetition signal — handled in title_checks)
                   density_title emits pass/fail informational only
  h1:    mirror of title thresholds (informational)

Density is Russian-lemmatized with stopwords stripped. Non-Russian or
missing target query → not_applicable.
"""

from __future__ import annotations

from app.core_audit.intent_codes import IntentCode
from app.core_audit.profile_protocol import SiteProfile
from app.core_audit.review.checks._text_utils import density, lemma_list, lemma_set
from app.core_audit.review.dto import ReviewInput
from app.core_audit.review.findings import CheckFinding, CheckResult, FindingStatus


COMMERCIAL_INTENTS = frozenset({
    IntentCode.COMM_MODIFIED,
    IntentCode.COMM_CATEGORY,
    IntentCode.TRANS_BOOK,
    IntentCode.LOCAL_GEO,
})


def _skip(signal: str, reason: str) -> CheckFinding:
    return CheckFinding(
        signal_type=signal,
        status=FindingStatus.not_applicable,
        confidence=1.0,
        evidence={"reason": reason},
    )


def check_density(ri: ReviewInput, profile: SiteProfile) -> CheckResult:
    findings: list[CheckFinding] = []
    stats: dict = {}

    if ri.lang != "ru":
        for signal in ("density_title", "density_h1", "density_body"):
            findings.append(_skip(signal, f"lang={ri.lang}"))
        return CheckResult(findings=findings, stats={"density_skipped": "non_russian"})

    if not ri.top_queries:
        for signal in ("density_title", "density_h1", "density_body"):
            findings.append(_skip(signal, "no_target_query"))
        return CheckResult(findings=findings, stats={"density_skipped": "no_target_query"})

    target = lemma_set(ri.top_queries[0], drop_stopwords=True)
    if not target:
        for signal in ("density_title", "density_h1", "density_body"):
            findings.append(_skip(signal, "empty_target_lemmas"))
        return CheckResult(findings=findings, stats={"density_skipped": "empty_target"})

    stats["target_lemmas"] = sorted(target)
    is_commercial = ri.target_intent in COMMERCIAL_INTENTS

    # Title scope
    findings.extend(_scope_density(
        signal="density_title", scope_text=ri.title, target=target,
        high_threshold=0.50, warn_threshold=0.33, is_commercial=is_commercial, stats=stats, stats_key="density_title",
    ))
    # H1 scope
    findings.extend(_scope_density(
        signal="density_h1", scope_text=ri.h1, target=target,
        high_threshold=0.50, warn_threshold=0.33, is_commercial=is_commercial, stats=stats, stats_key="density_h1",
    ))
    # Body scope — the primary Баден-Баден trigger
    findings.extend(_scope_density(
        signal="density_body", scope_text=ri.content_text, target=target,
        high_threshold=0.04, warn_threshold=0.02, is_commercial=is_commercial, stats=stats, stats_key="density_body",
        critical_threshold=0.04, over_threshold=0.03,
        under_threshold=0.003,   # <0.3% on commercial = under-optimized
    ))

    return CheckResult(findings=findings, stats=stats)


def _scope_density(
    *,
    signal: str,
    scope_text: str | None,
    target: frozenset[str],
    high_threshold: float,
    warn_threshold: float,
    is_commercial: bool,
    stats: dict,
    stats_key: str,
    critical_threshold: float | None = None,
    over_threshold: float | None = None,
    under_threshold: float | None = None,
) -> list[CheckFinding]:
    """Compute density in one scope; emit finding. Body has extra under-optimization gate."""
    if not scope_text:
        return [_skip(signal, "empty_scope_text")]
    lemmas = lemma_list(scope_text, drop_stopwords=True)
    if not lemmas:
        return [_skip(signal, "no_lemmas_in_scope")]

    ratio, matches = density(target, lemmas)
    stats[stats_key] = round(ratio, 4)
    stats[stats_key + "_matches"] = matches
    stats[stats_key + "_total"] = len(lemmas)

    ev = {
        "density": round(ratio, 4),
        "matches": matches,
        "total_lemmas": len(lemmas),
        "target_lemmas": sorted(target),
    }

    # Body-specific critical gate
    if critical_threshold is not None and ratio > critical_threshold:
        return [CheckFinding(
            signal_type=signal, status=FindingStatus.fail, severity="critical",
            confidence=0.9, evidence=ev,
        )]
    if over_threshold is not None and ratio > over_threshold:
        return [CheckFinding(
            signal_type=signal, status=FindingStatus.fail, severity="high",
            confidence=0.85, evidence=ev,
        )]
    if ratio > high_threshold:
        return [CheckFinding(
            signal_type=signal, status=FindingStatus.fail, severity="high",
            confidence=0.85, evidence=ev,
        )]
    if ratio > warn_threshold:
        return [CheckFinding(
            signal_type=signal, status=FindingStatus.warn, severity="medium",
            confidence=0.85, evidence=ev,
        )]
    if under_threshold is not None and is_commercial and ratio < under_threshold:
        return [CheckFinding(
            signal_type=signal, status=FindingStatus.warn, severity="medium",
            confidence=0.7, evidence={**ev, "under_optimization": True},
        )]
    return [CheckFinding(
        signal_type=signal, status=FindingStatus.passed, confidence=0.9, evidence=ev,
    )]
