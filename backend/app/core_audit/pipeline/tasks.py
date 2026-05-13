"""Extended full-pipeline: chord callback that runs BusinessTruth and
optionally fires competitor discovery after the primary collection
stages (crawl / webmaster / demand_map) finish.

Background
----------
The "Full analysis" button used to fire 3 parallel tasks and end there.
BusinessTruth and competitors ran on separate cadences (manual or
nightly), so an owner clicking the button saw yesterday's
recommendations. This module wires the 3 primary tasks into a Celery
chord whose callback extends the pipeline to BusinessTruth and the
gated competitor chain:
competitor_discovery -> competitor_deep_dive -> opportunities.

Gate: competitor discovery is EXPENSIVE (SERP API calls) and only
worth running if we have enough real money-queries to drive it.
`money_queries` = observed Webmaster queries that pass the business-
token filter. Below MIN_MONEY_QUERIES we skip competitor stages and
opportunities, then run the intent decisioner before review so the
page-review layer has fresh CoverageDecision rows to work from.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import desc, func, select

from app.core_audit.activity import emit_terminal, log_event
from app.models.daily_metric import DailyMetric
from app.models.search_query import SearchQuery
from app.models.site import Site
from app.workers.celery_app import celery_app
from app.workers.db_session import task_session


log = logging.getLogger(__name__)

# Threshold: fewer than this many money-queries → skip SERP discovery.
# At 5 queries or less the SERP sample is dominated by aggregators
# (sputnik8, tripster) rather than real niche competitors, and the
# opportunities built on top would mislead the owner.
MIN_MONEY_QUERIES = 5


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _count_money_queries(db, site_id: UUID) -> int:
    """Count observed Webmaster queries that pass the business-token filter.

    Mirrors the logic in competitors.tasks._pick_top_queries but returns
    only the count — the chord callback doesn't need the query list, just
    the gate decision.
    """
    from app.core_audit.competitors.tasks import (
        _business_tokens,
        _query_is_relevant,
    )

    site = await db.get(Site, site_id)
    if site is None:
        return 0

    biz_tokens = _business_tokens(site.target_config or {})
    if not biz_tokens:
        # No target profile yet — we'd accept everything, not a meaningful
        # signal. Treat as "no money queries" until owner runs onboarding.
        return 0

    since = date.today() - timedelta(days=14)
    stmt = (
        select(SearchQuery.query_text)
        .join(
            DailyMetric,
            (DailyMetric.site_id == SearchQuery.site_id)
            & (DailyMetric.dimension_id == SearchQuery.id)
            & (DailyMetric.metric_type == "query_performance")
            & (DailyMetric.date >= since),
        )
        .where(
            SearchQuery.site_id == site_id,
            SearchQuery.is_branded.is_(False),
        )
        .group_by(SearchQuery.id, SearchQuery.query_text)
        .having(func.coalesce(func.sum(DailyMetric.impressions), 0) > 0)
        .order_by(desc(func.coalesce(func.sum(DailyMetric.impressions), 0)))
        .limit(200)  # generous upper cap; real sites won't exceed this
    )
    try:
        rows = (await db.execute(stmt)).all()
    except Exception as exc:  # noqa: BLE001
        log.warning("pipeline.money_query_count_failed err=%s", exc)
        return 0

    return sum(1 for (q,) in rows if q and _query_is_relevant(q, biz_tokens))


async def _skip_competitor_stages(
    db, site_id: str, run_id: str | None, money_q: int,
) -> None:
    """Emit skipped terminal events for gated stages so the pipeline
    wrap-up closes properly."""
    msg = (
        f"Конкуренты пропущены: у сайта {money_q} реальных money-запросов "
        f"в Вебмастере (нужно от {MIN_MONEY_QUERIES}). Разбор SERP без них "
        "возвращает аггрегаторов, а не ваших конкурентов."
    )
    extra = {
        "money_queries": money_q,
        "threshold": MIN_MONEY_QUERIES,
    }
    await emit_terminal(
        db, site_id, "competitor_discovery", "skipped", msg,
        extra=extra, run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "competitor_deep_dive", "skipped",
        "Глубокий анализ пропущен — нет свежей разведки.",
        extra=extra, run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "opportunities", "skipped",
        "Точки роста пропущены — нет свежей разведки конкурентов.",
        extra=extra, run_id=run_id,
    )


def _primary_stage_failures(results) -> list[dict]:
    """Return failed primary-stage results from a Celery chord header.

    Header tasks must return failure payloads instead of raising, or the
    chord callback never runs and the UI keeps an open pipeline forever.
    """
    failures: list[dict] = []
    if not isinstance(results, list):
        return failures

    for item in results:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").lower()
        if status == "failed" or item.get("error"):
            failures.append({
                "stage": item.get("stage") or "primary",
                "error": item.get("error") or item.get("reason") or status,
            })
    return failures


async def _skip_after_primary_failure(
    db, site_id: str, run_id: str | None, failures: list[dict],
) -> None:
    """Terminal-fill downstream stages when a primary stage failed.

    Full pipeline declares every expected stage up front. If crawl,
    Webmaster, or demand_map failed, we should not run downstream AI/SERP
    work, but every queued downstream stage still needs a terminal row so
    the wrapper closes cleanly as `pipeline:failed`.
    """
    failed_names = ", ".join(str(f.get("stage") or "primary") for f in failures)
    extra = {"primary_failures": failures}

    await emit_terminal(
        db, site_id, "business_truth", "skipped",
        f"Понимание бизнеса пропущено: сначала упал этап {failed_names}.",
        extra=extra, run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "competitor_discovery", "skipped",
        f"Разведка конкурентов пропущена: сначала упал этап {failed_names}.",
        extra=extra, run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "competitor_deep_dive", "skipped",
        "Глубокий анализ пропущен — нет свежей разведки.",
        extra=extra, run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "opportunities", "skipped",
        "Точки роста пропущены — полный анализ не дошёл до конкурентов.",
        extra=extra, run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "classify_queries", "skipped",
        "Классификация запросов пропущена — базовый сбор завершился с ошибкой.",
        extra=extra, run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "intent_decide", "skipped",
        "Решения покрытия пропущены — базовый сбор завершился с ошибкой.",
        extra=extra, run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "review", "skipped",
        "Проверка страниц пропущена — сначала надо починить базовый сбор.",
        extra=extra, run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "priorities", "skipped",
        "Приоритеты пропущены — нет свежей проверки страниц.",
        extra=extra, run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "report", "skipped",
        "Отчёт пропущен — полный анализ завершился с ошибкой до аналитики.",
        extra=extra, run_id=run_id,
    )


def _queue_review_chain(site_id: str, run_id: str | None) -> bool:
    from app.core_audit.review.tasks import review_site_decisions_task

    try:
        review_site_decisions_task.apply_async(
            args=[site_id],
            kwargs={"run_id": run_id, "chain_report": True},
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("pipeline.review_chain_dispatch_failed site=%s err=%s", site_id, exc)
        return False


def _queue_classify_queries(site_id: str, run_id: str | None) -> bool:
    """Run SearchQuery relevance classification as part of full analysis.

    This fills own/adjacent/disputed/spam/unclassified counts that the
    SEO assistant uses. Intent coverage is a separate stage; without this
    task the assistant sees raw queries but cannot say which ones are ours.
    """
    from app.collectors.tasks import classify_queries_site_task

    try:
        classify_queries_site_task.apply_async(
            args=[site_id],
            kwargs={"run_id": run_id},
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("pipeline.classify_queries_dispatch_failed site=%s err=%s", site_id, exc)
        return False


async def _mark_classify_dispatch_failed(
    db, site_id: str, run_id: str | None,
) -> None:
    await emit_terminal(
        db, site_id, "classify_queries", "failed",
        "Не удалось запустить классификацию запросов.",
        run_id=run_id,
    )


async def _mark_review_chain_dispatch_failed(
    db, site_id: str, run_id: str | None,
) -> None:
    await emit_terminal(
        db, site_id, "review", "failed",
        "Не удалось запустить проверку страниц.",
        run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "priorities", "skipped",
        "Приоритеты пропущены — проверка страниц не запустилась.",
        run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "report", "skipped",
        "Отчёт пропущен — проверка страниц не запустилась.",
        run_id=run_id,
    )


async def _mark_intent_chain_dispatch_failed(
    db, site_id: str, run_id: str | None,
) -> None:
    await emit_terminal(
        db, site_id, "intent_decide", "failed",
        "Не удалось запустить решения покрытия страниц.",
        run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "review", "skipped",
        "Проверка страниц пропущена — решения покрытия не запустились.",
        run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "priorities", "skipped",
        "Приоритеты пропущены — нет свежей проверки страниц.",
        run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "report", "skipped",
        "Отчёт пропущен — нет свежих приоритетов.",
        run_id=run_id,
    )


def queue_intent_review_chain(site_id: str, run_id: str | None) -> bool:
    """Continue full analysis into intent_decide -> review -> priorities -> report."""
    try:
        pipeline_intent_then_review_task.apply_async(
            args=[site_id],
            kwargs={"run_id": run_id},
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("pipeline.intent_review_chain_dispatch_failed site=%s err=%s", site_id, exc)
        return False


@celery_app.task(name="pipeline_intent_then_review", bind=True, max_retries=0)
def pipeline_intent_then_review_task(
    self,
    site_id: str,
    run_id: str | None = None,
    use_llm: bool = True,
) -> dict:
    """Build fresh CoverageDecision rows before the page-review chain.

    Review candidates come from CoverageDecision. Running review directly
    after crawl/Webmaster/demand_map can therefore produce a clean but
    useless "0 checked" result on freshly reset data.
    """

    async def _run_decisioner() -> dict:
        async with task_session() as db:
            await log_event(
                db, site_id, "intent_decide", "started",
                "Классифицирую интенты и строю решения покрытия страниц…",
                run_id=run_id,
            )
            try:
                from app.intent.decisioner import Decisioner
                stats = await Decisioner().run_for_site(
                    db, UUID(site_id), use_llm_fallback=use_llm,
                )
            except Exception as exc:  # noqa: BLE001
                try:
                    await db.rollback()
                except Exception:  # noqa: BLE001
                    pass
                await emit_terminal(
                    db, site_id, "intent_decide", "failed",
                    f"Решения покрытия остановлены: {str(exc)[:200]}",
                    run_id=run_id,
                )
                await emit_terminal(
                    db, site_id, "review", "skipped",
                    "Проверка страниц пропущена — решения покрытия не завершились.",
                    run_id=run_id,
                )
                await emit_terminal(
                    db, site_id, "priorities", "skipped",
                    "Приоритеты пропущены — нет свежей проверки страниц.",
                    run_id=run_id,
                )
                await emit_terminal(
                    db, site_id, "report", "skipped",
                    "Отчёт пропущен — нет свежих приоритетов.",
                    run_id=run_id,
                )
                return {"status": "failed", "site_id": site_id, "error": str(exc)}

            decisions = stats.get("decisions_by_action") or {}
            total_decisions = sum(int(v or 0) for v in decisions.values())
            await emit_terminal(
                db, site_id, "intent_decide", "done",
                f"Решения покрытия готовы: {total_decisions} решений.",
                extra=stats,
                run_id=run_id,
            )
            return {
                "status": "ok",
                "site_id": site_id,
                "decisions": total_decisions,
                "stats": stats,
            }

    result = _run(_run_decisioner())
    if isinstance(result, dict) and result.get("status") == "failed":
        return result

    if not _queue_review_chain(site_id, run_id):
        async def _mark_review_failed() -> None:
            async with task_session() as db:
                await _mark_review_chain_dispatch_failed(db, site_id, run_id)

        _run(_mark_review_failed())
        return {
            "status": "failed",
            "site_id": site_id,
            "action": "review_dispatch_failed",
        }

    return result


@celery_app.task(
    name="pipeline_after_primary", bind=True, max_retries=0,
)
def pipeline_after_primary_task(
    self,
    _collected_results,  # chord passes the header's results list here
    site_id: str,
    run_id: str | None = None,
) -> dict:
    """Chord callback after crawl + webmaster + demand_map finish.

    Always runs BusinessTruth rebuild (cheap, local). Then gates
    competitor discovery on money-query count. Keeping this callback
    small — heavy lifting stays in the specialized tasks it fires.
    """
    failures = _primary_stage_failures(_collected_results)
    if failures:
        async def _skip_failed() -> None:
            async with task_session() as db:
                await _skip_after_primary_failure(db, site_id, run_id, failures)

        _run(_skip_failed())
        return {
            "status": "failed",
            "site_id": site_id,
            "action": "skipped_downstream_after_primary_failure",
            "primary_failures": failures,
        }

    # Step 4 — BusinessTruth rebuild (fire-and-forget; its own task
    # emits started/done events under stage="business_truth").
    from app.core_audit.business_truth.tasks import (
        business_truth_rebuild_site_task,
    )
    business_truth_rebuild_site_task.delay(site_id, run_id=run_id)

    # Step 4.5 — classify observed queries by relevance. This runs
    # independently from intent_decide: intent_decide maps demand to
    # pages, while classify_queries tells the assistant whether Search
    # Query rows are ours, adjacent, disputed or spam.
    if not _queue_classify_queries(site_id, run_id):
        async def _mark_classify_failed() -> None:
            async with task_session() as db:
                await _mark_classify_dispatch_failed(db, site_id, run_id)

        _run(_mark_classify_failed())

    # Step 5 — gated competitor discovery. Run the gate decision
    # synchronously so we know whether to queue the task or emit
    # skipped terminals.
    async def _decide() -> int:
        async with task_session() as db:
            return await _count_money_queries(db, UUID(site_id))

    money_q = _run(_decide())

    if money_q >= MIN_MONEY_QUERIES:
        from app.core_audit.competitors.tasks import (
            competitors_discover_site_task,
        )
        competitors_discover_site_task.delay(site_id, run_id=run_id)
        return {
            "status": "ok",
            "site_id": site_id,
            "money_queries": money_q,
            "action": "queued_competitor_discovery",
        }

    async def _skip() -> None:
        async with task_session() as db:
            await _skip_competitor_stages(db, site_id, run_id, money_q)

    _run(_skip())
    if not queue_intent_review_chain(site_id, run_id):
        async def _mark_intent_failed() -> None:
            async with task_session() as db:
                await _mark_intent_chain_dispatch_failed(db, site_id, run_id)

        _run(_mark_intent_failed())
    return {
        "status": "ok",
        "site_id": site_id,
        "money_queries": money_q,
        "action": "skipped_competitor_stages",
    }


__all__ = [
    "MIN_MONEY_QUERIES",
    "_primary_stage_failures",
    "pipeline_intent_then_review_task",
    "pipeline_after_primary_task",
    "queue_intent_review_chain",
]
