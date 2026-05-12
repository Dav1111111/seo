"""CTR benchmarks by SERP position.

Public industry data, conservative midpoints (commercial Yandex queries
typically run 5-15% lower than Google). Used to flag pages where the
snippet under-clicks for its ranked position.

Numbers are intentionally midpoints, not floors — a page at position 3
with 8% CTR is fine; the same page at 2% CTR is the signal we want.

Curves are stratified by intent. Informational queries (how-to, what-is,
guides) consistently under-click vs commercial / transactional ones at
the same position — users scan many results before settling, and the
top result rarely owns the click. Mixing the two into one curve was
making us flag healthy informational pages as «недоклик»; separate
curves keep the signal honest.
"""

from __future__ import annotations

# Expected CTR by integer position (1-10). Approximate, commerce-leaning.
# Source: aggregate Yandex CTR studies 2024-2026, conservative midpoints.
CTR_BY_POSITION_COMMERCIAL: dict[int, float] = {
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

# Informational intent CTR — consistently below commercial at every
# position. Same shape (steep top-1 then long tail), just lower
# amplitude. These are approximate midpoints; calibrate when the
# site has ≥1000 own observations.
CTR_BY_POSITION_INFO: dict[int, float] = {
    1: 0.12,
    2: 0.07,
    3: 0.05,
    4: 0.04,
    5: 0.03,
    6: 0.025,
    7: 0.02,
    8: 0.015,
    9: 0.012,
    10: 0.01,
}

# Back-compat alias so existing imports keep working. New code should
# use the explicit `expected_ctr(..., intent_code=...)` API.
CTR_BY_POSITION: dict[int, float] = CTR_BY_POSITION_COMMERCIAL

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


# Intent codes we treat as informational for benchmark purposes.
# Anything else (commercial, transactional, navigational, unknown) uses
# the commercial curve — that's a conservative default since the
# commercial curve has *higher* expectations and so generates more,
# not fewer, false-positive gap flags. Better to flag a maybe-info page
# than to silently miss a commercial under-clicker.
_INFO_INTENT_CODES: frozenset[str] = frozenset({
    "info",
    "informational",
    "info_guide",
    "info_howto",
    "info_concept",
    "info_research",
    "tofu",
})


def _curve_for_intent(intent_code: str | None) -> dict[int, float]:
    if intent_code and intent_code.lower() in _INFO_INTENT_CODES:
        return CTR_BY_POSITION_INFO
    return CTR_BY_POSITION_COMMERCIAL


def expected_ctr(position: int, intent_code: str = "commercial") -> float:
    """Approximate midpoint expected CTR at an integer position.

    Returns 0.0 for positions outside [1, POSITION_FLOOR]. Numbers are
    midpoints, not floors; calibrate when the site has ≥1000 own
    observations of impressions+clicks at each position.
    """
    if position < 1 or position > POSITION_FLOOR:
        return 0.0
    curve = _curve_for_intent(intent_code)
    return curve.get(int(position), 0.0)


def expected_ctr_for_position(
    avg_position: float | None,
    intent_code: str = "commercial",
) -> float | None:
    """Return midpoint expected CTR for a fractional avg position.

    Linear-interp between integer benchmarks; None if position is below
    floor or unparseable. Intent-aware — info-style pages use a lower
    expectations curve than commercial ones (see `expected_ctr`).
    """
    if avg_position is None:
        return None
    pos = float(avg_position)
    if pos < 1.0 or pos > POSITION_FLOOR + 0.5:
        return None

    curve = _curve_for_intent(intent_code)
    lo = max(1, int(pos))
    hi = min(POSITION_FLOOR, lo + 1)
    if lo == hi or hi not in curve:
        return curve[lo]

    frac = pos - lo
    return curve[lo] * (1 - frac) + curve[hi] * frac


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
