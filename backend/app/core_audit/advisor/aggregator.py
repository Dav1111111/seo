"""Unified advice aggregator — pulls signals from every module and
produces one ordered `AdviceFeed`.

This is the single read-only assembly point. NO DB WRITES happen here.
NO LLM CALLS happen here (the LLM-produced wording from brain rules
or review enricher is read verbatim from existing DB rows).

Sources currently wired:
  1. analysis_events:failed (last 24h) → critical/high technical cards
  2. analysis_events:robots_audit (latest) → critical robots card
  3. analysis_events:keyword_gaps (latest) → keyword optimisation card
  4. analysis_events:metrica (latest) → counter health card
  5. brain rules — every Action becomes a card
  6. Funnel raw signal — safety net when brain rule is silent
  7. Query coverage → concrete query/page action cards
  8. Schema audit per-page deep_extract → per-type «missing» cards

The aggregator's only job is: pull data, dispatch to formatters, dedupe
by `id`, sort by `sort_score` desc.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.advisor.dto import AdviceCard, AdviceFeed
from app.core_audit.advisor.formatters import (
    format_brain_action,
    format_funnel_top_raw,
    format_health_failure,
    format_keyword_gaps,
    format_metrica_counter,
    format_query_action,
    format_robots_critical,
    format_schema_missing,
    format_serp_gap,
)
from app.core_audit.query_coverage import (
    coverage_for_query,
    query_strategy_for_row,
)
from app.models.analysis_event import AnalysisEvent
from app.models.daily_metric import DailyMetric
from app.models.page import Page
from app.models.page_deep_extract import PageDeepExtract
from app.models.search_query import SearchQuery
from app.models.site import Site


# Stages we treat as "owner-actionable" when they fail. Anything else
# falling over is internal noise the owner can't fix from /studio.
_OWNER_RELEVANT_STAGES: frozenset[str] = frozenset({
    "crawl",
    "webmaster",
    "demand_map",
    "competitor_discovery",
    "competitor_deep_dive",
    "robots_audit",
    "keyword_gaps",
    "wordstat_refresh_site",
})

# Money pages whose schema audit we actually surface in the feed.
# Pulled from the latest PageDeepExtract per page. Limit so we don't
# generate hundreds of «missing FAQPage» cards — top 3 worst is enough.
_SCHEMA_MAX_CARDS_PER_TYPE = 3


async def collect_advice(db: AsyncSession, site_id: UUID) -> AdviceFeed:
    """Compose the unified advice feed for one site.

    Pure read + compose. The aggregator NEVER writes to the database —
    it's safe to call on a hot request path.
    """
    site = await db.get(Site, site_id)
    if site is None:
        # Endpoint already 404'd; this is the defensive fallback.
        return AdviceFeed(
            site_id=str(site_id),
            computed_at=datetime.now(timezone.utc).isoformat(),
            counts_by_severity={},
            counts_by_category={},
            cards=[],
        )

    cards: list[AdviceCard] = []

    # ── 1. Health: persistent stage failures in the last 24h ──────────
    cards.extend(await _collect_stage_failures(db, site_id))

    # ── 2. Robots audit ──────────────────────────────────────────────
    robots_card = await _collect_robots_critical(db, site_id)
    if robots_card is not None:
        cards.append(robots_card)

    # ── 3. Keyword gaps ──────────────────────────────────────────────
    keyword_card = await _collect_keyword_gaps(db, site_id)
    if keyword_card is not None:
        cards.append(keyword_card)

    # ── 4. Metrica counter health ────────────────────────────────────
    metrica_card = await _collect_metrica_counter(db, site_id)
    if metrica_card is not None:
        cards.append(metrica_card)

    # ── 5. Brain rules → advice cards ────────────────────────────────
    cards.extend(await _collect_brain_actions(db, site))

    # ── 6. Funnel raw signal (safety net) ────────────────────────────
    funnel_card = await _collect_funnel_top_raw(db, site_id)
    if funnel_card is not None:
        cards.append(funnel_card)

    # ── 7. Concrete query→page action cards ──────────────────────────
    cards.extend(await _collect_query_action_cards(db, site_id))

    # ── 8. Schema audit per-type missing ─────────────────────────────
    cards.extend(await _collect_schema_missing(db, site_id))

    # ── 9. SERP-intel per-query gaps ─────────────────────────────────
    cards.extend(await _collect_serp_snapshots(db, site))

    # Dedupe by id (first wins — brain rule wording dominates over raw
    # safety-net formatters when the same signal surfaces twice).
    deduped = _dedupe_cards(cards)

    # Sort descending by sort_score, then id for determinism.
    deduped.sort(key=lambda c: (-c.sort_score, c.id))

    counts_sev = Counter(c.severity for c in deduped)
    counts_cat = Counter(c.category for c in deduped)
    return AdviceFeed(
        site_id=str(site_id),
        computed_at=datetime.now(timezone.utc).isoformat(),
        counts_by_severity=dict(counts_sev),
        counts_by_category=dict(counts_cat),
        cards=deduped,
    )


# ── Helpers ───────────────────────────────────────────────────────────


def _dedupe_cards(cards: list[AdviceCard]) -> list[AdviceCard]:
    """Drop duplicate ids. Also dedupes funnel:top_gap_raw vs the brain
    rule (brain:funnel:top_gap): the brain version wins because its
    wording is the canonical one — the raw card is a safety net.
    """
    seen: set[str] = set()
    out: list[AdviceCard] = []
    # Pre-scan: if the brain rule fired, we drop the raw safety-net.
    has_brain_funnel_top = any(
        c.id == "brain:funnel:top_gap" for c in cards
    )
    for c in cards:
        if c.id in seen:
            continue
        if has_brain_funnel_top and c.id == "funnel:top_gap_raw":
            continue
        seen.add(c.id)
        out.append(c)
    return out


async def _collect_stage_failures(
    db: AsyncSession, site_id: UUID,
) -> list[AdviceCard]:
    """Find stages with at least one `failed` event in the last 24h.

    We aggregate by `(stage)` and surface the failure count + latest
    message. Anything outside the owner-relevant allowlist is dropped
    so we don't spam the feed with internal noise.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = (await db.execute(
        select(
            AnalysisEvent.stage,
            func.count(AnalysisEvent.id).label("fail_count"),
            func.max(AnalysisEvent.ts).label("latest_ts"),
        )
        .where(
            AnalysisEvent.site_id == site_id,
            AnalysisEvent.status == "failed",
            AnalysisEvent.ts >= since,
        )
        .group_by(AnalysisEvent.stage)
    )).all()
    if not rows:
        return []

    # Pull the latest failure message per stage in one extra query.
    stages = [r[0] for r in rows if r[0] in _OWNER_RELEVANT_STAGES]
    if not stages:
        return []
    latest_msgs = (await db.execute(
        select(
            AnalysisEvent.stage,
            AnalysisEvent.message,
        )
        .where(
            AnalysisEvent.site_id == site_id,
            AnalysisEvent.status == "failed",
            AnalysisEvent.ts >= since,
            AnalysisEvent.stage.in_(stages),
        )
        .order_by(AnalysisEvent.ts.desc())
    )).all()
    msg_by_stage: dict[str, str | None] = {}
    for stage, msg in latest_msgs:
        msg_by_stage.setdefault(stage, msg)

    out: list[AdviceCard] = []
    for stage, count, _ts in rows:
        if stage not in _OWNER_RELEVANT_STAGES:
            continue
        out.append(format_health_failure(
            stage=stage,
            count=int(count or 0),
            last_message=msg_by_stage.get(stage),
        ))
    return out


