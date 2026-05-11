"""CTR-gap scanner: flag queries that under-click for their position.

Reads `daily_metrics` (Webmaster pulls already populate per-query rows
with `metric_type='query'`, `dimension_id=SearchQuery.id`) over the last
N days, aggregates impressions/clicks, computes weighted avg position,
and compares actual CTR to position-based expected CTR.

Output is a flat list of CtrGap dataclasses — purely a signal layer,
no DB writes. The Brain snapshot consumes them; rules.py turns them
into recommended actions for the owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.behavioral.benchmarks import (
    GAP_THRESHOLD,
    MIN_IMPRESSIONS,
    POSITION_FLOOR,
    ctr_gap_severity,
    expected_ctr_for_position,
)
from app.models.daily_metric import DailyMetric
from app.models.search_query import SearchQuery


# DailyMetric.metric_type for per-query impressions/clicks/avg_position.
# Webmaster collector writes rows under "query_performance" (see
# collectors/webmaster.py); dimension_id is the SearchQuery.id.
QUERY_METRIC_TYPE = "query_performance"


@dataclass(frozen=True)
class CtrGap:
    """One under-clicking query — actionable if `severity` is set."""

    query_id: UUID
    query_text: str
    impressions: int
    clicks: int
    avg_position: float
    actual_ctr: float
    expected_ctr: float
    gap_ratio: float            # actual / expected, in [0, 1)
    severity: str               # "" | low | medium | high | critical
    wordstat_volume: int | None
    relevance: str              # own / adjacent / disputed / spam / unclassified


async def scan_ctr_gaps(
    db: AsyncSession,
    site_id: UUID,
    *,
    lookback_days: int = 30,
    min_impressions: int = MIN_IMPRESSIONS,
    gap_threshold: float = GAP_THRESHOLD,
    limit: int | None = 50,
) -> list[CtrGap]:
    """Return queries that under-click vs position benchmark.

    Filters out:
      - position floor 10+ (CTR meaningless below this)
      - <min_impressions in window (sample noise)
      - branded queries (CTR baseline very different)
      - spam-relevance queries (we don't want to optimize them)

    Sorted by (severity, impressions) descending — highest-impact first.
    """
    cutoff = date.today() - timedelta(days=lookback_days)

    rows = await db.execute(
        select(
            SearchQuery.id,
            SearchQuery.query_text,
            SearchQuery.is_branded,
            SearchQuery.relevance,
            SearchQuery.wordstat_volume,
            func.coalesce(func.sum(DailyMetric.impressions), 0).label("impressions"),
            func.coalesce(func.sum(DailyMetric.clicks), 0).label("clicks"),
            # Weighted-by-impressions avg position. Falls back to plain
            # AVG when sum(impressions)=0 (which we filter out anyway).
            func.coalesce(
                func.sum(DailyMetric.avg_position * DailyMetric.impressions)
                / func.nullif(func.sum(DailyMetric.impressions), 0),
                func.avg(DailyMetric.avg_position),
            ).label("avg_position"),
        )
        .join(
            DailyMetric,
            (DailyMetric.dimension_id == SearchQuery.id)
            & (DailyMetric.metric_type == QUERY_METRIC_TYPE),
        )
        .where(
            SearchQuery.site_id == site_id,
            DailyMetric.date >= cutoff,
        )
        .group_by(
            SearchQuery.id,
            SearchQuery.query_text,
            SearchQuery.is_branded,
            SearchQuery.relevance,
            SearchQuery.wordstat_volume,
        )
        .having(func.coalesce(func.sum(DailyMetric.impressions), 0) >= min_impressions)
    )

    gaps: list[CtrGap] = []
    for row in rows:
        # Skip noise: branded (CTR baseline differs), spam (don't optimize),
        # or unranked-deep (CTR signal is meaningless).
        if row.is_branded:
            continue
        if (row.relevance or "") == "spam":
            continue
        if row.avg_position is None or float(row.avg_position) > POSITION_FLOOR:
            continue
        if row.impressions <= 0:
            continue

        avg_pos = float(row.avg_position)
        expected = expected_ctr_for_position(avg_pos)
        if expected is None or expected <= 0:
            continue

        actual = float(row.clicks) / float(row.impressions)
        ratio = actual / expected
        if ratio >= gap_threshold:
            continue

        severity = ctr_gap_severity(actual, expected, int(row.impressions))
        if not severity:
            continue

        gaps.append(CtrGap(
            query_id=row.id,
            query_text=row.query_text,
            impressions=int(row.impressions),
            clicks=int(row.clicks),
            avg_position=avg_pos,
            actual_ctr=actual,
            expected_ctr=expected,
            gap_ratio=ratio,
            severity=severity,
            wordstat_volume=row.wordstat_volume,
            relevance=row.relevance or "unclassified",
        ))

    # Severity rank: critical=4 > high=3 > medium=2 > low=1
    sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    gaps.sort(
        key=lambda g: (sev_rank.get(g.severity, 0), g.impressions),
        reverse=True,
    )

    if limit is not None:
        gaps = gaps[:limit]
    return gaps
