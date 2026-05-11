"""Behavioral signals layer — CTR/dwell/bounce gap detection.

Yandex 2026 ranking formula gives 30-45% weight to behavioral factors.
This module reads Webmaster impressions/clicks/avg_position per query
(already collected by collectors/webmaster.py into DailyMetric) and
flags pages where the snippet under-clicks for its position. No LLM,
no Metrica required — just math on existing Webmaster data.

CTR-gap rationale (the cheapest, fastest behavioral win):
  - position 3 is expected to get ~10-12% CTR
  - if a page sits at position 3 with CTR 2% → snippet does not match
    the intent or the title is weak
  - rewriting title/meta for that page lifts CTR without any other work
  - this directly raises traffic without waiting for ranking changes
"""

from app.core_audit.behavioral.benchmarks import (
    expected_ctr_for_position,
    ctr_gap_severity,
)
from app.core_audit.behavioral.ctr_gap import (
    CtrGap,
    scan_ctr_gaps,
)

__all__ = [
    "CtrGap",
    "ctr_gap_severity",
    "expected_ctr_for_position",
    "scan_ctr_gaps",
]
