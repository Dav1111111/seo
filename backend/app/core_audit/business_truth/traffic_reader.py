"""traffic_reader — Webmaster queries → traffic share per direction.

Two layers:

  aggregate_traffic(query_impressions, services, geos)
      Pure function: given a list of (query_text, impressions) tuples
      + the owner's vocabulary, classify each query into direction(s),
      aggregate impressions, return TrafficDistribution.
      → unit-testable without DB

  load_traffic_distribution(db, site_id, services, geos, days_back)
      DB wrapper: pulls non-branded queries + their impressions over
      the last N days, then calls aggregate_traffic.
      → one integration test covers the SQL path
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import date, timedelta
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.business_truth.dto import DirectionKey
from app.core_audit.business_truth.matcher import classify_text
from app.models.daily_metric import DailyMetric
from app.models.search_query import SearchQuery


DEFAULT_TRAFFIC_LOOKBACK_DAYS = 30


@dataclasses.dataclass
class TrafficDistribution:
    """Result of aggregating query_performance metrics into directions."""
    direction_weights: dict[DirectionKey, float]
    total_impressions: int
    unclassified_impressions: int

    @property
    def coverage_share(self) -> float:
        """Fraction of impressions that mapped to some known direction."""
        if self.total_impressions <= 0:
            return 0.0
        return 1.0 - (self.unclassified_impressions / self.total_impressions)


def aggregate_traffic(
    query_impressions: Iterable[tuple[str, int]],
    services: Iterable[str],
    geos: Iterable[str],
) -> TrafficDistribution:
    """Aggregate (query, impressions) pairs into direction weights.

    Impressions of a query that matches multiple direction keys are
    split equally among them. Queries that match no direction go to
    `unclassified_impressions`. Final direction_weights sum to 1.0
    over the CLASSIFIED impression pool (so the "coverage" is reported
    separately as total − unclassified).
    """
    rows = list(query_impressions or [])
    if not rows:
        return TrafficDistribution({}, 0, 0)

    services_list = list(services)
    geos_list = list(geos)

    direction_raw: dict[DirectionKey, float] = {}
    total = 0
    unclassified = 0

    for q, imp in rows:
        imp = int(imp or 0)
        if imp <= 0:
            continue
        total += imp

        keys = classify_text(q or "", services_list, geos_list)
        if not keys:
            unclassified += imp
            continue

        share = imp / len(keys)
        for k in keys:
            direction_raw[k] = direction_raw.get(k, 0.0) + share

    classified = total - unclassified
    if classified <= 0:
        return TrafficDistribution({}, total, unclassified)

    weights = {k: v / classified for k, v in direction_raw.items()}
    return TrafficDistribution(
        direction_weights=weights,
        total_impressions=total,
        unclassified_impressions=unclassified,
    )


async def load_traffic_distribution(
    db: AsyncSession,
    site_id: uuid.UUID,
    services: Iterable[str],
    geos: Iterable[str],
    days_back: int = DEFAULT_TRAFFIC_LOOKBACK_DAYS,
) -> TrafficDistribution:
    """DB-backed variant: aggregate last-N-days impressions per query.

    Only non-branded queries count — branded traffic ("grandtourspirit")
    tells us nothing about market direction coverage.
    """
    since = date.today() - timedelta(days=days_back)

    stmt = (
        select(
            SearchQuery.query_text,
            func.coalesce(func.sum(DailyMetric.impressions), 0).label("imp"),
        )
        .join(
            DailyMetric,
            (DailyMetric.site_id == SearchQuery.site_id)
            & (DailyMetric.dimension_id == SearchQuery.id)
            & (DailyMetric.metric_type == "query_performance")
            & (DailyMetric.date >= since),
        )
        .where(
            SearchQuery.site_id == site_id,
            SearchQuery.is_branded.is_(False),
        )
        .group_by(SearchQuery.id, SearchQuery.query_text)
        .having(func.coalesce(func.sum(DailyMetric.impressions), 0) > 0)
    )
    rows = (await db.execute(stmt)).all()
    query_impressions = [(r.query_text, int(r.imp or 0)) for r in rows]
    return aggregate_traffic(query_impressions, services, geos)


__all__ = [
    "TrafficDistribution",
    "aggregate_traffic",
    "load_traffic_distribution",
    "DEFAULT_TRAFFIC_LOOKBACK_DAYS",
]
