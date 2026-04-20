"""PriorityService — orchestrates scoring over DB state.

Reads PageReviewRecommendation + PageReview + CoverageDecision + Page
joined rows, runs scorer, persists priority_score + components back to
the recommendation row. Then exposes rank / weekly_plan queries.

Only the most recent PageReview per (page_id, target_intent_code) is
used for ranking (older reviews stay in DB for audit).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core_audit.demand_map.models import TargetCluster
from app.core_audit.priority.aggregator import MAX_PER_PAGE_DEFAULT, rank, weekly_plan
from app.core_audit.priority.dto import PrioritizedItem, WeeklyPlan
from app.core_audit.priority.scorer import SCORER_VERSION, ScorerContext, score_recommendation
from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.intent.coverage import CoverageAnalyzer
from app.intent.models import CoverageDecision, PageIntentScore
from app.models.page import Page

logger = logging.getLogger(__name__)


EXCLUDE_STATUSES_DEFAULT = ("applied", "dismissed")


class PriorityService:
    def __init__(self, scorer_version: str = SCORER_VERSION) -> None:
        self.scorer_version = scorer_version

    # ── Scoring (write) ─────────────────────────────────────────────

    async def rescore_site(self, db: AsyncSession, site_id: UUID) -> dict:
        """Rescore every recommendation on a site.

        Only recs belonging to the LATEST PageReview per (page_id, intent)
        are scored; older review recs are zeroed out so they never appear
        in priority lists.
        """
        latest_ids = await self._latest_review_ids(db, site_id)
        if not latest_ids:
            return {"site_id": str(site_id), "scored": 0, "dropped": 0, "zeroed_older": 0}

        # Phase D — when flag is on, build the per-intent_code cluster
        # lookup + site-level non-brand coverage ratio up front so the
        # score loop can attach them to ScorerContext.
        cluster_by_intent: dict[str, dict] = {}
        non_brand_ratio: float | None = None
        if settings.USE_TARGET_DEMAND_MAP:
            cluster_by_intent, non_brand_ratio = (
                await self._build_phase_d_site_context(db, site_id)
            )

        # Zero out older review recommendations on this site
        zeroed = await db.execute(
            update(PageReviewRecommendation)
            .where(
                PageReviewRecommendation.site_id == site_id,
                PageReviewRecommendation.review_id.notin_(latest_ids),
                or_(
                    PageReviewRecommendation.priority_score.is_not(None),
                    PageReviewRecommendation.scored_at.is_not(None),
                ),
            )
            .values(
                priority_score=None,
                impact_score=None,
                confidence_score=None,
                ease_score=None,
                scored_at=None,
                scorer_version=None,
            )
        )
        zeroed_count = zeroed.rowcount or 0

        # Score recs from latest reviews
        scored = 0
        dropped = 0
        async for ctx, rec_id in self._score_inputs(
            db,
            site_id,
            latest_ids,
            cluster_by_intent=cluster_by_intent,
            non_brand_ratio=non_brand_ratio,
        ):
            result = score_recommendation(ctx)
            if result is None:
                # Schema below confidence floor — drop score; rec row stays
                dropped += 1
                await db.execute(
                    update(PageReviewRecommendation)
                    .where(PageReviewRecommendation.id == rec_id)
                    .values(
                        priority_score=None,
                        impact_score=None,
                        confidence_score=None,
                        ease_score=None,
                        scored_at=datetime.now(timezone.utc),
                        scorer_version=self.scorer_version,
                    )
                )
                continue
            await db.execute(
                update(PageReviewRecommendation)
                .where(PageReviewRecommendation.id == rec_id)
                .values(
                    priority_score=result.priority_score,
                    impact_score=result.impact,
                    confidence_score=result.confidence,
                    ease_score=result.ease,
                    scored_at=datetime.now(timezone.utc),
                    scorer_version=self.scorer_version,
                )
            )
            scored += 1

        await db.commit()
        return {
            "site_id": str(site_id),
            "scored": scored,
            "dropped": dropped,
            "zeroed_older": zeroed_count,
        }

    # ── Reads ────────────────────────────────────────────────────────

    async def priorities(
        self,
        db: AsyncSession,
        site_id: UUID,
        *,
        top_n: int = 20,
        category: str | None = None,
        priority: str | None = None,
        exclude_statuses: tuple[str, ...] = EXCLUDE_STATUSES_DEFAULT,
        include_dismissed: bool = False,
    ) -> list[PrioritizedItem]:
        """Flat ranked list of recommendations."""
        statuses = () if include_dismissed else exclude_statuses

        rows = await self._load_items(
            db, site_id,
            exclude_statuses=statuses,
            category=category,
            priority=priority,
            limit=top_n,
        )
        return rank(rows)[:top_n]

    async def weekly_plan(
        self,
        db: AsyncSession,
        site_id: UUID,
        *,
        top_n: int = 10,
        max_per_page: int = MAX_PER_PAGE_DEFAULT,
    ) -> WeeklyPlan:
        rows = await self._load_items(
            db, site_id,
            exclude_statuses=EXCLUDE_STATUSES_DEFAULT,
            category=None,
            priority=None,
            limit=200,                          # wide net for round-robin
        )
        return weekly_plan(rows, top_n=top_n, max_per_page=max_per_page)

    # ── Internals ────────────────────────────────────────────────────

    async def _latest_review_ids(
        self, db: AsyncSession, site_id: UUID,
    ) -> set[UUID]:
        """Return set of PageReview.id that are the most recent completed
        review for their (page_id, target_intent_code) on this site."""
        # window-function in Postgres: pick row_number=1 per page+intent
        # ordered by reviewed_at DESC and status=completed only.
        subq = (
            select(
                PageReview.id.label("id"),
                func.row_number().over(
                    partition_by=(PageReview.page_id, PageReview.target_intent_code),
                    order_by=PageReview.reviewed_at.desc(),
                ).label("rn"),
            )
            .where(
                PageReview.site_id == site_id,
                PageReview.status == "completed",
            )
            .subquery()
        )
        rows = await db.execute(select(subq.c.id).where(subq.c.rn == 1))
        return {r[0] for r in rows}

    async def _score_inputs(
        self,
        db: AsyncSession,
        site_id: UUID,
        latest_review_ids: set[UUID],
        *,
        cluster_by_intent: dict[str, dict] | None = None,
        non_brand_ratio: float | None = None,
    ):
        """Async generator yielding (ScorerContext, rec_id) tuples."""
        cluster_by_intent = cluster_by_intent or {}
        stmt = (
            select(
                PageReviewRecommendation.id,
                PageReviewRecommendation.category,
                PageReviewRecommendation.priority,
                PageReviewRecommendation.user_status,
                PageReviewRecommendation.after_text,
                PageReviewRecommendation.estimated_impact,
                PageReview.target_intent_code,
                PageReview.page_id,
                PageReview.reviewer_model,
                PageReview.top_queries_snapshot,
                PageReview.coverage_decision_id,
            )
            .join(PageReview, PageReview.id == PageReviewRecommendation.review_id)
            .where(
                PageReviewRecommendation.site_id == site_id,
                PageReviewRecommendation.review_id.in_(latest_review_ids),
            )
        )
        rows = (await db.execute(stmt)).all()

        # Preload coverage decisions for impressions
        decision_ids = {r.coverage_decision_id for r in rows if r.coverage_decision_id}
        decisions = {}
        if decision_ids:
            dec_rows = await db.execute(
                select(CoverageDecision.id, CoverageDecision.total_impressions)
                .where(CoverageDecision.id.in_(decision_ids))
            )
            decisions = {d.id: int(d.total_impressions or 0) for d in dec_rows}

        # Preload page intent scores
        page_intent_keys = [(r.page_id, r.target_intent_code) for r in rows]
        page_scores: dict = {}
        if page_intent_keys:
            pid_set = {pid for pid, _ in page_intent_keys}
            pis_rows = await db.execute(
                select(
                    PageIntentScore.page_id,
                    PageIntentScore.intent_code,
                    PageIntentScore.score,
                ).where(PageIntentScore.page_id.in_(pid_set))
            )
            for pid, ic, score in pis_rows:
                page_scores[(pid, ic)] = float(score or 0.0)

        for r in rows:
            signal_type, signal_name = self._split_source_finding_id(r.estimated_impact)
            top_q = self._first_top_query(r.top_queries_snapshot)

            # Phase D — attach target_cluster match for this rec's intent.
            # When flag is off or no cluster found, fields stay None/False
            # and scorer falls back to legacy formula.
            cluster_info = cluster_by_intent.get(r.target_intent_code)
            target_rel = cluster_info["business_relevance"] if cluster_info else None
            is_brand = bool(cluster_info["is_brand"]) if cluster_info else False
            cluster_cov = cluster_info.get("coverage_score") if cluster_info else None

            ctx = ScorerContext(
                category=r.category,
                priority=r.priority,
                user_status=r.user_status,
                has_after_text=bool(r.after_text),
                signal_type=signal_type,
                signal_name=signal_name,
                detector_confidence=None,       # not persisted in v1; falls back to default
                reviewer_model=r.reviewer_model,
                total_impressions_14d=int(decisions.get(r.coverage_decision_id, 0)),
                current_score=page_scores.get((r.page_id, r.target_intent_code), 0.0),
                top_query=top_q,
                target_cluster_relevance=target_rel,
                is_brand_cluster=is_brand,
                site_non_brand_coverage_ratio=non_brand_ratio,
                current_coverage_score=cluster_cov,
            )
            yield ctx, r.id

    async def _load_items(
        self,
        db: AsyncSession,
        site_id: UUID,
        *,
        exclude_statuses: tuple[str, ...],
        category: str | None,
        priority: str | None,
        limit: int,
    ) -> list[PrioritizedItem]:
        latest_ids = await self._latest_review_ids(db, site_id)
        if not latest_ids:
            return []

        conditions = [
            PageReviewRecommendation.site_id == site_id,
            PageReviewRecommendation.review_id.in_(latest_ids),
            PageReviewRecommendation.priority_score.is_not(None),
        ]
        if exclude_statuses:
            conditions.append(PageReviewRecommendation.user_status.notin_(exclude_statuses))
        if category:
            conditions.append(PageReviewRecommendation.category == category)
        if priority:
            conditions.append(PageReviewRecommendation.priority == priority)

        stmt = (
            select(
                PageReviewRecommendation.id,
                PageReviewRecommendation.review_id,
                PageReviewRecommendation.category,
                PageReviewRecommendation.priority,
                PageReviewRecommendation.reasoning_ru,
                PageReviewRecommendation.before_text,
                PageReviewRecommendation.after_text,
                PageReviewRecommendation.user_status,
                PageReviewRecommendation.priority_score,
                PageReviewRecommendation.impact_score,
                PageReviewRecommendation.confidence_score,
                PageReviewRecommendation.ease_score,
                PageReviewRecommendation.scored_at,
                PageReview.page_id,
                PageReview.target_intent_code,
                Page.url,
            )
            .join(PageReview, PageReview.id == PageReviewRecommendation.review_id)
            .outerjoin(Page, Page.id == PageReview.page_id)
            .where(and_(*conditions))
            .order_by(PageReviewRecommendation.priority_score.desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).all()

        return [
            PrioritizedItem(
                recommendation_id=r.id,
                review_id=r.review_id,
                page_id=r.page_id,
                page_url=r.url,
                target_intent_code=r.target_intent_code,
                category=r.category,
                priority=r.priority,
                reasoning_ru=r.reasoning_ru,
                before_text=r.before_text,
                after_text=r.after_text,
                user_status=r.user_status,
                priority_score=float(r.priority_score or 0.0),
                impact=float(r.impact_score or 0.0),
                confidence=float(r.confidence_score or 0.0),
                ease=float(r.ease_score or 0.0),
                scored_at=r.scored_at,
            )
            for r in rows
        ]

    # ── Phase D — site-level target-cluster context ──────────────────

    async def _build_phase_d_site_context(
        self, db: AsyncSession, site_id: UUID
    ) -> tuple[dict[str, dict], float | None]:
        """Return (cluster_by_intent, non_brand_coverage_ratio).

        cluster_by_intent maps intent_code -> best matching target_cluster
        (max business_relevance): {business_relevance, is_brand,
        coverage_score}. coverage_score comes from the Phase C analyzer
        output for the cluster's intent_code.
        non_brand_coverage_ratio: len(non_brand where coverage_score >=
        0.6) / len(non_brand). None when the site has no non-brand
        clusters at all.
        """
        # Phase C analyzer gives us per-cluster coverage_score; reuse it
        # both for the cluster lookup and for the non-brand ratio (Option
        # A in the plan — keeps things simple, no extra SQL pass).
        analyzer = CoverageAnalyzer()
        reports = await analyzer.analyze_site(
            db, site_id, mode="target_clusters"
        )

        # Cluster-by-intent: pick the report with highest business_relevance.
        by_intent: dict[str, dict] = {}
        for rep in reports:
            if rep.target_cluster_id is None:
                continue
            ic = rep.intent_code.value
            current = by_intent.get(ic)
            rel = float(rep.business_relevance or 0.0)
            if current is None or rel > current["business_relevance"]:
                by_intent[ic] = {
                    "business_relevance": rel,
                    "is_brand": bool(rep.is_brand_cluster),
                    "coverage_score": (
                        float(rep.coverage_score)
                        if rep.coverage_score is not None
                        else 0.0
                    ),
                }

        # Site-level non-brand coverage ratio.
        non_brand = [r for r in reports if not bool(r.is_brand_cluster)]
        if not non_brand:
            non_brand_ratio: float | None = None
        else:
            covered = sum(
                1 for r in non_brand
                if (r.coverage_score or 0.0) >= 0.6
            )
            non_brand_ratio = covered / len(non_brand)

        return by_intent, non_brand_ratio

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _first_top_query(snapshot: dict | None) -> str | None:
        if not snapshot:
            return None
        q = snapshot.get("queries")
        if not q:
            return None
        first = q[0]
        if isinstance(first, dict):
            return first.get("text")
        return str(first)

    @staticmethod
    def _split_source_finding_id(_estimated_impact: dict | None) -> tuple[str | None, str | None]:
        """source_finding_id encoding: 'signal_type' or 'signal_type:name'.

        v1 does not persist it on the ORM row — we derive signal_type from
        the category/signal naming convention. Future: add a column.
        Returns (signal_type, signal_name) best-effort.
        """
        # TODO: persist source_finding_id on PageReviewRecommendation
        # For now returns (None, None) — scorer falls back to category defaults.
        return None, None
