"""Structured review findings — Layer 1 output of the Python check pipeline.

Findings are PURE FACTS. No Russian prose, no recommendations. Layer 2
(composer.py) turns findings into Recommendation rows with reasoning text.

Every check function returns `list[CheckFinding]` — one per signal it
evaluated. Even "pass" and "not_applicable" findings are emitted so the
reviewer UI can render green checkmarks + skip reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FindingStatus(str, Enum):
    """Outcome of a single signal check."""
    passed = "pass"                 # signal checked, no issue
    warn = "warn"                   # mild deviation, should fix
    fail = "fail"                   # hard deviation, must fix
    not_applicable = "not_applicable"  # skipped (lang mismatch, missing data, etc.)


# Canonical signal_type registry. Checks MUST use these strings so composer
# can look up Russian templates deterministically. Add new signals here.
SIGNAL_TYPES: frozenset[str] = frozenset({
    # title_checks
    "title_length",
    "title_keyword_repetition",
    "title_missing",
    # h1_checks
    "h1_missing",
    "h1_equals_title",
    # density_checks (split per scope — user requirement 4)
    "density_title",
    "density_h1",
    "density_body",
    # h2_completeness (split — user requirement 5)
    "missing_critical_h2",
    "missing_recommended_h2",
    # schema_checks
    "schema_missing",
    "schema_types_recommended",
    "schema_cargo_cult_present",
    # eeat_checks
    "eeat_signal_missing",
    "eeat_signal_present",
    # commercial_checks
    "commercial_factor_missing",
    "commercial_factor_present",
    "commercial_factor_deferred_to_llm",
    # overoptimization
    "over_optimization_stuffing",
})


@dataclass(frozen=True)
class CheckFinding:
    """One structured observation. Pure — no Russian text here.

    Fields:
      signal_type     — from SIGNAL_TYPES (e.g. 'title_length')
      status          — FindingStatus (pass/warn/fail/not_applicable)
      severity        — RecPriority-like string, only set when status in
                        {warn, fail}. Composer uses it to map to Recommendation
                        priority. Accepts: critical | high | medium | low.
      confidence      — 0.0-1.0. How sure the detector is. Regex detection
                        for РТО is ~0.9 (regex can false-negative on spacing
                        variants); length checks are 1.0 (deterministic).
      evidence        — structured facts. Composer reads these to build
                        before/after text and reasoning_ru.
    """
    signal_type: str
    status: FindingStatus
    severity: str | None = None
    confidence: float = 1.0
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Light contract guard — catches typos early.
        if self.signal_type not in SIGNAL_TYPES:
            raise ValueError(f"unknown signal_type: {self.signal_type!r}")
        if self.status in (FindingStatus.warn, FindingStatus.fail) and self.severity is None:
            raise ValueError(f"severity required when status={self.status.value}")


@dataclass(frozen=True)
class CheckResult:
    """Container returned by every check function.

    `findings` is the primary output (structured). `stats` is a free-form
    dict the aggregator merges into PageLevelSummary (e.g. density_body=0.023,
    title_char_length=72). Never contains Russian prose.
    """
    findings: list[CheckFinding] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