async def _collect_robots_critical(
    db: AsyncSession, site_id: UUID,
) -> AdviceCard | None:
    row = (await db.execute(
        select(AnalysisEvent.extra)
        .where(
            AnalysisEvent.site_id == site_id,
            AnalysisEvent.stage == "robots_audit",
        )
        .order_by(AnalysisEvent.ts.desc())
        .limit(1)
    )).first()
    if row is None:
        return None
    extra = row[0] if isinstance(row[0], dict) else {}
    issues = extra.get("issues") if isinstance(extra, dict) else None
    crit = 0
    if isinstance(issues, list):
        for it in issues:
            if isinstance(it, dict) and it.get("severity") == "critical":
                crit += 1
    valid = extra.get("valid_for_yandex") if isinstance(extra, dict) else None
    valid_bool = True if valid is None else bool(valid)
    return format_robots_critical(crit, valid_bool)


async def _collect_keyword_gaps(
    db: AsyncSession, site_id: UUID,
) -> AdviceCard | None:
    row = (await db.execute(
        select(AnalysisEvent.extra, AnalysisEvent.status)
        .where(
            AnalysisEvent.site_id == site_id,
            AnalysisEvent.stage == "keyword_gaps",
            AnalysisEvent.status == "done",
        )
        .order_by(AnalysisEvent.ts.desc())
        .limit(1)
    )).first()
    if row is None:
        return None
    extra = row[0] if isinstance(row[0], dict) else {}
    if not isinstance(extra, dict):
        return None
    total = int(extra.get("total_gaps") or 0)
    uplift = int(extra.get("total_potential_clicks_per_month") or 0)
    pages = int(extra.get("pages_with_gaps") or 0)
    gaps = extra.get("gaps") or []
    examples = gaps[:3] if isinstance(gaps, list) else []
    return format_keyword_gaps(
        total_gaps=total,
        total_potential_clicks=uplift,
        pages_with_gaps=pages,
        top_examples=examples,
    )


