"""CTR-by-position curve for the keyword_match uplift calculation.

The existing `core_audit.behavioral.benchmarks` module already owns the
authoritative Yandex CTR curve (commercial vs informational, with
position interpolation). We *re-export* from there so we never end up
with two divergent CTR tables in the codebase.

If you need to tune CTR numbers, do it in `behavioral/benchmarks.py` —
this module will pick up the change automatically.
"""

from __future__ import annotations

from app.core_audit.behavioral.benchmarks import (
    expected_ctr as _benchmark_expected_ctr,
    expected_ctr_for_position,
)


def expected_ctr(
    position: float | None,
    intent_code: str = "commercial",
) -> float:
    """Approximate Yandex CTR at a SERP position.

    Accepts fractional positions (e.g. avg_position 4.3 from
    Webmaster) by interpolating between integer benchmarks. Returns
    0.0 outside the meaningful range (None, < 1, or below the noise
    floor used by behavioral/).

    Defaults to the commercial curve, which matches the dominant
    tourism-pilot intent and is the conservative choice (higher
    expectations → more candidate gaps to triage).
    """
    if position is None:
        return 0.0
    # behavioral/benchmarks.expected_ctr_for_position returns None
    # for positions below 1 or far beyond the noise floor. Map that
    # to 0.0 so callers can do plain arithmetic.
    if position < 1:
        return 0.0
    val = expected_ctr_for_position(float(position), intent_code=intent_code)
    if val is None:
        # Outside the benchmark window — treat as 0 click yield.
        # This catches the "ranks page 5+, CTR meaningless" case.
        return 0.0
    return val


def expected_clicks_uplift(
    volume: int,
    current_position: float | None,
    target_position: int = 5,
    intent_code: str = "commercial",
) -> int:
    """Estimated extra clicks/month if the page reaches `target_position`.

    Computes `volume × (CTR(target) − CTR(current))`. Floored at 0 — we
    never recommend a query where the projected uplift is negative
    (i.e. the page already ranks better than the target).

    `current_position=None` is treated as CTR=0 (the page is not in
    SERP at all), so the full target CTR counts as uplift.
    """
    if volume <= 0:
        return 0
    current_ctr = expected_ctr(current_position, intent_code=intent_code)
    target_ctr = _benchmark_expected_ctr(target_position, intent_code=intent_code)
    uplift = volume * (target_ctr - current_ctr)
    return max(0, round(uplift))


__all__ = [
    "expected_ctr",
    "expected_clicks_uplift",
]
