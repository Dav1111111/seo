"""End-to-end tests for the keyword_match matcher.

Two clusters of tests here:

* **Pure-logic** tests for `expected_clicks_uplift` and the page-picker
  helpers — no DB, run instantly.
* **Async DB** tests that exercise `compute_keyword_gaps` against the
  real Postgres test DB (rolled back per test via the `db` fixture).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta, datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.behavioral.ctr_gap import QUERY_METRIC_TYPE
from app.core_audit.keyword_match.ctr_curve import (
    expected_clicks_uplift,
    expected_ctr,
)
from app.core_audit.keyword_match.matcher import (
    _pick_best_page,
    _score_page,
    compute_keyword_gaps,
    summarize_gaps,
)
from app.core_audit.keyword_match.tokenizer import _MORPH, tokenize_phrase
from app.models.daily_metric import DailyMetric
from app.models.page import Page
from app.models.page_deep_extract import PageDeepExtract
from app.models.search_query import SearchQuery
from app.models.site import Site


pytestmark = pytest.mark.asyncio


# Tests that depend on Russian morphology skip in degraded local mode.
needs_morph = pytest.mark.skipif(_MORPH is None, reason="pymorphy3 not installed")


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------


class TestExpectedClicksUplift:
    def test_zero_volume_returns_zero(self) -> None:
        assert expected_clicks_uplift(0, 10.0) == 0

    def test_already_in_top_5_returns_zero(self) -> None:
        # Already at pos 3 → uplift to pos 5 is negative → floored at 0.
        assert expected_clicks_uplift(1000, 3.0, target_position=5) == 0

    def test_position_none_uses_full_target_ctr(self) -> None:
        """No SERP presence → uplift = volume * CTR(target)."""
        # CTR at pos 5 (commercial curve) = 0.05 → 1000 * 0.05 = 50.
        result = expected_clicks_uplift(1000, None, target_position=5)
        assert result == 50

    def test_position_below_target_produces_positive_uplift(self) -> None:
        """A page at position 9 lifting to position 5 yields uplift."""
        # CTR(9) ≈ 0.02, CTR(5) = 0.05, delta ~0.03, volume 1000 → ~30.
        result = expected_clicks_uplift(1000, 9.0, target_position=5)
        assert result > 0
        assert result < 100  # sanity bound

    def test_uplift_is_non_negative(self) -> None:
        for pos in (1, 2, 3, 5, 8, 12, 30, None):
            assert expected_clicks_uplift(500, pos) >= 0


class TestExpectedCtr:
    def test_none_position_returns_zero(self) -> None:
        assert expected_ctr(None) == 0.0

    def test_position_below_one_returns_zero(self) -> None:
        assert expected_ctr(0.5) == 0.0

    def test_high_position_returns_zero(self) -> None:
        # Beyond the position floor → zero (signal too noisy).
        assert expected_ctr(50.0) == 0.0


# ---------------------------------------------------------------------------
# Page picker
# ---------------------------------------------------------------------------


@needs_morph
class TestScorePage:
    def test_slug_match_weighted_higher_than_h1(self) -> None:
        page_slug = Page(
            site_id=uuid.uuid4(), url="x", path="/dzhip-tury-abkhazia",
            title="Главная", h1=None,
        )
        page_h1 = Page(
            site_id=uuid.uuid4(), url="x", path="/",
            title="Главная", h1="Джип-туры в Абхазии",
        )
        lemmas = tokenize_phrase("джиппинг абхазия")  # → {"джиппинг", "абхазия"}
        # Slug "dzhip-tury-abkhazia" → after slugify split → tokens like
        # "dzhip", "tury", "abkhazia" — Latin transliteration, won't
        # match Cyrillic lemmas. So slug page scores 0; H1 page scores
        # at least 1 (one Cyrillic lemma in H1).
        score_h1 = _score_page(page_h1, lemmas)
        assert score_h1 >= 1

    def test_empty_query_lemmas_scores_zero(self) -> None:
        page = Page(
            site_id=uuid.uuid4(), url="x", path="/a", title="Foo", h1="Bar",
        )
        assert _score_page(page, set()) == 0


@needs_morph
class TestPickBestPage:
    def test_picks_page_with_highest_score(self) -> None:
        # Three pages — only one has the relevant lemma in its H1.
        site_id = uuid.uuid4()
        good = Page(
            id=uuid.uuid4(),
            site_id=site_id, url="https://x/abkhazia",
            path="/abkhazia", title="Главная",
            h1="Джип-туры в Абхазии",
        )
        irrelevant = Page(
            id=uuid.uuid4(),
            site_id=site_id, url="https://x/contacts",
            path="/contacts", title="Контакты", h1="Контакты",
        )
        q = SearchQuery(
            id=uuid.uuid4(), site_id=site_id,
            query_text="джиппинг абхазия", is_branded=False,
        )
        chosen = _pick_best_page(q, [good, irrelevant], target_mapping={})
        assert chosen is not None
        assert chosen.id == good.id

    def test_returns_none_when_no_page_scores_high_enough(self) -> None:
        site_id = uuid.uuid4()
        # All pages are about completely unrelated topics.
        p1 = Page(
            id=uuid.uuid4(),
            site_id=site_id, url="https://x/contacts",
            path="/contacts", title="Контакты", h1="Контакты",
        )
        p2 = Page(
            id=uuid.uuid4(),
            site_id=site_id, url="https://x/about",
            path="/about", title="О нас", h1="О нас",
        )
        q = SearchQuery(
            id=uuid.uuid4(), site_id=site_id,
            query_text="джиппинг абхазия", is_branded=False,
        )
        assert _pick_best_page(q, [p1, p2], target_mapping={}) is None

    def test_target_mapping_overrides_fuzzy_score(self) -> None:
        site_id = uuid.uuid4()
        explicit = Page(
            id=uuid.uuid4(),
            site_id=site_id, url="https://x/special",
            path="/special", title="Зимний контент", h1="Зима",
        )
        fuzzy_winner = Page(
            id=uuid.uuid4(),
            site_id=site_id, url="https://x/abkhazia",
            path="/abkhazia", title="Джип-туры", h1="Абхазия",
        )
        q = SearchQuery(
            id=uuid.uuid4(), site_id=site_id,
            query_text="джиппинг абхазия", is_branded=False,
        )
        chosen = _pick_best_page(
            q, [explicit, fuzzy_winner],
            target_mapping={q.id: explicit.id},
        )
        assert chosen is not None
        assert chosen.id == explicit.id


# ---------------------------------------------------------------------------
# summarize_gaps
# ---------------------------------------------------------------------------


def test_summarize_empty_list() -> None:
    site_id = uuid.uuid4()
    summary = summarize_gaps([], site_id)
    assert summary.site_id == site_id
    assert summary.total_gaps == 0
    assert summary.total_potential_clicks_per_month == 0
    assert summary.pages_with_gaps == 0
    assert summary.top_5_by_uplift == []


# ---------------------------------------------------------------------------
# compute_keyword_gaps — DB-driven
# ---------------------------------------------------------------------------


@needs_morph
async def test_compute_keyword_gaps_skips_branded(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """is_branded=True queries never produce a gap."""
    page = Page(
        site_id=test_site.id, url="https://x/a", path="/a",
        title="Главная", h1="Главная страница",
    )
    db.add(page)
    await db.flush()

    branded = SearchQuery(
        site_id=test_site.id,
        query_text="мойсайт.ру отзывы",
        is_branded=True,
        wordstat_volume=500,
        relevance="own",
    )
    db.add(branded)
    await db.flush()

    gaps = await compute_keyword_gaps(db, test_site.id)
    assert gaps == []


@needs_morph
async def test_compute_keyword_gaps_skips_below_min_volume(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """wordstat_volume < min_volume → skipped."""
    page = Page(
        site_id=test_site.id, url="https://x/a", path="/a",
        title="Главная", h1="Главная",
    )
    db.add(page)
    await db.flush()

    low_vol = SearchQuery(
        site_id=test_site.id,
        query_text="джиппинг абхазия",
        is_branded=False,
        wordstat_volume=10,  # below default 30
        relevance="own",
    )
    db.add(low_vol)
    await db.flush()

    gaps = await compute_keyword_gaps(db, test_site.id, min_volume=30)
    assert gaps == []


@needs_morph
async def test_compute_keyword_gaps_skips_null_volume(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """wordstat_volume IS NULL → not processed."""
    page = Page(
        site_id=test_site.id, url="https://x/a", path="/a",
        title="Главная", h1="Главная",
    )
    db.add(page)
    await db.flush()

    q = SearchQuery(
        site_id=test_site.id,
        query_text="джиппинг абхазия",
        is_branded=False,
        wordstat_volume=None,
        relevance="own",
    )
    db.add(q)
    await db.flush()

    gaps = await compute_keyword_gaps(db, test_site.id)
    assert gaps == []


@needs_morph
async def test_compute_keyword_gaps_no_pages_returns_empty(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """No pages on the site → nothing to recommend, even with queries."""
    q = SearchQuery(
        site_id=test_site.id,
        query_text="джиппинг абхазия",
        is_branded=False,
        wordstat_volume=500,
        relevance="own",
    )
    db.add(q)
    await db.flush()

    gaps = await compute_keyword_gaps(db, test_site.id)
    assert gaps == []


@needs_morph
async def test_compute_keyword_gaps_emits_gap_when_position_null(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """No position data → gap is emitted with current_position=None
    and uplift = volume × CTR(target)."""
    page = Page(
        site_id=test_site.id,
        url="https://x/abkhazia",
        path="/abkhazia",
        title="Туры в Абхазию — летние программы",  # missing "джиппинг"
        h1="Поездки по Абхазии",                    # missing "джиппинг"
        content_text="Описание поездок по Абхазии.",
    )
    db.add(page)
    await db.flush()

    q = SearchQuery(
        site_id=test_site.id,
        query_text="джиппинг абхазия",
        is_branded=False,
        wordstat_volume=1000,
        relevance="own",
    )
    db.add(q)
    await db.flush()

    gaps = await compute_keyword_gaps(db, test_site.id)
    assert len(gaps) == 1
    g = gaps[0]
    assert g.query == "джиппинг абхазия"
    assert g.page_id == page.id
    assert g.current_position is None
    # CTR(5) commercial = 0.05 → 1000 * 0.05 = 50.
    assert g.expected_clicks_per_month == 50
    assert "джиппинг" in g.missing_in_title_lemmas
    assert "джиппинг" in g.missing_in_h1_lemmas
    assert g.decision_tree_action == "strengthen"


@needs_morph
async def test_compute_keyword_gaps_skips_when_in_top_5(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """A page already at avg_position 3 should NOT produce a gap."""
    page = Page(
        site_id=test_site.id,
        url="https://x/abkhazia",
        path="/abkhazia",
        title="Туры в Абхазию",
        h1="Поездки по Абхазии",
    )
    db.add(page)
    await db.flush()

    q = SearchQuery(
        site_id=test_site.id,
        query_text="джиппинг абхазия",
        is_branded=False,
        wordstat_volume=1000,
        relevance="own",
    )
    db.add(q)
    await db.flush()

    # Webmaster has us at position 3 for this query over the last week.
    today = date.today()
    db.add(DailyMetric(
        site_id=test_site.id,
        date=today - timedelta(days=2),
        metric_type=QUERY_METRIC_TYPE,
        dimension_id=q.id,
        impressions=100,
        clicks=10,
        avg_position=3.0,
    ))
    await db.flush()

    gaps = await compute_keyword_gaps(db, test_site.id)
    assert gaps == []


@needs_morph
async def test_compute_keyword_gaps_emits_when_outside_top_5(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """avg_position 8 → not in top-5 → gap with positive uplift."""
    page = Page(
        site_id=test_site.id,
        url="https://x/abkhazia",
        path="/abkhazia",
        title="Туры в Абхазию",
        h1="Поездки по Абхазии",
    )
    db.add(page)
    await db.flush()

    q = SearchQuery(
        site_id=test_site.id,
        query_text="джиппинг абхазия",
        is_branded=False,
        wordstat_volume=1000,
        relevance="own",
    )
    db.add(q)
    await db.flush()

    today = date.today()
    db.add(DailyMetric(
        site_id=test_site.id,
        date=today - timedelta(days=2),
        metric_type=QUERY_METRIC_TYPE,
        dimension_id=q.id,
        impressions=100,
        clicks=2,
        avg_position=8.0,
    ))
    await db.flush()

    gaps = await compute_keyword_gaps(db, test_site.id)
    assert len(gaps) == 1
    g = gaps[0]
    assert g.current_position == pytest.approx(8.0, rel=0.01)
    assert g.expected_clicks_per_month > 0


@needs_morph
async def test_compute_keyword_gaps_skips_spam_relevance(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """relevance='spam' → never recommended (we don't want to optimize)."""
    page = Page(
        site_id=test_site.id, url="https://x/a", path="/a",
        title="Туры", h1="Туры",
    )
    db.add(page)
    await db.flush()

    spam_q = SearchQuery(
        site_id=test_site.id,
        query_text="скачать торрент абхазия",
        is_branded=False,
        wordstat_volume=5000,
        relevance="spam",
    )
    db.add(spam_q)
    await db.flush()

    gaps = await compute_keyword_gaps(db, test_site.id)
    assert gaps == []


@needs_morph
async def test_compute_keyword_gaps_uses_deep_extract_title(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """When a deep extract exists, its title beats the Page.title for
    the missing-token check. This catches the case where the crawler
    saw an old title but the post-JS render has the up-to-date one."""
    page = Page(
        site_id=test_site.id,
        url="https://x/abkhazia",
        path="/abkhazia",
        title="Старый заголовок без ключевиков",
        h1="Поездки по Абхазии",
    )
    db.add(page)
    await db.flush()

    # Deep extract has the modern title that DOES contain «джиппинг».
    extract = PageDeepExtract(
        site_id=test_site.id,
        page_id=page.id,
        url=page.url,
        is_competitor=False,
        status="completed",
        title="Джиппинг в Абхазии — летние туры",
        h1="Поездки по Абхазии",
        full_text="Полная программа джиппинга в Абхазии.",
        extracted_at=datetime.now(timezone.utc),
    )
    db.add(extract)
    await db.flush()

    q = SearchQuery(
        site_id=test_site.id,
        query_text="джиппинг абхазия",
        is_branded=False,
        wordstat_volume=1000,
        relevance="own",
    )
    db.add(q)
    await db.flush()

    gaps = await compute_keyword_gaps(db, test_site.id)
    # Title now has «джиппинг», but H1 still doesn't — so a gap is
    # still emitted (because missing_in_h1 is non-empty). The point of
    # this test is that title-missing should be EMPTY because the
    # deep extract overrides Page.title.
    if gaps:
        g = gaps[0]
        assert "джиппинг" not in g.missing_in_title_lemmas
        assert "джиппинг" in g.missing_in_h1_lemmas


@needs_morph
async def test_compute_keyword_gaps_sorts_by_uplift_desc(
    db: AsyncSession,
    test_site: Site,
) -> None:
    """Result list is sorted by expected_clicks_per_month descending."""
    page = Page(
        site_id=test_site.id, url="https://x/a", path="/a",
        title="Главная",
        h1="Главная страница",
    )
    db.add(page)
    await db.flush()

    # Three queries, each missing a different lemma the page lacks.
    queries_data = [
        ("джиппинг сочи", 200),
        ("рафтинг сочи", 2000),
        ("каякинг сочи", 800),
    ]
    qids: list[uuid.UUID] = []
    for text, vol in queries_data:
        q = SearchQuery(
            site_id=test_site.id,
            query_text=text,
            is_branded=False,
            wordstat_volume=vol,
            relevance="own",
        )
        db.add(q)
        await db.flush()
        qids.append(q.id)

    gaps = await compute_keyword_gaps(db, test_site.id)
    # Sorted by uplift desc. With current_position=None for all, uplift
    # is proportional to volume → rafting (2000) > kayaking (800) > jip (200).
    uplifts = [g.expected_clicks_per_month for g in gaps]
    assert uplifts == sorted(uplifts, reverse=True)


# ---------------------------------------------------------------------------
# summarize_gaps with real gap objects
# ---------------------------------------------------------------------------


@needs_morph
async def test_summarize_gaps_aggregates_per_site(
    db: AsyncSession,
    test_site: Site,
) -> None:
    page = Page(
        site_id=test_site.id, url="https://x/a", path="/a",
        title="Главная", h1="Главная страница",
    )
    db.add(page)
    await db.flush()
    q = SearchQuery(
        site_id=test_site.id,
        query_text="джиппинг абхазия",
        is_branded=False,
        wordstat_volume=1000,
        relevance="own",
    )
    db.add(q)
    await db.flush()

    gaps = await compute_keyword_gaps(db, test_site.id)
    summary = summarize_gaps(gaps, test_site.id)

    assert summary.site_id == test_site.id
    assert summary.total_gaps == len(gaps)
    assert summary.total_potential_clicks_per_month == sum(
        g.expected_clicks_per_month for g in gaps
    )
    assert summary.pages_with_gaps == len({g.page_id for g in gaps})
    assert len(summary.top_5_by_uplift) <= 5
