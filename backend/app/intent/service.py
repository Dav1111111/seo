"""IntentService — orchestrates classification of queries + pages for a site."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.intent.classifier import classify_query
from app.intent.enums import IntentCode
from app.intent.models import PageIntentScore, QueryIntent
from app.intent.page_classifier import score_page_all_intents
from app.models.page import Page
from app.models.search_query import SearchQuery
from app.models.site import Site

logger = logging.getLogger(__name__)

CLASSIFIER_VERSION = "1.0.0"
SCORER_VERSION = "1.0.0"


class IntentService:
    async def classify_site_queries(
        self, db: AsyncSession, site_id: uuid.UUID
    ) -> dict:
        """Classify all queries of a site by intent.

        Idempotent — upserts into query_intents. Returns stats.
        """
        t0 = time.monotonic()
        now = datetime.now(timezone.utc)

        # Get site brand tokens
        site_row = await db.execute(select(Site).where(Site.id == site_id))
        site = site_row.scalar_one_or_none()
        known_brands = None
        if site and site.display_name:
            known_brands = [site.display_name.lower()]
            # Also add domain as brand
            if site.domain:
                known_brands.append(site.domain.split(".")[0].lower())

        # Fetch queries
        rows = await db.execute(
            select(SearchQuery.id, SearchQuery.query_text)
            .where(SearchQuery.site_id == site_id)
        )
        queries = [(q_id, q_text) for q_id, q_text in rows]

        stats = {
            "total": len(queries),
            "classified": 0,
            "ambiguous": 0,
            "by_intent": {intent.value: 0 for intent in IntentCode},
        }

        for query_id, query_text in queries:
            result = classify_query(query_text, known_brands=known_brands)
            try:
                await db.execute(
                    pg_insert(QueryIntent).values(
                        query_id=query_id,
                        site_id=site_id,
                        intent_code=result.intent.value,
                        confidence=result.confidence,
                        matched_pattern=result.matched_pattern,
                        is_brand=result.is_brand,
                        is_ambiguous=result.is_ambiguous,
                        classifier_source="regex",
                        classifier_version=CLASSIFIER_VERSION,
                        classified_at=now,
                    ).on_conflict_do_update(
                        index_elements=["query_id"],
                        set_={
                            "intent_code": result.intent.value,
                            "confidence": result.confidence,
                            "matched_pattern": result.matched_pattern,
                            "is_brand": result.is_brand,
                            "is_ambiguous": result.is_ambiguous,
                            "classifier_source": "regex",
                            "classifier_version": CLASSIFIER_VERSION,
                            "classified_at": now,
                            "updated_at": now,
                        },
                    )
                )
                stats["classified"] += 1
                if result.is_ambiguous:
                    stats["ambiguous"] += 1
                stats["by_intent"][result.intent.value] += 1
            except Exception as exc:
                logger.warning("query classification upsert failed q=%s: %s", query_id, exc)

        await db.commit()
        stats["duration_ms"] = int((time.monotonic() - t0) * 1000)
        logger.info("classify_site_queries site=%s stats=%s", site_id, stats)
        return stats

    async def score_site_pages(
        self, db: AsyncSession, site_id: uuid.UUID
    ) -> dict:
        """Score all pages of a site against all intents (N × 10 rows)."""
        t0 = time.monotonic()
        now = datetime.now(timezone.utc)

        # Fetch pages
        rows = await db.execute(
            select(
                Page.id, Page.path, Page.title, Page.h1,
                Page.meta_description, Page.content_text,
                Page.word_count, Page.has_schema, Page.images_count,
            ).where(Page.site_id == site_id)
        )
        pages = [dict(r._mapping) for r in rows]

        stats = {
            "pages_total": len(pages),
            "pages_scored": 0,
            "scores_written": 0,
        }

        for p in pages:
            scores = score_page_all_intents(
                path=p["path"],
                title=p["title"],
                h1=p["h1"],
                content_text=p["content_text"],
                word_count=p["word_count"],
                has_schema=p["has_schema"] or False,
                images_count=p["images_count"],
            )

            # Delete old scores for this page, re-insert
            await db.execute(
                delete(PageIntentScore).where(PageIntentScore.page_id == p["id"])
            )

            for intent_code, score_obj in scores.items():
                new_row = PageIntentScore(
                    page_id=p["id"],
                    site_id=site_id,
                    intent_code=intent_code.value,
                    score=score_obj.score,
                    s1_heading=score_obj.s1_heading,
                    s2_content=score_obj.s2_content,
                    s3_structure=score_obj.s3_structure,
                    s4_cta=score_obj.s4_cta,
                    s5_schema=score_obj.s5_schema,
                    s6_eeat=score_obj.s6_eeat,
                    scorer_version=SCORER_VERSION,
                    scored_at=now,
                )
                db.add(new_row)
                stats["scores_written"] += 1

            stats["pages_scored"] += 1

        await db.commit()
        stats["duration_ms"] = int((time.monotonic() - t0) * 1000)
        logger.info("score_site_pages site=%s stats=%s", site_id, stats)
        return stats
