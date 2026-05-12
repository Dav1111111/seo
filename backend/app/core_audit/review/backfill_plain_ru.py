"""One-shot backfill: fill `plain_ru` for existing recommendations.

When the `plain_ru` column was added, the two pilot sites already had
~93 historical recommendations without it. The review LLM emits
`plain_ru` for NEW recs from the migration date forward, but old rows
need a one-time sweep — that's what this module is for.

Usage (inside the backend container — `task_session` style isn't
needed because the script owns its own loop)::

    docker compose exec backend python -m app.core_audit.review.backfill_plain_ru
    docker compose exec backend python -m app.core_audit.review.backfill_plain_ru <site-uuid>

The function itself takes an `AsyncSession` so it can be invoked from
a Celery task, a one-off admin endpoint, or the __main__ CLI block at
the bottom. We do NOT touch recommendations that already have a
non-empty `plain_ru` — re-runs are safe and free.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.review.explain import translate_to_plain_ru
from app.core_audit.review.models import PageReviewRecommendation


logger = logging.getLogger(__name__)


# Small pause between calls so a few hundred recs in a row don't push
# us into OpenAI's per-minute rate ceiling. 0.1s × 93 ≈ 10s of pure
# sleep — negligible vs the LLM latency itself.
_RATE_SLEEP_SECS = 0.1


async def backfill_recommendations(
    db: AsyncSession,
    site_id: uuid.UUID | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Backfill `plain_ru` for recs where it's NULL or empty.

    Parameters
    ----------
    db:
        Async session. The caller is responsible for commit semantics
        outside the function — we ``commit`` after each successful rec
        because the LLM cost is already incurred; losing a batch on
        a crash would mean re-paying.
    site_id:
        Optional site filter. Useful for piloting the backfill on a
        single site before unleashing it on all of them.
    limit:
        Max recs touched per invocation. Defaults to 200, comfortably
        more than the ~93 historical rows on the pilot sites but small
        enough that a runaway loop can't silently spend $$$.

    Returns
    -------
    dict with keys: ``processed`` (int), ``total_cost_usd`` (float),
    ``errors`` (list of ``{rec_id, error}`` dicts). The endpoint and
    the CLI both print this dict.
    """
    # Read as plain tuples — NOT as ORM rows. With ORM rows + asyncpg
    # the framework lazy-touches the row on first attribute access
    # after the loop yielded once (any `await` resets the row state),
    # which raises MissingGreenlet on subsequent iterations. Plain
    # tuples are detached values — safe to pass to worker threads.
    stmt = select(
        PageReviewRecommendation.id,
        PageReviewRecommendation.category,
        PageReviewRecommendation.reasoning_ru,
        PageReviewRecommendation.before_text,
        PageReviewRecommendation.after_text,
    ).where(
        or_(
            PageReviewRecommendation.plain_ru.is_(None),
            PageReviewRecommendation.plain_ru == "",
        )
    )
    if site_id is not None:
        stmt = stmt.where(PageReviewRecommendation.site_id == site_id)
    stmt = stmt.limit(limit)

    rows = (await db.execute(stmt)).all()  # list[Row(id, cat, …)]

    processed = 0
    total_cost = 0.0
    errors: list[dict[str, Any]] = []

    for rec_id, category, reasoning_ru, before_text, after_text in rows:
        try:
            payload = {
                "category": category,
                "reasoning_ru": reasoning_ru,
                "before_text": before_text,
                "after_text": after_text,
            }
            # `translate_to_plain_ru` is sync (the underlying
            # Anthropic / OpenAI SDK calls are blocking). Run it in a
            # worker thread so we don't park the event loop.
            plain_ru, usage = await asyncio.to_thread(
                translate_to_plain_ru, payload,
            )
            if not plain_ru:
                # Don't poison the column with "" — leave NULL so the
                # next run can try again.
                errors.append({
                    "rec_id": str(rec_id),
                    "error": "empty plain_ru returned by LLM",
                })
                continue
            # Targeted UPDATE — no ORM row to expire, no lazy-load
            # surprises. Idempotent: re-running on the same id is OK.
            await db.execute(
                update(PageReviewRecommendation)
                .where(PageReviewRecommendation.id == rec_id)
                .values(plain_ru=plain_ru)
            )
            await db.commit()
            processed += 1
            total_cost += float(usage.get("cost_usd") or 0.0)
            logger.info(
                "backfill: rec=%s cost=$%.5f model=%s",
                rec_id,
                float(usage.get("cost_usd") or 0.0),
                usage.get("model"),
            )
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.warning("backfill failed for rec=%s: %s", rec_id, exc)
            errors.append({"rec_id": str(rec_id), "error": str(exc)})

        await asyncio.sleep(_RATE_SLEEP_SECS)

    return {
        "processed": processed,
        "total_cost_usd": round(total_cost, 6),
        "errors": errors,
    }


async def _cli_main(site_id: uuid.UUID | None) -> None:
    """CLI entry — opens its own engine + session, prints the summary.

    Kept separate from ``backfill_recommendations`` so library callers
    don't need to know about engine lifecycle.
    """
    # Local imports keep the module importable even when DATABASE_URL
    # is missing (e.g. during pytest collection of the explain test).
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as db:
            result = await backfill_recommendations(db, site_id=site_id)
        # Plain print — this is a one-shot operator tool, not a logger
        # sink, so stdout is the right channel.
        print(  # noqa: T201
            f"processed={result['processed']} "
            f"total_cost_usd=${result['total_cost_usd']:.4f} "
            f"errors={len(result['errors'])}"
        )
        for err in result["errors"]:
            print(f"  ! {err['rec_id']}: {err['error']}")  # noqa: T201
    finally:
        await engine.dispose()


def _parse_argv(argv: list[str]) -> uuid.UUID | None:
    if len(argv) <= 1:
        return None
    raw = argv[1].strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise SystemExit(f"invalid site UUID: {raw} ({exc})") from exc


if __name__ == "__main__":
    site = _parse_argv(sys.argv)
    asyncio.run(_cli_main(site))


__all__ = ["backfill_recommendations"]
