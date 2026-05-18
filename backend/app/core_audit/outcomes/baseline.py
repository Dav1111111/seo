"""Shared Webmaster windows for before/after outcome tracking."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_metric import DailyMetric


# Webmaster query_performance lags ~5-10 days. If we read "last 7 days
# from today" the window is often mostly empty. Shift both baseline and
# follow-up backward so deltas compare populated windows.
WEBMASTER_LAG_DAYS = 7
BASELINE_WINDOW_DAYS = 7


def webmaster_lagged_window() -> tuple[date, date]:
    window_end = date.today() - timedelta(days=WEBMASTER_LAG_DAYS)
    window_start = window_end - timedelta(days=BASELINE_WINDOW_DAYS)
    return window_start, window_end


async def baseline_metrics(db: AsyncSession, site_id: uuid.UUID) -> dict[str, Any]:
    """Site-wide metrics over the shared lag-aware 7-day window."""
    window_start, window_end = webmaster_lagged_window()
    row = (await db.execute(
        select(
            func.coalesce(func.sum(DailyMetric.impressions), 0).label("impressions"),
            func.coalesce(func.sum(DailyMetric.clicks), 0).label("clicks"),
            func.avg(DailyMetric.avg_position).label("avg_position"),
        ).where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.date.between(window_start, window_end),
        )
    )).first()
    if row is None:
        return {
            "impressions_7d": 0,
            "clicks_7d": 0,
            "avg_position": None,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }
    return {
        "impressions_7d": int(row.impressions or 0),
        "clicks_7d": int(row.clicks or 0),
        "avg_position": float(row.avg_position) if row.avg_position else None,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
    }


__all__ = [
    "BASELINE_WINDOW_DAYS",
    "WEBMASTER_LAG_DAYS",
    "baseline_metrics",
    "webmaster_lagged_window",
]
