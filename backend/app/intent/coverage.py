"""Coverage Analyzer — пересекает queries (с их intent) и pages (с их intent scores)
для каждого сайта. Отвечает на вопрос "какие интенты закрыты, какие нет".

Phase 2A: базовый coverage report БЕЗ decision tree и safety layer.
Decision Tree (Q1-Q7) + Standalone Value Test — Phase 2C.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.intent.enums import CoverageStatus, IntentCode
from app.intent.models import PageIntentScore, QueryIntent
from app.models.daily_metric import DailyMetric
from app.models.page import Page
from app.models.search_query import SearchQuery


@dataclass(frozen=True)
class IntentClusterReport:
    """Summary of how well an intent is covered on a site."""
    intent_code: IntentCode
    queries_count: int                    # сколько запросов в этом интенте
    total_impressions_14d: int            # суммарные показы за 14 дней
    total_clicks_14d: int
    avg_position: float | None
    top_queries: list[str]                # топ-5 запросов по показам
    ambiguous_queries_count: int          # сколько мы не смогли классифицировать уверенно

    # Page side
    best_page_id: uuid.UUID | None
    best_page_url: str | None
    best_page_score: float                # 0.0-5.0
    pages_with_score_gte_4: int          # strong coverage
    pages_with_score_2_3: int            # weak coverage

    # Combined status
    status: CoverageStatus


class CoverageAnalyzer:
    async def analyze_site(
        self, db: AsyncSession, site_id: uuid.UUID, days: int = 14
    ) -> list[IntentClusterReport]:
        """Build per-intent coverage report for a site."""
        today = date.today()
        end = today - timedelta(days=5)  # Webmaster lag
        start = end - timedelta(days=days - 1)

        # ── Query side: group queries by intent ───────────────────────
        query_stats: dict[str, dict] = {}
        rows = await db.execute(
            select(
                QueryIntent.intent_code,
                QueryIntent.query_id,
                QueryIntent.is_ambiguous,
            ).where(QueryIntent.site_id == site_id)
        )
        for intent_code, query_id, is_ambiguous in rows:
            s = query_stats.setdefault(intent_code, {
                "query_ids": [],
                "ambiguous_count": 0,
            })
            s["query_ids"].append(query_id)
            if is_ambiguous:
                s["ambiguous_count"] += 1

        # ── Fetch impressions per query for the period ────────────────
        metric_rows = await db.execute(
            select(
                DailyMetric.dimension_id,
                func.sum(DailyMetric.impressions).label("imp"),
                func.sum(DailyMetric.clicks).label("clk"),
                func.avg(DailyMetric.avg_position).label("pos"),
            ).where(
                DailyMetric.site_id == site_id,
                DailyMetric.metric_type == "query_performance",
                DailyMetric.date.between(start, end),
            ).group_by(DailyMetric.dimension_id)
        )
        impressions_per_query: dict[uuid.UUID, dict] = {
            r.dimension_id: {
                "impressions": int(r.imp or 0),
                "clicks": int(r.clk or 0),
                "avg_position": float(r.pos) if r.pos else None,
            }
            for r in metric_rows
        }

        # ── Query text lookup ─────────────────────────────────────────
        text_rows = await db.execute(
            select(SearchQuery.id, SearchQuery.query_text)
            .where(SearchQuery.site_id == site_id)
        )
        query_text_by_id: dict[uuid.UUID, str] = {qid: text for qid, text in text_rows}

        # ── Page side: best page score per intent ─────────────────────
        page_rows = await db.execute(
            select(
                PageIntentScore.intent_code,
                PageIntentScore.page_id,
                PageIntentScore.score,
                Page.url,
            ).join(Page, Page.id == PageIntentScore.page_id)
            .where(PageIntentScore.site_id == site_id)
            .order_by(PageIntentScore.intent_code, PageIntentScore.score.desc())
        )
        page_scores: dict[str, list] = {}
        for intent_code, page_id, score, url in page_rows:
            page_scores.setdefault(intent_code, []).append({
                "page_id": page_id, "score": score, "url": url,
            })

        # ── Build reports ─────────────────────────────────────────────
        reports: list[IntentClusterReport] = []
        for intent in IntentCode:
            ic = intent.value
            q_info = query_stats.get(ic, {"query_ids": [], "ambiguous_count": 0})
            query_ids = q_info["query_ids"]

            # Aggregate metrics for queries of this intent
            total_imp = 0
            total_clk = 0
            positions = []
            query_impressions: list[tuple[str, int]] = []
            for qid in query_ids:
                m = impressions_per_query.get(qid)
                if m:
                    total_imp += m["impressions"]
                    total_clk += m["clicks"]
                    if m["avg_position"]:
                        positions.append(m["avg_position"])
                    q_text = query_text_by_id.get(qid, "?")
                    query_impressions.append((q_text, m["impressions"]))
                else:
                    q_text = query_text_by_id.get(qid, "?")
                    query_impressions.append((q_text, 0))

            query_impressions.sort(key=lambda x: x[1], reverse=True)
            top_queries = [q for q, _ in query_impressions[:5]]

            # Page side
            pages = page_scores.get(ic, [])
            best = pages[0] if pages else None
            strong_count = sum(1 for p in pages if p["score"] >= 4.0)
            weak_count = sum(1 for p in pages if 2.0 <= p["score"] < 4.0)

            # Determine status
            queries_count = len(query_ids)
            best_score = best["score"] if best else 0.0

            if queries_count == 0:
                status = CoverageStatus.missing
            elif best_score >= 4.0:
                status = CoverageStatus.strong
                # Check over-coverage
                if strong_count >= 2:
                    status = CoverageStatus.over_covered
            elif best_score >= 2.0:
                status = CoverageStatus.weak
            else:
                status = CoverageStatus.missing

            reports.append(IntentClusterReport(
                intent_code=intent,
                queries_count=queries_count,
                total_impressions_14d=total_imp,
                total_clicks_14d=total_clk,
                avg_position=round(sum(positions) / len(positions), 1) if positions else None,
                top_queries=top_queries,
                ambiguous_queries_count=q_info["ambiguous_count"],
                best_page_id=best["page_id"] if best else None,
                best_page_url=best["url"] if best else None,
                best_page_score=best_score,
                pages_with_score_gte_4=strong_count,
                pages_with_score_2_3=weak_count,
                status=status,
            ))

        return reports
