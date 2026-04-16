"""Section 4 — Query Performance Trends (WoW).

Two 7-day windows. Top movers by absolute impression diff. New/lost
queries = presence in top-50 this-week vs prev-week.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.report.diffs import clamp_pct, pct_diff, prev_week_range, week_range
from app.core_audit.report.dto import QueryMove, QueryTrendsSection
from app.models.daily_metric import DailyMetric
from app.models.search_query import SearchQuery

TOP_MOVERS_LIMIT = 10
TOP_QUERY_WINDOW = 50


async def build_query_trends(
    db: AsyncSession, site_id: UUID, week_end: date,
) -> QueryTrendsSection:
    t_start, t_end = week_range(week_end)
    p_start, p_end = prev_week_range(week_end)

    # Site-level totals for both windows in one query using CASE
    totals_row = await db.execute(
        select(
            func.coalesce(func.sum(case(
                (and_(DailyMetric.date >= t_start, DailyMetric.date <= t_end),
                 DailyMetric.impressions), else_=0,
            )), 0).label("imp_this"),
            func.coalesce(func.sum(case(
                (and_(DailyMetric.date >= p_start, DailyMetric.date <= p_end),
                 DailyMetric.impressions), else_=0,
            )), 0).label("imp_prev"),
            func.coalesce(func.sum(case(
                (and_(DailyMetric.date >= t_start, DailyMetric.date <= t_end),
                 DailyMetric.clicks), else_=0,
            )), 0).label("clk_this"),
            func.coalesce(func.sum(case(
                (and_(DailyMetric.date >= p_start, DailyMetric.date <= p_end),
                 DailyMetric.clicks), else_=0,
            )), 0).label("clk_prev"),
        ).where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.date.between(p_start, t_end),
        )
    )
    t = totals_row.first()
    imp_this = int(t.imp_this or 0)
    imp_prev = int(t.imp_prev or 0)
    clk_this = int(t.clk_this or 0)
    clk_prev = int(t.clk_prev or 0)

    if imp_this == 0 and imp_prev == 0:
        return QueryTrendsSection(
            data_available=False,
            note_ru="Нет данных Yandex Webmaster за обе недели.",
        )

    # Per-query this + prev week impressions + avg_position
    per_query_stmt = (
        select(
            DailyMetric.dimension_id,
            SearchQuery.query_text,
            func.coalesce(func.sum(case(
                (and_(DailyMetric.date >= t_start, DailyMetric.date <= t_end),
                 DailyMetric.impressions), else_=0,
            )), 0).label("imp_this"),
            func.coalesce(func.sum(case(
                (and_(DailyMetric.date >= p_start, DailyMetric.date <= p_end),
                 DailyMetric.impressions), else_=0,
            )), 0).label("imp_prev"),
            func.avg(case(
                (and_(DailyMetric.date >= t_start, DailyMetric.date <= t_end),
                 DailyMetric.avg_position),
            )).label("pos_this"),
            func.avg(case(
                (and_(DailyMetric.date >= p_start, DailyMetric.date <= p_end),
                 DailyMetric.avg_position),
            )).label("pos_prev"),
        )
        .join(SearchQuery, SearchQuery.id == DailyMetric.dimension_id)
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.date.between(p_start, t_end),
        )
        .group_by(DailyMetric.dimension_id, SearchQuery.query_text)
    )
    rows = (await db.execute(per_query_stmt)).all()

    movers = []
    for r in rows:
        diff = int(r.imp_this or 0) - int(r.imp_prev or 0)
        if diff == 0:
            continue
        movers.append(QueryMove(
            query_text=r.query_text or "?",
            impressions_this_week=int(r.imp_this or 0),
            impressions_prev_week=int(r.imp_prev or 0),
            impressions_diff=diff,
            avg_position_this_week=round(float(r.pos_this), 1) if r.pos_this else None,
            avg_position_prev_week=round(float(r.pos_prev), 1) if r.pos_prev else None,
        ))

    movers.sort(key=lambda m: m.impressions_diff, reverse=True)
    top_up = [m for m in movers if m.impressions_diff > 0][:TOP_MOVERS_LIMIT]
    top_down = list(reversed([m for m in movers if m.impressions_diff < 0][-TOP_MOVERS_LIMIT:]))

    # New / lost queries: top-50 by impressions this week vs prev week
    this_top = sorted(
        [r for r in rows if (r.imp_this or 0) > 0],
        key=lambda r: int(r.imp_this or 0), reverse=True,
    )[:TOP_QUERY_WINDOW]
    prev_top = sorted(
        [r for r in rows if (r.imp_prev or 0) > 0],
        key=lambda r: int(r.imp_prev or 0), reverse=True,
    )[:TOP_QUERY_WINDOW]
    this_set = {r.query_text for r in this_top if r.query_text}
    prev_set = {r.query_text for r in prev_top if r.query_text}
    new_queries = sorted(this_set - prev_set)[:10]
    lost_queries = sorted(prev_set - this_set)[:10]

    return QueryTrendsSection(
        data_available=True,
        totals_this_week={"impressions": imp_this, "clicks": clk_this},
        totals_prev_week={"impressions": imp_prev, "clicks": clk_prev},
        wow_diff={
            "impressions_pct": clamp_pct(pct_diff(imp_this, imp_prev)) or 0.0,
            "clicks_pct": clamp_pct(pct_diff(clk_this, clk_prev)) or 0.0,
        },
        top_movers_up=top_up,
        top_movers_down=top_down,
        new_queries=new_queries,
        lost_queries=lost_queries,
    )
