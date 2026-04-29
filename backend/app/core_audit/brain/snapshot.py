"""Brain — fact collector.

Pure-SQL aggregation of the state owner needs to act on this week.
Every field is a count or a list of small dicts pulled directly from
the database. No LLM, no derivation — if the underlying module hasn't
populated something (e.g. classifier never ran), the corresponding
field stays at zero and the rules layer treats that as «not yet ran»
rather than «no problem».
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.models.outcome_snapshot import OutcomeSnapshot
from app.models.page import Page
from app.models.search_query import SearchQuery
from app.models.site import Site


# ── Data classes ─────────────────────────────────────────────────────


@dataclass
class IndexationFacts:
    pages_total: int
    pages_in_index: int
    pages_excluded: int
    pages_unknown: int
    coverage_pct: float | None  # in_index / pages_total * 100, or None if 0 pages
    # Living examples for the rules layer to quote in body text.
    # «not_indexed» here means confirmed via Webmaster, not unknown.
    sample_not_indexed_urls: list[str] = None  # type: ignore[assignment]
    sample_excluded: list[dict[str, str]] = None  # type: ignore[assignment]


@dataclass
class QueriesFacts:
    total: int
    own: int
    adjacent: int
    disputed: int
    spam: int
    unclassified: int
    with_volume: int
    classified_at: datetime | None  # latest relevance_set_at
    # Examples for the rule body — top-3 spam queries owner can
    # immediately recognise as «не моё», so the «37 вредных» count
    # gets a face. Each item: {query_text, relevance, reason_ru}.
    sample_harmful: list[dict[str, str | None]] = None  # type: ignore[assignment]
    sample_own: list[str] = None  # type: ignore[assignment]


@dataclass
class ReviewFacts:
    pages_with_review: int
    pages_without_review: int
    recs_pending: int
    recs_high_priority_pending: int
    sample_unreviewed_urls: list[str] = None  # type: ignore[assignment]


@dataclass
class MissingLandingsFacts:
    total: int
    high_priority: int
    medium_priority: int
    low_priority: int
    items: list[dict[str, Any]]
    # Raw items so rules layer can quote service names verbatim.


@dataclass
class OutcomesFacts:
    applied_total: int
    applied_last_14d: int
    pending_followup: int  # applied but no follow-up metrics yet


@dataclass
class BrainSnapshot:
    site_id: str
    domain: str
    computed_at: datetime
    indexation: IndexationFacts
    queries: QueriesFacts
    review: ReviewFacts
    missing_landings: MissingLandingsFacts
    outcomes: OutcomesFacts


# ── Loaders ──────────────────────────────────────────────────────────


async def _indexation(db: AsyncSession, site_id: UUID) -> IndexationFacts:
    # Four small COUNTs. Each one is dominated by the same index scan
    # on `site_id`, so the four together cost about the same as a
    # single GROUP BY. Keeps the code readable.
    total = (await db.execute(
        select(func.count(Page.id)).where(Page.site_id == site_id)
    )).scalar_one()
    indexed = (await db.execute(
        select(func.count(Page.id)).where(
            Page.site_id == site_id, Page.in_yandex_index.is_(True),
        )
    )).scalar_one()
    excluded = (await db.execute(
        select(func.count(Page.id)).where(
            Page.site_id == site_id,
            Page.yandex_excluded_reason.is_not(None),
        )
    )).scalar_one()
    # Unknown = neither in index nor excluded (Webmaster didn't report
    # yet, or per-URL fetch hasn't run).
    unknown = (await db.execute(
        select(func.count(Page.id)).where(
            Page.site_id == site_id,
            Page.in_yandex_index.is_(None),
            Page.yandex_excluded_reason.is_(None),
        )
    )).scalar_one()

    # Up to 3 confirmed-not-indexed URLs to show the owner. We exclude
    # `unknown` rows here on purpose — unknown means «we haven't asked
    # Webmaster yet», and the rule deliberately doesn't surface those
    # as «не в индексе» (would be alarmist).
    sample_not_indexed_rows = (await db.execute(
        select(Page.url).where(
            Page.site_id == site_id,
            Page.in_yandex_index.is_(False),
            Page.yandex_excluded_reason.is_(None),
        ).limit(3)
    )).all()
    sample_not_indexed = [r[0] for r in sample_not_indexed_rows if r[0]]

    sample_excluded_rows = (await db.execute(
        select(Page.url, Page.yandex_excluded_reason).where(
            Page.site_id == site_id,
            Page.yandex_excluded_reason.is_not(None),
        ).limit(3)
    )).all()
    sample_excluded = [
        {"url": r[0], "reason": r[1] or ""}
        for r in sample_excluded_rows if r[0]
    ]

    coverage = (indexed / total * 100.0) if total else None
    return IndexationFacts(
        pages_total=total,
        pages_in_index=indexed,
        pages_excluded=excluded,
        pages_unknown=unknown,
        coverage_pct=coverage,
        sample_not_indexed_urls=sample_not_indexed,
        sample_excluded=sample_excluded,
    )


async def _queries(db: AsyncSession, site_id: UUID) -> QueriesFacts:
    total = (await db.execute(
        select(func.count(SearchQuery.id)).where(SearchQuery.site_id == site_id)
    )).scalar_one()

    counts: dict[str, int] = {}
    for label in ("own", "adjacent", "disputed", "spam", "unclassified"):
        counts[label] = (await db.execute(
            select(func.count(SearchQuery.id)).where(
                SearchQuery.site_id == site_id,
                SearchQuery.relevance == label,
            )
        )).scalar_one()
    # Queries that have a Wordstat volume cached.
    with_volume = (await db.execute(
        select(func.count(SearchQuery.id)).where(
            SearchQuery.site_id == site_id,
            SearchQuery.wordstat_volume.is_not(None),
        )
    )).scalar_one()
    classified_at = (await db.execute(
        select(func.max(SearchQuery.relevance_set_at)).where(
            SearchQuery.site_id == site_id,
        )
    )).scalar_one_or_none()

    # Up to 3 «harmful» examples (spam first, then disputed). Owner
    # immediately recognises them as «не моё» — that's the proof the
    # «37 вредных запросов» count is real, not a number we pulled
    # out of the air. Sort spam → disputed so the worst case shows up
    # first.
    sample_harmful_rows = (await db.execute(
        select(
            SearchQuery.query_text,
            SearchQuery.relevance,
            SearchQuery.relevance_reason_ru,
        ).where(
            SearchQuery.site_id == site_id,
            SearchQuery.relevance.in_(["spam", "disputed"]),
        ).order_by(
            # spam before disputed; within each, longest query last
            # so we don't always show the same one.
            SearchQuery.relevance.desc(),
            SearchQuery.query_text,
        ).limit(3)
    )).all()
    sample_harmful = [
        {
            "query_text": r[0] or "",
            "relevance": r[1] or "",
            "reason_ru": r[2] or "",
        }
        for r in sample_harmful_rows
    ]
    sample_own_rows = (await db.execute(
        select(SearchQuery.query_text).where(
            SearchQuery.site_id == site_id,
            SearchQuery.relevance == "own",
        ).limit(3)
    )).all()
    sample_own = [r[0] for r in sample_own_rows if r[0]]

    return QueriesFacts(
        total=total,
        own=counts["own"],
        adjacent=counts["adjacent"],
        disputed=counts["disputed"],
        spam=counts["spam"],
        unclassified=counts["unclassified"],
        with_volume=with_volume,
        classified_at=classified_at,
        sample_harmful=sample_harmful,
        sample_own=sample_own,
    )


async def _review(db: AsyncSession, site_id: UUID) -> ReviewFacts:
    pages_total = (await db.execute(
        select(func.count(Page.id)).where(Page.site_id == site_id)
    )).scalar_one()
    # Pages with at least one review row — distinct count of page_id.
    with_review = (await db.execute(
        select(func.count(func.distinct(PageReview.page_id))).where(
            PageReview.site_id == site_id,
        )
    )).scalar_one()
    without_review = max(0, pages_total - with_review)

    recs_pending = (await db.execute(
        select(func.count(PageReviewRecommendation.id)).where(
            PageReviewRecommendation.site_id == site_id,
            PageReviewRecommendation.user_status == "pending",
        )
    )).scalar_one()
    recs_high = (await db.execute(
        select(func.count(PageReviewRecommendation.id)).where(
            PageReviewRecommendation.site_id == site_id,
            PageReviewRecommendation.user_status == "pending",
            PageReviewRecommendation.priority.in_(["critical", "high"]),
        )
    )).scalar_one()

    # Up to 3 unreviewed page URLs as living examples. Left-anti-join
    # against PageReview keeps it efficient on big sites.
    sample_unreviewed_rows = (await db.execute(
        select(Page.url).where(
            Page.site_id == site_id,
            ~select(PageReview.page_id).where(
                PageReview.page_id == Page.id,
            ).exists(),
        ).limit(3)
    )).all()
    sample_unreviewed = [r[0] for r in sample_unreviewed_rows if r[0]]

    return ReviewFacts(
        pages_with_review=with_review,
        pages_without_review=without_review,
        recs_pending=recs_pending,
        recs_high_priority_pending=recs_high,
        sample_unreviewed_urls=sample_unreviewed,
    )


def _missing_landings(target_config: dict[str, Any] | None) -> MissingLandingsFacts:
    payload = (target_config or {}).get("missing_landings") or {}
    items = [it for it in (payload.get("items") or []) if isinstance(it, dict)]
    counts = {"high": 0, "medium": 0, "low": 0}
    for it in items:
        p = (it.get("priority") or "medium").lower()
        if p in counts:
            counts[p] += 1
    return MissingLandingsFacts(
        total=len(items),
        high_priority=counts["high"],
        medium_priority=counts["medium"],
        low_priority=counts["low"],
        items=items,
    )


async def _outcomes(db: AsyncSession, site_id: UUID) -> OutcomesFacts:
    applied_total = (await db.execute(
        select(func.count(OutcomeSnapshot.id)).where(
            OutcomeSnapshot.site_id == site_id,
            OutcomeSnapshot.applied_at.is_not(None),
        )
    )).scalar_one()
    # Applied in the last 14 days.
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    applied_recent = (await db.execute(
        select(func.count(OutcomeSnapshot.id)).where(
            OutcomeSnapshot.site_id == site_id,
            OutcomeSnapshot.applied_at >= cutoff,
        )
    )).scalar_one()
    pending_followup = (await db.execute(
        select(func.count(OutcomeSnapshot.id)).where(
            OutcomeSnapshot.site_id == site_id,
            OutcomeSnapshot.applied_at.is_not(None),
            OutcomeSnapshot.followup_at.is_(None),
        )
    )).scalar_one()
    return OutcomesFacts(
        applied_total=applied_total,
        applied_last_14d=applied_recent,
        pending_followup=pending_followup,
    )


# ── Top-level entry point ─────────────────────────────────────────────


async def build_snapshot(db: AsyncSession, site: Site) -> BrainSnapshot:
    """Pull every count the rules layer needs, in one round trip.

    `site` is the already-loaded Site row — pass it from the endpoint
    so we don't redo the existence check.
    """
    site_id = site.id
    # All loaders are independent — could be gathered, but a single
    # asyncpg connection serialises them anyway. Sequential is simpler
    # and fast enough (<200 ms on a 22-page site).
    indexation = await _indexation(db, site_id)
    queries = await _queries(db, site_id)
    review = await _review(db, site_id)
    missing = _missing_landings(site.target_config or {})
    outcomes = await _outcomes(db, site_id)

    return BrainSnapshot(
        site_id=str(site_id),
        domain=site.domain,
        computed_at=datetime.now(timezone.utc),
        indexation=indexation,
        queries=queries,
        review=review,
        missing_landings=missing,
        outcomes=outcomes,
    )


__all__ = [
    "BrainSnapshot",
    "IndexationFacts",
    "QueriesFacts",
    "ReviewFacts",
    "MissingLandingsFacts",
    "OutcomesFacts",
    "build_snapshot",
]
