"""Celery tasks for Page Review (Module 3).

Each task uses `task_session()` which owns an ephemeral AsyncEngine scoped to
the invocation and disposes it on exit — prevents connection-pool leaks that
would otherwise pile up over worker_max_tasks_per_child cycles.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy import select

from app.core_audit.activity import emit_terminal, log_event
from app.core_audit.review.reviewer import DEFAULT_TOP_N, Reviewer
from app.models.site import Site
from app.workers.celery_app import celery_app
from app.workers.db_session import task_session

logger = logging.getLogger(__name__)


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="review_page", bind=True, max_retries=1)
def review_page_task(self, page_id: str, decision_id: str | None = None):
    async def _inner():
        async with task_session() as db:
            result = await Reviewer().review_page(
                db,
                UUID(page_id),
                UUID(decision_id) if decision_id else None,
            )
            return {
                "page_id": str(result.page_id),
                "status": result.status.value,
                "reviewer_model": result.reviewer_model,
                "cost_usd": result.cost_usd,
                "skip_reason": result.skip_reason.value if result.skip_reason else None,
                "recommendations": len(result.recommendations),
            }

    return _run(_inner())


@celery_app.task(name="review_site_decisions", bind=True, max_retries=1)
def review_site_decisions_task(
    self,
    site_id: str,
    top_n: int = DEFAULT_TOP_N,
    run_id: str | None = None,
):
    """Review top-N strengthen decisions for a single site. Chains
    priority_rescore_site so fresh recs get scored immediately."""

    async def _inner():
        async with task_session() as db:
            await log_event(
                db,
                site_id,
                "review",
                "started",
                f"Проверяю до {top_n} важных страниц и рекомендаций…",
                run_id=run_id,
            )
            try:
                result = await Reviewer().review_site(
                    db,
                    UUID(site_id),
                    top_n=top_n,
                )
            except Exception as exc:  # noqa: BLE001
                await emit_terminal(
                    db,
                    site_id,
                    "review",
                    "failed",
                    f"Проверка страниц остановлена: {str(exc)[:200]}",
                    run_id=run_id,
                )
                raise

            await emit_terminal(
                db,
                site_id,
                "review",
                "done",
                (
                    f"Проверка страниц готова: {result.get('reviewed', 0)} "
                    f"проверено, {result.get('skipped', 0)} пропущено."
                ),
                extra={
                    "reviewed": result.get("reviewed", 0),
                    "skipped": result.get("skipped", 0),
                    "failed": result.get("failed", 0),
                    "cost_total_usd": result.get("cost_total_usd", 0.0),
                    "candidates": result.get("candidates", 0),
                },
                run_id=run_id,
            )
            return result

    result = _run(_inner())
    # Chain rescore so priorities are fresh right after the review batch.
    try:
        from app.core_audit.priority.tasks import priority_rescore_site
        priority_rescore_site.delay(site_id, run_id=run_id)
    except Exception as exc:
        logger.warning("rescore chain dispatch failed site=%s: %s", site_id, exc)
    return result


@celery_app.task(name="review_all_nightly", bind=True, max_retries=1)
def review_all_nightly_task(self, top_n: int = DEFAULT_TOP_N):
    """Nightly: iterate active sites, fire one review_site_decisions per site."""

    async def _inner():
        from app.core_audit.onboarding.gate import onboarded_site_ids_with
        async with task_session() as db:
            rows = await onboarded_site_ids_with(db, Site.vertical)
            active = [(sid, vert) for sid, vert in rows]

        dispatched = []
        for sid, vert in active:
            if vert != "tourism":
                logger.warning("site %s vertical=%s — falls back to tourism profile", sid, vert)
            review_site_decisions_task.delay(str(sid), top_n)
            dispatched.append(str(sid))
        return {"dispatched": dispatched, "top_n": top_n}

    return _run(_inner())
