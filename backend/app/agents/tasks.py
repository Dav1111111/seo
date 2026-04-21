"""
Celery tasks for agent execution.
"""

import asyncio
import logging
from uuid import UUID

from sqlalchemy import select

from app.workers.celery_app import celery_app
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


def _make_session():
    """Create an isolated async session for Celery tasks."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app.config import settings
    eng = create_async_engine(settings.DATABASE_URL, pool_size=2, max_overflow=0)
    return async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


async def _run_agent_for_site(agent_name: str, site_id: UUID, trigger: str) -> dict:
    from app.agents.search_visibility import SearchVisibilityAgent
    from app.agents.technical_indexing import TechnicalIndexingAgent
    from app.agents.query_recommendations import TacticalQueryAgent, StrategicQueryAgent

    AGENTS = {
        "search_visibility": SearchVisibilityAgent,
        "technical_indexing": TechnicalIndexingAgent,
        "query_tactical": TacticalQueryAgent,
        "query_strategic": StrategicQueryAgent,
    }

    cls = AGENTS.get(agent_name)
    if not cls:
        return {"error": f"Unknown agent: {agent_name}"}

    session_factory = _make_session()
    async with session_factory() as db:
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
    """Nightly-eligible sites: active AND onboarding complete.

    Sites mid-wizard are intentionally skipped — see
    app.core_audit.onboarding.gate for rationale.
    """
    from app.core_audit.onboarding.gate import onboarded_site_ids
    session_factory = _make_session()
    async with session_factory() as db:
        return await onboarded_site_ids(db)


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
    session_factory = _make_session()
    async with session_factory() as db:
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


# ── Query Clustering ──────────────────────────────────────────────────────

async def _cluster_queries_for_site(site_id: UUID, force: bool = False) -> dict:
    from app.agents.query_clustering import QueryClusteringAgent
    agent = QueryClusteringAgent()
    session_factory = _make_session()
    async with session_factory() as db:
        result = await agent.run(db, site_id, force_recluster=force)
        await db.commit()
        return result


@celery_app.task(name="run_query_clustering_all", bind=True, max_retries=1)
def run_query_clustering_all(self):
    """Cluster queries for all active sites (weekly scheduled)."""
    site_ids = _run(_get_active_site_ids())
    results = {}
    for sid in site_ids:
        try:
            result = _run(_cluster_queries_for_site(sid, force=True))
            results[str(sid)] = result
        except Exception as exc:
            logger.error("Clustering failed for %s: %s", sid, exc)
            results[str(sid)] = {"error": str(exc)}
    return results


@celery_app.task(name="run_query_clustering_site")
def run_query_clustering_site(site_id: str, force: bool = False):
    """Cluster queries for a specific site (manual trigger)."""
    return _run(_cluster_queries_for_site(UUID(site_id), force=force))


# ── Query Recommendations ────────────────────────────────────────────────

@celery_app.task(name="run_query_tactical_all", bind=True, max_retries=1)
def run_query_tactical_all(self):
    """Run tactical query recommendations for all active sites (daily)."""
    site_ids = _run(_get_active_site_ids())
    results = {}
    for site_id in site_ids:
        try:
            result = _run(_run_agent_for_site("query_tactical", site_id, "scheduled"))
            results[str(site_id)] = result
        except Exception as exc:
            logger.error("query_tactical failed for %s: %s", site_id, exc)
            results[str(site_id)] = {"error": str(exc)}
    return results


# ── Task Generator ─────────────────────────────────────────────────

async def _generate_tasks_for_site(site_id: UUID, trigger: str = "manual") -> dict:
    from app.agents.task_generator import TaskGeneratorAgent
    agent = TaskGeneratorAgent()
    session_factory = _make_session()
    async with session_factory() as db:
        return await agent.run(db, site_id, trigger=trigger)


@celery_app.task(name="generate_seo_tasks")
def generate_seo_tasks(site_id: str):
    """Generate concrete SEO tasks with ready-to-use content for a site."""
    return _run(_generate_tasks_for_site(UUID(site_id), "manual"))


@celery_app.task(name="run_query_strategic_all", bind=True, max_retries=1)
def run_query_strategic_all(self):
    """Run strategic query recommendations for all active sites (weekly)."""
    site_ids = _run(_get_active_site_ids())
    results = {}
    for site_id in site_ids:
        try:
            result = _run(_run_agent_for_site("query_strategic", site_id, "scheduled"))
            results[str(site_id)] = result
        except Exception as exc:
            logger.error("query_strategic failed for %s: %s", site_id, exc)
            results[str(site_id)] = {"error": str(exc)}
    return results
