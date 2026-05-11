"""Materialize harmful-query fixes into actionable page recommendations.

`harmful_diagnoser.py` already asks Haiku to produce concrete fixes for
each spam/disputed query (title rewrite, H1 rewrite, meta-description,
content tweak, schema, noindex). Those fixes live in
`SearchQuery.harmful_diagnosis` JSONB but never reach the owner — the
UI only shows them as a paragraph buried inside the harmful-query card.

This module bridges that gap: for every diagnosed query with a
matched URL, we find the corresponding Page, attach a
PageReviewRecommendation per non-empty fix, and let the Studio
recommendation flow pick them up. The owner sees them on
`/studio/pages/{page_id}` next to all other pending recommendations.

Idempotent: re-running won't duplicate. We dedupe on
(review_id, category, reasoning_prefix) which uniquely identifies
"this fix for this query for this page".

Cost: zero LLM. Pure DB transformation.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.models.page import Page
from app.models.search_query import SearchQuery

log = logging.getLogger(__name__)


# Map JSONB fix keys → (RecCategory string, before-text accessor)
# Keep in sync with harmful_diagnoser.diagnose_one() return shape.
_FIX_TO_CATEGORY: dict[str, str] = {
    "title_change": "title",
    "h1_change": "h1_structure",
    "meta_description_change": "meta_description",
    "content_change_ru": "over_optimization",  # closest existing bucket
    "schema_recommendation": "schema",
}


# Reasoning prefix tag — first ~80 chars are stable per (query, category)
# so we can dedup later runs without storing the query_id explicitly.
_REASONING_TAG = "[harmful_fix:{query_text}]"


@dataclass(frozen=True)
class MaterializeResult:
    queries_processed: int           # had a diagnosis with matched_url
    queries_skipped: int             # no matched_url, page not in DB, etc.
    pages_touched: int
    recs_created: int
    recs_skipped_existing: int


def _before_text_for(category: str, page: Page) -> str | None:
    """Pull the current value of the field we're proposing to change."""
    if category == "title":
        return page.title
    if category == "h1_structure":
        return page.h1
    if category == "meta_description":
        meta = page.meta or {}
        if isinstance(meta, dict):
            return meta.get("meta_description") or meta.get("description")
        return None
    if category == "over_optimization":
        # No single field — leave blank, the fix text is self-explanatory.
        return None
    if category == "schema":
        meta = page.meta or {}
        if isinstance(meta, dict):
            schemas = meta.get("schema_types")
            if schemas:
                return ", ".join(schemas) if isinstance(schemas, list) else str(schemas)
        return None
    return None


def _composite_hash_for_review(page: Page) -> str:
    """Deterministic hash so retry on the same page hits the same review."""
    seed = "|".join([
        str(page.id),
        page.title or "",
        page.h1 or "",
        "harmful_fix_v1",
    ])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


