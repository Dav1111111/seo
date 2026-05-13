"""One-shot cleanup of misclassified `h1_structure` recommendations.

Background
----------
The h1_structure check is meant to police the *shape* of the H1 — a
short headline, single H1 per page, no all-caps, etc. A subset of
historical recs slipped through with ``after_text`` that's actually
paragraph prose (a marketing blurb the reviewer mistook for an "ideal
H1"). Three such rows were flagged in the 2026-05-14 audit; the LLM
emits clean h1 advice going forward, but the old rows need a sweep.

Heuristic
---------
A rec is "misclassified prose" when ``category='h1_structure'`` AND
the ``after_text``:

* is longer than 200 chars, OR
* contains 2+ sentence-ending punctuation marks (``.`` / ``!`` / ``?``).

The ASCII period heuristic skips ellipses (``…``) and decimals are
rare in H1 advice — false-positive cost is negligible because the
next h1_structure review will re-create the rec with proper shape if
the page genuinely needs one.

Usage::

    docker compose exec -T backend python -m \\
      app.core_audit.review.backfill_misclassified_h1_structure \\
      [--site-id UUID] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.review.models import PageReviewRecommendation


logger = logging.getLogger(__name__)


# Trip wires for "this rec body looks like prose, not an H1 spec".
_PROSE_MAX_LEN = 200
_SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")
_MIN_SENTENCE_ENDS = 2


def _looks_like_prose(after_text: str | None) -> bool:
    """Return True when ``after_text`` reads like a paragraph, not an H1.

    Both conditions are independent OR-merged so we catch:
      * a single very long sentence (>200 chars, no proper end marker)
      * two-plus shorter sentences with explicit punctuation
    """
    if not after_text:
        return False
    if len(after_text) > _PROSE_MAX_LEN:
        return True
    if len(_SENTENCE_END_RE.findall(after_text)) >= _MIN_SENTENCE_ENDS:
        return True
    return False


async def cleanup_misclassified_h1_recs(
    db: AsyncSession,
    *,
    site_id: uuid.UUID | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete h1_structure recs whose after_text is paragraph prose.

    Parameters
    ----------
    db:
        Async session. We ``commit`` once at the end — the volume is
        tiny (audit found ~3 rows on the pilot) so per-batch commits
        would be overkill.
    site_id:
        Optional UUID filter.
    dry_run:
        Skip the DELETE; counts still reflect what would happen.

    Returns
    -------
    dict with keys: ``total_matched``, ``deleted``, ``errors``,
    ``dry_run``.
    """
    stmt = select(
        PageReviewRecommendation.id,
        PageReviewRecommendation.after_text,
    ).where(PageReviewRecommendation.category == "h1_structure")
    if site_id is not None:
        stmt = stmt.where(PageReviewRecommendation.site_id == site_id)

    rows = (await db.execute(stmt)).all()

    total_matched = 0
    to_delete: list[uuid.UUID] = []
    errors: list[dict[str, Any]] = []

    for rec_id, after_text in rows:
        total_matched += 1
        try:
            if _looks_like_prose(after_text):
                to_delete.append(rec_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "h1-cleanup: classify failed for rec=%s: %s", rec_id, exc,
            )
            errors.append({"rec_id": str(rec_id), "error": str(exc)})

    deleted = 0
    if to_delete and not dry_run:
        try:
            await db.execute(
                delete(PageReviewRecommendation).where(
                    PageReviewRecommendation.id.in_(to_delete)
                )
            )
            await db.commit()
            deleted = len(to_delete)
            logger.info("h1-cleanup: deleted %d rows", deleted)
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.warning("h1-cleanup: delete failed: %s", exc)
            for rid in to_delete:
                errors.append({"rec_id": str(rid), "error": str(exc)})
            deleted = 0
    elif to_delete and dry_run:
        # Mirror the schema-cargo script: dry-run still credits the
        # would-be deletes so operators can size the real run.
        deleted = len(to_delete)

    return {
        "total_matched": total_matched,
        "deleted": deleted,
        "errors": errors,
        "dry_run": dry_run,
    }


async def _cli_main(
    site_id: uuid.UUID | None, dry_run: bool,
) -> None:
    """CLI entry — opens a task_session, prints the summary."""
    from app.workers.db_session import task_session

    async with task_session() as db:
        result = await cleanup_misclassified_h1_recs(
            db, site_id=site_id, dry_run=dry_run,
        )

    print(  # noqa: T201
        f"dry_run={result['dry_run']} "
        f"total_matched={result['total_matched']} "
        f"deleted={result['deleted']} "
        f"errors={len(result['errors'])}"
    )
    for err in result["errors"]:
        print(f"  ! {err['rec_id']}: {err['error']}")  # noqa: T201


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="backfill_misclassified_h1_structure",
        description=(
            "Delete h1_structure recommendations whose after_text is "
            "paragraph prose, not an H1 shape."
        ),
    )
    p.add_argument("--site-id", type=str, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv[1:])
    if args.site_id is not None:
        try:
            args.site_id = uuid.UUID(args.site_id)
        except ValueError as exc:
            raise SystemExit(
                f"invalid site UUID: {args.site_id} ({exc})"
            ) from exc
    return args


if __name__ == "__main__":
    parsed = _parse_argv(sys.argv)
    asyncio.run(_cli_main(parsed.site_id, parsed.dry_run))


__all__ = [
    "cleanup_misclassified_h1_recs",
]
