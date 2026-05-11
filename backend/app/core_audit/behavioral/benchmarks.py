"""CTR benchmarks by SERP position.

Public industry data, conservative midpoints (commercial Yandex queries
typically run 5-15% lower than Google). Used to flag pages where the
snippet under-clicks for its ranked position.

Numbers are intentionally midpoints, not floors — a page at position 3
with 8% CTR is fine; the same page at 2% CTR is the signal we want.
"""

from __future__ import annotations

# Expected CTR by integer position (1-10). Approximate, commerce-leaning.
# Source: aggregate Yandex CTR studies 2024-2026, conservative midpoints.
CTR_BY_POSITION: dict[int, float] = {
    1: 0.27,
    2: 0.16,
    3: 0.10,
    4: 0.07,
    5: 0.05,
    6: 0.04,
    7: 0.03,
    8: 0.025,
    9: 0.02,
    10: 0.015,
}

# Below this position the SERP CTR signal is too noisy — Yandex Maps,
# ads, and AI Overviews crowd the page.
POSITION_FLOOR = 10

# A query needs at least this many impressions in the lookback window
# before its CTR is meaningful. With less, sample noise dominates.
# 30 is a compromise: small sites with new traffic still get signals,
# while obvious noise (1-3 impressions) is filtered out.
MIN_IMPRESSIONS = 30

# Gap ratio (actual / expected) below which we flag. 0.6 means actual
# is at least 40% short of expected — well above noise.
GAP_THRESHOLD = 0.6

# Severe under-clicking — likely a snippet/intent mismatch, not just
# a wording weakness. Used to escalate severity.
SEVERE_GAP_THRESHOLD = 0.35


def expected_ctr_for_position(avg_position: float | None) -> float | None:
    """Return midpoint expected CTR for a fractional avg position.

    Linear-interp between integer benchmarks; None if position is below
    floor or unparseable.
    """
    if avg_position is None:
        return None
    pos = float(avg_position)
    if pos < 1.0 or pos > POSITION_FLOOR + 0.5:
        return None

    lo = max(1, int(pos))
    hi = min(POSITION_FLOOR, lo + 1)
    if lo == hi or hi not in CTR_BY_POSITION:
        return CTR_BY_POSITION[lo]

    frac = pos - lo
    return CTR_BY_POSITION[lo] * (1 - frac) + CTR_BY_POSITION[hi] * frac


def ctr_gap_severity(
    actual_ctr: float,
    expected_ctr: float,
    impressions: int,
) -> str:
    """Map (actual, expected, impressions) to severity bucket.

    Returns one of: "critical", "high", "medium", "low", or "" if no gap.
    """
    if expected_ctr <= 0:
        return ""
    ratio = actual_ctr / expected_ctr
    if ratio >= GAP_THRESHOLD:
        return ""

    # Severe gap = clear snippet/intent mismatch.
    if ratio < SEVERE_GAP_THRESHOLD:
        if impressions >= 1000:
            return "critical"
        if impressions >= 300:
            return "high"
        return "medium"

    # Mild gap = title weak but not broken.
    if impressions >= 1000:
        return "high"
    if impressions >= 300:
        return "medium"
    return "low"
