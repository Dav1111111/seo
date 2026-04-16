"""Reviewer orchestrator — runs the full review pipeline for one page.

Pipeline:
  1. ContextBuilder.build → ReviewInput | SkipReason
  2. is_unchanged → skip if previous completed review exists
  3. run_python_checks_with_findings → ReviewResult + findings
  4. enrich_with_llm → ReviewResult with LLM rewrites (or python-only on fail)
  5. persist → PageReview + PageReviewRecommendation rows

Error contract (see Plan): skips/failures write a row, caller continues.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.registry import get_profile
from app.core_audit.review.context_builder import ContextBuilder
from app.core_audit.review.dto import ReviewInput, ReviewResult
from app.core_audit.review.enums import ReviewStatus, SkipReason
from app.core_audit.review.idempotency import is_unchanged
from app.core_audit.review.llm import enrich_with_llm
from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.core_audit.review.run_python_checks import run_python_checks_with_findings
from app.intent.models import CoverageDecision
from app.models.site import Site

logger = logging.getLogger(__name__)

REVIEWER_VERSION = "1.0.0"
DEFAULT_TOP_N = 20
PER_RUN_COST_CAP_USD = 0.10


class Reviewer:
    """Runs reviewer pipeline and persists results."""

    def __init__(self, reviewer_version: str = REVIEWER_VERSION) -> None:
        self.reviewer_version = reviewer_version
        self.builder = ContextBuilder(reviewer_version=reviewer_version)

    # ── Public entry points ─────────────────────────────────────────

    async def review_page(
        self,
        db: AsyncSession,
        page_id: UUID,
        decision_id: UUID | None,
    ) -> ReviewResult:
        """Review a single page end-to-end. Always writes one page_reviews row."""
        t0 = time.monotonic()
        built = await self.builder.build(db, page_id, decision_id)
        if isinstance(built, tuple):
            _, skip = built
            return await self._persist_skip(db, page_id, decision_id, None, skip, t0)

        ri: ReviewInput = built

        if await is_unchanged(db, page_id, ri.composite_hash, self.reviewer_version):
            return await self._persist_skip(db, page_id, decision_id, ri, SkipReason.unchanged_hash, t0)

        profile = get_profile(*(await self._site_profile_keys(db, ri.site_id)))

        try:
            py_out = run_python_checks_with_findings(ri, profile)
        except Exception as exc:
            logger.warning("python checks raised page=%s: %s", page_id, exc)
            return await self._persist_failed(db, ri, decision_id, str(exc), t0)

        try:
            enriched = enrich_with_llm(py_out.result, ri, py_out.findings)
        except Exception as exc:
            logger.warning("llm enrich raised page=%s: %s — keeping python result", page_id, exc)
            enriched = py_out.result

        try:
            return await self._persist_completed(db, ri, decision_id, enriched, t0)
        except IntegrityError:
            await db.rollback()
            logger.info("concurrent review write page=%s hash=%s — skipping", page_id, ri.composite_hash)
            return await self._persist_skip(db, page_id, decision_id, ri, SkipReason.unchanged_hash, t0)
        except Exception as exc:
            await db.rollback()
            logger.exception("persist failed page=%s: %s", page_id, exc)
            return await self._persist_failed(db, ri, decision_id, str(exc), t0)

    async def review_site(
        self,
        db: AsyncSession,
        site_id: UUID,
        *,
        top_n: int = DEFAULT_TOP_N,
    ) -> dict:
        """Review top-N 'strengthen' decisions for a site, ordered by impressions."""
        rows = await db.execute(
            select(
                CoverageDecision.id,
                CoverageDecision.target_page_id,
                CoverageDecision.total_impressions,
            )
            .where(
                CoverageDecision.site_id == site_id,
                CoverageDecision.action == "strengthen",
                CoverageDecision.status == "open",
                CoverageDecision.target_page_id.is_not(None),
            )
            .order_by(CoverageDecision.total_impressions.desc())
            .limit(top_n)
        )
        decisions = [(did, pid) for did, pid, _imp in rows]

        stats = {
            "site_id": str(site_id),
            "candidates": len(decisions),
            "reviewed": 0,
            "skipped": 0,
            "failed": 0,
            "cost_total_usd": 0.0,
            "capped_by_budget": False,
        }
        run_cost = 0.0
        for decision_id, page_id in decisions:
            if run_cost >= PER_RUN_COST_CAP_USD:
                stats["capped_by_budget"] = True
                # Emit explicit skipped row for remaining decisions so they're
                # visible in UI / stats
                try:
                    result = await self._persist_skip(
                        db, page_id, decision_id, None,
                        SkipReason.over_budget_cap, time.monotonic(),
                    )
                    stats["skipped"] += 1
                except Exception:
                    pass
                continue

            try:
                result = await self.review_page(db, page_id, decision_id)
            except Exception as exc:
                logger.exception("review_page unhandled page=%s: %s", page_id, exc)
                stats["failed"] += 1
                continue

            if result.status == ReviewStatus.completed:
                stats["reviewed"] += 1
                run_cost += float(result.cost_usd or 0.0)
                stats["cost_total_usd"] = round(run_cost, 6)
            elif result.status == ReviewStatus.skipped:
                stats["skipped"] += 1
            elif result.status == ReviewStatus.failed:
                stats["failed"] += 1

        return stats

    # ── Internal persistence ────────────────────────────────────────

    async def _site_profile_keys(self, db: AsyncSession, site_id: UUID) -> tuple[str, str]:
        row = await db.execute(select(Site).where(Site.id == site_id))
        site = row.scalar_one_or_none()
        if site is None:
            return "tourism", "tour_operator"
        return site.vertical, site.business_model

    async def _persist_skip(
        self,
        db: AsyncSession,
        page_id: UUID,
        decision_id: UUID | None,
        ri: ReviewInput | None,
        skip_reason: SkipReason,
        t0: float,
    ) -> ReviewResult:
        """Write a single PageReview row with status='skipped'."""
        site_id, intent_code, composite_hash = self._identity(ri, page_id, skip_reason)
        row = PageReview(
            id=uuid4(),
            page_id=page_id,
            site_id=site_id,
            coverage_decision_id=decision_id,
            target_intent_code=intent_code,
            composite_hash=composite_hash,
            reviewer_model="python-only",
            reviewer_version=self.reviewer_version,
            status=ReviewStatus.skipped.value,
            skip_reason=skip_reason.value,
            cost_usd=0,
            input_tokens=0,
            output_tokens=0,
            duration_ms=int((time.monotonic() - t0) * 1000),
            top_queries_snapshot=self._queries_snapshot(ri),
            page_level_summary=None,
            reviewed_at=datetime.now(timezone.utc),
        )
        db.add(row)
        try:
            await db.commit()
        except IntegrityError:
            # unique-key collision — another writer recorded a skip/complete for
            # same (page, hash, version). Drop silently.
            await db.rollback()
            logger.info("skip row collision page=%s reason=%s", page_id, skip_reason.value)
        logger.info("review skipped page=%s reason=%s", page_id, skip_reason.value)
        return _result_for_skip(page_id, site_id, intent_code, composite_hash, skip_reason)

    async def _persist_failed(
        self,
        db: AsyncSession,
        ri: ReviewInput,
        decision_id: UUID | None,
        error: str,
        t0: float,
    ) -> ReviewResult:
        row = PageReview(
            id=uuid4(),
            page_id=ri.page_id,
            site_id=ri.site_id,
            coverage_decision_id=decision_id,
            target_intent_code=ri.target_intent.value,
            composite_hash=ri.composite_hash,
            reviewer_model="python-only",
            reviewer_version=self.reviewer_version,
            status=ReviewStatus.failed.value,
            skip_reason=None,
            error=error[:2000],
            cost_usd=0,
            input_tokens=0,
            output_tokens=0,
            duration_ms=int((time.monotonic() - t0) * 1000),
            top_queries_snapshot=self._queries_snapshot(ri),
            page_level_summary=None,
            reviewed_at=datetime.now(timezone.utc),
        )
        db.add(row)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
        return ReviewResult(
            page_id=ri.page_id, site_id=ri.site_id, target_intent=ri.target_intent,
            composite_hash=ri.composite_hash, status=ReviewStatus.failed,
            reviewer_model="python-only", reviewer_version=self.reviewer_version,
            error=error,
        )

    async def _persist_completed(
        self,
        db: AsyncSession,
        ri: ReviewInput,
        decision_id: UUID | None,
        enriched: ReviewResult,
        t0: float,
    ) -> ReviewResult:
        duration_ms = int((time.monotonic() - t0) * 1000)
        page_review_id = uuid4()
        review = PageReview(
            id=page_review_id,
            page_id=ri.page_id,
            site_id=ri.site_id,
            coverage_decision_id=decision_id,
            target_intent_code=ri.target_intent.value,
            composite_hash=ri.composite_hash,
            reviewer_model=enriched.reviewer_model,
            reviewer_version=self.reviewer_version,
            status=ReviewStatus.completed.value,
            skip_reason=None,
            cost_usd=float(enriched.cost_usd or 0.0),
            input_tokens=int(enriched.input_tokens or 0),
            output_tokens=int(enriched.output_tokens or 0),
            duration_ms=duration_ms,
            top_queries_snapshot=self._queries_snapshot(ri),
            page_level_summary=_summary_to_jsonb(enriched.summary),
            reviewed_at=datetime.now(timezone.utc),
        )
        db.add(review)
        await db.flush()

        for rec in enriched.recommendations:
            db.add(PageReviewRecommendation(
                id=uuid4(),
                review_id=page_review_id,
                site_id=ri.site_id,
                category=rec.category.value,
                priority=rec.priority.value,
                before_text=rec.before,
                after_text=rec.after,
                reasoning_ru=rec.reasoning_ru,
                estimated_impact=rec.estimated_impact,
                user_status="pending",
            ))
        await db.commit()
        return replace(enriched, duration_ms=duration_ms)

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _identity(
        ri: ReviewInput | None,
        page_id: UUID,
        skip_reason: SkipReason,
    ) -> tuple[UUID, str, str]:
        """Supply (site_id, intent_code, composite_hash) for the skip row.

        When ri is None (ContextBuilder failed early), we have no intent and
        no hash. Fabricate a placeholder hash so the unique constraint doesn't
        fire on multiple early-skip rows for the same page.
        """
        if ri is not None:
            return ri.site_id, ri.target_intent.value, ri.composite_hash
        placeholder = f"skipped:{skip_reason.value}:{uuid4().hex}"
        # site_id is None in this path — callers that expect it should resolve
        # via the page. Keep as dummy UUID by loading when needed.
        return page_id, "unknown", placeholder[:64]

    @staticmethod
    def _queries_snapshot(ri: ReviewInput | None) -> dict | None:
        if ri is None or not ri.top_queries:
            return None
        return {"queries": [{"text": q, "impressions": None} for q in ri.top_queries]}


# ── Module helpers ────────────────────────────────────────────────────

def _summary_to_jsonb(summary) -> dict | None:
    if summary is None:
        return None
    from dataclasses import asdict
    d = asdict(summary)
    # tuples of strings → lists for JSON
    for k in ("missing_h2_blocks", "missing_eeat_signals", "missing_commercial_factors"):
        if k in d and isinstance(d[k], tuple):
            d[k] = list(d[k])
    return d


def _result_for_skip(
    page_id: UUID, site_id: UUID, intent_code: str, composite_hash: str, skip: SkipReason,
) -> ReviewResult:
    from app.core_audit.intent_codes import IntentCode
    try:
        intent_enum = IntentCode(intent_code)
    except ValueError:
        intent_enum = IntentCode.INFO_DEST
    return ReviewResult(
        page_id=page_id,
        site_id=site_id,
        target_intent=intent_enum,
        composite_hash=composite_hash,
        status=ReviewStatus.skipped,
        reviewer_model="python-only",
        reviewer_version=REVIEWER_VERSION,
        skip_reason=skip,
    )