async def _collect_metrica_counter(
    db: AsyncSession, site_id: UUID,
) -> AdviceCard | None:
    # The Metrica collector writes its counter_status into daily_metrics
    # `extra` (read by brain._metrica). We re-read the same source here
    # so the advice feed stays consistent with the brain.
    from app.models.daily_metric import DailyMetric

    row = (await db.execute(
        select(DailyMetric.extra)
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "site_traffic",
            DailyMetric.dimension_id.is_(None),
        )
        .order_by(DailyMetric.date.desc(), DailyMetric.id.desc())
        .limit(1)
    )).first()
    if row is None:
        return None
    extra = row[0] if isinstance(row[0], dict) else {}
    if not isinstance(extra, dict):
        return None
    return format_metrica_counter(
        counter_status=extra.get("counter_status"),
        counter_code_status=extra.get("counter_code_status"),
    )


async def _collect_brain_actions(
    db: AsyncSession, site: Site,
) -> list[AdviceCard]:
    """Build the brain plan and convert each Action to an AdviceCard."""
    from app.core_audit.brain import build_plan, build_snapshot

    snap = await build_snapshot(db, site)
    plan = build_plan(
        snap,
        max_actions=20,
        target_config=site.target_config or {},
    )
    return [format_brain_action(a) for a in plan.actions]


async def _collect_funnel_top_raw(
    db: AsyncSession, site_id: UUID,
) -> AdviceCard | None:
    """Raw funnel_top coverage signal — kept as a safety net.

    If brain's `_rule_funnel_top_gap` fired, the dedupe step drops this.
    Otherwise this still surfaces the same demand-gap so the feed isn't
    silent on real coverage gaps when the brain rule is e.g. masked by
    focus filters.
    """
    from app.models.search_query import SearchQuery

    counts = (await db.execute(
        select(
            func.count(SearchQuery.id).filter(
                SearchQuery.relevance == "funnel_top",
            ).label("ft_count"),
            func.coalesce(
                func.sum(SearchQuery.wordstat_volume).filter(
                    SearchQuery.relevance == "funnel_top",
                ),
                0,
            ).label("ft_volume"),
        ).where(SearchQuery.site_id == site_id)
    )).one()
    ft_count = int(counts.ft_count or 0)
    ft_volume = int(counts.ft_volume or 0)
    if ft_count == 0:
        return None
    # «With ranking» proxy: count of funnel_top SearchQueries that have
    # a query_performance row with avg_position <= 20 in the last 30 days.
    with_ranking = await _funnel_layer_pages_with_ranking(
        db, site_id, "funnel_top",
    )
    return format_funnel_top_raw(
        funnel_top_count=ft_count,
        funnel_top_total_volume=ft_volume,
        funnel_top_with_ranking=with_ranking,
    )


