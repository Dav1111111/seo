"""Celery tasks for intent classification + coverage."""

import asyncio
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.intent.coverage import CoverageAnalyzer
from app.intent.service import IntentService
from app.models.site import Site
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_session() -> async_sessionmaker[AsyncSession]:
    eng = create_async_engine(settings.DATABASE_URL, pool_size=2, max_overflow=0)
    return async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


async def _classify_and_score(site_id: UUID) -> dict:
    """Full intent pipeline for a site: classify queries + score pages."""
    session_factory = _make_session()
    svc = IntentService()

    async with session_factory() as db:
        query_stats = await svc.classify_site_queries(db, site_id)

    async with session_factory() as db:
        page_stats = await svc.score_site_pages(db, site_id)

    return {
        "site_id": str(site_id),
        "query_classification": query_stats,
        "page_scoring": page_stats,
    }


@celery_app.task(name="intent_classify_site", bind=True, max_retries=1)
def intent_classify_site(self, site_id: str):
    """Classify queries + score pages for a site."""
    return _run(_classify_and_score(UUID(site_id)))


@celery_app.task(name="intent_classify_all", bind=True, max_retries=1)
def intent_classify_all(self):
    """Nightly: intent classification for all active sites."""

    async def _run_all():
        session_factory = _make_session()
        async with session_factory() as db:
            rows = await db.execute(
                select(Site.id).where(Site.is_active == True)  # noqa: E712
            )
            site_ids = [r[0] for r in rows]

        results = {}
        for sid in site_ids:
            try:
                results[str(sid)] = await _classify_and_score(sid)
            except Exception as exc:
                logger.error("intent_classify_all failed for %s: %s", sid, exc)
                results[str(sid)] = {"error": str(exc)}
        return results

    return _run(_run_all())


@celery_app.task(name="intent_analyze_coverage")
def intent_analyze_coverage(site_id: str):
    """Run coverage analysis for a site (read-only, returns reports)."""

    async def _analyze():
        session_factory = _make_session()
        analyzer = CoverageAnalyzer()
        async with session_factory() as db:
            reports = await analyzer.analyze_site(db, UUID(site_id))
            return {
                "site_id": site_id,
                "reports": [
                    {
                        "intent_code": r.intent_code.value,
                        "queries_count": r.queries_count,
                        "total_impressions_14d": r.total_impressions_14d,
                        "total_clicks_14d": r.total_clicks_14d,
                        "avg_position": r.avg_position,
                        "top_queries": r.top_queries,
                        "ambiguous_queries_count": r.ambiguous_queries_count,
                        "best_page_url": r.best_page_url,
                        "best_page_score": r.best_page_score,
                        "pages_strong": r.pages_with_score_gte_4,
                        "pages_weak": r.pages_with_score_2_3,
                        "status": r.status.value,
                    }
                    for r in reports
                ],
            }

    return _run(_analyze())
