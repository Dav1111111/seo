"""
Celery tasks for agent execution.
"""

import asyncio
import logging
from uuid import UUID

from sqlalchemy import select

from app.workers.celery_app import celery_app
from app.database import async_session
from app.models.site import Site

logger = logging.getLogger(__name__)


def _run(coro):
    """Run async coroutine from sync Celery task."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _run_agent_for_site(agent_name: str, site_id: UUID, trigger: str) -> dict:
    from app.agents.search_visibility import SearchVisibilityAgent
    from app.agents.technical_indexing import TechnicalIndexingAgent

    AGENTS = {
        "search_visibility": SearchVisibilityAgent,
        "technical_indexing": TechnicalIndexingAgent,
    }

    cls = AGENTS.get(agent_name)
    if not cls:
        return {"error": f"Unknown agent: {agent_name}"}

    async with async_session() as db:
        agent = cls()
        result = await agent.run(db, site_id, trigger=trigger)
        await db.commit()
        return {
            "agent": agent_name,
            "issues_found": result.issues_found,
            "issues_saved": result.issues_saved,
            "summary": result.summary,
            "cost_usd": result.cost_usd,
            "error": result.error,
        }


async def _get_active_site_ids() -> list[UUID]:
    async with async_session() as db:
        rows = await db.execute(select(Site.id).where(Site.is_active == True))  # noqa: E712
        return [r[0] for r in rows]


@celery_app.task(name="run_search_visibility_all", bind=True, max_retries=1)
def run_search_visibility_all(self):
    """Run SearchVisibilityAgent for all active sites."""
    site_ids = _run(_get_active_site_ids())
    results = {}
    for site_id in site_ids:
        try:
            result = _run(_run_agent_for_site("search_visibility", site_id, "scheduled"))
            results[str(site_id)] = result
        except Exception as exc:
            logger.error("search_visibility failed for %s: %s", site_id, exc)
            results[str(site_id)] = {"error": str(exc)}
    return results


@celery_app.task(name="run_technical_indexing_all", bind=True, max_retries=1)
def run_technical_indexing_all(self):
    """Run TechnicalIndexingAgent for all active sites."""
    site_ids = _run(_get_active_site_ids())
    results = {}
    for site_id in site_ids:
        try:
            result = _run(_run_agent_for_site("technical_indexing", site_id, "scheduled"))
            results[str(site_id)] = result
        except Exception as exc:
            logger.error("technical_indexing failed for %s: %s", site_id, exc)
            results[str(site_id)] = {"error": str(exc)}
    return results


@celery_app.task(name="run_agent_for_site")
def run_agent_for_site(agent_name: str, site_id: str, trigger: str = "manual"):
    """Run a specific agent for a specific site (manual trigger)."""
    return _run(_run_agent_for_site(agent_name, UUID(site_id), trigger))


async def _pipeline_for_site(site_id: str, trigger: str) -> dict:
    from app.services.issue_pipeline import IssuePipeline
    pipeline = IssuePipeline()
    async with async_session() as db:
        return await pipeline.run(db, UUID(site_id), trigger=trigger)


@celery_app.task(name="run_full_pipeline", bind=True, max_retries=0)
def run_full_pipeline(self, site_id: str, trigger: str = "scheduled"):
    """Run full issue pipeline: detect → validate → store."""
    return _run(_pipeline_for_site(site_id, trigger))


@celery_app.task(name="run_daily_pipeline_all", bind=True, max_retries=1)
def run_daily_pipeline_all(self):
    """Run full pipeline for all active sites (daily scheduled)."""
    site_ids = _run(_get_active_site_ids())
    results = {}
    for sid in site_ids:
        try:
            result = _run(_pipeline_for_site(str(sid), "scheduled"))
            results[str(sid)] = result
        except Exception as exc:
            logger.error("Pipeline failed for %s: %s", sid, exc)
            results[str(sid)] = {"error": str(exc)}
    return results