async def _funnel_layer_pages_with_ranking(
    db: AsyncSession, site_id: UUID, relevance: str,
) -> int:
    """Count of SearchQueries within `relevance` that the site already
    ranks for (avg_position <= 20 across the last 30 days).

    Used as a deterministic proxy for «pages currently targeting this
    funnel layer». We don't have a Page<->SearchQuery FK yet, so we
    settle for «we ARE in the top-20 on this query» as evidence the
    site has something resembling a landing for it.
    """
    from app.models.daily_metric import DailyMetric
    from app.models.search_query import SearchQuery

    cutoff = datetime.now(timezone.utc).date() - timedelta(days=30)
    rows = (await db.execute(
        select(func.count(func.distinct(SearchQuery.id)))
        .select_from(SearchQuery)
        .join(
            DailyMetric,
            (DailyMetric.dimension_id == SearchQuery.id)
            & (DailyMetric.metric_type == "query_performance")
            & (DailyMetric.site_id == site_id)
            & (DailyMetric.date >= cutoff)
            & (DailyMetric.avg_position.is_not(None))
            & (DailyMetric.avg_position <= 20),
        )
        .where(
            SearchQuery.site_id == site_id,
            SearchQuery.relevance == relevance,
        )
    )).scalar_one_or_none()
    return int(rows or 0)


async def _collect_query_action_cards(
    db: AsyncSession, site_id: UUID,
) -> list[AdviceCard]:
    """Surface the top concrete query→page tasks for /studio.

    This reads the same deterministic coverage helpers as
    /studio/queries, but emits only actionable weak/missing cases so
    the home feed becomes a work queue instead of another table.
    """
    query_rows = (await db.execute(
        select(SearchQuery)
        .where(
            SearchQuery.site_id == site_id,
            SearchQuery.relevance.in_([
                "direct_product",
                "funnel_warm",
                "funnel_top",
                "own",
                "adjacent",
                "disputed",
            ]),
        )
        .order_by(SearchQuery.wordstat_volume.desc().nulls_last())
        .limit(250)
    )).scalars().all()
    if not query_rows:
        return []

    page_rows = (await db.execute(
        select(
            Page.id,
            Page.url,
            Page.path,
            Page.title,
            Page.h1,
            Page.meta_description,
        )
        .where(
            Page.site_id == site_id,
            or_(Page.http_status.is_(None), Page.http_status.between(200, 299)),
        )
        .limit(5000)
    )).all()
    pages_for_coverage: list[dict[str, Any]] = [
        dict(row._mapping) for row in page_rows
    ]

    cutoff = datetime.now(timezone.utc).date() - timedelta(days=30)
    query_ids = [q.id for q in query_rows]
    metric_rows = (await db.execute(
        select(DailyMetric)
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.dimension_id.in_(query_ids),
            DailyMetric.date >= cutoff,
        )
        .order_by(DailyMetric.date.desc())
        .limit(10000)
    )).scalars().all()
    latest_metric_by_qid: dict[UUID, DailyMetric] = {}
    for metric in metric_rows:
        if metric.dimension_id is None:
            continue
        latest_metric_by_qid.setdefault(metric.dimension_id, metric)

    cards: list[AdviceCard] = []
    for q in query_rows:
        relevance = q.relevance or "unclassified"
        metric = latest_metric_by_qid.get(q.id)
        last_position = (
            float(metric.avg_position)
            if metric and metric.avg_position is not None
            else None
        )
        strategy = query_strategy_for_row(
            query_text=q.query_text,
            relevance=relevance,
            wordstat_volume=q.wordstat_volume,
            last_position=last_position,
        )
        coverage = coverage_for_query(
            query_text=q.query_text,
            relevance=relevance,
            strategy_code=strategy["strategy_code"],
            last_position=last_position,
            pages=pages_for_coverage,
        )
        card = format_query_action(
            query_id=str(q.id),
            query_text=q.query_text,
            relevance=relevance,
            wordstat_volume=q.wordstat_volume,
            last_position=last_position,
            strategy_code=strategy["strategy_code"],
            strategy_label_ru=strategy["strategy_label_ru"],
            strategy_action_ru=strategy["strategy_action_ru"],
            coverage_status=coverage["coverage_status"],
            coverage_score=int(coverage["coverage_score"] or 0),
            coverage_reason_ru=coverage["coverage_reason_ru"],
            coverage_action_ru=coverage["coverage_action_ru"],
            best_page_id=(
                str(coverage["best_page_id"])
                if coverage.get("best_page_id") else None
            ),
            best_page_url=coverage.get("best_page_url"),
            best_page_title=coverage.get("best_page_title"),
        )
        if card is not None:
            cards.append(card)

    cards.sort(key=lambda c: (-c.sort_score, c.id))
    return cards[:8]


