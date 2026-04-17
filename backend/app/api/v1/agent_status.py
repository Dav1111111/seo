"""
Agent Status API — shows health and activity of the SEO agent pipeline.
Answers the question: "Is the agent working?"
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select, func, desc, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent_run import AgentRun
from app.models.daily_metric import DailyMetric
from app.models.issue import Issue
from app.models.page import Page
from app.models.search_query import SearchQuery
from app.models.task import Task

router = APIRouter()


@router.get("/sites/{site_id}/agent-status")
async def get_agent_status(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Full status of the SEO agent for a site — data collection, crawling, AI runs, tasks, impact."""

    now = datetime.now(timezone.utc)
    today = date.today()

    # 1. Last data collection times (from DailyMetric)
    last_webmaster = await db.execute(
        select(func.max(DailyMetric.created_at))
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
        )
    )
    last_webmaster_at = last_webmaster.scalar()

    last_metrica = await db.execute(
        select(func.max(DailyMetric.created_at))
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "site_traffic",
        )
    )
    last_metrica_at = last_metrica.scalar()

    # 2. Data coverage — how much data do we have?
    queries_count = await db.execute(
        select(func.count(SearchQuery.id)).where(SearchQuery.site_id == site_id)
    )

    clustered_count = await db.execute(
        select(func.count(SearchQuery.id))
        .where(SearchQuery.site_id == site_id, SearchQuery.cluster.isnot(None))
    )

    pages_count = await db.execute(
        select(func.count(Page.id)).where(Page.site_id == site_id)
    )

    pages_with_content = await db.execute(
        select(func.count(Page.id))
        .where(Page.site_id == site_id, Page.word_count.isnot(None), Page.word_count > 0)
    )

    last_crawl = await db.execute(
        select(func.max(Page.last_crawled_at)).where(Page.site_id == site_id)
    )
    last_crawl_at = last_crawl.scalar()

    # 3. Agent runs last 7 days
    week_ago = now - timedelta(days=7)
    runs_rows = await db.execute(
        select(
            AgentRun.agent_name,
            func.count().label("runs"),
            func.sum(AgentRun.cost_usd).label("total_cost"),
            func.max(AgentRun.completed_at).label("last_run"),
            func.sum(
                case((AgentRun.status == "completed", 1), else_=0)
            ).label("successful"),
        )
        .where(
            AgentRun.site_id == site_id,
            AgentRun.started_at >= week_ago,
        )
        .group_by(AgentRun.agent_name)
        .order_by(desc(func.max(AgentRun.completed_at)))
    )
    agent_runs = [
        {
            "agent_name": r.agent_name,
            "runs_last_7d": r.runs,
            "successful": r.successful,
            "total_cost_usd": round(float(r.total_cost or 0), 6),
            "last_run": r.last_run.isoformat() if r.last_run else None,
        }
        for r in runs_rows
    ]

    # 4. Tasks summary
    tasks_by_status = await db.execute(
        select(Task.status, func.count()).where(Task.site_id == site_id).group_by(Task.status)
    )
    task_statuses = {row[0]: row[1] for row in tasks_by_status}

    # 5. Issues summary
    issues_by_status = await db.execute(
        select(Issue.status, func.count()).where(Issue.site_id == site_id).group_by(Issue.status)
    )
    issue_statuses = {row[0]: row[1] for row in issues_by_status}

    # 6. Impact: tasks that were completed, and their effect
    tasks_measuring = await db.execute(
        select(func.count(Task.id))
        .where(Task.site_id == site_id, Task.status == "measuring")
    )

    tasks_with_effect = await db.execute(
        select(func.count(Task.id))
        .where(Task.site_id == site_id, Task.effect_tracked == True)  # noqa: E712
    )

    # 7. Overall health checks
    def hours_since(ts):
        if not ts:
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return round((now - ts).total_seconds() / 3600, 1)

    health = {
        "data_collection": {
            "status": "ok" if last_webmaster_at else "no_data",
            "hours_since_webmaster": hours_since(last_webmaster_at),
            "hours_since_metrica": hours_since(last_metrica_at),
        },
        "site_crawled": {
            "status": "ok" if last_crawl_at else "not_crawled",
            "hours_since_crawl": hours_since(last_crawl_at),
            "pages": pages_count.scalar() or 0,
        },
        "ai_active": {
            "status": "ok" if agent_runs else "idle",
            "runs_last_7d": sum(r["runs_last_7d"] for r in agent_runs),
            "total_cost_usd": round(sum(r["total_cost_usd"] for r in agent_runs), 4),
        },
        "has_tasks": {
            "status": "ok" if sum(task_statuses.values()) > 0 else "no_tasks",
            "total": sum(task_statuses.values()),
            "in_progress": task_statuses.get("in_progress", 0),
            "done": task_statuses.get("done", 0),
            "measuring": task_statuses.get("measuring", 0),
            "completed": task_statuses.get("completed", 0),
        },
    }

    # 8. Activity timeline — recent events (last 10)
    recent_runs = await db.execute(
        select(
            AgentRun.agent_name, AgentRun.status,
            AgentRun.started_at, AgentRun.completed_at,
            AgentRun.cost_usd, AgentRun.output_summary,
        )
        .where(AgentRun.site_id == site_id)
        .order_by(desc(AgentRun.started_at))
        .limit(10)
    )
    timeline = [
        {
            "agent_name": r.agent_name,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "cost_usd": float(r.cost_usd or 0),
            "summary": r.output_summary,
        }
        for r in recent_runs
    ]

    return {
        "health": health,
        "data_coverage": {
            "queries_total": queries_count.scalar() or 0,
            "queries_clustered": clustered_count.scalar() or 0,
            "pages_total": pages_count.scalar() or 0,
            "pages_with_content": pages_with_content.scalar() or 0,
        },
        "agent_runs": agent_runs,
        "tasks_by_status": task_statuses,
        "issues_by_status": issue_statuses,
        "tasks_measuring": tasks_measuring.scalar() or 0,
        "tasks_with_effect": tasks_with_effect.scalar() or 0,
        "timeline": timeline,
    }
