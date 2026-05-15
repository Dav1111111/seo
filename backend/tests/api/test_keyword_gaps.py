"""HTTP-level tests for keyword-gaps endpoints + AI-advisor injection.

The keyword_match Celery task is exercised via its cached output (an
`analysis_events` row with `stage="keyword_gaps"`) rather than by
running the task itself — the matcher is covered separately under
`tests/core_audit/keyword_match/`. Here we pin the read-side wire
contract that the frontend depends on, plus the anti-fabrication
guarantee that the deep-extract LLM advisor only sees pre-computed
gaps in its user message.

Pattern mirrors `test_studio_robots_audit.py`: route functions called
directly so the DB transaction fixture rolls back cleanly, no live
network or LLM.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.studio import (
    KeywordPlacementApplyBody,
    _build_keyword_gaps_block,
    apply_keyword_placement,
    get_page_keyword_gaps,
    get_site_keyword_gaps,
)
from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.models.analysis_event import AnalysisEvent
from app.models.page import Page
from app.models.search_query import SearchQuery
from app.models.site import Site


pytestmark = pytest.mark.asyncio


# ── Fixtures helpers ─────────────────────────────────────────────────


def _make_gap(
    *,
    site_id: uuid.UUID,
    page_id: uuid.UUID,
    page_url: str,
    page_title: str,
    page_h1: str,
    query: str,
    query_id: uuid.UUID,
    volume: int,
    position: float | None,
    uplift: int,
    missing_title: list[str],
    missing_h1: list[str],
) -> dict:
    """JSONB-shape dict matching `keyword_match_for_site._gap_to_dict`."""
    return {
        "site_id": str(site_id),
        "page_id": str(page_id),
        "page_url": page_url,
        "page_current_title": page_title,
        "page_current_h1": page_h1,
        "query": query,
        "query_id": str(query_id),
        "wordstat_volume": volume,
        "wordstat_volume_peak_3mo": volume * 2,
        "is_off_season": False,
        "current_position": position,
        "expected_clicks_per_month": uplift,
        "missing_in_title_lemmas": missing_title,
        "missing_in_h1_lemmas": missing_h1,
        "missing_in_h2_lemmas": [],
        "missing_in_first_para_lemmas": [],
        "has_synonym_in_title": False,
        "decision_tree_action": "strengthen",
    }


async def _seed_keyword_gaps_event(
    db: AsyncSession,
    site_id: uuid.UUID,
    gaps: list[dict],
) -> AnalysisEvent:
    """Write a `keyword_gaps` analysis_events row that the read paths
    look up. Mirrors the payload that the Celery task would write."""
    total = len(gaps)
    pages_with_gaps = len({g["page_id"] for g in gaps})
    total_uplift = sum(int(g["expected_clicks_per_month"]) for g in gaps)
    ev = AnalysisEvent(
        site_id=site_id,
        stage="keyword_gaps",
        status="done" if gaps else "done",
        message=f"done · {total} gaps",
        extra={
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "total_gaps": total,
            "total_potential_clicks_per_month": total_uplift,
            "pages_with_gaps": pages_with_gaps,
            "gaps": gaps,
        },
    )
    db.add(ev)
    await db.flush()
    return ev


# ── 1. GET /sites/{site_id}/keyword-gaps ─────────────────────────────


async def test_get_keyword_gaps_404_when_never_ran(
    db: AsyncSession, test_site: Site,
) -> None:
    """No analysis_events row → 404. Frontend uses the 404 to render the
    «запустите keyword_match сейчас» CTA, so this must NOT be a 200 with
    empty payload."""
    with pytest.raises(HTTPException) as exc:
        await get_site_keyword_gaps(site_id=test_site.id, db=db)
    assert exc.value.status_code == 404


async def test_get_keyword_gaps_returns_summary(
    db: AsyncSession, test_site: Site,
) -> None:
    """Seed one page with two gaps; the summary surfaces page-level
    aggregates (gaps_count, page_potential_clicks) and the top gap by
    uplift gets promoted into `top_gap`."""
    page = Page(
        site_id=test_site.id, url="https://x/buggy", path="/buggy",
        title="Активный отдых", h1="Активный отдых",
    )
    db.add(page)
    await db.flush()

    q1 = uuid.uuid4()
    q2 = uuid.uuid4()
    gaps = [
        _make_gap(
            site_id=test_site.id, page_id=page.id,
            page_url=page.url, page_title=page.title, page_h1=page.h1,
            query="багги абхазия", query_id=q1,
            volume=112, position=17.0, uplift=7,
            missing_title=["багги", "абхазия"], missing_h1=["абхазия"],
        ),
        _make_gap(
            site_id=test_site.id, page_id=page.id,
            page_url=page.url, page_title=page.title, page_h1=page.h1,
            query="квадроциклы сочи", query_id=q2,
            volume=40, position=22.0, uplift=2,
            missing_title=["квадроциклы"], missing_h1=[],
        ),
    ]
    await _seed_keyword_gaps_event(db, test_site.id, gaps)

    resp = await get_site_keyword_gaps(site_id=test_site.id, db=db)
    assert resp.total_gaps == 2
    assert resp.pages_with_gaps == 1
    assert resp.total_potential_clicks_per_month == 9
    assert len(resp.top_pages) == 1
    top = resp.top_pages[0]
    assert top.page_id == str(page.id)
    assert top.gaps_count == 2
    assert top.page_potential_clicks == 9
    # Top gap = highest uplift = «багги абхазия»
    assert top.top_gap.query == "багги абхазия"
    assert top.top_gap.wordstat_volume == 112
    assert top.top_gap.expected_clicks_uplift == 7
    # Missing tokens dedupes title+H1 (preserves title order first).
    assert "багги" in top.top_gap.missing_tokens
    assert "абхазия" in top.top_gap.missing_tokens


# ── 2. GET /pages/{page_id}/keyword-gaps ─────────────────────────────


async def test_get_page_keyword_gaps_404_when_never_ran(
    db: AsyncSession, test_site: Site,
) -> None:
    """No analysis_events row → 404. We distinguish this from «page
    clean» (which returns 200 + empty list)."""
    page = Page(site_id=test_site.id, url="https://x/a", path="/a")
    db.add(page)
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await get_page_keyword_gaps(page_id=page.id, db=db)
    assert exc.value.status_code == 404


async def test_get_page_keyword_gaps_empty_list_when_page_clean(
    db: AsyncSession, test_site: Site,
) -> None:
    """Cached row exists but this page is not in it → 200 with `gaps: []`.
    Owner reads this as «эта страница в порядке», not as «никогда не
    проверяли»."""
    # Page under test (no gaps for it).
    clean_page = Page(
        site_id=test_site.id, url="https://x/clean", path="/clean",
    )
    # Another page that DOES have a gap, so the event row isn't empty.
    other_page = Page(
        site_id=test_site.id, url="https://x/other", path="/other",
    )
    db.add_all([clean_page, other_page])
    await db.flush()

    gaps = [_make_gap(
        site_id=test_site.id, page_id=other_page.id,
        page_url=other_page.url, page_title="t", page_h1="h",
        query="q", query_id=uuid.uuid4(),
        volume=50, position=10.0, uplift=3,
        missing_title=["q"], missing_h1=[],
    )]
    await _seed_keyword_gaps_event(db, test_site.id, gaps)

    resp = await get_page_keyword_gaps(page_id=clean_page.id, db=db)
    assert resp.page_id == str(clean_page.id)
    assert resp.gaps == []


async def test_get_page_keyword_gaps_returns_full_list(
    db: AsyncSession, test_site: Site,
) -> None:
    """Page with cached gaps returns the full per-gap shape (full
    missing_in_* arrays + has_synonym_in_title), sorted DESC by uplift."""
    page = Page(
        site_id=test_site.id, url="https://x/p", path="/p",
        title="t", h1="h",
    )
    db.add(page)
    await db.flush()

    big = _make_gap(
        site_id=test_site.id, page_id=page.id,
        page_url=page.url, page_title="t", page_h1="h",
        query="big", query_id=uuid.uuid4(),
        volume=500, position=8.0, uplift=20,
        missing_title=["big"], missing_h1=["big"],
    )
    small = _make_gap(
        site_id=test_site.id, page_id=page.id,
        page_url=page.url, page_title="t", page_h1="h",
        query="small", query_id=uuid.uuid4(),
        volume=30, position=12.0, uplift=1,
        missing_title=["small"], missing_h1=[],
    )
    # Seed in reverse order so the read path must sort.
    await _seed_keyword_gaps_event(db, test_site.id, [small, big])

    resp = await get_page_keyword_gaps(page_id=page.id, db=db)
    assert [g.query for g in resp.gaps] == ["big", "small"]
    assert resp.gaps[0].expected_clicks_uplift == 20
    assert resp.gaps[0].missing_in_title_lemmas == ["big"]
    assert resp.gaps[0].has_synonym_in_title is False


# ── 3. POST /recommendations/keyword-placement/apply ─────────────────


async def test_post_keyword_placement_apply_creates_recommendation(
    db: AsyncSession, test_site: Site,
) -> None:
    """Apply creates exactly one PageReviewRecommendation with the
    `keyword_placement.<query_id>` source id, priority_score = uplift,
    and category=title when new_title is supplied."""
    page = Page(
        site_id=test_site.id, url="https://x/p", path="/p",
        title="Активный отдых", h1="Активный отдых",
    )
    db.add(page)
    await db.flush()

    query_id = uuid.uuid4()
    gap = _make_gap(
        site_id=test_site.id, page_id=page.id,
        page_url=page.url,
        page_title="Активный отдых", page_h1="Активный отдых",
        query="багги абхазия", query_id=query_id,
        volume=112, position=17.0, uplift=15,
        missing_title=["багги", "абхазия"], missing_h1=["абхазия"],
    )
    await _seed_keyword_gaps_event(db, test_site.id, [gap])

    resp = await apply_keyword_placement(
        body=KeywordPlacementApplyBody(
            page_id=page.id,
            query_id=query_id,
            new_title="Багги-туры в Абхазию из Сочи",
        ),
        db=db,
    )
    assert resp.priority == "high"  # uplift=15 ≥ 10
    assert resp.priority_score == 15

    rec = (await db.execute(
        select(PageReviewRecommendation).where(
            PageReviewRecommendation.id == uuid.UUID(resp.recommendation_id),
        )
    )).scalar_one()
    assert rec.category == "title"
    assert rec.priority == "high"
    assert float(rec.priority_score or 0) == 15.0
    assert rec.before_text == "Активный отдых"
    assert rec.after_text == "Багги-туры в Абхазию из Сочи"
    assert rec.source_finding_id == f"keyword_placement.{query_id}"
    assert "багги абхазия" in (rec.reasoning_ru or "")
    assert "112" in (rec.reasoning_ru or "")

    # A parent PageReview was auto-created so the rec has a home.
    review = (await db.execute(
        select(PageReview).where(PageReview.id == rec.review_id)
    )).scalar_one()
    assert review.status == "completed"
    assert review.target_intent_code == "keyword_placement"


async def test_post_keyword_placement_apply_upserts_existing(
    db: AsyncSession, test_site: Site,
) -> None:
    """Two POSTs for the same (page, query) → the second updates the
    first in place. No duplicate rows, and the after_text reflects the
    latest call."""
    page = Page(
        site_id=test_site.id, url="https://x/p", path="/p",
        title="t", h1="h",
    )
    db.add(page)
    await db.flush()

    query_id = uuid.uuid4()
    gap = _make_gap(
        site_id=test_site.id, page_id=page.id,
        page_url=page.url, page_title="t", page_h1="h",
        query="q", query_id=query_id,
        volume=50, position=10.0, uplift=4,
        missing_title=["q"], missing_h1=[],
    )
    await _seed_keyword_gaps_event(db, test_site.id, [gap])

    first = await apply_keyword_placement(
        body=KeywordPlacementApplyBody(
            page_id=page.id,
            query_id=query_id,
            new_title="First title",
        ),
        db=db,
    )
    second = await apply_keyword_placement(
        body=KeywordPlacementApplyBody(
            page_id=page.id,
            query_id=query_id,
            new_title="Second title",
        ),
        db=db,
    )

    assert first.recommendation_id == second.recommendation_id
    rows = (await db.execute(
        select(PageReviewRecommendation).where(
            PageReviewRecommendation.source_finding_id
            == f"keyword_placement.{query_id}",
        )
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].after_text == "Second title"
    # uplift=4 falls below the high-threshold (10) → priority=medium
    assert rows[0].priority == "medium"


async def test_post_keyword_placement_apply_404_when_gap_not_cached(
    db: AsyncSession, test_site: Site,
) -> None:
    """If keyword_match has run but this (page, query) pair isn't in the
    cache, the apply endpoint returns 404 — never invents the gap."""
    page = Page(site_id=test_site.id, url="https://x/p", path="/p")
    db.add(page)
    await db.flush()
    await _seed_keyword_gaps_event(db, test_site.id, [])

    with pytest.raises(HTTPException) as exc:
        await apply_keyword_placement(
            body=KeywordPlacementApplyBody(
                page_id=page.id,
                query_id=uuid.uuid4(),
                new_title="anything",
            ),
            db=db,
        )
    assert exc.value.status_code == 404


# ── 4. AI advisor user-message injection ─────────────────────────────


async def test_build_keyword_gaps_block_returns_none_when_never_ran(
    db: AsyncSession, test_site: Site,
) -> None:
    """No cached event → helper returns None and the deep-extract
    advisor runs with no keyword context (old behavior)."""
    block = await _build_keyword_gaps_block(
        db, site_id=test_site.id, page_id=uuid.uuid4(),
    )
    assert block is None


async def test_build_keyword_gaps_block_positive_note_when_page_clean(
    db: AsyncSession, test_site: Site,
) -> None:
    """Cached event exists but page has no gaps → short positive note
    so the LLM doesn't invent gaps for a clean page."""
    other_page_id = uuid.uuid4()
    gap = _make_gap(
        site_id=test_site.id, page_id=other_page_id,
        page_url="https://x/o", page_title="t", page_h1="h",
        query="q", query_id=uuid.uuid4(),
        volume=50, position=10.0, uplift=2,
        missing_title=["q"], missing_h1=[],
    )
    await _seed_keyword_gaps_event(db, test_site.id, [gap])

    clean_page_id = uuid.uuid4()
    block = await _build_keyword_gaps_block(
        db, site_id=test_site.id, page_id=clean_page_id,
    )
    assert block is not None
    assert "ключевые слова в порядке" in block


