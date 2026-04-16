"""Site health score — deterministic 0-100 aggregate across 4 signals.

  Coverage (40%)      — fraction of intents 'strong' or 'over_covered'
  Critical recs (30%) — 1 - critical_count/50 (capped)
  Indexation (20%)    — pages_indexed / pages_total
  Click trend (10%)   — +1 if WoW impressions positive, 0.5 neutral, 0 negative

Pure function for testability.
"""

from __future__ import annotations


WEIGHT_COVERAGE = 0.40
WEIGHT_CRITICAL = 0.30
WEIGHT_INDEXATION = 0.20
WEIGHT_TREND = 0.10

CRITICAL_REC_CAP = 50                              # 50+ criticals → component 0


def compute_health_score(
    *,
    coverage_strong_pct: float,                    # 0-1, strong+over_covered / total intents
    critical_recs_count: int,
    indexation_rate: float,                        # 0-1
    wow_impressions_pct: float | None,             # + / 0 / - / None
) -> int:
    coverage = max(min(coverage_strong_pct, 1.0), 0.0)

    crit_clamped = max(min(critical_recs_count, CRITICAL_REC_CAP), 0)
    critical = 1.0 - (crit_clamped / CRITICAL_REC_CAP)

    indexation = max(min(indexation_rate, 1.0), 0.0)

    if wow_impressions_pct is None:
        trend = 0.5
    elif wow_impressions_pct > 0:
        trend = 1.0
    elif wow_impressions_pct < 0:
        trend = 0.0
    else:
        trend = 0.5

    raw = (
        WEIGHT_COVERAGE * coverage
        + WEIGHT_CRITICAL * critical
        + WEIGHT_INDEXATION * indexation
        + WEIGHT_TREND * trend
    )
    return int(round(raw * 100))
