"""Builds an immutable ReviewInput from a (page_id, coverage_decision_id) pair.

Returns `ReviewInput` on success or `(None, SkipReason)` when the review
should not proceed. Unexpected infra errors (DB failures, missing profile
with no fallback) propagate as exceptions — caller writes a `failed` row.

Skip branches (each logged by caller):
  page_deleted       — Page row missing or http_status in {404,410}
                       or last_seen_at > 30d ago
  no_fingerprint     — no PageFingerprint row for this page
  missing_content    — fingerprint.content_length_tokens < 50
  not_strengthen     — CoverageDecision.action != 'strengthen'
  no_profile_rules   — profile.page_requirements has no entry for target intent
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.intent_codes import IntentCode
from app.core_audit.registry import get_profile
from app.core_audit.review.dto import LinkCandidate, ReviewInput
from app.core_audit.review.enums import SkipReason
from app.core_audit.review.hash_utils import compute_composite_hash
from app.fingerprint.models import PageFingerprint
from app.intent.models import CoverageDecision, PageIntentScore, QueryIntent
from app.models.daily_metric import DailyMetric
from app.models.page import Page
from app.models.search_query import SearchQuery
from app.models.site import Site

logger = logging.getLogger(__name__)

STALE_PAGE_DAYS = 30
MIN_CONTENT_TOKENS = 50
TOP_QUERIES_LIMIT = 5
LOOKBACK_DAYS = 14
LINK_CANDIDATES_LIMIT = 10


class ContextBuilder:
    """Assembles a ReviewInput snapshot from DB state."""

    def __init__(self, reviewer_version: str = "1.0.0", lookback_days: int = LOOKBACK_DAYS) -> None:
        self.reviewer_version = reviewer_version
        self.lookback_days = lookback_days

    async def build(
        self,
        db: AsyncSession,
        page_id: UUID,
        coverage_decision_id: UUID | None,
    ) -> ReviewInput | tuple[None, SkipReason]:
        decision = await self._load_decision(db, coverage_decision_id)
        if decision is None:
            return None, SkipReason.not_strengthen
        if decision.action != "strengthen":
            return None, SkipReason.not_strengthen

        try:
            target_intent = IntentCode(decision.intent_code)
        except ValueError:
            return None, SkipReason.no_profile_rules

        page = await self._load_page(db, page_id)
        if page is None or self._is_deleted(page):
            return None, SkipReason.page_deleted

        fingerprint = await self._load_fingerprint(db, page_id)
        if fingerprint is None:
            return None, SkipReason.no_fingerprint
        if (fingerprint.content_length_tokens or 0) < MIN_CONTENT_TOKENS:
            return None, SkipReason.missing_content

        site = await self._load_site(db, page.site_id)
        vertical = site.vertical if site else "tourism"
        business_model = site.business_model if site else "tour_operator"
        profile = get_profile(vertical, business_model)
        if target_intent not in profile.page_requirements:
            return None, SkipReason.no_profile_rules

        top_queries = await self._load_top_queries(db, page.site_id, target_intent.value)
        score = await self._load_page_intent_score(db, page_id, target_intent.value)
        link_candidates = await self._load_link_candidates(page_id)

        composite_hash = compute_composite_hash(
            fingerprint.content_hash,
            page.title,
            page.meta_description,
            page.h1,
            target_intent.value,
        )

        return ReviewInput(
            page_id=page_id,
            site_id=page.site_id,
            coverage_decision_id=decision.id,
            target_intent=target_intent,
            path=page.path or "",
            url=page.url or "",
            title=page.title,
            meta_description=page.meta_description,
            h1=page.h1,
            content_text=page.content_text,
            word_count=page.word_count or 0,
            has_schema=bool(page.has_schema),
            images_count=page.images_count or 0,
            content_hash=fingerprint.content_hash,
            composite_hash=composite_hash,
            h2_blocks=self._extract_h2_blocks(page),
            lemmas=(),                              # populated on demand in Step 3
            link_candidates=link_candidates,
            last_updated_at=page.last_crawled_at,
            lang=fingerprint.content_language or "ru",
            top_queries=top_queries,
            current_score=float(score.score) if score else 0.0,
            s1_heading=float(score.s1_heading) if score else 0.0,
            s2_content=float(score.s2_content) if score else 0.0,
            s3_structure=float(score.s3_structure) if score else 0.0,
            s4_cta=float(score.s4_cta) if score else 0.0,
            s5_schema=float(score.s5_schema) if score else 0.0,
            s6_eeat=float(score.s6_eeat) if score else 0.0,
        )

    # ── Loaders ─────────────────────────────────────────────────────

    async def _load_decision(
        self, db: AsyncSession, decision_id: UUID | None,
    ) -> CoverageDecision | None:
        if decision_id is None:
            return None
        row = await db.execute(
            select(CoverageDecision).where(CoverageDecision.id == decision_id)
        )
        return row.scalar_one_or_none()

    async def _load_page(self, db: AsyncSession, page_id: UUID) -> Page | None:
        row = await db.execute(select(Page).where(Page.id == page_id))
        return row.scalar_one_or_none()

    async def _load_fingerprint(
        self, db: AsyncSession, page_id: UUID,
    ) -> PageFingerprint | None:
        row = await db.execute(
            select(PageFingerprint).where(PageFingerprint.page_id == page_id)
        )
        return row.scalar_one_or_none()

    async def _load_site(self, db: AsyncSession, site_id: UUID) -> Site | None:
        row = await db.execute(select(Site).where(Site.id == site_id))
        return row.scalar_one_or_none()

    async def _load_page_intent_score(
        self, db: AsyncSession, page_id: UUID, intent_code: str,
    ) -> PageIntentScore | None:
        row = await db.execute(
            select(PageIntentScore).where(
                PageIntentScore.page_id == page_id,
                PageIntentScore.intent_code == intent_code,
            )
        )
        return row.scalar_one_or_none()

    async def _load_top_queries(
        self, db: AsyncSession, site_id: UUID, intent_code: str,
    ) -> tuple[str, ...]:
        today = date.today()
        start = today - timedelta(days=self.lookback_days)
        stmt = (
            select(
                SearchQuery.query_text,
                func.coalesce(func.sum(DailyMetric.impressions), 0).label("imp"),
            )
            .join(QueryIntent, QueryIntent.query_id == SearchQuery.id)
            .outerjoin(
                DailyMetric,
                (DailyMetric.dimension_id == SearchQuery.id)
                & (DailyMetric.metric_type == "query_performance")
                & (DailyMetric.date.between(start, today)),
            )
            .where(
                QueryIntent.site_id == site_id,
                QueryIntent.intent_code == intent_code,
                QueryIntent.is_brand.is_(False),
            )
            .group_by(SearchQuery.id, SearchQuery.query_text)
            .order_by(func.coalesce(func.sum(DailyMetric.impressions), 0).desc())
            .limit(TOP_QUERIES_LIMIT)
        )
        rows = await db.execute(stmt)
        return tuple(q for q, _ in rows)

    async def _load_link_candidates(self, page_id: UUID) -> tuple[LinkCandidate, ...]:
        """Pull similarity neighbors from fingerprint so LLM can't hallucinate URLs."""
        try:
            from app.fingerprint import api as fp_api
        except Exception:
            return ()
        try:
            neighbors = await fp_api.find_similar_pages(
                page_id=page_id,
                limit=LINK_CANDIDATES_LIMIT,
                threshold=0.30,
                same_site_only=True,
            )
        except Exception as exc:
            logger.warning("link_candidates load failed page=%s: %s", page_id, exc)
            return ()
        return tuple(
            LinkCandidate(
                url=n["url"],
                anchor_hint=n.get("title") or n.get("h1"),
                similarity=float(n.get("score", 0.0)),
            )
            for n in neighbors
            if n.get("url")
        )

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _is_deleted(page: Page) -> bool:
        if page.http_status in (404, 410):
            return True
        if page.last_seen_at is None:
            return False
        last_seen = page.last_seen_at
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        return last_seen < datetime.now(timezone.utc) - timedelta(days=STALE_PAGE_DAYS)

    @staticmethod
    def _extract_h2_blocks(page: Page) -> tuple[str, ...]:
        """Pull H2 blocks from page.meta if crawler stored them; otherwise empty.

        The existing crawler does not yet emit structured headings — Step 3
        and Step 4 handle an empty tuple gracefully (all H2s flagged missing).
        When the crawler adds an `h2_blocks` list to Page.meta, this starts
        returning real values without any call-site changes.
        """
        meta = page.meta or {}
        blocks = meta.get("h2_blocks")
        if isinstance(blocks, list):
            return tuple(str(b) for b in blocks if b)
        return ()
