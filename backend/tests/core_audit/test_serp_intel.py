"""SERP intelligence tests — selector + snapshot + brain rule + Celery
registration.

These pin the spec contract that downstream agents (frontend, brain,
advisor) rely on:

  * selector picks by `wordstat_volume × layer_weight`, capped at N,
    and never spends quota on spam / out_of_market / null-volume rows
  * snapshot writes a row per query, sets `our_position` only on a
    matching domain (subdomain-aware, IDN-aware), populates
    `top_competitor_domains` with up to 3 non-our domains
  * snapshot still writes a row when fetch_serp errors (anti-fabrication)
  * brain rule fires high at ≥3 queries lost to same competitor and
    critical at ≥5; silent below
  * Celery task is registered + beat schedule has the weekly fan-out
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.yandex_serp import SerpDoc
from app.core_audit.brain.rules import _rule_serp_competitor_pressure
from app.core_audit.brain.snapshot import BrainSnapshot, SerpFacts
from app.core_audit.serp_intel import (
    SerpRanking,
    SerpSnapshotResult,
    collect_serp_snapshot_for_site,
    pick_queries_to_probe,
)
from app.core_audit.serp_intel.snapshot import _canonicalise_host
from app.models.query_serp_snapshot import QuerySerpSnapshot
from app.models.search_query import SearchQuery
from app.models.site import Site


# ─── selector (pure) ─────────────────────────────────────────────────


class _FakeQuery:
    """Tiny duck-typed stand-in — selector reads only by attribute."""

    def __init__(
        self, query_text: str, relevance: str, wordstat_volume: int | None,
    ):
        self.id = uuid.uuid4()
        self.query_text = query_text
        self.relevance = relevance
        self.wordstat_volume = wordstat_volume


def test_selector_skips_spam_and_out_of_market():
    queries = [
        _FakeQuery("spam q", "spam", 1000),
        _FakeQuery("oom q", "out_of_market", 1000),
        _FakeQuery("real q", "direct_product", 100),
    ]
    picked = pick_queries_to_probe(queries, max_n=10)
    texts = [q.query_text for q in picked]
    assert texts == ["real q"]


def test_selector_skips_null_volume():
    """Wordstat=None means we don't yet have demand data — never burn
    a SERP-API call on a query whose value we can't even estimate."""
    queries = [
        _FakeQuery("no volume", "direct_product", None),
        _FakeQuery("real q", "direct_product", 50),
    ]
    picked = pick_queries_to_probe(queries, max_n=10)
    texts = [q.query_text for q in picked]
    assert texts == ["real q"]


def test_selector_skips_volume_below_floor():
    """< MIN_VOLUME_TO_PROBE = 10 is rounded-zero noise from Wordstat."""
    queries = [
        _FakeQuery("tiny", "direct_product", 5),
        _FakeQuery("real", "direct_product", 50),
    ]
    picked = pick_queries_to_probe(queries, max_n=10)
    texts = [q.query_text for q in picked]
    assert texts == ["real"]


def test_selector_scores_by_volume_times_layer_weight():
    """direct_product weight=1.0, funnel_top weight=0.5, funnel_warm
    weight=0.7. A funnel_top query at volume=200 (score=100) should
    OUT-rank a direct_product query at volume=80 (score=80) but
    UNDER-rank funnel_warm at volume=200 (score=140).
    """
    queries = [
        _FakeQuery("direct-80", "direct_product", 80),     # 80
        _FakeQuery("funnel-top-200", "funnel_top", 200),    # 100
        _FakeQuery("funnel-warm-200", "funnel_warm", 200),  # 140
    ]
    picked = pick_queries_to_probe(queries, max_n=10)
    texts = [q.query_text for q in picked]
    assert texts == ["funnel-warm-200", "funnel-top-200", "direct-80"]


def test_selector_caps_at_max_n():
    queries = [
        _FakeQuery(f"q{i}", "direct_product", 100 + i) for i in range(50)
    ]
    picked = pick_queries_to_probe(queries, max_n=5)
    assert len(picked) == 5
    # Top 5 by volume — q49, q48, q47, q46, q45 (all direct_product, weight=1).
    assert picked[0].query_text == "q49"
    assert picked[-1].query_text == "q45"


