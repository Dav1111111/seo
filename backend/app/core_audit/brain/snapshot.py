"""Brain — fact collector.

Pure-SQL aggregation of the state owner needs to act on this week.
Every field is a count or a list of small dicts pulled directly from
the database. No LLM, no derivation — if the underlying module hasn't
populated something (e.g. classifier never ran), the corresponding
field stays at zero and the rules layer treats that as «not yet ran»
rather than «no problem».
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.models.analysis_event import AnalysisEvent
from app.models.daily_metric import DailyMetric
from app.models.outcome_snapshot import OutcomeSnapshot
from app.models.page import Page
from app.models.search_query import SearchQuery
from app.models.site import Site


# Keep free-chat factual without turning every request into a full dump
# on very large sites. grandtourspirit.ru has 90 pending recs, so this
# passes the whole current set.
REVIEW_RECOMMENDATIONS_CONTEXT_LIMIT = 150

# Sample sizes — must match `free_chat.py` constants. Mismatch (snapshot
# returns 3, free_chat reads [:8]) means the LLM gets fewer examples
# than the prompt advertises. Keep these centralised here.
SAMPLE_URL_LIMIT = 5      # paired with free_chat.URL_EXAMPLES_LIMIT
SAMPLE_HARMFUL_LIMIT = 8  # paired with free_chat.HARMFUL_EXAMPLES_LIMIT

# Stale-data threshold for JSONB-cached snapshots (missing_landings,
# competitor_profile, growth_opportunities). Past this, brain marks the
# source as stale so rules + free-chat can warn the LLM/owner instead
# of presenting old conclusions as current.
JSONB_STALE_AFTER_DAYS = 30


def _parse_computed_at(value: Any) -> datetime | None:
    """Parse ISO-8601 timestamps from JSONB. Returns None on anything
    we don't recognise — caller treats None as «never computed»."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        # Python's fromisoformat handles "+00:00" but historically
        # not the trailing "Z" — normalise both.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _stale_days(ts: datetime | None) -> int | None:
    """Days since `ts`. None on missing input."""
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    return max(0, delta.days)


# ── Data classes ─────────────────────────────────────────────────────


@dataclass
class IndexationFacts:
    pages_total: int
    pages_in_index: int
    pages_excluded: int
    pages_unknown: int
    coverage_pct: float | None  # in_index / pages_total * 100, or None if 0 pages
    checked_pages: int = 0
    last_checked_at: datetime | None = None
    latest_indexing_date: date | None = None
    latest_pages_indexed_metric: int | None = None
    latest_indexing_extra: dict[str, Any] | None = None
    latest_search_events_date: date | None = None
    latest_pages_in_search_metric: int | None = None
    latest_search_events_extra: dict[str, Any] | None = None
    non_200_count: int = 0
    noindex_count: int = 0
    not_in_sitemap_count: int = 0
    canonical_missing_count: int = 0
    canonical_external_count: int = 0
    canonical_mismatch_count: int = 0
    low_word_count_count: int = 0
    missing_title_count: int = 0
    missing_h1_count: int = 0
    # Living examples for the rules layer to quote in body text.
    # «not_indexed» here means confirmed via Webmaster, not unknown.
    sample_not_indexed_urls: list[str] = None  # type: ignore[assignment]
    sample_excluded: list[dict[str, str]] = None  # type: ignore[assignment]
    sample_non_200: list[dict[str, Any]] = None  # type: ignore[assignment]
    sample_noindex: list[str] = None  # type: ignore[assignment]
    sample_not_in_sitemap: list[str] = None  # type: ignore[assignment]
    sample_canonical_issues: list[dict[str, str]] = None  # type: ignore[assignment]
    sample_low_word_count: list[dict[str, Any]] = None  # type: ignore[assignment]


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
    # Compact inventory of pending recommendations passed to free chat.
    # On normal sites this is the full list; on very large sites it is
    # capped by REVIEW_RECOMMENDATIONS_CONTEXT_LIMIT and the formatter
    # states that explicitly.
    top_pending_recommendations: list[dict[str, Any]] = None  # type: ignore[assignment]
    # Same recommendations re-grouped by (category, priority, problem
    # signature) so the assistant can talk about TOPICS (e.g. "title
    # too long on 12 pages") instead of repeating the same reasoning
    # verbatim 12 times. Each group: count, sample URLs, one
    # representative reasoning + after_text, list of rec_ids.
    recommendation_groups: list[dict[str, Any]] = None  # type: ignore[assignment]


@dataclass
class MissingLandingsFacts:
    total: int
    high_priority: int
    medium_priority: int
    low_priority: int
    items: list[dict[str, Any]]
    # Raw items so rules layer can quote service names verbatim.
    computed_at: datetime | None = None
    stale_days: int | None = None
    is_stale: bool = False


@dataclass
class OutcomesFacts:
    applied_total: int
    applied_last_14d: int
    pending_followup: int  # applied but no follow-up metrics yet


