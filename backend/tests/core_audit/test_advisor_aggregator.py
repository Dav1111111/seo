"""Tests for `core_audit/advisor/` — the unified advice center.

Two contracts pinned here:

  1. **AdviceCard field shape is frozen.** The frontend reads these
     names directly; renaming any of them silently breaks the UI.
  2. **Sort order is deterministic.** sort_score = severity_weight +
     category_bump + uplift/10 — critical/technical always above
     info/seo_content, ties broken by expected impact.

DB-backed scenarios cover:
  - empty site → AdviceFeed with no cards
  - failed analysis_events in last 24h → critical/high technical card
  - robots_audit with critical issues → critical robots card
  - keyword_gaps payload → keyword card with expected impact
  - Metrica counter CS_ERR_UNKNOWN → high health card
  - funnel_top demand without rankings → funnel:top_gap_raw card
    (the brain rule also emits `brain:funnel:top_gap`; the dedup
    drops the raw safety-net version so the feed shows ONE card)
  - the sort orders the feed: critical/technical > schema/high >
    seo_content/info
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.advisor import (
    AdviceCard,
    AdviceFeed,
    collect_advice,
    compute_sort_score,
)
from app.core_audit.advisor.dto import CATEGORY_BUMP, SEVERITY_WEIGHT
from app.core_audit.advisor.formatters import (
    format_brain_action,
    format_funnel_top_raw,
    format_keyword_gaps,
    format_metrica_counter,
    format_robots_critical,
    format_schema_missing,
    format_health_failure,
)
from app.models.analysis_event import AnalysisEvent
from app.models.daily_metric import DailyMetric
from app.models.search_query import SearchQuery
from app.models.site import Site


pytestmark = pytest.mark.asyncio


# ── Pure (no DB) tests for the formatters + sort score ────────────────


def test_sort_score_formula() -> None:
    """Critical/technical must always dominate info/seo_content."""
    assert compute_sort_score("critical", "technical") == 1200.0
    assert compute_sort_score("info", "seo_content") == 80.0
    # uplift breaks ties — but never beats severity gap.
    a = compute_sort_score("high", "keywords", expected_clicks_uplift=1000)
    b = compute_sort_score("critical", "technical")
    assert a < b, "uplift never beats severity"


def test_severity_weight_map_is_complete() -> None:
    """Every Severity literal value has a weight."""
    for sev in ("critical", "high", "medium", "low", "info"):
        assert sev in SEVERITY_WEIGHT


def test_category_bump_map_is_complete() -> None:
    for cat in (
        "technical", "health", "funnel", "schema", "keywords", "seo_content",
    ):
        assert cat in CATEGORY_BUMP


def test_format_robots_critical_silent_when_zero() -> None:
    assert format_robots_critical(0, True) is None


def test_format_robots_critical_emits_card() -> None:
    card = format_robots_critical(2, True)
    assert isinstance(card, AdviceCard)
    assert card.severity == "critical"
    assert card.category == "technical"
    assert card.id == "robots:critical"
    assert "2" in card.title_ru
    assert "robots.txt" in card.title_ru
    assert card.link == "/studio/indexation"
    assert card.cta_ru
    assert card.source_module == "advisor.robots"


def test_format_robots_critical_invalid_file_mentions_unavailable() -> None:
    card = format_robots_critical(1, False)
    assert card is not None
    body_lower = card.body_ru.lower()
    assert any(
        marker in body_lower
        for marker in ("недоступ", "не парс", "не распарс")
    ), card.body_ru


def test_format_keyword_gaps_silent_when_zero() -> None:
    assert format_keyword_gaps(0, 0, 0, []) is None


def test_format_keyword_gaps_high_when_big_uplift() -> None:
    card = format_keyword_gaps(
        total_gaps=50, total_potential_clicks=1200,
        pages_with_gaps=12, top_examples=[
            {"query": "багги Сочи", "page_url": "https://x/buggy"},
        ],
    )
    assert card is not None
    assert card.severity == "high"
    assert card.category == "keywords"
    assert "+1200" in (card.expected_impact_ru or "")
    # uplift contributes to sort_score so big-uplift cards bubble.
    assert card.sort_score > compute_sort_score("high", "keywords")


def test_format_funnel_top_raw_silent_below_threshold() -> None:
    """Under 20 funnel_top queries, signal is too small."""
    assert format_funnel_top_raw(5, 1000, 0) is None


def test_format_funnel_top_raw_silent_when_already_covered() -> None:
    """If site already ranks on half, no gap to flag."""
    assert format_funnel_top_raw(20, 5000, 15) is None


def test_format_funnel_top_raw_emits_card_when_no_coverage() -> None:
    card = format_funnel_top_raw(50, 30_000, 0)
    assert card is not None
    assert card.severity == "high"
    assert card.category == "funnel"
    assert card.id == "funnel:top_gap_raw"
    assert "тыс" in (card.expected_impact_ru or "")
    assert card.link == "/studio/queries?layer=funnel_top"


def test_format_metrica_counter_silent_when_ok() -> None:
    assert format_metrica_counter("CS_OK", "CS_OK") is None
    assert format_metrica_counter(None, None) is None


def test_format_metrica_counter_emits_when_err() -> None:
    card = format_metrica_counter("CS_ERR_UNKNOWN", None)
    assert card is not None
    assert card.severity == "high"
    assert card.category == "health"
    assert card.id == "health:metrica_counter"
    assert "CS_ERR_UNKNOWN" in card.title_ru


def test_format_schema_missing_silent_when_zero() -> None:
    assert format_schema_missing("FAQPage", 0, None) is None


def test_format_schema_missing_severity_scales() -> None:
    medium = format_schema_missing("FAQPage", 2, "https://x/a")
    assert medium is not None
    assert medium.severity == "medium"
    high = format_schema_missing("FAQPage", 10, "https://x/a")
    assert high is not None
    assert high.severity == "high"


def test_format_health_failure_severity_scales() -> None:
    one = format_health_failure("webmaster", count=1, last_message="boom")
    three = format_health_failure("webmaster", count=3, last_message="boom")
    assert one.severity == "high"
    assert three.severity == "critical"
    # Stage-id stable across both → frontend can persist dismissal.
    assert one.id == three.id == "health:stage_failed:webmaster"


def test_format_brain_action_maps_funnel_prefix_to_funnel_category() -> None:
    """Brain action ids starting with `funnel:` must surface as the
    funnel advice category (not generic seo_content). Frontend filters
    on category, so a wrong mapping hides the card from the funnel
    section of the UI."""
    from app.core_audit.brain.rules import Action

    a = Action(
        id="funnel:top_gap",
        severity="high",
        title="X",
        body_ru="B",
        what_to_do_ru="W",
        link_to="/studio/queries",
        link_label="K",
    )
    card = format_brain_action(a)
    assert card.id == "brain:funnel:top_gap"
    assert card.category == "funnel"
    assert card.severity == "high"
    assert card.source_module == "brain"


# ── DB-backed scenarios ───────────────────────────────────────────────


async def test_collect_advice_pristine_site_is_empty(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """A brand-new site with no analysis events, no queries, no pages
    → empty feed. Counts must be zero, not missing-keys."""
    feed = await collect_advice(db, test_site.id)
    assert isinstance(feed, AdviceFeed)
    assert feed.site_id == str(test_site.id)
    assert feed.cards == []
    assert feed.counts_by_severity == {}
    assert feed.counts_by_category == {}


async def test_collect_advice_surfaces_failed_stage(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """A failed `webmaster` event in the last 24h becomes one technical
    card. Severity reflects the retry count: 1 → high, 3+ → critical."""
    now = datetime.now(timezone.utc)
    # 3 failed events → critical.
    for i in range(3):
        db.add(AnalysisEvent(
            site_id=test_site.id,
            stage="webmaster",
            status="failed",
            message=f"Webmaster timeout #{i}",
            ts=now - timedelta(minutes=i * 10),
        ))
    await db.flush()
    feed = await collect_advice(db, test_site.id)
    ids = [c.id for c in feed.cards]
    assert "health:stage_failed:webmaster" in ids
    card = next(c for c in feed.cards if c.id == "health:stage_failed:webmaster")
    assert card.severity == "critical"
    assert card.category == "technical"
    assert "webmaster" in card.title_ru.lower() or "Webmaster" in card.title_ru


async def test_collect_advice_old_failures_ignored(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """Failed events older than 24h must NOT show up — Watchdog has
    already retried them or marked the run as stuck."""
    old = datetime.now(timezone.utc) - timedelta(days=2)
    db.add(AnalysisEvent(
        site_id=test_site.id,
        stage="webmaster",
        status="failed",
        message="ancient failure",
        ts=old,
    ))
    await db.flush()
    feed = await collect_advice(db, test_site.id)
    assert "health:stage_failed:webmaster" not in [c.id for c in feed.cards]


async def test_collect_advice_surfaces_robots_critical(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """A robots_audit event with two critical issues → one critical
    technical card. Sort_score must put it above SEO advice."""
    now = datetime.now(timezone.utc)
    db.add(AnalysisEvent(
        site_id=test_site.id,
        stage="robots_audit",
        status="done",
        message="ok",
        ts=now,
        extra={
            "valid_for_yandex": True,
            "issues": [
                {"severity": "critical", "rule": "Disallow: /"},
                {"severity": "critical", "rule": "User-agent: missing"},
            ],
        },
    ))
    await db.flush()
    feed = await collect_advice(db, test_site.id)
    ids = [c.id for c in feed.cards]
    assert "robots:critical" in ids
    card = next(c for c in feed.cards if c.id == "robots:critical")
    assert card.severity == "critical"
    assert card.category == "technical"


async def test_collect_advice_surfaces_keyword_gaps(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """A `keyword_gaps:done` event with payload → one keyword card.
    Expected impact rides in `expected_impact_ru`."""
    now = datetime.now(timezone.utc)
    db.add(AnalysisEvent(
        site_id=test_site.id,
        stage="keyword_gaps",
        status="done",
        message="ok",
        ts=now,
        extra={
            "total_gaps": 12,
            "total_potential_clicks_per_month": 800,
            "pages_with_gaps": 5,
            "gaps": [
                {"query": "багги сочи", "page_url": "https://x/buggy"},
            ],
        },
    ))
    await db.flush()
    feed = await collect_advice(db, test_site.id)
    ids = [c.id for c in feed.cards]
    assert "keywords:gaps" in ids
    card = next(c for c in feed.cards if c.id == "keywords:gaps")
    assert card.severity == "high"  # 800 clicks > 500
    assert card.category == "keywords"
    assert "+800" in (card.expected_impact_ru or "")


async def test_collect_advice_surfaces_metrica_counter_error(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """A daily_metrics row with counter_status=CS_ERR_UNKNOWN in
    `extra` → high health card."""
    today = datetime.now(timezone.utc).date()
    db.add(DailyMetric(
        site_id=test_site.id,
        date=today,
        metric_type="site_traffic",
        dimension_id=None,
        impressions=0, clicks=0, visits=10, pageviews=20,
        extra={"counter_status": "CS_ERR_UNKNOWN"},
    ))
    await db.flush()
    feed = await collect_advice(db, test_site.id)
    ids = [c.id for c in feed.cards]
    assert "health:metrica_counter" in ids
    card = next(c for c in feed.cards if c.id == "health:metrica_counter")
    assert card.severity == "high"
    assert card.category == "health"


async def test_collect_advice_dedupe_brain_vs_raw_funnel(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """When the brain rule fires `funnel:top_gap` AND the raw safety-net
    formatter would also emit `funnel:top_gap_raw`, only the brain card
    survives — the dedup step preserves the canonical wording."""
    # 25 funnel_top queries, no rankings → both layers fire.
    for i in range(25):
        db.add(SearchQuery(
            site_id=test_site.id,
            query_text=f"что посмотреть {i}",
            relevance="funnel_top",
            wordstat_volume=2000,
        ))
    await db.flush()
    feed = await collect_advice(db, test_site.id)
    ids = [c.id for c in feed.cards]
    # Brain card present
    assert "brain:funnel:top_gap" in ids
    # Raw card suppressed by dedupe
    assert "funnel:top_gap_raw" not in ids


async def test_collect_advice_sort_critical_above_info(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """The feed must surface critical/technical first, then medium-
    /seo, then info-level cards. We pin this so the UI can rely on
    .cards[0] being the most urgent without re-sorting."""
    now = datetime.now(timezone.utc)
    # 3 failed events → critical technical card.
    for i in range(3):
        db.add(AnalysisEvent(
            site_id=test_site.id,
            stage="webmaster",
            status="failed",
            message="boom",
            ts=now - timedelta(minutes=i),
        ))
    # Plus a low-severity «out_of_market summary» from the brain rule:
    db.add(SearchQuery(
        site_id=test_site.id,
        query_text="экскурсии в москве",
        relevance="out_of_market",
        wordstat_volume=500,
    ))
    await db.flush()
    feed = await collect_advice(db, test_site.id)
    assert len(feed.cards) >= 2
    # First card must be a critical one.
    assert feed.cards[0].severity == "critical"
    # Scores monotonically decreasing.
    scores = [c.sort_score for c in feed.cards]
    assert scores == sorted(scores, reverse=True)


async def test_collect_advice_counts_reflect_cards(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """`counts_by_severity` / `counts_by_category` must sum to len(cards).
    Frontend renders the badges from these counters — drift here means
    wrong badge numbers."""
    now = datetime.now(timezone.utc)
    db.add(AnalysisEvent(
        site_id=test_site.id,
        stage="webmaster",
        status="failed",
        message="x",
        ts=now,
    ))
    await db.flush()
    feed = await collect_advice(db, test_site.id)
    assert sum(feed.counts_by_severity.values()) == len(feed.cards)
    assert sum(feed.counts_by_category.values()) == len(feed.cards)


async def test_collect_advice_card_ids_unique(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """Card ids must be unique within a feed — frontend uses them as
    React keys and as dismissal keys."""
    now = datetime.now(timezone.utc)
    db.add_all([
        AnalysisEvent(
            site_id=test_site.id,
            stage="webmaster",
            status="failed",
            message="x",
            ts=now,
        ),
        AnalysisEvent(
            site_id=test_site.id,
            stage="webmaster",
            status="failed",
            message="x",
            ts=now - timedelta(minutes=1),
        ),
    ])
    await db.flush()
    feed = await collect_advice(db, test_site.id)
    ids = [c.id for c in feed.cards]
    assert len(ids) == len(set(ids))


async def test_collect_advice_link_shapes_are_studio_paths(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """Every card whose link is not None must be a /studio/* path —
    the frontend's router knows how to route those. External or empty
    links would break the CTA button."""
    now = datetime.now(timezone.utc)
    for i in range(3):
        db.add(AnalysisEvent(
            site_id=test_site.id,
            stage="webmaster",
            status="failed",
            message="x",
            ts=now - timedelta(minutes=i),
        ))
    db.add(SearchQuery(
        site_id=test_site.id,
        query_text="что посмотреть 1",
        relevance="funnel_top",
        wordstat_volume=2000,
    ))
    await db.flush()
    feed = await collect_advice(db, test_site.id)
    for c in feed.cards:
        if c.link is not None:
            assert c.link.startswith("/studio/"), c
