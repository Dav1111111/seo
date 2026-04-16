"""Layer 3 — Aggregator. Runs all 8 checks, composes recommendations,
builds PageLevelSummary, returns ReviewResult (reviewer_model='python-only').

Order of execution:
  1. check_title          (produces title_keyword_count)
  2. check_h1             (produces h1_equals_title)
  3. check_density        (produces density_body, density_title, density_h1)
  4. check_overoptimization (reads prior stats — exception to uniform signature)
  5. check_h2_completeness
  6. check_schema
  7. check_eeat
  8. check_commercial

No short-circuit — reviewer UI needs the full picture.
"""

from __future__ import annotations

import time
from dataclasses import replace

from app.core_audit.profile_protocol import SiteProfile
from app.core_audit.review.checks import (
    check_commercial,
    check_density,
    check_eeat,
    check_h1,
    check_h2_completeness,
    check_schema,
    check_title,
)
from app.core_audit.review.checks.overoptimization import check_overoptimization
from app.core_audit.review.composer import compose
from app.core_audit.review.dto import PageLevelSummary, ReviewInput, ReviewResult
from app.core_audit.review.enums import ReviewStatus
from app.core_audit.review.findings import CheckFinding, FindingStatus
from typing import NamedTuple


class PythonCheckOutput(NamedTuple):
    """Dual output — ReviewResult plus the raw findings list for Step 4."""
    result: ReviewResult
    findings: list[CheckFinding]

REVIEWER_MODEL = "python-only"
REVIEWER_VERSION = "1.0.0"


def run_python_checks(ri: ReviewInput, profile: SiteProfile) -> ReviewResult:
    """Convenience entry point — returns ReviewResult only.

    Use `run_python_checks_with_findings` when the caller (e.g. Step 4
    LLM enricher) also needs the raw findings list for merging.
    """
    return run_python_checks_with_findings(ri, profile).result


def run_python_checks_with_findings(
    ri: ReviewInput, profile: SiteProfile,
) -> PythonCheckOutput:
    t0 = time.monotonic()
    all_findings: list[CheckFinding] = []
    merged_stats: dict = {}

    def _run(fn, *extra_kwargs):
        result = fn(ri, profile, **(extra_kwargs[0] if extra_kwargs else {}))
        all_findings.extend(result.findings)
        merged_stats.update(result.stats)

    _run(check_title)
    _run(check_h1)
    _run(check_density)
    _run(check_overoptimization, {"prior_stats": dict(merged_stats)})
    _run(check_h2_completeness)
    _run(check_schema)
    _run(check_eeat)
    _run(check_commercial)

    recommendations = compose(all_findings)
    summary = _build_summary(ri, all_findings, recommendations, merged_stats)
    duration_ms = int((time.monotonic() - t0) * 1000)

    result = ReviewResult(
        page_id=ri.page_id,
        site_id=ri.site_id,
        target_intent=ri.target_intent,
        composite_hash=ri.composite_hash,
        status=ReviewStatus.completed,
        reviewer_model=REVIEWER_MODEL,
        reviewer_version=REVIEWER_VERSION,
        summary=summary,
        recommendations=recommendations,
        cost_usd=0.0,
        input_tokens=0,
        output_tokens=0,
        duration_ms=duration_ms,
    )
    return PythonCheckOutput(result=result, findings=all_findings)


def _build_summary(
    ri: ReviewInput,
    findings: list[CheckFinding],
    recs,
    stats: dict,
) -> PageLevelSummary:
    crit = sum(1 for r in recs if r.priority.value == "critical")
    high = sum(1 for r in recs if r.priority.value == "high")
    med = sum(1 for r in recs if r.priority.value == "medium")
    low = sum(1 for r in recs if r.priority.value == "low")

    # Rough score-after heuristic (clamped 0-5)
    est_after = min(5.0, ri.current_score + 0.30 * crit + 0.15 * high + 0.05 * med)

    verdict = _verdict_ru(crit, high, med, low, stats)

    return PageLevelSummary(
        verdict_ru=verdict,
        current_score=ri.current_score,
        estimated_score_after=round(est_after, 2),
        critical_count=crit,
        high_count=high,
        medium_count=med,
        low_count=low,
        title_keyword_count=int(stats.get("title_keyword_count", 0) or 0),
        title_char_length=int(stats.get("title_char_length", 0) or 0),
        h1_equals_title=bool(stats.get("h1_equals_title", False)),
        keyword_density=float(stats.get("density_body", 0.0) or 0.0),
        missing_h2_blocks=tuple(
            [*(stats.get("missing_critical_h2") or []),
             *(stats.get("missing_recommended_h2") or [])]
        ),
        missing_eeat_signals=tuple(stats.get("missing_eeat_signals") or []),
        missing_commercial_factors=tuple(stats.get("missing_commercial_factors") or []),
    )


def _verdict_ru(crit: int, high: int, med: int, low: int, stats: dict) -> str:
    parts: list[str] = []
    if crit:
        parts.append(f"{crit} критич.")
    if high:
        parts.append(f"{high} важн.")
    if med:
        parts.append(f"{med} средн.")
    if low:
        parts.append(f"{low} низк.")
    if not parts:
        return "Серьёзных проблем не обнаружено (Python-проверки)."
    return "Обнаружено: " + ", ".join(parts) + "."