@dataclass
class ActivityFacts:
    latest_pipeline_status: str | None
    latest_pipeline_message: str | None
    latest_pipeline_ts: datetime | None
    running_stages: list[dict[str, Any]]
    recent_events: list[dict[str, Any]]


@dataclass
class CompetitorFacts:
    domains: list[str] = field(default_factory=list)
    profile_available: bool = False
    queries_probed: int = 0
    queries_with_results: int = 0
    unique_domains_seen: int = 0
    cost_usd: float = 0.0
    errors: dict[str, int] = field(default_factory=dict)
    top_competitors: list[dict[str, Any]] = field(default_factory=list)
    deep_dive_available: bool = False
    self_signals: dict[str, Any] | None = None
    deep_dive_competitors: list[dict[str, Any]] = field(default_factory=list)
    growth_opportunities: list[dict[str, Any]] = field(default_factory=list)
    # When the SERP-driven discovery last ran. None means «never».
    # Brain rules + free-chat use this to flag stale recommendations
    # so the LLM doesn't present month-old conclusions as «сейчас».
    profile_computed_at: datetime | None = None
    profile_stale_days: int | None = None
    profile_is_stale: bool = False


@dataclass
class BehavioralFacts:
    """Behavioral signals — Yandex weighs them heavily, no published exact weight."""
    ctr_gaps_total: int = 0                    # number of under-clicking queries
    ctr_gaps_critical: int = 0
    ctr_gaps_high: int = 0
    ctr_gaps_medium: int = 0
    sample_gaps: list[dict[str, Any]] = field(default_factory=list)
    impressions_at_risk: int = 0               # sum of impressions across all flagged queries


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
    activity: ActivityFacts = field(
        default_factory=lambda: ActivityFacts(
            latest_pipeline_status=None,
            latest_pipeline_message=None,
            latest_pipeline_ts=None,
            running_stages=[],
            recent_events=[],
        ),
    )
    competitors: CompetitorFacts = field(default_factory=CompetitorFacts)
    behavioral: BehavioralFacts = field(default_factory=BehavioralFacts)
    # robots.txt audit signals — populated from the latest
    # `analysis_events` row with stage="robots_audit". Defaults are
    # «never ran, assume fine»: 0 critical issues, valid_for_yandex=True.
    # Rule layer surfaces critical issues as a top-priority action;
    # when the file is unavailable (valid_for_yandex=False) the brain
    # cannot trust crawl/indexation conclusions and the dedicated rule
    # nudges the owner to look.
    robots_critical_issues: int = 0
    robots_valid_for_yandex: bool = True


# ── Loaders ──────────────────────────────────────────────────────────