async def _collect_schema_missing(
    db: AsyncSession, site_id: UUID,
) -> list[AdviceCard]:
    """Aggregate per-type `schema_missing_type` findings across all
    pages by tallying PageReviewRecommendation rows with
    `category="schema"` and `source_finding_id` shaped
    `schema.missing_type.{type}`.

    The review pipeline already produces these per-page recommendations
    via review/checks/schema_checks.py — we re-aggregate them at the
    site level (how many pages are missing each type) so the advice feed
    can show one card per type instead of N per-page cards.

    The result is one card per type, capped at
    `_SCHEMA_MAX_CARDS_PER_TYPE` (top types by pages_missing).
    """
    from app.core_audit.review.models import PageReviewRecommendation
    rows = (await db.execute(
        select(
            PageReviewRecommendation.source_finding_id,
            PageReviewRecommendation.review_id,
            PageReviewRecommendation.priority,
        )
        .where(
            PageReviewRecommendation.site_id == site_id,
            PageReviewRecommendation.category == "schema",
            PageReviewRecommendation.user_status == "pending",
            PageReviewRecommendation.source_finding_id.like(
                "schema.missing_type.%"
            ),
        )
    )).all()
    if not rows:
        return []

    pages_missing: dict[str, int] = {}
    sample_url: dict[str, str] = {}
    for source_finding_id, review_id, priority in rows:
        # parse "schema.missing_type.faqpage" → "faqpage" → "FAQPage"
        if not isinstance(source_finding_id, str):
            continue
        prefix = "schema.missing_type."
        if not source_finding_id.startswith(prefix):
            continue
        t_lower = source_finding_id[len(prefix):]
        if not t_lower:
            continue
        # Map a few known lowercased forms back to canonical TitleCase.
        canonical = {
            "touristtrip": "TouristTrip",
            "offer": "Offer",
            "product": "Product",
            "faqpage": "FAQPage",
            "service": "Service",
            "aggregateoffer": "AggregateOffer",
            "breadcrumblist": "BreadcrumbList",
            "article": "Article",
            "howto": "HowTo",
            "organization": "Organization",
            "localbusiness": "LocalBusiness",
            "itemlist": "ItemList",
        }.get(t_lower, t_lower.title())
        pages_missing[canonical] = pages_missing.get(canonical, 0) + 1
        # No URL here — link is to /studio/pages (the page workflow)
        sample_url.setdefault(canonical, "")

    if not pages_missing:
        return []
    # Top N types by pages_missing
    top = sorted(
        pages_missing.items(), key=lambda x: (-x[1], x[0]),
    )[:_SCHEMA_MAX_CARDS_PER_TYPE]
    out: list[AdviceCard] = []
    for schema_type, count in top:
        card = format_schema_missing(
            schema_type=schema_type,
            pages_missing=count,
            sample_url=sample_url.get(schema_type) or None,
        )
        if card is not None:
            out.append(card)
    return out


