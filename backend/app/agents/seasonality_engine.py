"""
Deterministic seasonality engine for Russian tourism.
No AI — pure logic based on calendar, holidays, and historical weights.

Usage:
    engine = SeasonalityEngine()
    info = engine.get_season_info(date(2026, 7, 15))
    # → {"season": "summer_peak", "multiplier": 1.8, "is_holiday": False, ...}

    threshold = engine.adjust_anomaly_threshold(base=0.30, target_date=date(2026, 1, 5))
    # → 0.45  (holiday period — raise threshold to avoid false alarms)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class SeasonInfo:
    season: str                 # winter_peak, spring_shoulder, summer_peak, autumn_shoulder
    month_weight: float         # 0.0-1.0 relative expected traffic
    is_holiday: bool
    holiday_name: str | None
    traffic_multiplier: float   # combined weight × holiday adjustment
    note: str


# ── Russian federal holidays (fixed dates) ─────────────────────────────────
# Format: (month, day_start, day_end, name, traffic_multiplier)
RUSSIAN_HOLIDAYS: list[tuple[int, int, int, str, float]] = [
    (1, 1, 8, "Новогодние каникулы", 1.6),          # New Year holidays — peak for winter tours
    (2, 23, 23, "День защитника Отечества", 1.1),
    (3, 8, 8, "Международный женский день", 1.15),
    (5, 1, 3, "Праздник Весны и Труда", 1.4),       # May holidays — short trip peak
    (5, 9, 11, "День Победы + выходные", 1.4),       # Victory Day extended
    (6, 12, 12, "День России", 1.1),
    (11, 4, 4, "День народного единства", 1.05),
]

# School holidays (approximate, affects family travel)
SCHOOL_HOLIDAYS: list[tuple[int, int, int, int, str]] = [
    (3, 22, 3, 31, "Весенние каникулы"),    # Spring break
    (6, 1, 8, 31, "Летние каникулы"),       # Summer break
    (10, 28, 11, 5, "Осенние каникулы"),    # Autumn break
    (12, 28, 1, 10, "Зимние каникулы"),     # Winter break
]

# Tourism season weights by month (relative 0.0-1.0)
# Based on Russian tourism patterns: summer sea + winter mountains
MONTH_WEIGHTS: dict[int, tuple[float, str]] = {
    1:  (0.65, "winter_peak"),        # New Year + ski season
    2:  (0.55, "winter_peak"),        # Ski season
    3:  (0.40, "spring_shoulder"),    # End of ski, low demand
    4:  (0.35, "spring_shoulder"),    # Low season
    5:  (0.50, "spring_shoulder"),    # May holidays spike
    6:  (0.75, "summer_peak"),        # Summer starts
    7:  (1.00, "summer_peak"),        # Peak summer
    8:  (0.95, "summer_peak"),        # Peak summer
    9:  (0.65, "summer_peak"),        # Velvet season
    10: (0.35, "autumn_shoulder"),    # Low season
    11: (0.30, "autumn_shoulder"),    # Lowest
    12: (0.50, "winter_peak"),        # Pre-NYE bookings
}


class SeasonalityEngine:
    """Deterministic tourism seasonality for grandtourspirit.ru and similar sites."""

    def __init__(
        self,
        custom_month_weights: dict[int, float] | None = None,
        custom_holidays: list[tuple[int, int, int, str, float]] | None = None,
    ):
        self._month_weights = custom_month_weights or {m: w for m, (w, _) in MONTH_WEIGHTS.items()}
        self._holidays = custom_holidays or RUSSIAN_HOLIDAYS

    def get_season_info(self, target_date: date) -> SeasonInfo:
        month = target_date.month
        day = target_date.day

        weight, season = MONTH_WEIGHTS.get(month, (0.5, "unknown"))
        if month in self._month_weights:
            weight = self._month_weights[month]

        # Check holidays
        is_holiday = False
        holiday_name: str | None = None
        holiday_mult = 1.0

        for h_month, h_start, h_end, h_name, h_mult in self._holidays:
            if month == h_month and h_start <= day <= h_end:
                is_holiday = True
                holiday_name = h_name
                holiday_mult = h_mult
                break

        traffic_multiplier = round(weight * holiday_mult, 3)

        # Build note
        notes = []
        if is_holiday:
            notes.append(f"Праздник: {holiday_name}")
        if self._is_school_holiday(target_date):
            notes.append("Школьные каникулы — рост семейных поездок")
        if season == "summer_peak":
            notes.append("Пик сезона морских туров")
        elif season == "winter_peak":
            notes.append("Пик сезона горных/лыжных туров")
        elif "shoulder" in season:
            notes.append("Межсезонье — низкий спрос")

        return SeasonInfo(
            season=season,
            month_weight=weight,
            is_holiday=is_holiday,
            holiday_name=holiday_name,
            traffic_multiplier=traffic_multiplier,
            note="; ".join(notes) if notes else "Обычный период",
        )

    def adjust_anomaly_threshold(
        self,
        base_threshold: float,
        target_date: date,
    ) -> float:
        """
        Raise the anomaly threshold during low-traffic periods and holidays
        to avoid false positives.

        base_threshold: e.g. 0.30 (30% drop triggers alert)
        Returns: adjusted threshold (higher = less sensitive)
        """
        info = self.get_season_info(target_date)

        adjustment = 1.0

        # Shoulder seasons: traffic naturally lower, raise threshold
        if "shoulder" in info.season:
            adjustment *= 1.3

        # Holidays: traffic spikes/dips are expected
        if info.is_holiday:
            adjustment *= 1.2

        # Very low traffic months (Oct-Nov): noise dominates
        if info.month_weight < 0.35:
            adjustment *= 1.4

        return round(min(base_threshold * adjustment, 0.80), 3)

    def get_expected_traffic_ratio(
        self,
        current_date: date,
        comparison_date: date,
    ) -> float:
        """
        Expected traffic ratio: current / comparison.
        E.g. July vs January = 1.0 / 0.65 = 1.54 (July should be 54% higher).
        """
        curr = self.get_season_info(current_date)
        comp = self.get_season_info(comparison_date)
        if comp.traffic_multiplier == 0:
            return 1.0
        return round(curr.traffic_multiplier / comp.traffic_multiplier, 3)

    def to_context_dict(self, target_date: date) -> dict[str, Any]:
        """Serialize for agent prompt context."""
        info = self.get_season_info(target_date)
        return {
            "date": target_date.isoformat(),
            "season": info.season,
            "month_weight": info.month_weight,
            "is_holiday": info.is_holiday,
            "holiday_name": info.holiday_name,
            "traffic_multiplier": info.traffic_multiplier,
            "note": info.note,
        }

    @staticmethod
    def _is_school_holiday(d: date) -> bool:
        for sm, sd, em, ed, _ in SCHOOL_HOLIDAYS:
            if sm <= em:
                start = date(d.year, sm, sd)
                end = date(d.year, em, ed)
                if start <= d <= end:
                    return True
            else:
                # Crosses year boundary (e.g. Dec 28 → Jan 10)
                start_this = date(d.year, sm, sd)
                end_next = date(d.year + 1, em, ed)
                start_prev = date(d.year - 1, sm, sd)
                end_this = date(d.year, em, ed)
                if d >= start_this or d <= end_this:
                    return True
        return False