async def _indexation(db: AsyncSession, site_id: UUID) -> IndexationFacts:
    # Keep this loader cheap on large sites: counts stay in Postgres,
    # and Python only receives a few LIMITed examples for grounding.
    agg = (await db.execute(text("""
        WITH p AS (
            SELECT
                url,
                http_status,
                in_sitemap,
                word_count,
                title,
                h1,
                meta,
                in_yandex_index,
                yandex_excluded_reason,
                yandex_index_checked_at,
                (http_status IS NULL OR http_status < 400) AS crawl_ok,
                NULLIF(BTRIM(COALESCE(meta->>'canonical_url', '')), '') AS canonical,
                LOWER(REGEXP_REPLACE(SPLIT_PART(url, '/', 3), '^www[.]', '')) AS page_host
            FROM pages
            WHERE site_id = :site_id
        ),
        c AS (
            SELECT
                *,
                LOWER(REGEXP_REPLACE(SPLIT_PART(canonical, '/', 3), '^www[.]', '')) AS canon_host,
                REGEXP_REPLACE(
                    REGEXP_REPLACE(LOWER(COALESCE(canonical, '')), '^https?://(www[.])?', ''),
                    '/+$',
                    ''
                ) AS canonical_norm,
                REGEXP_REPLACE(
                    REGEXP_REPLACE(LOWER(url), '^https?://(www[.])?', ''),
                    '/+$',
                    ''
                ) AS url_norm
            FROM p
        ),
        flags AS (
            SELECT
                *,
                (
                    canonical IS NOT NULL
                    AND canon_host <> ''
                    AND page_host <> ''
                    AND canon_host <> page_host
                ) AS canonical_external
            FROM c
        )
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE in_yandex_index IS TRUE) AS indexed,
            COUNT(*) FILTER (WHERE yandex_excluded_reason IS NOT NULL) AS excluded,
            COUNT(*) FILTER (
                WHERE in_yandex_index IS NULL
                  AND yandex_excluded_reason IS NULL
            ) AS unknown,
            COUNT(*) FILTER (
                WHERE in_yandex_index IS NOT NULL
                   OR yandex_excluded_reason IS NOT NULL
            ) AS checked_pages,
            MAX(yandex_index_checked_at) AS last_checked_at,
            COUNT(*) FILTER (WHERE http_status >= 400) AS non_200_count,
            COUNT(*) FILTER (
                WHERE crawl_ok
                  AND (
                      LOWER(COALESCE(meta->>'noindex', '')) IN ('true', '1', 'yes')
                      OR LOWER(COALESCE(meta->>'meta_robots', '')) LIKE '%noindex%'
                  )
            ) AS noindex_count,
            COUNT(*) FILTER (WHERE crawl_ok AND in_sitemap IS FALSE) AS not_in_sitemap_count,
            COUNT(*) FILTER (WHERE crawl_ok AND canonical IS NULL) AS canonical_missing_count,
            COUNT(*) FILTER (WHERE crawl_ok AND canonical_external) AS canonical_external_count,
            COUNT(*) FILTER (
                WHERE crawl_ok
                  AND canonical IS NOT NULL
                  AND NOT canonical_external
                  AND canonical_norm <> url_norm
            ) AS canonical_mismatch_count,
            COUNT(*) FILTER (
                WHERE crawl_ok
                  AND word_count IS NOT NULL
                  AND word_count < 200
            ) AS low_word_count_count,
            COUNT(*) FILTER (
                WHERE crawl_ok
                  AND BTRIM(COALESCE(title, '')) = ''
            ) AS missing_title_count,
            COUNT(*) FILTER (
                WHERE crawl_ok
                  AND BTRIM(COALESCE(h1, '')) = ''
            ) AS missing_h1_count
        FROM flags
    """), {"site_id": site_id})).mappings().one()

    total = _row_int(agg, "total")
    indexed = _row_int(agg, "indexed")
    excluded = _row_int(agg, "excluded")
    unknown = _row_int(agg, "unknown")

    # Up to 3 confirmed-not-indexed URLs to show the owner. We exclude
    # `unknown` rows here on purpose — unknown means «we haven't asked
    # Webmaster yet», and the rule deliberately doesn't surface those
    # as «не в индексе» (would be alarmist).
    sample_not_indexed_rows = (await db.execute(
        select(Page.url).where(
            Page.site_id == site_id,
            Page.in_yandex_index.is_(False),
            Page.yandex_excluded_reason.is_(None),
        ).limit(SAMPLE_URL_LIMIT)
    )).all()
    sample_not_indexed = [r[0] for r in sample_not_indexed_rows if r[0]]

    sample_excluded_rows = (await db.execute(
        select(Page.url, Page.yandex_excluded_reason).where(
            Page.site_id == site_id,
            Page.yandex_excluded_reason.is_not(None),
        ).limit(SAMPLE_URL_LIMIT)
    )).all()
    sample_excluded = [
        {"url": r[0], "reason": r[1] or ""}
        for r in sample_excluded_rows if r[0]
    ]

    sample_non_200_rows = (await db.execute(
        select(Page.url, Page.http_status).where(
            Page.site_id == site_id,
            Page.http_status >= 400,
        ).limit(SAMPLE_URL_LIMIT)
    )).all()
    sample_non_200 = [
        {"url": r[0], "http_status": int(r[1])}
        for r in sample_non_200_rows
        if r[0] and r[1] is not None
    ]

    sample_noindex_rows = (await db.execute(text("""
        SELECT url
        FROM pages
        WHERE site_id = :site_id
          AND (http_status IS NULL OR http_status < 400)
          AND (
              LOWER(COALESCE(meta->>'noindex', '')) IN ('true', '1', 'yes')
              OR LOWER(COALESCE(meta->>'meta_robots', '')) LIKE '%noindex%'
          )
        LIMIT :sample_limit
    """), {"site_id": site_id, "sample_limit": SAMPLE_URL_LIMIT})).all()
    sample_noindex = [r[0] for r in sample_noindex_rows if r[0]]

    sample_not_in_sitemap_rows = (await db.execute(
        select(Page.url).where(
            Page.site_id == site_id,
            (Page.http_status.is_(None)) | (Page.http_status < 400),
            Page.in_sitemap.is_(False),
        ).limit(SAMPLE_URL_LIMIT)
    )).all()
    sample_not_in_sitemap = [r[0] for r in sample_not_in_sitemap_rows if r[0]]

    sample_low_word_count_rows = (await db.execute(
        select(Page.url, Page.word_count).where(
            Page.site_id == site_id,
            (Page.http_status.is_(None)) | (Page.http_status < 400),
            Page.word_count.is_not(None),
            Page.word_count < 200,
        ).limit(5)
    )).all()
    sample_low_word_count = [
        {"url": r[0], "word_count": int(r[1] or 0)}
        for r in sample_low_word_count_rows if r[0]
    ]
    sample_canonical_issues = await _sample_canonical_issues(db, site_id)

    latest_indexing_row = (await db.execute(
        select(DailyMetric.date, DailyMetric.pages_indexed, DailyMetric.extra)
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "indexing",
        )
        .order_by(DailyMetric.date.desc())
        .limit(1)
    )).first()
    latest_search_row = (await db.execute(
        select(DailyMetric.date, DailyMetric.pages_in_search, DailyMetric.extra)
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "search_events",
        )
        .order_by(DailyMetric.date.desc())
        .limit(1)
    )).first()

    coverage = (indexed / total * 100.0) if total else None
    return IndexationFacts(
        pages_total=total,
        pages_in_index=indexed,
        pages_excluded=excluded,
        pages_unknown=unknown,
        coverage_pct=coverage,
        checked_pages=_row_int(agg, "checked_pages"),
        last_checked_at=agg["last_checked_at"],
        latest_indexing_date=latest_indexing_row[0] if latest_indexing_row else None,
        latest_pages_indexed_metric=(
            int(latest_indexing_row[1])
            if latest_indexing_row and latest_indexing_row[1] is not None
            else None
        ),
        latest_indexing_extra=(
            dict(latest_indexing_row[2] or {}) if latest_indexing_row else None
        ),
        latest_search_events_date=latest_search_row[0] if latest_search_row else None,
        latest_pages_in_search_metric=(
            int(latest_search_row[1])
            if latest_search_row and latest_search_row[1] is not None
            else None
        ),
        latest_search_events_extra=(
            dict(latest_search_row[2] or {}) if latest_search_row else None
        ),
        non_200_count=_row_int(agg, "non_200_count"),
        noindex_count=_row_int(agg, "noindex_count"),
        not_in_sitemap_count=_row_int(agg, "not_in_sitemap_count"),
        canonical_missing_count=_row_int(agg, "canonical_missing_count"),
        canonical_external_count=_row_int(agg, "canonical_external_count"),
        canonical_mismatch_count=_row_int(agg, "canonical_mismatch_count"),
        low_word_count_count=_row_int(agg, "low_word_count_count"),
        missing_title_count=_row_int(agg, "missing_title_count"),
        missing_h1_count=_row_int(agg, "missing_h1_count"),
        sample_not_indexed_urls=sample_not_indexed,
        sample_excluded=sample_excluded,
        sample_non_200=sample_non_200,
        sample_noindex=sample_noindex,
        sample_not_in_sitemap=sample_not_in_sitemap,
        sample_canonical_issues=sample_canonical_issues,
        sample_low_word_count=sample_low_word_count,
    )


