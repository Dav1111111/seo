"""One-shot cleanup of recommendations that ask for a field already on the page.

Background
----------
A subset of historical recs say "add a phone number / РТО / ИНН /
ОГРН / booking CTA" on pages that already have them. The page-level
checks were fooled by markup that hides these fields from a naive
DOM crawl but they're plainly visible in Playwright's
``page_deep_extracts.full_text`` (rendered text). The 2026-05-14
audit counted ~13 telephone + ~5 РТО false positives on the pilot.

Strategy
--------
For each owner-visible signal we have:

  * a *match* predicate on the recommendation row (category +
    ``after_text`` substring),
  * a regex over the **latest** ``page_deep_extracts.full_text`` for
    the same page.

If the rec matches a signal AND the regex fires on the rendered text,
the rec is "field already present, false positive" and we delete it.
If no deep-extract exists for the page we SKIP — refusing to delete
without evidence, just like the schema-cargo cleanup.

Signal table (mirror of the audit spec):

  ============= ================================================ ================================
  signal        recommendation pattern (case-insensitive ILIKE)  "already present" regex
  ============= ================================================ ================================
  phone         after_text LIKE %телефон% AND category=commercial \\+7\\s*\\(?\\d{3}\\)?
  rto           after_text LIKE %РТО% AND category in            (?i)РТО\\s*[№N]?\\s*\\d{6}
                  (commercial, eeat)
  inn           after_text LIKE %ИНН%                            (?i)ИНН\\s*\\d{10,12}
  ogrn          after_text LIKE %ОГРН%                           (?i)ОГРН\\s*\\d{13,15}
  booking       after_text LIKE %забронир% OR %booking%          (?i)(забронир|оставить
                                                                  заявк|купить тур)
  ============= ================================================ ================================

Usage::

    docker compose exec -T backend python -m \\
      app.core_audit.review.backfill_false_field_present \\
      [--site-id UUID] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.models.page_deep_extract import PageDeepExtract


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal definitions. Each entry is one bucket in the report counters.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Signal:
    """One field-presence cleanup rule.

    Attributes
    ----------
    name:
        Stable key used in the report dict (``deleted_by_signal``).
    categories:
        Recommendation categories the rule applies to. Empty tuple
        means "any category".
    after_text_substrings:
        Case-insensitive substrings that must appear in ``after_text``
        for the rec to be in scope. We OR them so a single rule can
        cover Cyrillic + ASCII synonyms (booking).
    present_regex:
        Compiled regex run against the page's latest full_text. If it
        matches, the rec is considered redundant and is deleted.
    """

    name: str
    categories: tuple[str, ...]
    after_text_substrings: tuple[str, ...]
    present_regex: re.Pattern[str]


SIGNALS: tuple[_Signal, ...] = (
    _Signal(
        name="phone",
        categories=("commercial",),
        after_text_substrings=("телефон",),
        # +7 followed by an area code, parens optional, optional space.
        present_regex=re.compile(r"\+7\s*\(?\d{3}\)?"),
    ),
    _Signal(
        name="rto",
        categories=("commercial", "eeat"),
        after_text_substrings=("РТО",),
        present_regex=re.compile(r"РТО\s*[№N]?\s*\d{6}", re.IGNORECASE),
    ),
    _Signal(
        name="inn",
        # ИНН can appear in any category — usually eeat, sometimes
        # commercial. Keep the filter open.
        categories=(),
        after_text_substrings=("ИНН",),
        present_regex=re.compile(r"ИНН\s*\d{10,12}", re.IGNORECASE),
    ),
    _Signal(
        name="ogrn",
        categories=(),
        after_text_substrings=("ОГРН",),
        present_regex=re.compile(r"ОГРН\s*\d{13,15}", re.IGNORECASE),
    ),
    _Signal(
        name="booking",
        categories=("commercial",),
        after_text_substrings=("забронир", "booking"),
        present_regex=re.compile(
            r"(забронир|оставить\s+заявк|купить\s+тур)", re.IGNORECASE,
        ),
    ),
)


def _rec_matches_signal(
    rec_category: str, rec_after_text: str | None, sig: _Signal,
) -> bool:
    """True when the rec is in scope for this signal."""
    if sig.categories and rec_category not in sig.categories:
        return False
    if not rec_after_text:
        return False
    haystack = rec_after_text.lower()
    return any(s.lower() in haystack for s in sig.after_text_substrings)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _fetch_candidates(
    db: AsyncSession, site_id: uuid.UUID | None,
) -> list[tuple[uuid.UUID, uuid.UUID, str, str | None]]:
    """Pull every rec that could possibly match any signal.

    Returns ``(rec_id, page_id, category, after_text)``. We use a
    coarse SQL ILIKE prefilter so we only hydrate rows worth inspecting
    in Python — the signal-specific category gate runs there.
    """
    # Build a big OR of "after_text ILIKE %sub%" across all configured
    # substrings. Done once so the SELECT touches every signal in a
    # single pass; the Python loop then sorts by signal.
    substring_clauses = []
    for sig in SIGNALS:
        for sub in sig.after_text_substrings:
            substring_clauses.append(
                PageReviewRecommendation.after_text.ilike(f"%{sub}%")
            )
    stmt = (
        select(
            PageReviewRecommendation.id,
            PageReview.page_id,
            PageReviewRecommendation.category,
            PageReviewRecommendation.after_text,
        )
        .join(PageReview, PageReview.id == PageReviewRecommendation.review_id)
        .where(or_(*substring_clauses))
    )
    if site_id is not None:
        stmt = stmt.where(PageReviewRecommendation.site_id == site_id)
    rows = (await db.execute(stmt)).all()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


async def _latest_full_text(
    db: AsyncSession, page_id: uuid.UUID,
) -> str | None:
    """Return the most recent ``full_text`` for the page, or None.

    ``None`` means "no deep-extract row at all" — the cleanup treats
    that as "no evidence" and skips. An empty string would be evidence
    that we crawled and found nothing; the regexes will simply not
    match and the rec will survive.
    """
    stmt = (
        select(PageDeepExtract.full_text)
        .where(PageDeepExtract.page_id == page_id)
        .order_by(PageDeepExtract.extracted_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        return None
    return row[0] or ""


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

async def cleanup_false_field_present(
    db: AsyncSession,
    *,
    site_id: uuid.UUID | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete recs that ask for a field already present on the page.

    Returns
    -------
    dict with keys: ``checked``, ``deleted_by_signal``
    (``{phone: N, rto: M, …}``), ``skipped_no_extract``, ``errors``,
    ``dry_run``.
    """
    candidates = await _fetch_candidates(db, site_id)

    checked = 0
    skipped_no_extract = 0
    errors: list[dict[str, Any]] = []
    deleted_by_signal: dict[str, int] = {sig.name: 0 for sig in SIGNALS}
    to_delete: list[uuid.UUID] = []

    # Cache full_text per page so multiple recs on the same page only
    # cost one round-trip. ``None`` sentinel = no deep-extract row.
    page_text_cache: dict[uuid.UUID, str | None] = {}

    for rec_id, page_id, category, after_text in candidates:
        # Which signal does this rec match? First-match wins. Most recs
        # carry only one of the substrings so collisions are rare.
        matched_signal: _Signal | None = None
        for sig in SIGNALS:
            if _rec_matches_signal(category, after_text, sig):
                matched_signal = sig
                break
        if matched_signal is None:
            # Fell into the SQL prefilter but no signal claims it (e.g.
            # an eeat rec mentioning "телефон" — we restrict phone to
            # commercial). Leave it alone.
            continue
        checked += 1

        try:
            if page_id not in page_text_cache:
                page_text_cache[page_id] = await _latest_full_text(db, page_id)
            full_text = page_text_cache[page_id]
            if full_text is None:
                skipped_no_extract += 1
                continue
            if matched_signal.present_regex.search(full_text):
                to_delete.append(rec_id)
                deleted_by_signal[matched_signal.name] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "field-present cleanup: classify failed for rec=%s: %s",
                rec_id, exc,
            )
            errors.append({"rec_id": str(rec_id), "error": str(exc)})

    if to_delete and not dry_run:
        try:
            await db.execute(
                delete(PageReviewRecommendation).where(
                    PageReviewRecommendation.id.in_(to_delete)
                )
            )
            await db.commit()
            logger.info(
                "field-present cleanup: deleted %d rows", len(to_delete),
            )
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.warning("field-present cleanup: delete failed: %s", exc)
            # Counts already incremented — roll them back so the report
            # reflects what really happened.
            for sig in SIGNALS:
                deleted_by_signal[sig.name] = 0
            for rid in to_delete:
                errors.append({"rec_id": str(rid), "error": str(exc)})

    return {
        "checked": checked,
        "deleted_by_signal": deleted_by_signal,
        "skipped_no_extract": skipped_no_extract,
        "errors": errors,
        "dry_run": dry_run,
    }


async def _cli_main(
    site_id: uuid.UUID | None, dry_run: bool,
) -> None:
    """CLI entry — task_session, print summary."""
    from app.workers.db_session import task_session

    async with task_session() as db:
        result = await cleanup_false_field_present(
            db, site_id=site_id, dry_run=dry_run,
        )

    by_sig = " ".join(
        f"{k}={v}" for k, v in result["deleted_by_signal"].items()
    )
    print(  # noqa: T201
        f"dry_run={result['dry_run']} "
        f"checked={result['checked']} "
        f"skipped_no_extract={result['skipped_no_extract']} "
        f"deleted_by_signal[{by_sig}] "
        f"errors={len(result['errors'])}"
    )
    for err in result["errors"]:
        print(f"  ! {err['rec_id']}: {err['error']}")  # noqa: T201


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="backfill_false_field_present",
        description=(
            "Delete recommendations asking to add a field that is "
            "already present in page_deep_extracts.full_text "
            "(phone / РТО / ИНН / ОГРН / booking CTA)."
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
    "SIGNALS",
    "cleanup_false_field_present",
]
