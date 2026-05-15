from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.brain.snapshot import _queries, _review, build_snapshot
from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.models.page import Page
from app.models.page_deep_extract import PageDeepExtract
from app.models.search_query import SearchQuery
from app.models.site import Site


pytestmark = pytest.mark.asyncio


async def test_review_snapshot_uses_latest_completed_reviews_per_intent(
    db: AsyncSession,
    test_site: Site,
) -> None:
    page = Page(site_id=test_site.id, url="https://x/a", path="/a")
    skipped_only_page = Page(site_id=test_site.id, url="https://x/b", path="/b")
    db.add_all([page, skipped_only_page])
    await db.flush()

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    old_info = PageReview(
        site_id=test_site.id,
        page_id=page.id,
        target_intent_code="info",
        composite_hash="old-info",
        status="completed",
        reviewed_at=base,
    )
    latest_info = PageReview(
        site_id=test_site.id,
        page_id=page.id,
        target_intent_code="info",
        composite_hash="latest-info",
        status="completed",
        reviewed_at=base + timedelta(days=1),
    )
    latest_commercial = PageReview(
        site_id=test_site.id,
        page_id=page.id,
        target_intent_code="commercial",
        composite_hash="latest-commercial",
        status="completed",
        reviewed_at=base + timedelta(days=2),
    )
    skipped_newer = PageReview(
        site_id=test_site.id,
        page_id=page.id,
        target_intent_code="info",
        composite_hash="skipped-info",
        status="skipped",
        skip_reason="unchanged_hash",
        reviewed_at=base + timedelta(days=3),
    )
    skipped_only = PageReview(
        site_id=test_site.id,
        page_id=skipped_only_page.id,
        target_intent_code="info",
        composite_hash="skipped-only",
        status="skipped",
        skip_reason="unchanged_hash",
        reviewed_at=base + timedelta(days=4),
    )
    db.add_all([
        old_info,
        latest_info,
        latest_commercial,
        skipped_newer,
        skipped_only,
    ])
    await db.flush()

    db.add_all([
        PageReviewRecommendation(
            site_id=test_site.id,
            review_id=old_info.id,
            category="title",
            priority="critical",
            user_status="pending",
            priority_score=99,
            reasoning_ru="old recommendation must not reach chat",
        ),
        PageReviewRecommendation(
            site_id=test_site.id,
            review_id=latest_info.id,
            category="title",
            priority="high",
            user_status="pending",
            priority_score=8,
            impact_score=0.7,
            confidence_score=0.8,
            ease_score=0.9,
            source_finding_id="title_length",
            reasoning_ru="current info recommendation",
        ),
        PageReviewRecommendation(
            site_id=test_site.id,
            review_id=latest_commercial.id,
            category="h1",
            priority="medium",
            user_status="pending",
            priority_score=6,
            reasoning_ru="current commercial recommendation",
        ),
        PageReviewRecommendation(
            site_id=test_site.id,
            review_id=skipped_newer.id,
            category="title",
            priority="critical",
            user_status="pending",
            priority_score=100,
            reasoning_ru="skipped recommendation must not reach chat",
        ),
        PageReviewRecommendation(
            site_id=test_site.id,
            review_id=skipped_only.id,
            category="title",
            priority="critical",
            user_status="pending",
            priority_score=100,
            reasoning_ru="skipped-only recommendation must not reach chat",
        ),
    ])
    await db.flush()

    facts = await _review(db, test_site.id)

    assert facts.pages_with_review == 1
    assert facts.pages_without_review == 1
    assert facts.recs_pending == 2
    assert facts.recs_high_priority_pending == 1
    assert facts.sample_unreviewed_urls == ["https://x/b"]
    assert [r["reasoning_ru"] for r in facts.top_pending_recommendations] == [
        "current info recommendation",
        "current commercial recommendation",
    ]
    assert facts.top_pending_recommendations[0]["source_finding_id"] == "title_length"
    assert facts.top_pending_recommendations[0]["impact_score"] == 0.7
    assert facts.top_pending_recommendations[0]["confidence_score"] == 0.8
    assert facts.top_pending_recommendations[0]["ease_score"] == 0.9


