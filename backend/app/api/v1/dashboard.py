"""
Dashboard API — aggregated data for the frontend.
"""

import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.daily_metric import DailyMetric
from app.models.issue import Issue
from app.models.agent_run import AgentRun
from app.models.alert import Alert
from app.agents.seasonality_engine import SeasonalityEngine

router = APIRouter()
_season = SeasonalityEngine()


@router.get("/sites/{site_id}/dashboard")
async def get_dashboard(site_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Aggregated overview for the main dashboard page."""
    today = date.today()
    week_start = today - timedelta(days=6)
    prev_week_start = today - timedelta(days=13)
    prev_week_end = today - timedelta(days=7)

    # Current week traffic
    curr = await db.execute(
        select(
            func.sum(DailyMetric.impressions).label("impressions"),
            func.sum(DailyMetric.clicks).label("clicks"),
            func.avg(DailyMetric.avg_position).label("avg_position"),
        ).where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.date.between(week_start, today),
        )
    )
    curr_row = curr.fetchone()

    # Previous week for comparison
    prev = await db.execute(
        select(
            func.sum(DailyMetric.impressions).label("impressions"),
            func.sum(DailyMetric.clicks).label("clicks"),
        ).where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.date.between(prev_week_start, prev_week_end),
        )
    )
    prev_row = prev.fetchone()

    # Issues counts
    issues_counts = await db.execute(
        select(Issue.status, func.count().label("n"))
        .where(Issue.site_id == site_id)
        .group_by(Issue.status)
    )
    issue_stats = {r.status: r.n for r in issues_counts}

    # Latest indexing
    latest_idx = await db.execute(
        select(DailyMetric.pages_indexed, DailyMetric.date)
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "indexing",
            DailyMetric.pages_indexed.isnot(None),
        )
        .order_by(DailyMetric.date.desc())
        .limit(1)
    )
    idx_row = latest_idx.fetchone()

    # Today's alerts
    alerts_today = await db.execute(
        select(func.count()).where(
            Alert.site_id == site_id,
            func.date(Alert.created_at) == today,
        )
    )

    # Agent last run
    last_run = await db.execute(
        select(AgentRun.agent_name, AgentRun.status, AgentRun.completed_at, AgentRun.cost_usd)
        .where(AgentRun.site_id == site_id, AgentRun.status == "completed")
        .order_by(AgentRun.completed_at.desc())
        .limit(1)
    )
    last_run_row = last_run.fetchone()

    # Season info
    season = _season.to_context_dict(today)

    def safe_int(v): return int(v) if v is not None else 0
    def safe_float(v): return round(float(v), 2) if v is not None else 0.0
    def pct_change(curr_val, prev_val):
        if not prev_val: return None
        return round((curr_val - prev_val) / prev_val * 100, 1)

    curr_imp = safe_int(curr_row.impressions if curr_row else 0)
    curr_clk = safe_int(curr_row.clicks if curr_row else 0)
    prev_imp = safe_int(prev_row.impressions if prev_row else 0)
    prev_clk = safe_int(prev_row.clicks if prev_row else 0)

    return {
        "kpis": {
            "impressions": curr_imp,
            "impressions_change_pct": pct_change(curr_imp, prev_imp),
            "clicks": curr_clk,
            "clicks_change_pct": pct_change(curr_clk, prev_clk),
            "avg_position": safe_float(curr_row.avg_position if curr_row else None),
            "pages_indexed": safe_int(idx_row.pages_indexed if idx_row else None),
            "indexing_date": idx_row.date.isoformat() if idx_row else None,
        },
        "issues": {
            "open": issue_stats.get("open", 0),
            "review": issue_stats.get("review", 0),
            "suppressed": issue_stats.get("suppressed", 0),
            "resolved": issue_stats.get("resolved", 0) + issue_stats.get("false_positive", 0),
        },
        "alerts_today": alerts_today.scalar() or 0,
        "last_run": {
            "agent": last_run_row.agent_name if last_run_row else None,
            "completed_at": last_run_row.completed_at.isoformat() if last_run_row and last_run_row.completed_at else None,
        },
        "season": season,
    }


@router.get("/sites/{site_id}/metrics/traffic")
async def get_traffic_metrics(
    site_id: uuid.UUID,
    days: int = Query(default=30, ge=7, le=90),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Time series traffic data for charts."""
    start = date.today() - timedelta(days=days)

    rows = await db.execute(
        select(
            DailyMetric.date,
            func.sum(DailyMetric.impressions).label("impressions"),
            func.sum(DailyMetric.clicks).label("clicks"),
            func.avg(DailyMetric.avg_position).label("avg_position"),
        ).where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.date >= start,
        )
        .group_by(DailyMetric.date)
        .order_by(DailyMetric.date)
    )

    data = [
        {
            "date": r.date.isoformat(),
            "impressions": int(r.impressions or 0),
            "clicks": int(r.clicks or 0),
            "avg_position": round(float(r.avg_position), 1) if r.avg_position else None,
        }
        for r in rows
    ]
    return {"data": data, "days": days}


