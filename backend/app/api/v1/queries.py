"""
Query analytics API — search queries with positions, metrics, clusters.
"""

import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.daily_metric import DailyMetric
from app.models.search_query import SearchQuery

router = APIRouter()


def _period_dates(days: int) -> tuple[date, date, date, date]:
    """Calculate current and previous period date ranges (with Webmaster 5-day lag)."""
    today = date.today()
    curr_end = today - timedelta(days=5)
    curr_start = curr_end - timedelta(days=days - 1)
    prev_end = curr_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    return curr_start, curr_end, prev_start, prev_end


@router.get("/sites/{site_id}/queries")
async def list_queries(
    site_id: uuid.UUID,
    days: int = Query(default=7, ge=7, le=30),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(default="impressions"),
    sort_dir: str = Query(default="desc"),
    cluster: str | None = None,
    search: str | None = None,
    min_impressions: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List all search queries with current and previous period metrics."""
    curr_start, curr_end, prev_start, prev_end = _period_dates(days)

    # Current period subquery
    curr_sq = (
        select(
            DailyMetric.dimension_id,
            func.sum(DailyMetric.impressions).label("impressions"),
            func.sum(DailyMetric.clicks).label("clicks"),
            func.avg(DailyMetric.avg_position).label("avg_position"),
            func.count(DailyMetric.date).label("days_count"),
        )
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.date.between(curr_start, curr_end),
        )
        .group_by(DailyMetric.dimension_id)
        .subquery("curr")
    )

    # Previous period subquery
    prev_sq = (
        select(
            DailyMetric.dimension_id,
            func.sum(DailyMetric.impressions).label("impressions"),
            func.sum(DailyMetric.clicks).label("clicks"),
            func.avg(DailyMetric.avg_position).label("avg_position"),
        )
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.date.between(prev_start, prev_end),
        )
        .group_by(DailyMetric.dimension_id)
        .subquery("prev")
    )

    # Main query joining search_queries with metrics
    base = (
        select(
            SearchQuery.id,
            SearchQuery.query_text,
            SearchQuery.cluster,
            SearchQuery.is_branded,
            SearchQuery.wordstat_volume,
            SearchQuery.first_seen_at,
            SearchQuery.last_seen_at,
            func.coalesce(curr_sq.c.impressions, 0).label("curr_impressions"),
            func.coalesce(curr_sq.c.clicks, 0).label("curr_clicks"),
            curr_sq.c.avg_position.label("curr_position"),
            func.coalesce(curr_sq.c.days_count, 0).label("curr_days"),
            func.coalesce(prev_sq.c.impressions, 0).label("prev_impressions"),
            func.coalesce(prev_sq.c.clicks, 0).label("prev_clicks"),
            prev_sq.c.avg_position.label("prev_position"),
        )
        .outerjoin(curr_sq, SearchQuery.id == curr_sq.c.dimension_id)
        .outerjoin(prev_sq, SearchQuery.id == prev_sq.c.dimension_id)
        .where(SearchQuery.site_id == site_id)
    )

    # Filters
    if cluster:
        base = base.where(SearchQuery.cluster == cluster)
    if search:
        base = base.where(SearchQuery.query_text.ilike(f"%{search}%"))
    if min_impressions > 0:
        base = base.where(func.coalesce(curr_sq.c.impressions, 0) >= min_impressions)

    # Count total
    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Sorting
    sort_map = {
        "impressions": func.coalesce(curr_sq.c.impressions, 0),
        "clicks": func.coalesce(curr_sq.c.clicks, 0),
        "position": curr_sq.c.avg_position,
        "query": SearchQuery.query_text,
        "volume": func.coalesce(SearchQuery.wordstat_volume, 0),
    }
    sort_col = sort_map.get(sort_by, sort_map["impressions"])
    if sort_dir == "asc":
        base = base.order_by(sort_col.asc().nullslast())
    else:
        base = base.order_by(sort_col.desc().nullslast())

    # Pagination
    base = base.offset(offset).limit(limit)

    rows = await db.execute(base)

    def pct_change(curr_val: int, prev_val: int) -> float | None:
        if not prev_val:
            return None
        return round((curr_val - prev_val) / prev_val * 100, 1)

    items = []
    for r in rows:
        curr_imp = int(r.curr_impressions)
        curr_clk = int(r.curr_clicks)
        curr_pos = round(float(r.curr_position), 1) if r.curr_position else None
        curr_ctr = round(curr_clk / curr_imp, 4) if curr_imp > 0 else 0.0

        prev_imp = int(r.prev_impressions)
        prev_clk = int(r.prev_clicks)
        prev_pos = round(float(r.prev_position), 1) if r.prev_position else None
        prev_ctr = round(prev_clk / prev_imp, 4) if prev_imp > 0 else 0.0

        # position_delta: positive = improved (position went from 10 to 7 = +3 improvement)
        pos_delta = None
        if curr_pos is not None and prev_pos is not None:
            pos_delta = round(prev_pos - curr_pos, 1)

        items.append({
            "id": str(r.id),
            "query_text": r.query_text,
            "cluster": r.cluster,
            "is_branded": r.is_branded,
            "wordstat_volume": r.wordstat_volume,
            "current": {
                "impressions": curr_imp,
                "clicks": curr_clk,
                "ctr": curr_ctr,
                "avg_position": curr_pos,
                "days_with_data": int(r.curr_days),
            },
            "previous": {
                "impressions": prev_imp,
                "clicks": prev_clk,
                "ctr": prev_ctr,
                "avg_position": prev_pos,
            },
            "changes": {
                "impressions_pct": pct_change(curr_imp, prev_imp),
                "clicks_pct": pct_change(curr_clk, prev_clk),
                "position_delta": pos_delta,
            },
            "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        })

    return {"total": total, "items": items}


@router.get("/sites/{site_id}/queries/{query_id}/history")
async def query_history(
    site_id: uuid.UUID,
    query_id: uuid.UUID,
    days: int = Query(default=30, ge=7, le=60),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Daily position/impression/click history for a specific query."""
    start = date.today() - timedelta(days=days + 5)  # account for data lag

    # Query info
    sq = await db.execute(
        select(SearchQuery).where(SearchQuery.id == query_id, SearchQuery.site_id == site_id)
    )
    query_row = sq.scalar_one_or_none()
    if not query_row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Query not found")

    # Daily metrics
    rows = await db.execute(
        select(
            DailyMetric.date,
            DailyMetric.impressions,
            DailyMetric.clicks,
            DailyMetric.ctr,
            DailyMetric.avg_position,
        )
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.dimension_id == query_id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.date >= start,
        )
        .order_by(DailyMetric.date)
    )

    history = [
        {
            "date": r.date.isoformat(),
            "impressions": int(r.impressions or 0),
            "clicks": int(r.clicks or 0),
            "ctr": round(float(r.ctr), 4) if r.ctr else 0.0,
            "avg_position": round(float(r.avg_position), 1) if r.avg_position else None,
        }
        for r in rows
    ]

    return {
        "id": str(query_row.id),
        "query_text": query_row.query_text,
        "cluster": query_row.cluster,
        "is_branded": query_row.is_branded,
        "wordstat_volume": query_row.wordstat_volume,
        "history": history,
    }


