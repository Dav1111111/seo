"""Coverage Analyzer — пересекает queries (с их intent) и pages (с их intent scores)
для каждого сайта. Отвечает на вопрос "какие интенты закрыты, какие нет".

Phase 2A: базовый coverage report БЕЗ decision tree и safety layer.
Decision Tree (Q1-Q7) + Standalone Value Test — Phase 2C.

Phase C (Target Demand Map integration): added a new code path
`mode="target_clusters"` which iterates rows from the `target_clusters`
table instead of the `IntentCode` enum. Legacy mode is the default and
is byte-identical to the pre-Phase-C behavior — parity stays green.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.demand_map.models import TargetCluster
from app.fingerprint.lemmatize import lemmatize_tokens, tokenize
from app.intent.enums import CoverageStatus, IntentCode
from app.intent.models import PageIntentScore, QueryIntent
from app.models.daily_metric import DailyMetric
from app.models.page import Page
from app.models.search_query import SearchQuery


# Expected observed-impressions floor per volume tier over a 14-day window.
# Used by the target_clusters coverage score: the more volume a cluster
# is expected to attract, the more observed impressions we demand before
# calling it "covered".
_VOLUME_TIER_FLOOR_14D: dict[str, int] = {
    "xs": 10,
    "s": 30,
    "m": 100,
    "l": 300,
    "xl": 1000,
}


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

    # Phase C additions — populated only by the target_clusters code path.
    # Legacy path leaves these None so downstream consumers that don't
    # know about them keep working unchanged.
    target_cluster_id: uuid.UUID | None = None
    cluster_type: str | None = None
    quality_tier: str | None = None
    business_relevance: float | None = None
    coverage_score: float | None = None
    coverage_gap: float | None = None
    is_brand_cluster: bool | None = None


class CoverageAnalyzer:
    async def analyze_site(
        self,
        db: AsyncSession,
        site_id: uuid.UUID,
        days: int = 14,
        *,
        mode: Literal["legacy_intents", "target_clusters"] = "legacy_intents",
    ) -> list[IntentClusterReport]:
        """Build per-intent coverage report for a site.

        mode="legacy_intents" (default) — iterate IntentCode enum, aggregate
        observed QueryIntent rows. Byte-identical to pre-Phase-C behavior.

        mode="target_clusters" — iterate TargetCluster rows for the site,
        match observed queries by lemma overlap, compute coverage_score
        and coverage_gap driven by expected_volume_tier + business_relevance.
        """
        if mode == "target_clusters":
            return await self._analyze_target_clusters(db, site_id, days)
        return await self._analyze_legacy(db, site_id, days)

    # ─────────────────────────────────────────────────────────────────────
    # Legacy path — UNCHANGED logic, just extracted into its own method.
    # ─────────────────────────────────────────────────────────────────────
    async def _analyze_legacy(
        self, db: AsyncSession, site_id: uuid.UUID, days: int = 14
    ) -> list[IntentClusterReport]:
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

    # ─────────────────────────────────────────────────────────────────────
    # Phase C path — iterate target_clusters, match observed queries by
    # lemma overlap, compute coverage_score + coverage_gap.
    # ─────────────────────────────────────────────────────────────────────
    async def _analyze_target_clusters(
        self, db: AsyncSession, site_id: uuid.UUID, days: int = 14
    ) -> list[IntentClusterReport]:
        today = date.today()
        end = today - timedelta(days=5)  # Webmaster lag
        start = end - timedelta(days=days - 1)

        # ── Preload: all target_clusters for the site ────────────────
        cluster_rows = await db.execute(
            select(TargetCluster).where(TargetCluster.site_id == site_id)
        )
        if hasattr(cluster_rows, "scalars"):
            clusters = list(cluster_rows.scalars())
        else:
            # Test path: mock returns a plain iterable of TargetCluster
            # objects (or 1-tuples). Normalise to a flat list.
            clusters = [c[0] if isinstance(c, tuple) else c for c in cluster_rows]

        if not clusters:
            return []

        # ── Preload: observed SearchQuery rows (id + text) ───────────
        text_rows = await db.execute(
            select(SearchQuery.id, SearchQuery.query_text)
            .where(SearchQuery.site_id == site_id)
        )
        observed_queries: list[tuple[uuid.UUID, str]] = [
            (qid, text) for qid, text in text_rows
        ]
        query_text_by_id: dict[uuid.UUID, str] = dict(observed_queries)

        # Pre-lemmatize observed queries ONCE (cost control).
        observed_lemmas_by_id: dict[uuid.UUID, set[str]] = {
            qid: set(lemmatize_tokens(tokenize(text), drop_stopwords=True))
            for qid, text in observed_queries
        }

        # ── Preload: DailyMetric aggregates per query ────────────────
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

        # ── Preload: PageIntentScore rows per intent_code ────────────
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

        # ── Build one report per target_cluster ──────────────────────
        reports: list[IntentClusterReport] = []
        for cluster in clusters:
            ic_str = cluster.intent_code
            try:
                intent_enum = IntentCode(ic_str)
            except ValueError:
                # Unknown intent_code string — skip defensively.
                continue

            # Build the lemma set that defines this cluster.
            keyword_lemmas: set[str] = set()
            for kw in (cluster.keywords or []):
                keyword_lemmas.update(
                    lemmatize_tokens(tokenize(kw), drop_stopwords=True)
                )
            name_lemmas: set[str] = set(
                lemmatize_tokens(tokenize(cluster.name_ru or ""), drop_stopwords=True)
            )
            cluster_lemmas = keyword_lemmas | name_lemmas

            # Match observed queries: any lemma overlap counts.
            matched_query_ids: list[uuid.UUID] = []
            if cluster_lemmas:
                for qid, lemmas in observed_lemmas_by_id.items():
                    if lemmas & cluster_lemmas:
                        matched_query_ids.append(qid)

            # Aggregate observed metrics.
            total_imp = 0
            total_clk = 0
            positions: list[float] = []
            query_impressions: list[tuple[str, int]] = []
            for qid in matched_query_ids:
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

            # Page side — best page for this intent_code.
            pages = page_scores.get(ic_str, [])
            best = pages[0] if pages else None
            strong_count = sum(1 for p in pages if p["score"] >= 4.0)
            weak_count = sum(1 for p in pages if 2.0 <= p["score"] < 4.0)
            best_score = float(best["score"]) if best else 0.0

            # ── Coverage score ───────────────────────────────────────
            expected_floor = _VOLUME_TIER_FLOOR_14D.get(
                cluster.expected_volume_tier or "s", 30
            )
            observed_component = (
                min(total_imp / expected_floor, 1.0) if expected_floor > 0 else 0.0
            )
            page_component = best_score / 5.0
            matched_component = 1.0 if matched_query_ids else 0.0

            coverage_score = (
                0.5 * page_component
                + 0.3 * observed_component
                + 0.2 * matched_component
            )
            coverage_score = max(0.0, min(1.0, coverage_score))

            business_relevance = float(cluster.business_relevance or 0.0)
            coverage_gap = (1.0 - coverage_score) * business_relevance

            # ── Status ───────────────────────────────────────────────
            # Baseline buckets from coverage_score.
            if coverage_score >= 0.8:
                status = CoverageStatus.strong
            elif coverage_score >= 0.4:
                status = CoverageStatus.weak
            else:
                status = CoverageStatus.missing

            # Preserve legacy over_covered semantics: multiple strong pages
            # competing for the same intent, non-brand only.
            if (
                not bool(cluster.is_brand)
                and strong_count >= 2
                and best_score >= 4.0
            ):
                status = CoverageStatus.over_covered

            reports.append(IntentClusterReport(
                intent_code=intent_enum,
                queries_count=len(matched_query_ids),
                total_impressions_14d=total_imp,
                total_clicks_14d=total_clk,
                avg_position=round(sum(positions) / len(positions), 1) if positions else None,
                top_queries=top_queries,
                ambiguous_queries_count=0,  # no ambiguous concept in target_clusters path
                best_page_id=best["page_id"] if best else None,
                best_page_url=best["url"] if best else None,
                best_page_score=best_score,
                pages_with_score_gte_4=strong_count,
                pages_with_score_2_3=weak_count,
                status=status,
                # Phase C additive fields.
                target_cluster_id=cluster.id,
                cluster_type=cluster.cluster_type,
                quality_tier=cluster.quality_tier,
                business_relevance=business_relevance,
                coverage_score=round(coverage_score, 4),
                coverage_gap=round(coverage_gap, 4),
                is_brand_cluster=bool(cluster.is_brand),
            ))

        return reports
