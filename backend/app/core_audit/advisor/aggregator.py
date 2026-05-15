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
  7. Schema audit per-page deep_extract → per-type «missing» cards

The aggregator's only job is: pull data, dispatch to formatters, dedupe
by `id`, sort by `sort_score` desc.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.advisor.dto import AdviceCard, AdviceFeed
from app.core_audit.advisor.formatters import (
    format_brain_action,
    format_funnel_top_raw,
    format_health_failure,
    format_keyword_gaps,
    format_metrica_counter,
    format_robots_critical,
    format_schema_missing,
)
from app.models.analysis_event import AnalysisEvent
from app.models.page_deep_extract import PageDeepExtract
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

    # ── 7. Schema audit per-type missing ─────────────────────────────
    cards.extend(await _collect_schema_missing(db, site_id))

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


__all__ = ["collect_advice"]
