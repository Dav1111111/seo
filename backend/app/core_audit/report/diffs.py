"""Pure week-over-week math helpers. No DB, no IO."""

from __future__ import annotations

from datetime import date, timedelta


def week_range(week_end: date) -> tuple[date, date]:
    """Return (week_start, week_end) inclusive 7-day window ending on week_end."""
    return week_end - timedelta(days=6), week_end


def prev_week_range(week_end: date) -> tuple[date, date]:
    return week_end - timedelta(days=13), week_end - timedelta(days=7)


def pct_diff(this: float | int | None, prev: float | int | None) -> float | None:
    """WoW percent: (this - prev) / prev. None if prev missing/zero."""
    if this is None or prev is None:
        return None
    if prev == 0:
        return None
    return round(((float(this) - float(prev)) / float(prev)) * 100, 2)


def clamp_pct(value: float | None, ceiling: float = 999.0) -> float | None:
    if value is None:
        return None
    return round(max(min(value, ceiling), -ceiling), 2)


def default_week_end(today: date | None = None) -> date:
    """Last completed Sunday (UTC). Monday run → last Sunday."""
    d = today or date.today()
    # Monday=0 ... Sunday=6
    days_since_sunday = (d.weekday() + 1) % 7
    return d - timedelta(days=days_since_sunday or 7)