async def test_build_keyword_gaps_block_includes_gap_facts(
    db: AsyncSession, test_site: Site,
) -> None:
    """Cached gaps for this page → block contains the query text,
    Wordstat volume, position, uplift, and the anti-fabrication guard
    sentences. Anti-fabrication contract is the load-bearing assertion."""
    page_id = uuid.uuid4()
    gap = _make_gap(
        site_id=test_site.id, page_id=page_id,
        page_url="https://x/p", page_title="Активный отдых",
        page_h1="Активный отдых",
        query="багги абхазия", query_id=uuid.uuid4(),
        volume=112, position=17.0, uplift=7,
        missing_title=["багги", "абхазия"], missing_h1=["абхазия"],
    )
    await _seed_keyword_gaps_event(db, test_site.id, [gap])

    block = await _build_keyword_gaps_block(
        db, site_id=test_site.id, page_id=page_id,
    )
    assert block is not None
    # Header surfaces the section name so the LLM groups it.
    assert "КЛЮЧЕВЫЕ ЗАПРОСЫ" in block
    # Anti-fabrication contract — the exact sentences the brief mandates.
    assert "НЕ выдумывай новые запросы" in block
    assert "НЕ выдумывай объёмы" in block
    # Actual cached facts must appear verbatim — load-bearing because
    # this is the ONLY data path the LLM has for these numbers.
    assert "багги абхазия" in block
    assert "112" in block            # Wordstat volume
    assert "позиция 17" in block     # current position
    assert "+7" in block             # expected uplift
    # Missing lemmas surfaced so the LLM can place them in title/H1.
    assert "багги" in block
    assert "абхазия" in block


async def test_build_keyword_gaps_block_caps_at_three(
    db: AsyncSession, test_site: Site,
) -> None:
    """Block shows at most 3 gaps and appends a «есть ещё N меньших»
    note. Keeps the user msg from ballooning on huge pages."""
    page_id = uuid.uuid4()
    gaps = [
        _make_gap(
            site_id=test_site.id, page_id=page_id,
            page_url="https://x/p", page_title="t", page_h1="h",
            query=f"q{i}", query_id=uuid.uuid4(),
            volume=200 - i, position=10.0 + i, uplift=20 - i,
            missing_title=[f"q{i}"], missing_h1=[],
        )
        for i in range(5)
    ]
    await _seed_keyword_gaps_event(db, test_site.id, gaps)

    block = await _build_keyword_gaps_block(
        db, site_id=test_site.id, page_id=page_id,
    )
    assert block is not None
    # Top-3 by uplift = q0, q1, q2 (uplifts 20, 19, 18).
    assert "«q0»" in block
    assert "«q1»" in block
    assert "«q2»" in block
    # The lower-uplift ones are summarised in the leftover hint.
    assert "ещё 2 меньших" in block