@router.get("/sites/{site_id}/queries/clusters")
async def list_clusters(
    site_id: uuid.UUID,
    days: int = Query(default=7, ge=7, le=30),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Cluster summary with aggregate metrics."""
    curr_start, curr_end, _, _ = _period_dates(days)

    rows = await db.execute(
        select(
            SearchQuery.cluster,
            func.count(SearchQuery.id.distinct()).label("query_count"),
            func.coalesce(func.sum(DailyMetric.impressions), 0).label("total_impressions"),
            func.coalesce(func.sum(DailyMetric.clicks), 0).label("total_clicks"),
            func.avg(DailyMetric.avg_position).label("avg_position"),
        )
        .outerjoin(
            DailyMetric,
            (DailyMetric.dimension_id == SearchQuery.id)
            & (DailyMetric.metric_type == "query_performance")
            & (DailyMetric.date.between(curr_start, curr_end)),
        )
        .where(SearchQuery.site_id == site_id)
        .group_by(SearchQuery.cluster)
        .order_by(func.coalesce(func.sum(DailyMetric.impressions), 0).desc())
    )

    clusters = []
    unclustered_count = 0

    for r in rows:
        name = r.cluster
        total_imp = int(r.total_impressions)
        total_clk = int(r.total_clicks)
        avg_ctr = round(total_clk / total_imp, 4) if total_imp > 0 else 0.0

        if name is None:
            unclustered_count = int(r.query_count)
            continue

        clusters.append({
            "name": name,
            "query_count": int(r.query_count),
            "total_impressions": total_imp,
            "total_clicks": total_clk,
            "avg_position": round(float(r.avg_position), 1) if r.avg_position else None,
            "avg_ctr": avg_ctr,
        })

    return {"clusters": clusters, "unclustered_count": unclustered_count}


class ClusterRenameBody(BaseModel):
    new_name: str


@router.patch("/sites/{site_id}/queries/clusters/{cluster_name}")
async def rename_cluster(
    site_id: uuid.UUID,
    cluster_name: str,
    body: ClusterRenameBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Rename or merge a cluster. All queries with old name get the new name."""
    new_name = body.new_name.strip().lower().replace(" ", "_")
    if not new_name:
        raise HTTPException(status_code=400, detail="new_name is required")

    result = await db.execute(
        update(SearchQuery)
        .where(SearchQuery.site_id == site_id, SearchQuery.cluster == cluster_name)
        .values(cluster=new_name)
    )
    count = result.rowcount
    if count == 0:
        raise HTTPException(status_code=404, detail="Cluster not found")

    return {"old_name": cluster_name, "new_name": new_name, "queries_updated": count}
