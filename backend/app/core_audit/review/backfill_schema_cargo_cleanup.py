"""One-shot cleanup of false cargo-cult Schema recommendations.

Background
----------
A bug in the schema reviewer (separately fixed in `review/llm/*`)
hallucinated `before_text` values like ``TouristTrip`` /
``TouristAttraction`` / ``TouristDestination`` / ``TravelAction`` /
``Event`` on pages that didn't actually carry those types. Owners
saw these as fake advice — «у вас на странице TouristTrip, надо
заменить» — which is embarrassing and erodes trust.

This module wipes those false rows. It has two modes:

* **Conservative** (default): DELETE every ``page_review_recommendation``
  whose ``category='schema'`` and ``before_text`` is one of the five
  cargo-cult sentinels. After the bug fix, the next review for each
  affected page will re-create the rec ONLY if the type is genuinely
  present — so a false positive deletion is self-healing.

* **Smart** (``--check-deep-extract``): same set but JOIN through
  ``page_reviews.page_id → page_deep_extracts.schema_blocks`` (latest
  extract per page) and DELETE only when the type is *provably* absent
  from any block's ``@type``. When no deep-extract row exists for the
  page we SKIP — we refuse to delete without evidence.

Both modes work in batches of 100 to avoid long-running transactions,
and report ``total_matched / deleted / skipped / errors``.

Usage::

    docker compose exec -T backend python -m \\
      app.core_audit.review.backfill_schema_cargo_cleanup \\
      [--site-id UUID] [--check-deep-extract] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from typing import Any, Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.models.page_deep_extract import PageDeepExtract


logger = logging.getLogger(__name__)


# The five Schema.org types the broken cargo-cult detector liked to
# hallucinate. Frozen here as a tuple so a stray edit can't widen the
# blast radius without code review.
CARGO_CULT_SCHEMA_TYPES: tuple[str, ...] = (
    "TouristTrip",
    "TouristAttraction",
    "TouristDestination",
    "TravelAction",
    "Event",
)


# Batch size for both SELECT-id and DELETE-by-id. 100 is small enough
# that any single transaction holds row locks for milliseconds, and
# large enough that the cleanup completes for the pilot (~1k bad rows)
# in a handful of round-trips.
_BATCH_SIZE = 100


def _extract_schema_types(schema_blocks: Iterable[Any] | None) -> set[str]:
    """Pull out every ``@type`` value from a list of JSON-LD blocks.

    ``schema_blocks`` is whatever Playwright extraction stored — usually
    a list of dicts, but defensively we handle None, non-list, and
    blocks where ``@type`` is itself a list (the JSON-LD spec allows
    multi-typed nodes). We do NOT recurse into ``@graph`` here; the
    cargo-cult bug only emitted top-level type names, so a top-level
    membership check is the right scope.
    """
    out: set[str] = set()
    if not schema_blocks or not isinstance(schema_blocks, list):
        return out
    for block in schema_blocks:
        if not isinstance(block, dict):
            continue
        t = block.get("@type")
        if isinstance(t, str):
            out.add(t)
        elif isinstance(t, list):
            for v in t:
                if isinstance(v, str):
                    out.add(v)
    return out


async def _latest_deep_extract_types(
    db: AsyncSession, page_id: uuid.UUID,
) -> set[str] | None:
    """Return the @type set from the most recent deep-extract for the page.

    Returns ``None`` when there is no deep-extract row at all — that's
    the signal smart-mode uses to SKIP (we refuse to delete without
    evidence). Returns an empty ``set()`` when the extract exists but
    has no schema_blocks — that's still evidence (we crawled the page
    and saw no schema), so the row will be deleted.
    """
    stmt = (
        select(PageDeepExtract.schema_blocks)
        .where(PageDeepExtract.page_id == page_id)
        .order_by(PageDeepExtract.extracted_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        return None
    return _extract_schema_types(row[0])


async def _fetch_candidate_batch(
    db: AsyncSession,
    site_id: uuid.UUID | None,
    already_seen: set[uuid.UUID],
    limit: int,
) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
    """Fetch one page's worth of cargo-cult rec candidates.

    Returns rows of ``(rec_id, page_id, before_text)``. We page by
    excluding already-seen ids rather than OFFSET so a concurrent
    delete (smart-mode skip writes nothing, but conservative DELETE
    shrinks the result set as it runs) can't make us miss rows.
    """
    stmt = (
        select(
            PageReviewRecommendation.id,
            PageReview.page_id,
            PageReviewRecommendation.before_text,
        )
        .join(PageReview, PageReview.id == PageReviewRecommendation.review_id)
        .where(
            PageReviewRecommendation.category == "schema",
            PageReviewRecommendation.before_text.in_(CARGO_CULT_SCHEMA_TYPES),
        )
    )
    if site_id is not None:
        stmt = stmt.where(PageReviewRecommendation.site_id == site_id)
    if already_seen:
        stmt = stmt.where(PageReviewRecommendation.id.notin_(already_seen))
    stmt = stmt.limit(limit)
    rows = (await db.execute(stmt)).all()
    return [(r[0], r[1], r[2]) for r in rows]


async def cleanup_schema_cargo_cult_recs(
    db: AsyncSession,
    *,
    site_id: uuid.UUID | None = None,
    check_deep_extract: bool = False,
    dry_run: bool = False,
    batch_size: int = _BATCH_SIZE,
) -> dict[str, Any]:
    """Delete false cargo-cult schema recommendations.

    Parameters
    ----------
    db:
        Async session. We ``commit`` after each successful DELETE batch
        so a crash mid-run only loses one batch worth of work.
    site_id:
        Optional UUID — when given, restricts the sweep to a single
        site (useful for piloting on one tenant).
    check_deep_extract:
        Smart mode. When True, every candidate is cross-checked against
        ``page_deep_extracts.schema_blocks``; only candidates whose type
        is *provably absent* from the latest extract are deleted. Pages
        with no deep-extract row are SKIPPED (counted in ``skipped``).
    dry_run:
        Don't execute the DELETE — only count what would happen. The
        returned counts still reflect the would-be deletes.
    batch_size:
        How many rows to delete in a single DELETE statement. Default
        100; only the tests override this.

    Returns
    -------
    dict with keys: ``total_matched``, ``deleted``, ``skipped``,
    ``errors`` (list of ``{rec_id, error}``), ``dry_run``, ``mode``.
    """
    total_matched = 0
    deleted = 0
    skipped = 0
    errors: list[dict[str, Any]] = []

    # Track every candidate id we've inspected so the SELECT pagination
    # is monotonic — important in smart-mode where we don't delete the
    # skipped rows. In conservative dry-run we'd otherwise loop forever
    # on the same batch.
    seen_ids: set[uuid.UUID] = set()
    # Cache the @type set per page_id within the run; smart-mode often
    # has multiple cargo-cult recs on the same page (one per bad type)
    # and re-querying page_deep_extracts each time is wasted I/O.
    page_types_cache: dict[uuid.UUID, set[str] | None] = {}

    # Per-batch buffer of rec_ids we've decided to actually DELETE.
    pending_delete: list[uuid.UUID] = []

    while True:
        candidates = await _fetch_candidate_batch(
            db, site_id, seen_ids, batch_size,
        )
        if not candidates:
            break

        for rec_id, page_id, before_text in candidates:
            seen_ids.add(rec_id)
            total_matched += 1
            try:
                if check_deep_extract:
                    if page_id not in page_types_cache:
                        page_types_cache[page_id] = (
                            await _latest_deep_extract_types(db, page_id)
                        )
                    page_types = page_types_cache[page_id]
                    if page_types is None:
                        # No deep-extract row → no evidence → SKIP.
                        skipped += 1
                        continue
                    if before_text in page_types:
                        # The type really is on the page; this rec is
                        # legitimate (or at least not provably false).
                        skipped += 1
                        continue
                # Either conservative mode, or smart mode with proof.
                pending_delete.append(rec_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "cargo-cleanup: classify failed for rec=%s: %s",
                    rec_id, exc,
                )
                errors.append({"rec_id": str(rec_id), "error": str(exc)})

        # Flush the per-batch delete buffer. We commit per batch so a
        # crash later doesn't undo work we've already announced.
        if pending_delete and not dry_run:
            try:
                await db.execute(
                    delete(PageReviewRecommendation).where(
                        PageReviewRecommendation.id.in_(pending_delete)
                    )
                )
                await db.commit()
                deleted += len(pending_delete)
                logger.info(
                    "cargo-cleanup: deleted batch of %d", len(pending_delete),
                )
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                logger.warning(
                    "cargo-cleanup: delete batch failed (%d rows): %s",
                    len(pending_delete), exc,
                )
                for rid in pending_delete:
                    errors.append({"rec_id": str(rid), "error": str(exc)})
        elif pending_delete and dry_run:
            # Still credit the would-be deletion in the report so
            # operators can size the real run.
            deleted += len(pending_delete)
        pending_delete = []

    return {
        "total_matched": total_matched,
        "deleted": deleted,
        "skipped": skipped,
        "errors": errors,
        "dry_run": dry_run,
        "mode": "smart" if check_deep_extract else "conservative",
    }


async def _cli_main(
    site_id: uuid.UUID | None,
    check_deep_extract: bool,
    dry_run: bool,
) -> None:
    """CLI entry point — uses ``task_session`` so the engine is disposed."""
    # Local import keeps the module importable even when DATABASE_URL
    # is unset (e.g. during pytest collection or `--help`).
    from app.workers.db_session import task_session

    async with task_session() as db:
        result = await cleanup_schema_cargo_cult_recs(
            db,
            site_id=site_id,
            check_deep_extract=check_deep_extract,
            dry_run=dry_run,
        )

    # Plain print — operator-facing one-shot, matching backfill_plain_ru.
    print(  # noqa: T201
        f"mode={result['mode']} dry_run={result['dry_run']} "
        f"total_matched={result['total_matched']} "
        f"deleted={result['deleted']} "
        f"skipped={result['skipped']} "
        f"errors={len(result['errors'])}"
    )
    for err in result["errors"]:
        print(f"  ! {err['rec_id']}: {err['error']}")  # noqa: T201


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="backfill_schema_cargo_cleanup",
        description=(
            "Delete false cargo-cult schema recommendations "
            "(TouristTrip / TouristAttraction / TouristDestination / "
            "TravelAction / Event)."
        ),
    )
    p.add_argument(
        "--site-id",
        type=str,
        default=None,
        help="Restrict cleanup to a single site UUID.",
    )
    p.add_argument(
        "--check-deep-extract",
        action="store_true",
        help=(
            "Smart mode: only delete when the type is provably absent "
            "from page_deep_extracts.schema_blocks. Default is "
            "conservative mode (delete all five cargo-cult types)."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts without executing DELETE.",
    )
    args = p.parse_args(argv[1:])
    if args.site_id is not None:
        try:
            args.site_id = uuid.UUID(args.site_id)
        except ValueError as exc:
            raise SystemExit(f"invalid site UUID: {args.site_id} ({exc})") from exc
    return args


if __name__ == "__main__":
    parsed = _parse_argv(sys.argv)
    asyncio.run(
        _cli_main(
            parsed.site_id, parsed.check_deep_extract, parsed.dry_run,
        )
    )


__all__ = [
    "CARGO_CULT_SCHEMA_TYPES",
    "cleanup_schema_cargo_cult_recs",
]
