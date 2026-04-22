"""Celery tasks — Target Demand Map build orchestration (Phase B).

Two tasks:
  * `demand_map_build_site(site_id)` — per-site orchestrator. Runs the
    Phase A Cartesian expansion, optionally enriches via Suggest and
    Haiku, rescores against observed impressions, and persists the
    result with an idempotent delete-then-insert.
  * `demand_map_build_all_weekly()` — beat-scheduled fan-out that
    enqueues a per-site task for every active site.

Both tasks are fail-open at each enrichment stage — a Suggest outage or
LLM error never prevents the Cartesian result from landing. The only
hard-failure mode is DB unavailability, which is retried once.

Feature flag `settings.USE_DEMAND_MAP_ENRICHMENT` gates the Suggest +
LLM stages: set to False and the task runs Phase A only.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy import select

import app.profiles  # noqa: F401 — triggers profile registration
from app.config import settings
from app.core_audit.demand_map.expander import expand_for_site
from app.core_audit.demand_map.persistence import (
    load_observed_queries,
    persist_demand_map,
)
from app.core_audit.demand_map.rescoring import rescore_with_observed_overlap
from app.core_audit.registry import get_profile
from app.models.site import Site
from app.workers.celery_app import celery_app
from app.workers.db_session import task_session

logger = logging.getLogger(__name__)


def _run(coro):
    """Standard Celery sync-wrapper pattern used elsewhere in the codebase."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="demand_map_build_site", bind=True, max_retries=1)
def demand_map_build_site_task(self, site_id: str, run_id: str | None = None) -> dict:
    """Build + persist the demand map for one site.

    Pipeline:
      1. Load the Site row and resolve the vertical profile.
      2. Skip if `target_config` is empty.
      3. Run Phase A Cartesian expansion.
      4. If enrichment flag is on:
         a) Yandex Suggest enrichment (fail-open).
         b) Single Haiku gap-filler call (fail-open).
      5. Rescore with observed-overlap boost.
      6. Persist via idempotent delete-then-insert.

    Returns a summary dict for Flower / alerting.
    """
    async def _inner() -> dict:
        async with task_session() as db:
            site = await db.get(Site, UUID(site_id))
            if site is None:
                return {"status": "skipped", "reason": "site_not_found"}

            target_config: dict = dict(site.target_config or {})
            if not target_config:
                return {
                    "status": "skipped",
                    "reason": "no_target_config",
                    "site_id": site_id,
                }

            profile = get_profile(site.vertical, site.business_model)

            # 1. Cartesian expansion (Phase A — pure, no network).
            clusters = expand_for_site(
                profile, target_config, site_id=UUID(site_id)
            )

            # 2. Enrichment stages (gated by feature flag).
            queries: list = []
            enrichment_stats = {"suggest_queries": 0, "llm_queries": 0}

            if settings.USE_DEMAND_MAP_ENRICHMENT and clusters:
                # Lazy imports — the HTTP/LLM deps are heavier.
                try:
                    from app.core_audit.demand_map.suggest import (
                        enrich_clusters_with_suggest,
                    )
                    suggest_q = enrich_clusters_with_suggest(clusters)
                    queries.extend(suggest_q)
                    enrichment_stats["suggest_queries"] = len(suggest_q)
                except Exception as exc:  # noqa: BLE001 — fail-open
                    logger.warning("demand_map.suggest_failed: %s", exc)

                try:
                    from app.core_audit.demand_map.llm_expansion import (
                        expand_with_llm,
                    )
                    llm_q = expand_with_llm(
                        target_config, clusters, profile
                    )
                    queries.extend(llm_q)
                    enrichment_stats["llm_queries"] = len(llm_q)
                except Exception as exc:  # noqa: BLE001 — fail-open
                    logger.warning("demand_map.llm_failed: %s", exc)

            # 3. Observed-overlap rescoring.
            try:
                observed = await load_observed_queries(db, UUID(site_id))
            except Exception as exc:  # noqa: BLE001 — fail-open
                logger.warning(
                    "demand_map.observed_load_failed: %s", exc
                )
                observed = []
            clusters = rescore_with_observed_overlap(clusters, observed)

            # 4. Persist.
            stats = await persist_demand_map(
                db, UUID(site_id), clusters, queries
            )

            return {
                "status": "ok",
                "site_id": site_id,
                "enrichment_enabled": settings.USE_DEMAND_MAP_ENRICHMENT,
                **stats,
                **enrichment_stats,
            }

    return _run(_inner())


@celery_app.task(name="demand_map_build_all_weekly", bind=True, max_retries=1)
def demand_map_build_all_weekly_task(self) -> dict:
    """Fan-out beat task — enqueues a per-site build for every active site.

    Runs weekly on Mondays 03:30 UTC (ahead of the existing intent
    pipeline so downstream Phase C+ readers can consume fresh data).
    """
    async def _inner() -> dict:
        from app.core_audit.onboarding.gate import onboarded_site_ids
        async with task_session() as db:
            site_ids = await onboarded_site_ids(db)
        for sid in site_ids:
            demand_map_build_site_task.delay(str(sid))
        return {"dispatched": [str(s) for s in site_ids]}

    return _run(_inner())


__all__ = [
    "demand_map_build_site_task",
    "demand_map_build_all_weekly_task",
]