async def _sample_canonical_issues(
    db: AsyncSession,
    site_id: UUID,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for kind in ("missing", "external", "mismatch"):
        remaining = 5 - len(out)
        if remaining <= 0:
            break
        rows = (await db.execute(text("""
            WITH p AS (
                SELECT
                    url,
                    (http_status IS NULL OR http_status < 400) AS crawl_ok,
                    NULLIF(BTRIM(COALESCE(meta->>'canonical_url', '')), '') AS canonical,
                    LOWER(REGEXP_REPLACE(SPLIT_PART(url, '/', 3), '^www[.]', '')) AS page_host
                FROM pages
                WHERE site_id = :site_id
            ),
            c AS (
                SELECT
                    *,
                    LOWER(REGEXP_REPLACE(SPLIT_PART(canonical, '/', 3), '^www[.]', '')) AS canon_host,
                    REGEXP_REPLACE(
                        REGEXP_REPLACE(LOWER(COALESCE(canonical, '')), '^https?://(www[.])?', ''),
                        '/+$',
                        ''
                    ) AS canonical_norm,
                    REGEXP_REPLACE(
                        REGEXP_REPLACE(LOWER(url), '^https?://(www[.])?', ''),
                        '/+$',
                        ''
                    ) AS url_norm
                FROM p
            ),
            flags AS (
                SELECT
                    *,
                    (
                        canonical IS NOT NULL
                        AND canon_host <> ''
                        AND page_host <> ''
                        AND canon_host <> page_host
                    ) AS canonical_external
                FROM c
            )
            SELECT
                url,
                :kind AS kind,
                COALESCE(canonical, '') AS canonical
            FROM flags
            WHERE crawl_ok
              AND (
                  (:kind = 'missing' AND canonical IS NULL)
                  OR (:kind = 'external' AND canonical_external)
                  OR (
                      :kind = 'mismatch'
                      AND canonical IS NOT NULL
                      AND NOT canonical_external
                      AND canonical_norm <> url_norm
                  )
              )
            LIMIT :limit
        """), {
            "site_id": site_id,
            "kind": kind,
            "limit": remaining,
        })).mappings().all()
        out.extend([
            {
                "url": row["url"] or "",
                "kind": row["kind"] or kind,
                "canonical": row["canonical"] or "",
            }
            for row in rows
            if row["url"]
        ])
    return out


def _row_int(row: Any, key: str) -> int:
    value = row[key]
    return int(value or 0)


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
        ).limit(SAMPLE_HARMFUL_LIMIT)
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
        ).limit(SAMPLE_URL_LIMIT)
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

    # Current review state must match /studio/pages: latest completed
    # review per (page_id, target_intent_code). Skipped/failed rows can
    # explain pipeline history, but they must not become actionable SEO
    # facts for the assistant.
    latest_completed_reviews = (
        select(
            PageReview.id.label("id"),
            PageReview.page_id.label("page_id"),
            func.row_number().over(
                partition_by=(
                    PageReview.page_id,
                    PageReview.target_intent_code,
                ),
                order_by=(
                    PageReview.reviewed_at.desc(),
                    PageReview.created_at.desc(),
                    PageReview.id.desc(),
                ),
            ).label("rn"),
        )
        .where(
            PageReview.site_id == site_id,
            PageReview.status == "completed",
        )
        .subquery()
    )
    current_review_ids = (
        select(latest_completed_reviews.c.id)
        .where(latest_completed_reviews.c.rn == 1)
    )

    # Pages with at least one current completed review.
    with_review = (await db.execute(
        select(func.count(func.distinct(latest_completed_reviews.c.page_id))).where(
            latest_completed_reviews.c.rn == 1,
        )
    )).scalar_one()
    without_review = max(0, pages_total - with_review)

    recs_pending = (await db.execute(
        select(func.count(PageReviewRecommendation.id))
        .join(
            latest_completed_reviews,
            latest_completed_reviews.c.id == PageReviewRecommendation.review_id,
        )
        .where(
            latest_completed_reviews.c.rn == 1,
            PageReviewRecommendation.site_id == site_id,
            PageReviewRecommendation.user_status == "pending",
        )
    )).scalar_one()
    recs_high = (await db.execute(
        select(func.count(PageReviewRecommendation.id))
        .join(
            latest_completed_reviews,
            latest_completed_reviews.c.id == PageReviewRecommendation.review_id,
        )
        .where(
            latest_completed_reviews.c.rn == 1,
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
                PageReview.status == "completed",
            ).exists(),
        ).limit(SAMPLE_URL_LIMIT)
    )).all()
    sample_unreviewed = [r[0] for r in sample_unreviewed_rows if r[0]]

    top_rec_rows = (await db.execute(
        select(
            PageReviewRecommendation.id,
            PageReviewRecommendation.category,
            PageReviewRecommendation.priority,
            PageReviewRecommendation.priority_score,
            PageReviewRecommendation.reasoning_ru,
            PageReviewRecommendation.before_text,
            PageReviewRecommendation.after_text,
            Page.url,
            PageReview.target_intent_code,
            PageReviewRecommendation.source_finding_id,
            PageReviewRecommendation.impact_score,
            PageReviewRecommendation.confidence_score,
            PageReviewRecommendation.ease_score,
        )
        .join(PageReview, PageReview.id == PageReviewRecommendation.review_id)
        .join(Page, Page.id == PageReview.page_id)
        .where(
            PageReviewRecommendation.site_id == site_id,
            PageReviewRecommendation.user_status == "pending",
            PageReviewRecommendation.review_id.in_(current_review_ids),
        )
        .order_by(
            desc(PageReviewRecommendation.priority_score).nullslast(),
            PageReviewRecommendation.priority,
            Page.url,
            PageReviewRecommendation.category,
        )
        .limit(REVIEW_RECOMMENDATIONS_CONTEXT_LIMIT)
    )).all()
    top_recs = [
        {
            "rec_id": str(r[0]),
            "category": r[1],
            "priority": r[2],
            "priority_score": float(r[3]) if r[3] is not None else None,
            "reasoning_ru": (r[4] or "")[:220],
            "before_text": (r[5] or "")[:120],
            "after_text": (r[6] or "")[:160],
            "url": r[7],
            "target_intent_code": r[8],
            "source_finding_id": r[9],
            "impact_score": float(r[10]) if r[10] is not None else None,
            "confidence_score": float(r[11]) if r[11] is not None else None,
            "ease_score": float(r[12]) if r[12] is not None else None,
        }
        for r in top_rec_rows
    ]

    return ReviewFacts(
        pages_with_review=with_review,
        pages_without_review=without_review,
        recs_pending=recs_pending,
        recs_high_priority_pending=recs_high,
        sample_unreviewed_urls=sample_unreviewed,
        top_pending_recommendations=top_recs,
        recommendation_groups=_group_recommendations(top_recs),
    )