# Cap on per-query SERP-gap cards so the feed stays focused even when
# the site has dozens of probed queries falling out of top-5.
_SERP_GAP_MAX_CARDS = 5


async def _collect_serp_snapshots(
    db: AsyncSession, site: Site,
) -> list[AdviceCard]:
    """Surface per-query SERP-intel gap cards.

    Reads the LATEST non-errored snapshot per query (within last 14
    days) and emits a `format_serp_gap` card for every query where
    we're outside top-5 and the same competitor sits in top-3 and the
    Wordstat volume crosses 50/мес. Capped at `_SERP_GAP_MAX_CARDS` by
    expected uplift so the home feed stays focused.
    """
    from app.models.query_serp_snapshot import QuerySerpSnapshot
    from app.models.search_query import SearchQuery

    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    # Order DESC by taken_at then dedup by query_id in Python — keeps
    # the query portable across SQLite test backends (no DISTINCT ON).
    rows = (await db.execute(
        select(QuerySerpSnapshot)
        .where(
            QuerySerpSnapshot.site_id == site.id,
            QuerySerpSnapshot.error_tag.is_(None),
            QuerySerpSnapshot.taken_at >= cutoff,
        )
        .order_by(
            QuerySerpSnapshot.query_id,
            QuerySerpSnapshot.taken_at.desc(),
        )
    )).scalars().all()
    if not rows:
        return []

    latest_by_query: dict[Any, QuerySerpSnapshot] = {}
    for r in rows:
        if r.query_id not in latest_by_query:
            latest_by_query[r.query_id] = r
    snapshots = list(latest_by_query.values())
    if not snapshots:
        return []

    q_meta_rows = (await db.execute(
        select(
            SearchQuery.id, SearchQuery.query_text, SearchQuery.wordstat_volume,
        )
        .where(SearchQuery.id.in_({s.query_id for s in snapshots}))
    )).all()
    q_meta: dict[Any, tuple[str, int]] = {
        qid: (qtext, int(vol or 0))
        for qid, qtext, vol in q_meta_rows
    }

    candidates: list[tuple[int, AdviceCard]] = []
    for snap in snapshots:
        meta = q_meta.get(snap.query_id)
        if meta is None:
            continue
        query_text, volume = meta
        top_competitors = snap.top_competitor_domains or []
        if not isinstance(top_competitors, list) or not top_competitors:
            continue
        top_domain = str(top_competitors[0] or "").strip()
        if not top_domain:
            continue
        # Find the URL for that domain in the top-N results.
        top_url = ""
        for row in (snap.results or []):
            if not isinstance(row, dict):
                continue
            dom = str(row.get("domain") or "").lower().lstrip(".")
            if dom == top_domain:
                top_url = str(row.get("url") or "")
                break

        card = format_serp_gap(
            query_text=query_text,
            wordstat_volume=volume,
            our_position=snap.our_position,
            top_competitor_domain=top_domain,
            top_competitor_url=top_url,
            site_id=site.id,
            query_id=snap.query_id,
        )
        if card is None:
            continue
        candidates.append((volume, card))

    if not candidates:
        return []
    candidates.sort(key=lambda x: (-x[0], x[1].id))
    return [c for _v, c in candidates[:_SERP_GAP_MAX_CARDS]]


__all__ = ["collect_advice"]