# ─── snapshot collector — uses DB + mocked fetch_serp ─────────────────


async def _mk_search_query(
    db: AsyncSession, site_id: uuid.UUID, text: str,
    *, volume: int = 100, relevance: str = "direct_product",
) -> SearchQuery:
    q = SearchQuery(
        site_id=site_id,
        query_text=text,
        relevance=relevance,
        wordstat_volume=volume,
    )
    db.add(q)
    await db.flush()
    return q


def _doc(position: int, domain: str, url: str | None = None) -> SerpDoc:
    return SerpDoc(
        position=position,
        url=url or f"https://{domain}/p{position}",
        domain=domain,
        title=f"Title {position}",
        headline=f"Headline {position}",
    )


@pytest.mark.asyncio
async def test_snapshot_records_our_position_when_domain_in_results(
    db: AsyncSession, test_site: Site,
):
    """When `fetch_serp` returns a doc whose domain matches the site's
    host, the snapshot row's `our_position` reflects that exact rank
    and `our_url` carries the exact URL."""
    q = await _mk_search_query(db, test_site.id, "test q", volume=100)

    own = _canonicalise_host(test_site.domain)
    serp = [
        _doc(1, "rival.ru"),
        _doc(2, "other.ru"),
        _doc(3, own, url=f"https://{own}/winning-page"),
    ]

    with patch(
        "app.collectors.yandex_serp.fetch_serp",
        return_value=(serp, None),
    ):
        result = await collect_serp_snapshot_for_site(db, test_site.id)

    assert isinstance(result, SerpSnapshotResult)
    assert result.queries_probed == 1
    assert result.queries_failed == 0

    snap_rows = (await db.execute(
        QuerySerpSnapshot.__table__.select().where(
            QuerySerpSnapshot.site_id == test_site.id,
            QuerySerpSnapshot.query_id == q.id,
        )
    )).all()
    assert len(snap_rows) == 1
    row = snap_rows[0]._mapping
    assert row["our_position"] == 3
    assert row["our_url"] == f"https://{own}/winning-page"
    assert row["error_tag"] is None
    # First 3 non-our domains — own is excluded.
    assert row["top_competitor_domains"] == ["rival.ru", "other.ru"]


@pytest.mark.asyncio
async def test_snapshot_records_null_position_when_we_not_in_top_10(
    db: AsyncSession, test_site: Site,
):
    """Our domain never appears in top-N → `our_position` is NULL.
    NULL is meaningful (≠ no probe), so callers can render «not in top-10»
    distinctly from «we never probed»."""
    await _mk_search_query(db, test_site.id, "test q", volume=100)

    serp = [_doc(i, f"rival-{i}.ru") for i in range(1, 11)]

    with patch(
        "app.collectors.yandex_serp.fetch_serp",
        return_value=(serp, None),
    ):
        result = await collect_serp_snapshot_for_site(db, test_site.id)

    assert result.queries_probed == 1
    rows = (await db.execute(
        QuerySerpSnapshot.__table__.select().where(
            QuerySerpSnapshot.site_id == test_site.id,
        )
    )).all()
    assert len(rows) == 1
    assert rows[0]._mapping["our_position"] is None
    assert rows[0]._mapping["our_url"] is None


@pytest.mark.asyncio
async def test_snapshot_handles_idn_domain_match(
    db: AsyncSession, test_site: Site,
):
    """When site.domain is Cyrillic and Yandex returns the punycode
    form (xn--...), the snapshot MUST still set our_position — both
    sides canonicalise to the same Unicode form.

    Real-world punycode value is derived inline so the test is robust
    to encoder differences across Python versions.
    """
    # Switch the test site's domain to a Cyrillic one.
    cyrillic = "южный-континент.рф"
    test_site.domain = cyrillic
    db.add(test_site)
    await db.flush()

    await _mk_search_query(db, test_site.id, "test q", volume=100)

    # Derive the real punycode form Yandex would return. This avoids
    # hard-coding a value that can drift between Python's IDN encoders.
    punycode_form = cyrillic.encode("idna").decode("ascii")
    assert punycode_form.startswith("xn--"), punycode_form

    serp = [
        _doc(1, "rival.ru"),
        # Yandex sometimes returns the punycode form
        _doc(2, punycode_form),
    ]

    with patch(
        "app.collectors.yandex_serp.fetch_serp",
        return_value=(serp, None),
    ):
        result = await collect_serp_snapshot_for_site(db, test_site.id)

    assert result.queries_probed == 1
    rows = (await db.execute(
        QuerySerpSnapshot.__table__.select().where(
            QuerySerpSnapshot.site_id == test_site.id,
        )
    )).all()
    assert len(rows) == 1
    # The punycode form should be recognised as us — our_position == 2.
    assert rows[0]._mapping["our_position"] == 2


