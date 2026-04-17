"""Intent API — manual trigger + coverage reports + query classifications."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.intent.coverage import CoverageAnalyzer
from app.intent.models import PageIntentScore, QueryIntent
from app.models.page import Page
from app.models.search_query import SearchQuery

router = APIRouter()


class QueuedResponse(BaseModel):
    task_id: str
    status: str


@router.post("/intent/sites/{site_id}/classify", response_model=QueuedResponse)
async def trigger_classify(site_id: uuid.UUID):
    """Classify queries + score pages for a site."""
    from app.intent.tasks import intent_classify_site
    task = intent_classify_site.delay(str(site_id))
    return QueuedResponse(task_id=task.id, status="queued")


@router.get("/intent/sites/{site_id}/queries")
async def list_query_intents(
    site_id: uuid.UUID,
    intent_code: str | None = None,
    ambiguous_only: bool = False,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List classified queries for a site."""
    q = select(
        QueryIntent.query_id,
        SearchQuery.query_text,
        QueryIntent.intent_code,
        QueryIntent.confidence,
        QueryIntent.is_brand,
        QueryIntent.is_ambiguous,
        QueryIntent.matched_pattern,
    ).join(SearchQuery, SearchQuery.id == QueryIntent.query_id)\
     .where(QueryIntent.site_id == site_id)

    if intent_code:
        q = q.where(QueryIntent.intent_code == intent_code)
    if ambiguous_only:
        q = q.where(QueryIntent.is_ambiguous == True)  # noqa: E712

    rows = await db.execute(q.limit(500))
    items = [
        {
            "query_id": str(qid),
            "query_text": text,
            "intent_code": ic,
            "confidence": round(conf, 3),
            "is_brand": brand,
            "is_ambiguous": amb,
            "matched_pattern": pat[:80] if pat else None,
        }
        for qid, text, ic, conf, brand, amb, pat in rows
    ]

    # Summary counts by intent
    summary_rows = await db.execute(
        select(QueryIntent.intent_code, func.count())
        .where(QueryIntent.site_id == site_id)
        .group_by(QueryIntent.intent_code)
    )
    summary = {ic: c for ic, c in summary_rows}

    return {"total": len(items), "summary": summary, "items": items}


@router.get("/intent/sites/{site_id}/pages")
async def list_page_scores(
    site_id: uuid.UUID,
    intent_code: str | None = None,
    min_score: float = 0.0,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List page intent scores."""
    q = select(
        PageIntentScore.page_id,
        Page.url,
        Page.path,
        PageIntentScore.intent_code,
        PageIntentScore.score,
        PageIntentScore.s1_heading,
        PageIntentScore.s2_content,
        PageIntentScore.s3_structure,
        PageIntentScore.s4_cta,
        PageIntentScore.s5_schema,
        PageIntentScore.s6_eeat,
    ).join(Page, Page.id == PageIntentScore.page_id)\
     .where(PageIntentScore.site_id == site_id, PageIntentScore.score >= min_score)

    if intent_code:
        q = q.where(PageIntentScore.intent_code == intent_code)

    q = q.order_by(PageIntentScore.score.desc()).limit(500)
    rows = await db.execute(q)
    items = [
        {
            "page_id": str(pid),
            "url": url,
            "path": path,
            "intent_code": ic,
            "score": round(score, 2),
            "signals": {
                "s1_heading": round(s1, 2),
                "s2_content": round(s2, 2),
                "s3_structure": round(s3, 2),
                "s4_cta": round(s4, 2),
                "s5_schema": round(s5, 2),
                "s6_eeat": round(s6, 2),
            },
        }
        for pid, url, path, ic, score, s1, s2, s3, s4, s5, s6 in rows
    ]
    return {"total": len(items), "items": items}


@router.get("/intent/sites/{site_id}/coverage")
async def get_coverage(
    site_id: uuid.UUID,
    days: int = 14,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get coverage report — intent × pages summary."""
    analyzer = CoverageAnalyzer()
    reports = await analyzer.analyze_site(db, site_id, days=days)
    return {
        "site_id": str(site_id),
        "days": days,
        "reports": [
            {
                "intent_code": r.intent_code.value,
                "queries_count": r.queries_count,
                "total_impressions_14d": r.total_impressions_14d,
                "total_clicks_14d": r.total_clicks_14d,
                "avg_position": r.avg_position,
                "top_queries": r.top_queries,
                "ambiguous_queries_count": r.ambiguous_queries_count,
                "best_page_url": r.best_page_url,
                "best_page_score": r.best_page_score,
                "pages_strong": r.pages_with_score_gte_4,
                "pages_weak": r.pages_with_score_2_3,
                "status": r.status.value,
            }
            for r in reports
        ],
    }