async def test_review_snapshot_marks_recs_when_deep_extract_is_newer(
    db: AsyncSession,
    test_site: Site,
) -> None:
    page = Page(site_id=test_site.id, url="https://x/a", path="/a")
    db.add(page)
    await db.flush()

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    review = PageReview(
        site_id=test_site.id,
        page_id=page.id,
        target_intent_code="commercial",
        composite_hash="commercial-old",
        status="completed",
        reviewed_at=base,
    )
    db.add(review)
    await db.flush()

    db.add(
        PageReviewRecommendation(
            site_id=test_site.id,
            review_id=review.id,
            category="schema",
            priority="high",
            user_status="pending",
            priority_score=10,
            source_finding_id="schema.product_missing",
            reasoning_ru="Добавить Product + Offer",
        )
    )
    db.add(
        PageDeepExtract(
            site_id=test_site.id,
            page_id=page.id,
            url=page.url,
            status="completed",
            extracted_at=base + timedelta(days=1),
            title="Свежий title",
            h1="Свежий H1",
            full_text="Тур по Абхазии от 24900 рублей.",
            performance={"lcp": 3276, "cls": 0},
            js_errors=[],
            schema_blocks=[
                {
                    "@context": "https://schema.org",
                    "@type": "Product",
                    "name": "Багги-экспедиция",
                    "offers": {
                        "@type": "Offer",
                        "price": 24900,
                        "priceCurrency": "RUB",
                    },
                },
            ],
        )
    )
    await db.flush()

    facts = await _review(db, test_site.id)
    rec = facts.top_pending_recommendations[0]
    current = rec["current_snapshot"]

    assert facts.recs_with_fresh_snapshot_after_review == 1
    assert current["after_review"] is True
    assert current["title"] == "Свежий title"
    assert current["h1"] == "Свежий H1"
    assert current["lcp_ms"] == 3276
    assert current["js_error_count"] == 0
    assert "Product" in current["schema_types"]
    assert current["freshness_warning"] == "latest_browser_snapshot_is_newer_than_review"


# ── robots.txt audit signal pulled into BrainSnapshot ────────────────


async def test_snapshot_includes_robots_fields_with_defaults(
    db: AsyncSession, test_site: Site,
) -> None:
    """No robots_audit event for the site → snapshot reports the
    safe defaults: 0 critical issues, valid_for_yandex=True. This
    is the «never ran» state — rule layer stays silent."""
    snap = await build_snapshot(db, test_site)
    assert snap.robots_critical_issues == 0
    assert snap.robots_valid_for_yandex is True


async def test_snapshot_reads_robots_from_latest_event(
    db: AsyncSession, test_site: Site,
) -> None:
    """Latest robots_audit row drives the snapshot fields.

    Seed an older event with one critical, then a newer event with
    two criticals + one warning + valid_for_yandex=False. The
    snapshot must reflect the newer row only: critical count == 2,
    valid_for_yandex == False.
    """
    from app.models.analysis_event import AnalysisEvent

    db.add(AnalysisEvent(
        site_id=test_site.id,
        stage="robots_audit",
        status="done",
        message="older audit",
        extra={
            "valid_for_yandex": True,
            "issues": [
                {"severity": "critical", "code": "ignored-old"},
            ],
        },
        ts=datetime(2020, 1, 1, tzinfo=timezone.utc),
    ))
    db.add(AnalysisEvent(
        site_id=test_site.id,
        stage="robots_audit",
        status="done",
        message="latest audit",
        extra={
            "valid_for_yandex": False,
            "issues": [
                {"severity": "critical", "code": "x1"},
                {"severity": "critical", "code": "x2"},
                {"severity": "warning", "code": "x3"},
            ],
        },
        ts=datetime(2099, 1, 1, tzinfo=timezone.utc),
    ))
    await db.flush()

    snap = await build_snapshot(db, test_site)
    assert snap.robots_critical_issues == 2
    assert snap.robots_valid_for_yandex is False


# ── Tri-state Wordstat coverage (audit-2026-05-15) ───────────────────


async def test_queries_facts_tri_state_wordstat_counters(
    db: AsyncSession, test_site: Site,
) -> None:
    """QueriesFacts splits Wordstat coverage into three honest counters:
    with_volume_known (any answer), with_demand (volume>0), never_fetched
    (no row). Regression test for the audit-2026-05-15 silent-coverage
    bug — prod showed `with_volume_known=4, total=13` and consumers
    silently treated the other 9 as «no demand»."""
    now = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)

    # 3 queries — Wordstat answered with real demand (volume > 0)
    for i in range(3):
        db.add(SearchQuery(
            site_id=test_site.id,
            query_text=f"phrase-with-demand-{i}",
            relevance="own",
            wordstat_volume=100 + i,
            wordstat_updated_at=now,
        ))
    # 1 query — Wordstat answered «no demand» (volume == 0, updated_at set)
    db.add(SearchQuery(
        site_id=test_site.id,
        query_text="phrase-api-said-zero",
        relevance="own",
        wordstat_volume=0,
        wordstat_updated_at=now,
    ))
    # 9 queries — never fetched yet (both fields NULL)
    for i in range(9):
        db.add(SearchQuery(
            site_id=test_site.id,
            query_text=f"phrase-never-fetched-{i}",
            relevance="own",
            wordstat_volume=None,
            wordstat_updated_at=None,
        ))
    await db.flush()

    facts = await _queries(db, test_site.id)

    assert facts.total == 13
    assert facts.with_volume_known == 4   # 3 with demand + 1 «API said zero»
    assert facts.with_demand == 3         # only those with volume > 0
    assert facts.never_fetched == 9       # both NULLs
    # Backwards-compat alias must equal `with_volume_known`.
    assert facts.with_volume == facts.with_volume_known