@router.get("/sites/{site_id}/metrics/indexing")
async def get_indexing_metrics(
    site_id: uuid.UUID,
    days: int = Query(default=30, ge=7, le=90),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Time series indexing data."""
    start = date.today() - timedelta(days=days)

    rows = await db.execute(
        select(DailyMetric.date, DailyMetric.pages_indexed, DailyMetric.extra)
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "indexing",
            DailyMetric.date >= start,
        )
        .order_by(DailyMetric.date)
    )

    data = [
        {
            "date": r.date.isoformat(),
            "pages_indexed": r.pages_indexed or 0,
            "http_4xx": (r.extra or {}).get("http_4xx", 0),
            "http_5xx": (r.extra or {}).get("http_5xx", 0),
        }
        for r in rows
    ]
    return {"data": data, "days": days}


@router.get("/sites/{site_id}/issues")
async def list_issues(
    site_id: uuid.UUID,
    status: str | None = None,
    severity: str | None = None,
    agent_name: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Paginated issues list with filters."""
    filters = [Issue.site_id == site_id]
    if status:
        filters.append(Issue.status == status)
    if severity:
        filters.append(Issue.severity == severity)
    if agent_name:
        filters.append(Issue.agent_name == agent_name)

    total_q = await db.execute(
        select(func.count()).where(and_(*filters))
    )
    total = total_q.scalar() or 0

    rows = await db.execute(
        select(Issue)
        .where(and_(*filters))
        .order_by(
            Issue.status.asc(),
            Issue.created_at.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    issues = rows.scalars().all()

    return {
        "total": total,
        "items": [
            {
                "id": str(i.id),
                "agent_name": i.agent_name,
                "issue_type": i.issue_type,
                "severity": i.severity,
                "confidence": float(i.confidence),
                "title": i.title,
                "description": i.description,
                "recommendation": i.recommendation,
                "status": i.status,
                "evidence": i.evidence,
                "created_at": i.created_at.isoformat() if i.created_at else None,
                "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
            }
            for i in issues
        ],
    }


@router.patch("/sites/{site_id}/issues/{issue_id}")
async def update_issue(
    site_id: uuid.UUID,
    issue_id: uuid.UUID,
    body: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update issue status (acknowledge, resolve, false_positive)."""
    from fastapi import HTTPException
    from datetime import datetime, timezone

    result = await db.execute(
        select(Issue).where(Issue.id == issue_id, Issue.site_id == site_id)
    )
    issue = result.scalar_one_or_none()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    allowed_status = {"open", "acknowledged", "in_progress", "resolved", "false_positive", "suppressed"}
    new_status = body.get("status")
    if new_status and new_status in allowed_status:
        issue.status = new_status
        if new_status in ("resolved", "false_positive"):
            issue.resolved_at = datetime.now(timezone.utc)
        if body.get("resolution_note"):
            issue.resolution_note = body["resolution_note"]

    await db.flush()  # get_db() dependency handles commit
    return {"id": str(issue.id), "status": issue.status}


@router.get("/sites/{site_id}/agent-runs")
async def list_agent_runs(
    site_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Agent execution history with costs."""
    rows = await db.execute(
        select(AgentRun)
        .where(AgentRun.site_id == site_id)
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
    )
    runs = rows.scalars().all()

    # Aggregate total cost
    total_cost = await db.execute(
        select(func.sum(AgentRun.cost_usd)).where(AgentRun.site_id == site_id)
    )

    return {
        "total_cost_usd": round(float(total_cost.scalar() or 0), 6),
        "items": [
            {
                "id": str(r.id),
                "agent_name": r.agent_name,
                "model_used": r.model_used,
                "trigger": r.trigger,
                "status": r.status,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cost_usd": float(r.cost_usd or 0),
                "duration_ms": r.duration_ms,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "output_summary": r.output_summary,
                "error_message": r.error_message,
            }
            for r in runs
        ],
    }
