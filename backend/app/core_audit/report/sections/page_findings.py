"""Section 5 — Page Review Findings (grouped by page).

Uses only the LATEST PageReview per (page_id, target_intent_code) to
avoid showing stale recs. E-E-A-T + commercial factor signal names
surface as a subsection on each page card.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.report.dto import PageFindingsSection, PageFindingSummary
from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.models.page import Page

PAGE_CARDS_LIMIT = 20


async def build_page_findings(
    db: AsyncSession, site_id: UUID,
) -> PageFindingsSection:
    # Latest completed PageReview per (page_id, target_intent_code)
    subq = (
        select(
            PageReview.id.label("id"),
            func.row_number().over(
                partition_by=(PageReview.page_id, PageReview.target_intent_code),
                order_by=PageReview.reviewed_at.desc(),
            ).label("rn"),
        )
        .where(PageReview.site_id == site_id, PageReview.status == "completed")
        .subquery()
    )
    latest_ids = {r[0] for r in (await db.execute(select(subq.c.id).where(subq.c.rn == 1))).all()}

    if not latest_ids:
        return PageFindingsSection(
            warning_ru=(
                "Ревью страниц ещё не запускались. Запустите Module 3 перед "
                "следующим отчётом (POST /api/v1/reviews/sites/{id}/run)."
            ),
        )

    reviews_row = await db.execute(
        select(
            PageReview.id, PageReview.page_id, PageReview.target_intent_code,
            PageReview.reviewed_at, PageReview.page_level_summary,
            Page.url,
        )
        .outerjoin(Page, Page.id == PageReview.page_id)
        .where(PageReview.id.in_(latest_ids))
    )
    reviews = reviews_row.all()

    rec_rows = await db.execute(
        select(
            PageReviewRecommendation.review_id,
            PageReviewRecommendation.category,
            PageReviewRecommendation.priority,
            PageReviewRecommendation.reasoning_ru,
        )
        .where(PageReviewRecommendation.review_id.in_(latest_ids))
    )
    recs_by_review: dict = {}
    by_category: dict = {}
    by_priority: dict = {}
    for review_id, cat, prio, _reasoning in rec_rows:
        recs_by_review.setdefault(review_id, []).append((cat, prio))
        by_category[cat] = by_category.get(cat, 0) + 1
        by_priority[prio] = by_priority.get(prio, 0) + 1

    pages: list[PageFindingSummary] = []
    for r in reviews:
        recs = recs_by_review.get(r.id, [])
        crit = sum(1 for _, p in recs if p == "critical")
        high = sum(1 for _, p in recs if p == "high")
        med = sum(1 for _, p in recs if p == "medium")
        low = sum(1 for _, p in recs if p == "low")

        # Top issues = unique top-3 categories by count on this page
        cat_count: dict = {}
        for c, _ in recs:
            cat_count[c] = cat_count.get(c, 0) + 1
        top_cats = sorted(cat_count.items(), key=lambda x: x[1], reverse=True)[:3]
        top_issues = [f"{cat} ({cnt})" for cat, cnt in top_cats]

        summary = r.page_level_summary or {}
        pages.append(PageFindingSummary(
            page_id=r.page_id,
            page_url=r.url,
            target_intent_code=r.target_intent_code,
            reviewed_at=r.reviewed_at,
            critical_count=crit,
            high_count=high,
            medium_count=med,
            low_count=low,
            top_issues=top_issues,
            missing_eeat_signals=list(summary.get("missing_eeat_signals", []))[:5],
            missing_commercial_factors=list(summary.get("missing_commercial_factors", []))[:5],
        ))

    pages.sort(key=lambda p: (p.critical_count, p.high_count), reverse=True)
    pages = pages[:PAGE_CARDS_LIMIT]

    return PageFindingsSection(
        reviews_run_count=len(latest_ids),
        pages_reviewed=len({r.page_id for r in reviews}),
        by_category_count=by_category,
        by_priority_count=by_priority,
        pages=pages,
    )