async def _get_or_create_review(
    db: AsyncSession, page: Page, site_id: UUID,
) -> PageReview:
    """Use the latest completed review if present; otherwise create a stub.

    Reusing keeps the page's review list short and avoids spawning a
    fake "review" on pages the owner has never opened. The stub
    creation path is for pages we've seen but never reviewed — without
    it the harmful-fix recommendation has nowhere to attach.
    """
    existing = (await db.execute(
        select(PageReview)
        .where(PageReview.page_id == page.id)
        .where(PageReview.status == "completed")
        .order_by(desc(PageReview.reviewed_at))
        .limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        return existing

    composite_hash = _composite_hash_for_review(page)
    stub = PageReview(
        page_id=page.id,
        site_id=site_id,
        coverage_decision_id=None,
        target_intent_code="harmful_query_fix",
        composite_hash=composite_hash,
        reviewer_model="harmful_fix_v1",
        reviewer_version="1.0.0",
        status="completed",
        cost_usd=0.0,
        input_tokens=0,
        output_tokens=0,
        page_level_summary={"source": "harmful_fix"},
    )
    db.add(stub)
    await db.flush()
    return stub


async def _existing_rec_keys(
    db: AsyncSession, review_id: UUID,
) -> set[tuple[str, str]]:
    """Set of (category, reasoning_first_80) already present for the review."""
    rows = (await db.execute(
        select(
            PageReviewRecommendation.category,
            PageReviewRecommendation.reasoning_ru,
        )
        .where(PageReviewRecommendation.review_id == review_id)
    )).all()
    return {(r.category, (r.reasoning_ru or "")[:80]) for r in rows}


def _normalize_url(url: str | None) -> str:
    if not url:
        return ""
    s = url.strip().lower()
    # strip protocol + leading www. + trailing slash for matching
    for p in ("https://", "http://"):
        if s.startswith(p):
            s = s[len(p):]
    if s.startswith("www."):
        s = s[4:]
    return s.rstrip("/")


async def _find_page_for_query(
    db: AsyncSession, site_id: UUID, matched_url: str | None,
) -> Page | None:
    if not matched_url:
        return None
    target = _normalize_url(matched_url)
    if not target:
        return None
    # Pull all pages for the site once and match in Python — sites have
    # ~20-100 pages typically, so this is cheaper than per-row LIKE.
    rows = (await db.execute(
        select(Page).where(Page.site_id == site_id)
    )).scalars().all()
    for p in rows:
        if _normalize_url(p.url) == target:
            return p
    return None


async def materialize_harmful_fixes(
    db: AsyncSession, site_id: UUID,
) -> MaterializeResult:
    """Convert every cached harmful_diagnosis into page recommendations.

    Idempotent — call as often as you like; existing recs aren't
    duplicated. Caller commits.
    """
    queries = (await db.execute(
        select(SearchQuery)
        .where(SearchQuery.site_id == site_id)
        .where(SearchQuery.relevance.in_(("spam", "disputed")))
        .where(SearchQuery.harmful_diagnosis.is_not(None))
    )).scalars().all()

    queries_processed = 0
    queries_skipped = 0
    pages_touched: set[UUID] = set()
    recs_created = 0
    recs_skipped_existing = 0

    for sq in queries:
        diag = sq.harmful_diagnosis or {}
        matched_url = diag.get("matched_url")
        fixes = diag.get("fixes") or {}
        if not matched_url or not fixes:
            queries_skipped += 1
            continue

        page = await _find_page_for_query(db, site_id, matched_url)
        if page is None:
            queries_skipped += 1
            continue

        review = await _get_or_create_review(db, page, site_id)
        existing_keys = await _existing_rec_keys(db, review.id)
        pages_touched.add(page.id)
        queries_processed += 1

        cause = (diag.get("cause_ru") or "").strip()
        for fix_key, category in _FIX_TO_CATEGORY.items():
            after_text = (fixes.get(fix_key) or "").strip()
            if not after_text:
                continue

            tag = _REASONING_TAG.format(query_text=sq.query_text)
            reasoning = (
                f"{tag} Запрос «{sq.query_text}» приводит на эту страницу "
                f"нежелательный трафик ({sq.relevance}). "
                f"{cause if cause else 'Содержание страницы пересекается с не-нашей темой.'} "
                f"Применение этой правки уберёт сигнал, по которому Яндекс "
                f"сейчас ранжирует страницу по чужому запросу."
            )

            key = (category, reasoning[:80])
            if key in existing_keys:
                recs_skipped_existing += 1
                continue

            db.add(PageReviewRecommendation(
                review_id=review.id,
                site_id=site_id,
                category=category,
                priority="high",
                before_text=_before_text_for(category, page),
                after_text=after_text,
                reasoning_ru=reasoning,
                estimated_impact={
                    "source": "harmful_fix",
                    "harmful_query": sq.query_text,
                    "harmful_relevance": sq.relevance,
                    "matched_position": diag.get("matched_position"),
                },
            ))
            existing_keys.add(key)
            recs_created += 1

        # noindex case has no after_text — surface it as a separate
        # rec via category=meta (since it's a meta-level decision).
        if fixes.get("noindex_recommended"):
            tag = _REASONING_TAG.format(query_text=sq.query_text)
            reasoning = (
                f"{tag} Эта страница ранжируется по чужому запросу "
                f"«{sq.query_text}» ({sq.relevance}), и переписать её "
                f"под свою тему смысла нет. Поставь `<meta name=\"robots\" "
                f"content=\"noindex,follow\">` чтобы Яндекс убрал её из выдачи."
            )
            key = ("meta_description", reasoning[:80])
            if key not in existing_keys:
                db.add(PageReviewRecommendation(
                    review_id=review.id,
                    site_id=site_id,
                    category="meta_description",
                    priority="high",
                    before_text=None,
                    after_text='<meta name="robots" content="noindex,follow">',
                    reasoning_ru=reasoning,
                    estimated_impact={
                        "source": "harmful_fix_noindex",
                        "harmful_query": sq.query_text,
                    },
                ))
                recs_created += 1

    return MaterializeResult(
        queries_processed=queries_processed,
        queries_skipped=queries_skipped,
        pages_touched=len(pages_touched),
        recs_created=recs_created,
        recs_skipped_existing=recs_skipped_existing,
    )


__all__ = [
    "MaterializeResult",
    "materialize_harmful_fixes",
]
