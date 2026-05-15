"""Backfill `search_queries.relevance` with funnel-aware classifier.

Re-runs ``classify_wordstat_discovery_phrase`` over every SearchQuery
row for a site and updates `relevance` / `relevance_reason_ru` /
`relevance_set_at` / `relevance_set_by` when the verdict changes.

Hard rules:

1. **Never overwrite a user verdict.** Rows where ``relevance_set_by ==
   "user"`` are skipped entirely. The owner's manual triage is sacred —
   even a "spam" verdict from the user wins over any rules verdict.
2. **Delete URL-shaped rows.** Pre-classifier ingestion sometimes wrote
   URL-shaped queries into `search_queries`. They're noise (no real
   demand) and the funnel classifier already classifies them as spam.
   We delete them outright so the «show only own» filter cleans up too.
3. **Commit every 50 rows.** Large sites have ~5–10k SearchQuery rows.
   Streaming through them in one transaction would hold a long lock on
   the table; batches of 50 keep the worker responsive and partial
   progress visible.
4. **Pipeline-cascade safe (CLAUDE.md rule 1).** Emits
   started/done/failed events with the same `task_session` +
   `emit_terminal` pattern as `collect_site_metrica`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, select, update

from app.workers.celery_app import celery_app
from app.workers.db_session import task_session

logger = logging.getLogger(__name__)


_BACKFILL_COMMIT_BATCH = 50


def _looks_like_url(text: str) -> bool:
    """True when the raw query text is a URL/domain leak.

    Anything with `://` or `www.` is obvious. A bare domain like
    `example.ru` is recognised by a trailing common TLD with no space.
    """
    if not text:
        return False
    cleaned = text.strip().lower()
    if "://" in cleaned or cleaned.startswith(("www.", "http")):
        return True
    if " " not in cleaned and "." in cleaned and any(
        cleaned.endswith(tld)
        for tld in (".ru", ".рф", ".com", ".org", ".net", ".su")
    ):
        return True
    return False


@celery_app.task(name="backfill_funnel_relevance_for_site", bind=True)
def backfill_funnel_relevance_for_site(
    self,
    site_id: str,
    run_id: str | None = None,
):
    """Re-classify every SearchQuery row for the given site.

    Stage name: ``relevance_backfill``. Emits started + terminal events
    so the activity feed shows the run; `run_id` propagates as required
    by CLAUDE.md rule 3.

    Returns a dict with counts so callers (admin endpoint) can show a
    summary without re-reading the activity row.
    """
    import asyncio

    from app.collectors.tasks import classify_wordstat_discovery_phrase
    from app.core_audit.activity import emit_terminal, log_event
    from app.models.search_query import SearchQuery
    from app.models.site import Site

    async def _run() -> dict:
        async with task_session() as db:
            site = (await db.execute(
                select(Site).where(Site.id == UUID(site_id))
            )).scalar_one_or_none()
            if site is None:
                await emit_terminal(
                    db, site_id, "relevance_backfill", "failed",
                    "Сайт не найден в базе.",
                    run_id=run_id,
                )
                return {
                    "status": "failed",
                    "stage": "relevance_backfill",
                    "error": "Site not found",
                }

            cfg = site.target_config or {}

            await log_event(
                db, site_id, "relevance_backfill", "started",
                "Пересчитываю relevance по новой воронке "
                "(direct_product / funnel_warm / funnel_top / "
                "out_of_market / spam)…",
                run_id=run_id,
            )

            # ── Step 1: clean URL-shaped junk ──────────────────────
            # Only delete rules-set or NULL — never user-touched.
            rows = (await db.execute(
                select(SearchQuery.id, SearchQuery.query_text)
                .where(
                    SearchQuery.site_id == site.id,
                    (
                        SearchQuery.relevance_set_by.is_(None)
                        | (SearchQuery.relevance_set_by != "user")
                    ),
                )
            )).all()
            url_row_ids: list[UUID] = [
                row_id for row_id, qt in rows if _looks_like_url(qt or "")
            ]
            if url_row_ids:
                await db.execute(
                    delete(SearchQuery).where(SearchQuery.id.in_(url_row_ids))
                )
                await db.commit()

            # ── Step 2: walk remaining rows ───────────────────────
            all_rows = (await db.execute(
                select(
                    SearchQuery.id,
                    SearchQuery.query_text,
                    SearchQuery.relevance,
                    SearchQuery.relevance_set_by,
                )
                .where(SearchQuery.site_id == site.id)
            )).all()

            stats = {
                "rows_total": len(all_rows),
                "rows_changed": 0,
                "rows_unchanged": 0,
                "rows_skipped_user": 0,
                "url_rows_deleted": len(url_row_ids),
                "by_relevance": {},  # final-state counts
            }

            now = datetime.now(timezone.utc)
            updates_in_batch = 0
            by_rel: dict[str, int] = {}

            try:
                for row_id, query_text, current_rel, set_by in all_rows:
                    if set_by == "user":
                        stats["rows_skipped_user"] += 1
                        # Track the user verdict towards the final
                        # distribution so the summary reflects truth.
                        by_rel[current_rel or "unclassified"] = (
                            by_rel.get(current_rel or "unclassified", 0) + 1
                        )
                        continue

                    _, new_rel, reason_ru = classify_wordstat_discovery_phrase(
                        query_text or "", cfg,
                    )
                    by_rel[new_rel] = by_rel.get(new_rel, 0) + 1

                    if new_rel == current_rel:
                        stats["rows_unchanged"] += 1
                        continue

                    await db.execute(
                        update(SearchQuery)
                        .where(SearchQuery.id == row_id)
                        .values(
                            relevance=new_rel,
                            relevance_reason_ru=reason_ru,
                            relevance_set_at=now,
                            relevance_set_by="rules",
                        )
                    )
                    stats["rows_changed"] += 1
                    updates_in_batch += 1

                    if updates_in_batch >= _BACKFILL_COMMIT_BATCH:
                        await db.commit()
                        updates_in_batch = 0

                if updates_in_batch:
                    await db.commit()
            except Exception as exc:  # noqa: BLE001
                try:
                    await db.rollback()
                except Exception:  # noqa: BLE001
                    pass
                logger.exception(
                    "relevance_backfill.failed site=%s", site_id,
                )
                await emit_terminal(
                    db, site_id, "relevance_backfill", "failed",
                    f"Бэкфилл relevance упал: {str(exc)[:200]}",
                    run_id=run_id,
                )
                return {
                    "status": "failed",
                    "stage": "relevance_backfill",
                    "error": str(exc),
                    **stats,
                }

            stats["by_relevance"] = by_rel

            message = (
                f"Пересчитан relevance: {stats['rows_changed']} строк "
                f"обновлено, {stats['rows_unchanged']} без изменений, "
                f"{stats['rows_skipped_user']} ручных — не трогаем"
            )
            if stats["url_rows_deleted"]:
                message += (
                    f"; удалено {stats['url_rows_deleted']} URL-мусора"
                )

            await emit_terminal(
                db, site_id, "relevance_backfill", "done", message,
                extra=stats, run_id=run_id,
            )
            return {"status": "done", "stage": "relevance_backfill", **stats}

    return asyncio.run(_run())


__all__ = [
    "backfill_funnel_relevance_for_site",
    "_looks_like_url",
]