_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _rec_signature(reasoning: str | None, before_text: str | None) -> str:
    """Stable, low-cardinality signature so duplicate problems land in
    the same bucket.

    Uses the first ~70 chars of reasoning_ru, falling back to the first
    50 chars of before_text. Lowercased + collapsed whitespace. The
    cardinality stays low because reasoning_ru is template-generated
    by the Python checks: «title слишком длинный (>90 символов): N»
    differs only by N, but our trim drops the suffix.
    """
    base = (reasoning or "").strip()
    if not base:
        base = (before_text or "").strip()
    base = " ".join(base.split()).lower()
    return base[:70]


def _group_recommendations(
    top_recs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse a flat rec list into (category, priority, signature) groups.

    Output, sorted by (priority, count desc):
      [{
         "category": "title", "priority": "high", "count": 12,
         "sample_urls": ["...", "...", "..."],
         "reasoning_sample": "title слишком длинный (>90 символов)",
         "after_sample": "Сократи до 60-70 символов: ...",
         "rec_ids": [...]
      }, ...]
    """
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in top_recs:
        key = (
            r.get("category") or "",
            r.get("priority") or "",
            _rec_signature(r.get("reasoning_ru"), r.get("before_text")),
        )
        b = buckets.get(key)
        if b is None:
            b = {
                "category": key[0],
                "priority": key[1],
                "signature": key[2],
                "count": 0,
                "sample_urls": [],
                "reasoning_sample": (r.get("reasoning_ru") or "")[:200],
                "after_sample": (r.get("after_text") or "")[:200],
                "rec_ids": [],
            }
            buckets[key] = b
        b["count"] += 1
        url = r.get("url")
        if url and len(b["sample_urls"]) < 5 and url not in b["sample_urls"]:
            b["sample_urls"].append(url)
        rec_id = r.get("rec_id")
        if rec_id and len(b["rec_ids"]) < 25:
            b["rec_ids"].append(rec_id)

    groups = list(buckets.values())
    groups.sort(
        key=lambda g: (
            _PRIORITY_RANK.get(g["priority"], 9),
            -g["count"],
            g["category"],
        ),
    )
    return groups


def _missing_landings(target_config: dict[str, Any] | None) -> MissingLandingsFacts:
    payload = (target_config or {}).get("missing_landings") or {}
    items = [it for it in (payload.get("items") or []) if isinstance(it, dict)]
    counts = {"high": 0, "medium": 0, "low": 0}
    for it in items:
        p = (it.get("priority") or "medium").lower()
        if p in counts:
            counts[p] += 1
    computed_at = _parse_computed_at(payload.get("computed_at"))
    stale_days = _stale_days(computed_at)
    return MissingLandingsFacts(
        total=len(items),
        high_priority=counts["high"],
        medium_priority=counts["medium"],
        low_priority=counts["low"],
        items=items,
        computed_at=computed_at,
        stale_days=stale_days,
        is_stale=stale_days is not None and stale_days >= JSONB_STALE_AFTER_DAYS,
    )


def _competitors(
    target_config: dict[str, Any] | None,
    competitor_domains: list[Any] | None,
) -> CompetitorFacts:
    """Compact persisted competitor intelligence for free chat.

    This is deliberately read-only: discovery/deep-dive modules do the
    network work and persist JSONB; the brain only exposes those facts.
    """
    cfg = target_config or {}
    domains = _normalise_domain_list(competitor_domains or [])

    profile_raw = cfg.get("competitor_profile") or {}
    profile = profile_raw if isinstance(profile_raw, dict) else {}
    raw_competitors = profile.get("competitors") or []
    top_competitors: list[dict[str, Any]] = []
    if isinstance(raw_competitors, list):
        for row in raw_competitors[:10]:
            if not isinstance(row, dict):
                continue
            top_competitors.append({
                "domain": _trim(row.get("domain"), 120),
                "serp_hits": _safe_int(row.get("serp_hits")),
                "best_position": _safe_int(row.get("best_position")),
                "avg_position": _safe_float(row.get("avg_position")),
                "example_query": _trim(row.get("example_query"), 180),
                "example_url": _trim(row.get("example_url"), 260),
                "example_title": _trim(row.get("example_title"), 180),
            })

    errors_raw = profile.get("errors") or {}
    errors = (
        {str(k): _safe_int(v) for k, v in errors_raw.items()}
        if isinstance(errors_raw, dict)
        else {}
    )

    deep_raw = cfg.get("competitor_deep_dive") or {}
    deep = deep_raw if isinstance(deep_raw, dict) else {}
    self_raw = deep.get("self")
    self_signals = (
        _compact_competitor_page(self_raw)
        if isinstance(self_raw, dict)
        else None
    )
    deep_competitors: list[dict[str, Any]] = []
    raw_deep_competitors = deep.get("competitors") or []
    if isinstance(raw_deep_competitors, list):
        for row in raw_deep_competitors[:8]:
            if isinstance(row, dict):
                deep_competitors.append(_compact_competitor_site(row))

    raw_opportunities = cfg.get("growth_opportunities") or []
    opportunities: list[dict[str, Any]] = []
    if isinstance(raw_opportunities, list):
        for row in raw_opportunities[:15]:
            if isinstance(row, dict):
                opportunities.append(_compact_growth_opportunity(row))

    profile_computed_at = _parse_computed_at(profile.get("computed_at"))
    profile_stale_days = _stale_days(profile_computed_at)
    profile_is_stale = (
        profile_stale_days is not None
        and profile_stale_days >= JSONB_STALE_AFTER_DAYS
    )

    return CompetitorFacts(
        domains=domains,
        profile_available=bool(profile),
        queries_probed=_safe_int(profile.get("queries_probed")),
        queries_with_results=_safe_int(profile.get("queries_with_results")),
        unique_domains_seen=_safe_int(profile.get("unique_domains_seen")),
        cost_usd=_safe_float(profile.get("cost_usd")),
        errors=errors,
        top_competitors=top_competitors,
        profile_computed_at=profile_computed_at,
        profile_stale_days=profile_stale_days,
        profile_is_stale=profile_is_stale,
        deep_dive_available=bool(self_signals or deep_competitors),
        self_signals=self_signals,
        deep_dive_competitors=deep_competitors,
        growth_opportunities=opportunities,
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


async def _activity(db: AsyncSession, site_id: UUID) -> ActivityFacts:
    rows = (await db.execute(
        select(AnalysisEvent)
        .where(AnalysisEvent.site_id == site_id)
        .order_by(AnalysisEvent.ts.desc())
        .limit(20)
    )).scalars().all()

    latest_by_stage: dict[str, AnalysisEvent] = {}
    for ev in rows:
        if ev.stage not in latest_by_stage:
            latest_by_stage[ev.stage] = ev

    terminal = {"done", "failed", "skipped"}
    running = [
        {
            "stage": ev.stage,
            "status": ev.status,
            "message": ev.message,
            "ts": ev.ts.isoformat() if ev.ts else None,
        }
        for ev in latest_by_stage.values()
        if ev.status not in terminal
    ]

    latest_pipeline = latest_by_stage.get("pipeline")
    return ActivityFacts(
        latest_pipeline_status=latest_pipeline.status if latest_pipeline else None,
        latest_pipeline_message=latest_pipeline.message if latest_pipeline else None,
        latest_pipeline_ts=latest_pipeline.ts if latest_pipeline else None,
        running_stages=running,
        recent_events=[
            {
                "stage": ev.stage,
                "status": ev.status,
                "message": ev.message,
                "ts": ev.ts.isoformat() if ev.ts else None,
            }
            for ev in rows[:10]
        ],
    )


def _normalise_domain_list(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _trim(item, 160).lower().removeprefix("www.")
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _compact_competitor_page(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": _trim(row.get("url"), 260),
        "status": _trim(row.get("status"), 40),
        "error": _trim(row.get("error"), 120),
        "title": _trim(row.get("title"), 180),
        "h1": _trim(row.get("h1"), 180),
        "meta_description": _trim(row.get("meta_description"), 220),
        "word_count": _safe_int(row.get("word_count")),
        "has_price": bool(row.get("has_price")),
        "has_booking_cta": bool(row.get("has_booking_cta")),
        "has_reviews": bool(row.get("has_reviews")),
        "has_phone": bool(row.get("has_phone")),
        "has_telegram": bool(row.get("has_telegram")),
        "has_whatsapp": bool(row.get("has_whatsapp")),
        "schema_types": _first_strings(row.get("schema_types"), 12, 80),
    }


def _compact_competitor_site(row: dict[str, Any]) -> dict[str, Any]:
    pages = row.get("pages") or []
    compact_pages: list[dict[str, Any]] = []
    if isinstance(pages, list):
        compact_pages = [
            _compact_competitor_page(page)
            for page in pages[:2]
            if isinstance(page, dict)
        ]
    return {
        "domain": _trim(row.get("domain"), 140),
        "has_price": bool(row.get("has_price")),
        "has_booking_cta": bool(row.get("has_booking_cta")),
        "has_reviews": bool(row.get("has_reviews")),
        "has_phone": bool(row.get("has_phone")),
        "has_telegram": bool(row.get("has_telegram")),
        "has_whatsapp": bool(row.get("has_whatsapp")),
        "schema_types": _first_strings(row.get("schema_types"), 12, 80),
        "pages": compact_pages,
    }


def _compact_growth_opportunity(row: dict[str, Any]) -> dict[str, Any]:
    evidence_raw = row.get("evidence") or {}
    evidence = evidence_raw if isinstance(evidence_raw, dict) else {}
    keep_keys = (
        "queries",
        "competitor_domain",
        "competitor_position",
        "competitor_url",
        "competitor_title",
        "site_position",
        "other_competitors",
        "matched_page",
        "feature",
        "competitors_with",
        "share_competitors_with",
        "schema_type",
    )
    compact_evidence: dict[str, Any] = {}
    for key in keep_keys:
        if key not in evidence:
            continue
        value = evidence.get(key)
        if isinstance(value, str):
            compact_evidence[key] = _trim(value, 240)
        elif isinstance(value, list):
            compact_evidence[key] = [
                _trim(v, 160) if isinstance(v, str) else v
                for v in value[:8]
            ]
        elif isinstance(value, dict):
            compact_evidence[key] = {
                str(k): _trim(v, 160) if isinstance(v, str) else v
                for k, v in list(value.items())[:8]
            }
        else:
            compact_evidence[key] = value

    return {
        "id": _trim(row.get("id"), 80),
        "source": _trim(row.get("source"), 60),
        "category": _trim(row.get("category"), 80),
        "priority": _trim(row.get("priority"), 40),
        "title_ru": _trim(row.get("title_ru"), 180),
        "reasoning_ru": _trim(row.get("reasoning_ru"), 260),
        "suggested_action_ru": _trim(row.get("suggested_action_ru"), 260),
        "evidence": compact_evidence,
    }


def _safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _trim(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _first_strings(value: Any, limit: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value[:limit]:
        text = _trim(item, item_limit)
        if text:
            out.append(text)
    return out


# ── Top-level entry point ─────────────────────────────────────────────


async def _behavioral(db: AsyncSession, site_id: UUID) -> BehavioralFacts:
    """Aggregate CTR-gaps for the brain plan.

    Best-effort: if behavioral collectors haven't populated daily_metrics
    yet, returns zeros — the loader never blocks a snapshot build.
    """
    try:
        from app.core_audit.behavioral import scan_ctr_gaps
        gaps = await scan_ctr_gaps(db, site_id, limit=20)
    except Exception:  # noqa: BLE001 — never let this break the snapshot
        return BehavioralFacts()

    if not gaps:
        return BehavioralFacts()

    crit = sum(1 for g in gaps if g.severity == "critical")
    high = sum(1 for g in gaps if g.severity == "high")
    med = sum(1 for g in gaps if g.severity == "medium")
    impressions_at_risk = sum(g.impressions for g in gaps)

    sample = [
        {
            "query": g.query_text,
            "impressions": g.impressions,
            "avg_position": round(g.avg_position, 1),
            "actual_ctr": round(g.actual_ctr * 100, 2),
            "expected_ctr": round(g.expected_ctr * 100, 2),
            "severity": g.severity,
            "wordstat_volume": g.wordstat_volume,
        }
        for g in gaps[:8]
    ]

    return BehavioralFacts(
        ctr_gaps_total=len(gaps),
        ctr_gaps_critical=crit,
        ctr_gaps_high=high,
        ctr_gaps_medium=med,
        sample_gaps=sample,
        impressions_at_risk=impressions_at_risk,
    )


async def _robots_audit_facts(
    db: AsyncSession, site_id: UUID,
) -> tuple[int, bool]:
    """Read the latest `analysis_events` row with stage='robots_audit'.

    Returns (critical_issues, valid_for_yandex). Defaults to
    (0, True) when no event exists — «never ran, assume nothing
    is wrong yet». A done/failed/skipped run with no `extra` payload
    is treated the same as «no signal».
    """
    row = (await db.execute(
        select(AnalysisEvent.extra, AnalysisEvent.status)
        .where(
            AnalysisEvent.site_id == site_id,
            AnalysisEvent.stage == "robots_audit",
        )
        .order_by(AnalysisEvent.ts.desc())
        .limit(1)
    )).first()
    if row is None:
        return 0, True

    extra = row[0] if isinstance(row[0], dict) else {}
    issues = extra.get("issues") if isinstance(extra, dict) else None
    crit = 0
    if isinstance(issues, list):
        for it in issues:
            if isinstance(it, dict) and it.get("severity") == "critical":
                crit += 1

    valid_raw = extra.get("valid_for_yandex") if isinstance(extra, dict) else None
    if isinstance(valid_raw, bool):
        valid = valid_raw
    elif valid_raw is None:
        # No explicit signal — assume valid so the rule stays silent;
        # the dedicated «file unavailable» branch only fires on an
        # explicit False from the audit module.
        valid = True
    else:
        valid = bool(valid_raw)

    return crit, valid


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
    activity = await _activity(db, site_id)
    competitors = _competitors(
        site.target_config or {},
        list(site.competitor_domains or []),
    )
    behavioral = await _behavioral(db, site_id)
    robots_critical, robots_valid = await _robots_audit_facts(db, site_id)

    return BrainSnapshot(
        site_id=str(site_id),
        domain=site.domain,
        computed_at=datetime.now(timezone.utc),
        indexation=indexation,
        queries=queries,
        review=review,
        missing_landings=missing,
        outcomes=outcomes,
        activity=activity,
        competitors=competitors,
        behavioral=behavioral,
        robots_critical_issues=robots_critical,
        robots_valid_for_yandex=robots_valid,
    )


__all__ = [
    "BrainSnapshot",
    "IndexationFacts",
    "QueriesFacts",
    "ReviewFacts",
    "MissingLandingsFacts",
    "OutcomesFacts",
    "ActivityFacts",
    "CompetitorFacts",
    "BehavioralFacts",
    "REVIEW_RECOMMENDATIONS_CONTEXT_LIMIT",
    "build_snapshot",
]