@pytest.mark.asyncio
async def test_snapshot_records_error_tag_on_api_failure(
    db: AsyncSession, test_site: Site,
):
    """fetch_serp errored → row is still inserted, with `error_tag`
    set, `results=[]`, `our_position=None`. The owner needs to see
    «API failed» honestly — never silently skip."""
    await _mk_search_query(db, test_site.id, "test q", volume=100)

    with patch(
        "app.collectors.yandex_serp.fetch_serp",
        return_value=([], "http_500_on_submit"),
    ):
        result = await collect_serp_snapshot_for_site(db, test_site.id)

    assert result.queries_probed == 0
    assert result.queries_failed == 1
    rows = (await db.execute(
        QuerySerpSnapshot.__table__.select().where(
            QuerySerpSnapshot.site_id == test_site.id,
        )
    )).all()
    assert len(rows) == 1
    row = rows[0]._mapping
    assert row["error_tag"] == "http_500_on_submit"
    assert row["results"] == []
    assert row["our_position"] is None
    assert row["top_competitor_domains"] == []


@pytest.mark.asyncio
async def test_snapshot_top_competitor_domains_excludes_us(
    db: AsyncSession, test_site: Site,
):
    """`top_competitor_domains` is the first 3 non-our domains in
    rank order — owner subdomains and the bare hostname must be
    excluded so a self-hosted m.example.com doesn't show up as a
    «competitor»."""
    await _mk_search_query(db, test_site.id, "test q", volume=100)
    own = _canonicalise_host(test_site.domain)

    serp = [
        _doc(1, own),
        _doc(2, f"m.{own}"),           # owner subdomain — exclude
        _doc(3, "rival-a.ru"),
        _doc(4, "rival-b.ru"),
        _doc(5, "rival-c.ru"),
        _doc(6, "rival-d.ru"),         # should be capped — 3 kept
    ]

    with patch(
        "app.collectors.yandex_serp.fetch_serp",
        return_value=(serp, None),
    ):
        await collect_serp_snapshot_for_site(db, test_site.id)

    rows = (await db.execute(
        QuerySerpSnapshot.__table__.select().where(
            QuerySerpSnapshot.site_id == test_site.id,
        )
    )).all()
    assert len(rows) == 1
    competitors = rows[0]._mapping["top_competitor_domains"]
    assert competitors == ["rival-a.ru", "rival-b.ru", "rival-c.ru"]
    # And our_position == 1 (the bare own host).
    assert rows[0]._mapping["our_position"] == 1


# ─── brain rule (no DB — works on a SerpFacts in memory) ──────────────


