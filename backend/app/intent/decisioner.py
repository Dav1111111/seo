"""Decisioner — orchestrates full pipeline:

  1. Ensure queries classified (regex, LLM fallback for ambiguous)
  2. Ensure pages scored
  3. Build coverage reports
  4. Run decision tree per intent
  5. Persist CoverageDecision rows

Output is durable — decisions stored in coverage_decisions table
for UI/review by user.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.profiles  # noqa: F401 — triggers profile registration
from app.core_audit.decision_tree import DecisionTree
from app.core_audit.registry import get_profile
from app.intent.classifier import classify_query
from app.intent.coverage import CoverageAnalyzer
from app.intent.enums import IntentCode
from app.intent.llm_classifier import classify_ambiguous_batch
from app.intent.models import CoverageDecision, QueryIntent
from app.intent.service import IntentService
from app.models.search_query import SearchQuery
from app.models.site import Site

logger = logging.getLogger(__name__)

LLM_BATCH_SIZE = 20  # queries per LLM call


class Decisioner:
    """Full pipeline runner."""

    async def run_for_site(
        self,
        db: AsyncSession,
        site_id: uuid.UUID,
        *,
        use_llm_fallback: bool = True,
        rebuild_decisions: bool = True,
    ) -> dict:
        t0 = time.monotonic()
        stats = {"site_id": str(site_id)}

        # Resolve per-site audit profile (vertical + business_model). Fallback
        # to tourism/tour_operator inside get_profile if unknown.
        site_row = await db.execute(select(Site).where(Site.id == site_id))
        site = site_row.scalar_one_or_none()
        vertical = site.vertical if site else "tourism"
        business_model = site.business_model if site else "tour_operator"
        profile = get_profile(vertical, business_model)
        stats["profile"] = {"vertical": vertical, "business_model": business_model}

        # 1. Classify queries
        svc = IntentService()
        classify_stats = await svc.classify_site_queries(db, site_id, profile)
        stats["query_classification"] = classify_stats

        # 2. LLM fallback for ambiguous (if enabled)
        if use_llm_fallback:
            llm_stats = await self._run_llm_fallback(db, site_id)
            stats["llm_fallback"] = llm_stats

        # 3. Score pages
        score_stats = await svc.score_site_pages(db, site_id, profile)
        stats["page_scoring"] = score_stats

        # 4. Build coverage reports
        analyzer = CoverageAnalyzer()
        reports = await analyzer.analyze_site(db, site_id)
        stats["intents_analyzed"] = len(reports)

        # 5. Run decision tree
        tree = DecisionTree()
        now = datetime.now(timezone.utc)

        if rebuild_decisions:
            await db.execute(
                delete(CoverageDecision)
                .where(CoverageDecision.site_id == site_id, CoverageDecision.status == "open")
            )
            await db.commit()

        decisions_by_action = {a: 0 for a in ["create", "strengthen", "merge", "split", "leave", "block_create"]}

        for report in reports:
            # Phase 1: compute decision
            try:
                decision = await tree.decide(db, report, site_id, profile)
            except Exception as exc:
                logger.warning("decide failed for intent %s: %s", report.intent_code.value, exc)
                try:
                    await db.rollback()
                except Exception:
                    pass
                continue

            decisions_by_action[decision.action.value] = decisions_by_action.get(decision.action.value, 0) + 1

            # Phase 2: build evidence + persist
            try:
                evidence_dict: dict = {}
                if decision.standalone_test:
                    evidence_dict["standalone_test"] = {
                        "passed": decision.standalone_test.passed_count,
                        "applicable": decision.standalone_test.applicable_count,
                        "verdict": decision.standalone_test.verdict_pass,
                        "reasoning": decision.standalone_test.reasoning,
                    }
                if decision.safety_verdict:
                    safety_warnings = [
                        {"reason": w.reason, "evidence": w.evidence}
                        for w in decision.safety_verdict.warnings
                    ]
                    evidence_dict["safety"] = {
                        "safe_to_create": decision.safety_verdict.safe_to_create,
                        "blocks": [
                            {"reason": b.reason, "evidence": b.evidence}
                            for b in decision.safety_verdict.blocks
                        ],
                        "warnings": safety_warnings,
                        "alternative_action": decision.safety_verdict.alternative_action,
                    }
                    evidence_dict["safety_warnings"] = safety_warnings
                if decision.evidence:
                    evidence_dict["decision_reasoning"] = decision.evidence
                evidence_dict["top_queries"] = report.top_queries
                evidence_dict["best_page_score"] = report.best_page_score
                evidence_dict["proposed"] = {
                    "title": decision.proposed_title,
                    "url": decision.proposed_url,
                }

                new_row = CoverageDecision(
                    site_id=site_id,
                    intent_code=decision.intent_code.value,
                    cluster_key=decision.cluster_key,
                    action=decision.action.value,
                    coverage_status=report.status.value,
                    justification_ru=decision.justification_ru,
                    target_page_id=decision.target_page_id,
                    proposed_url=decision.proposed_url,
                    queries_in_cluster=decision.queries_count,
                    total_impressions=decision.total_impressions,
                    expected_lift_impressions=decision.expected_lift_impressions,
                    evidence=evidence_dict,
                    status="open",
                    decided_at=now,
                )
                db.add(new_row)
                await db.commit()
            except Exception as exc:
                logger.warning("persist failed for intent %s: %s", report.intent_code.value, exc)
                try:
                    await db.rollback()
                except Exception:
                    pass
        stats["decisions_by_action"] = decisions_by_action
        stats["duration_ms"] = int((time.monotonic() - t0) * 1000)
        logger.info("decisioner done site=%s stats=%s", site_id, stats)
        return stats

    async def _run_llm_fallback(
        self, db: AsyncSession, site_id: uuid.UUID
    ) -> dict:
        """Re-classify ambiguous queries using LLM."""
        # Fetch ambiguous queries that were classified by regex
        rows = await db.execute(
            select(
                QueryIntent.query_id,
                SearchQuery.query_text,
            )
            .join(SearchQuery, SearchQuery.id == QueryIntent.query_id)
            .where(
                QueryIntent.site_id == site_id,
                QueryIntent.is_ambiguous == True,  # noqa: E712
                QueryIntent.classifier_source == "regex",
            )
        )
        ambiguous = [(qid, text) for qid, text in rows]

        if not ambiguous:
            return {"reclassified": 0, "cost_usd": 0}

        # Site brand tokens
        site_row = await db.execute(select(Site).where(Site.id == site_id))
        site = site_row.scalar_one_or_none()
        known_brands = [site.display_name.lower()] if site and site.display_name else None

        reclassified = 0
        # Process in batches
        for i in range(0, len(ambiguous), LLM_BATCH_SIZE):
            batch = ambiguous[i:i + LLM_BATCH_SIZE]
            queries_text = [t for _, t in batch]
            results = classify_ambiguous_batch(queries_text, known_brands=known_brands)

            if len(results) != len(batch):
                continue

            for (qid, _), res in zip(batch, results):
                try:
                    await db.execute(
                        QueryIntent.__table__.update()
                        .where(QueryIntent.query_id == qid)
                        .values(
                            intent_code=res["intent_code"],
                            confidence=res["confidence"],
                            matched_pattern=res.get("reasoning_ru", "")[:200],
                            is_ambiguous=False,
                            classifier_source="llm",
                            classifier_version="1.0.0",
                            classified_at=datetime.now(timezone.utc),
                        )
                    )
                    reclassified += 1
                except Exception as exc:
                    logger.warning("LLM reclassify failed for %s: %s", qid, exc)

        await db.commit()
        return {"reclassified": reclassified, "ambiguous_input": len(ambiguous)}
