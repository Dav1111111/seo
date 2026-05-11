"""Celery tasks for Lateral Query Expansion.

Pattern mirrors `core_audit/demand_map/tasks.py`:

  * `lateral_expand_site_task(site_id)` — per-site orchestrator.
  * `lateral_expand_all_weekly_task()` — beat fan-out for active sites.

The single LLM call is wrapped in agent_runs auditing so cost is
visible in the same admin views as every other LLM-using module.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from uuid import UUID

from app.core_audit.activity import emit_terminal, log_event
from app.core_audit.lateral.context import build_context
from app.core_audit.lateral.llm_expansion import expand_with_llm
from app.core_audit.lateral.persistence import upsert_lateral_candidates
from app.models.agent_run import AgentRun
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


@celery_app.task(name="lateral_expand_site", bind=True, max_retries=1)
def lateral_expand_site_task(
    self, site_id: str, run_id: str | None = None,
) -> dict:
    """Expand lateral queries for one site.

    Pipeline:
      1. Load site; bail if missing or no business_summary signal at all.
      2. Build LLM context (DB-only).
      3. Single Haiku call → list of candidates.
      4. UPSERT with owner-status guard.
      5. Record agent_runs row so cost is visible.

    Fail-open at the LLM step: if Anthropic dies, we mark the run failed
    in agent_runs and emit a terminal `failed` activity event. No retry —
    the weekly cadence will catch up next Monday.
    """

    async def _inner() -> dict:
        t0 = time.monotonic()
        async with task_session() as db:
            site = await db.get(Site, UUID(site_id))
            if site is None:
                await emit_terminal(
                    db, site_id, "lateral", "failed",
                    "Сайт не найден в базе.",
                    run_id=run_id,
                )
                return {"status": "failed", "reason": "site_not_found"}

            await log_event(
                db, site_id, "lateral", "started",
                "Расширяю список запросов: ищу косвенно-релевантные идеи…",
                run_id=run_id,
            )

            ctx = await build_context(db, site)
            if not ctx.services and not ctx.geo and not ctx.business_summary:
                await emit_terminal(
                    db, site_id, "lateral", "skipped",
                    "Нет бизнес-контекста — заверши онбординг.",
                    run_id=run_id,
                )
                return {"status": "skipped", "reason": "no_business_context"}

            # 1. Create an in-progress agent_run row.
            run_record = AgentRun(
                site_id=site.id,
                agent_name="lateral_query_expansion",
                model_used="pending",
                trigger="scheduled" if run_id else "manual",
                status="running",
                started_at=datetime.now(timezone.utc),
                input_summary={
                    "services": ctx.services,
                    "geo": ctx.geo,
                    "competitor_brands": ctx.competitor_brands,
                    "observed_count": len(ctx.top_observed_queries),
                    "existing_lateral_count": len(ctx.existing_lateral_norms),
                },
            )
            db.add(run_record)
            await db.flush()

            try:
                candidates, usage = expand_with_llm(ctx)
            except Exception as exc:  # noqa: BLE001
                run_record.status = "failed"
                run_record.error_message = str(exc)[:2000]
                run_record.completed_at = datetime.now(timezone.utc)
                run_record.duration_ms = int((time.monotonic() - t0) * 1000)
                await db.commit()
                await emit_terminal(
                    db, site_id, "lateral", "failed",
                    f"LLM упал: {str(exc)[:200]}",
                    run_id=run_id,
                )
                logger.warning("lateral.llm_failed site=%s err=%s", site_id, exc)
                return {"status": "failed", "stage": "llm", "error": str(exc)}

            stats = await upsert_lateral_candidates(
                db, site.id, candidates, agent_run_id=run_record.id,
            )

            run_record.status = "completed"
            run_record.model_used = usage.get("model", "")
            run_record.input_tokens = usage.get("input_tokens", 0)
            run_record.output_tokens = usage.get("output_tokens", 0)
            run_record.cost_usd = usage.get("cost_usd", 0.0)
            run_record.prompt_hash = usage.get("prompt_hash")
            run_record.completed_at = datetime.now(timezone.utc)
            run_record.duration_ms = int((time.monotonic() - t0) * 1000)
            run_record.output_summary = {
                "candidates_generated": len(candidates),
                **stats,
            }
            await db.commit()

            await emit_terminal(
                db, site_id, "lateral", "done",
                (
                    f"Lateral: предложено {len(candidates)} идей "
                    f"(новых {stats['inserted']}, обновлено "
                    f"{stats['refreshed']}, защищено владельцем "
                    f"{stats['skipped_owner_locked']})."
                ),
                extra={
                    "candidates_generated": len(candidates),
                    "cost_usd": float(usage.get("cost_usd", 0.0)),
                    **stats,
                },
                run_id=run_id,
            )
            return {
                "status": "ok",
                "site_id": site_id,
                "agent_run_id": str(run_record.id),
                "candidates_generated": len(candidates),
                **stats,
            }

    return _run(_inner())


@celery_app.task(name="lateral_expand_all_weekly", bind=True, max_retries=1)
def lateral_expand_all_weekly_task(self) -> dict:
    """Beat fan-out — every onboarded site, one task each."""

    async def _inner() -> dict:
        from app.core_audit.onboarding.gate import onboarded_site_ids
        async with task_session() as db:
            site_ids = await onboarded_site_ids(db)
        for sid in site_ids:
            lateral_expand_site_task.delay(str(sid))
        return {"dispatched": [str(s) for s in site_ids]}

    return _run(_inner())


__all__ = [
    "lateral_expand_site_task",
    "lateral_expand_all_weekly_task",
]