def _mk_snapshot_for_rule(
    *, competitor_tallies: list[tuple[str, int]],
    probed_queries: int = 10,
    our_in_top10_count: int = 0,
) -> BrainSnapshot:
    """Construct just the slice of BrainSnapshot the rule reads.

    Everything outside SerpFacts gets a zero-default value — the rule
    under test only reads `snap.serp`. We use the same dataclass
    construction pattern as `test_brain_rules.py::_snap`.
    """
    from app.core_audit.brain.snapshot import (
        IndexationFacts,
        MissingLandingsFacts,
        OutcomesFacts,
        QueriesFacts,
        ReviewFacts,
    )

    leaderboard = [
        {
            "domain": dom,
            "queries_in_top3": cnt,
            "sample_queries": [f"q{i}" for i in range(min(cnt, 5))],
        }
        for dom, cnt in sorted(competitor_tallies, key=lambda x: (-x[1], x[0]))
    ]
    serp = SerpFacts(
        probed_queries=probed_queries,
        our_in_top10_count=our_in_top10_count,
        top_competitor_by_queries=leaderboard,
    )

    return BrainSnapshot(
        site_id="00000000-0000-0000-0000-000000000000",
        domain="test.example",
        computed_at=datetime.now(timezone.utc),
        indexation=IndexationFacts(
            pages_total=0, pages_in_index=0, pages_excluded=0,
            pages_unknown=0, coverage_pct=None,
        ),
        queries=QueriesFacts(
            total=0, own=0, adjacent=0, disputed=0, spam=0, unclassified=0,
            with_volume=0,
        ),
        review=ReviewFacts(
            pages_with_review=0,
            pages_without_review=0,
            recs_pending=0,
            recs_high_priority_pending=0,
        ),
        missing_landings=MissingLandingsFacts(
            total=0, high_priority=0, medium_priority=0, low_priority=0,
            items=[],
        ),
        outcomes=OutcomesFacts(
            applied_total=0, applied_last_14d=0, pending_followup=0,
        ),
        serp=serp,
    )


def test_rule_serp_competitor_pressure_silent_below_threshold():
    """0-2 queries lost to same competitor → silent. Random variance,
    not a structural threat — the rule must not cry wolf."""
    snap = _mk_snapshot_for_rule(competitor_tallies=[("rival.ru", 2)])
    assert _rule_serp_competitor_pressure(snap, focus_tokens=[]) is None

    snap_zero = _mk_snapshot_for_rule(competitor_tallies=[])
    assert _rule_serp_competitor_pressure(snap_zero, focus_tokens=[]) is None


def test_rule_serp_competitor_pressure_fires_high_at_3_queries():
    """≥3 → high severity. Same competitor on 3 of our top-priority
    queries is a real pattern."""
    snap = _mk_snapshot_for_rule(competitor_tallies=[("rival.ru", 3)])
    action = _rule_serp_competitor_pressure(snap, focus_tokens=[])
    assert action is not None
    assert action.severity == "high"
    assert action.id == "serp:competitor_pressure:rival.ru"
    assert action.evidence["queries_in_top3"] == 3


def test_rule_serp_competitor_pressure_fires_critical_at_5_queries():
    """≥5 → critical severity. One competitor owning that much of the
    niche is a top-priority action."""
    snap = _mk_snapshot_for_rule(competitor_tallies=[("rival.ru", 6)])
    action = _rule_serp_competitor_pressure(snap, focus_tokens=[])
    assert action is not None
    assert action.severity == "critical"
    assert action.id == "serp:competitor_pressure:rival.ru"
    assert action.evidence["competitor_domain"] == "rival.ru"
    assert action.evidence["queries_in_top3"] == 6


# ─── Celery task + beat registration ─────────────────────────────────


def test_celery_task_registered_and_beat_scheduled():
    """Probing only happens if (a) the task name matches the @task
    decorator AND (b) the beat schedule references the right task name.
    A typo either side silently no-ops weekly probes."""
    import app.collectors.tasks  # noqa: F401 — force task module import
    from app.workers.celery_app import celery_app

    registered = set(celery_app.tasks.keys())
    assert "serp_intel_probe_for_site" in registered
    assert "serp_intel_probe_all" in registered

    schedule = celery_app.conf.beat_schedule
    assert "serp-intel-probe-weekly" in schedule
    assert schedule["serp-intel-probe-weekly"]["task"] == "serp_intel_probe_all"


# ─── DTO field-name contract (frozen — frontend depends on it) ──────


def test_serp_ranking_to_dict_matches_jsonb_shape():
    """SerpRanking.to_dict() MUST emit the exact field names the JSONB
    `results` column stores. Changing this without coordinating with
    the frontend breaks SERP rendering silently."""
    r = SerpRanking(
        position=1,
        url="https://example.com/p",
        domain="example.com",
        title="Title",
        headline="Headline",
    )
    assert r.to_dict() == {
        "position": 1,
        "url": "https://example.com/p",
        "domain": "example.com",
        "title": "Title",
        "headline": "Headline",
    }
