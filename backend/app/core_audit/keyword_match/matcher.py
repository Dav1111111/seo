"""Deterministic keyword-gap matcher.

For each Wordstat-known query on a site, this module:

1. Picks the page on the site that most likely "owns" that query
   (either via the explicit `target_queries` mapping, or via a fuzzy
   URL-/title-/H1-based score).
2. Loads the latest `page_deep_extracts` row for that page (ground-truth
   text after JS rendering).
3. Tokenizes the query and the page's title / H1 / H2 / first paragraph,
   diffs them, and applies the tourism synonym table.
4. Pulls the page's recent Webmaster average position for the query,
   computes how many extra monthly clicks the page would gain if it
   reached the top-5.
5. Emits a `KeywordGap` only when:
   * the page is currently outside the top-5 (or not ranking at all);
   * at least one query lemma is missing from title / H1 (the two
     surfaces that actually move the needle);
   * the gap has non-zero projected uplift.

No LLM. Every gap is a verifiable claim.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.behavioral.ctr_gap import QUERY_METRIC_TYPE
from app.core_audit.keyword_match.ctr_curve import expected_clicks_uplift
from app.core_audit.keyword_match.dto import (
    KeywordGap,
    KeywordGapsSummary,
)
from app.core_audit.keyword_match.tokenizer import (
    missing_lemmas_after_synonyms,
    tokenize_phrase,
)
from app.models.daily_metric import DailyMetric
from app.models.page import Page
from app.models.page_deep_extract import PageDeepExtract
from app.models.search_query import SearchQuery
from app.profiles.tourism.synonyms import TOURISM_SYNONYMS


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Below this avg position we DO want to recommend strengthening (the
# page exists but ranks too low). At or above this we treat as "already
# winning, leave it alone" — anything 1-5 is on the first screen.
DEFAULT_MIN_POSITION_FOR_STRENGTHEN = 6

# Reach the top-5 — chosen because Yandex CTR drops sharply after pos 5
# and because hitting top-3 from beyond the first page is an unrealistic
# "uplift" promise for a single title rewrite.
DEFAULT_TARGET_POSITION = 5

# Wordstat volume floor — below this the click yield is too small to
# justify a recommendation, even at perfect CTR.
DEFAULT_MIN_VOLUME = 30

# Window for averaging Webmaster avg_position. Matches the CTR-gap
# module's default so the two signals stay comparable.
POSITION_LOOKBACK_DAYS = 30

# A query is "off season" when its current volume is < 30% of the peak
# in the next 3 months. We surface but don't actively prioritize these.
OFF_SEASON_RATIO = 0.30


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def compute_keyword_gaps(
    db: AsyncSession,
    site_id: UUID,
    *,
    min_volume: int = DEFAULT_MIN_VOLUME,
    min_position_for_strengthen: int = DEFAULT_MIN_POSITION_FOR_STRENGTHEN,
    target_position: int = DEFAULT_TARGET_POSITION,
) -> list[KeywordGap]:
    """Compute all keyword gaps for a site.

    Steps:
      1. Load all SearchQuery rows with wordstat_volume >= min_volume,
         is_branded=False, relevance != 'spam'.
      2. For each query, pick the best page (target_queries mapping
         first, else fuzzy score).
      3. Load latest PageDeepExtract per picked page.
      4. Tokenize query → lemmas. Tokenize title / H1 / H2 / first
         paragraph → lemmas. Diff with synonym coverage.
      5. Load avg_position from daily_metrics for the last 30 days.
      6. Compute expected_clicks_per_month using the CTR curve.
      7. Filter: position >= 6 OR position is None; at least one
         lemma missing in title OR H1; uplift > 0.
      8. Return sorted by expected_clicks_per_month DESC.

    The caller is responsible for wrapping this in a `task_session()`
    inside Celery tasks — this function itself is plain async and
    does not commit.
    """
    # ---- 1. Load candidate queries
    queries = await _load_candidate_queries(db, site_id, min_volume)
    if not queries:
        return []

    # ---- 2. Load all candidate pages for fuzzy-match scoring
    pages = await _load_pages(db, site_id)
    if not pages:
        return []

    # ---- 2b. Load explicit target_queries → page mapping (if any).
    # Schema note (2026-05-16): target_queries currently has no
    # `target_page_id` column — that's a Phase C+ feature. So this
    # mapping is best-effort: today it returns {} until the column
    # lands. The fuzzy fallback covers the gap.
    target_mapping = await _load_target_query_mapping(db, site_id)

    # ---- 3. Load latest deep extracts per page (one row per page_id)
    extracts = await _load_latest_extracts(db, site_id)

    # ---- 5. Load avg positions per (page, query) over last 30d
    positions = await _load_avg_positions(db, site_id)

    gaps: list[KeywordGap] = []

    for q in queries:
        # Pick the best page for this query
        best_page = _pick_best_page(q, pages, target_mapping)
        if best_page is None:
            # No page scored high enough — this is a CREATE candidate,
            # which is out of scope. STRENGTHEN-only philosophy.
            continue

        extract = extracts.get(best_page.id)
        # Surface text — prefer deep_extract (post-JS render) over
        # crawler defaults; fall back to Page columns when no extract
        # is available yet.
        page_title = (extract.title if extract else None) or best_page.title
        page_h1 = (extract.h1 if extract else None) or best_page.h1
        page_h2_text = _h2_text_from_extract(extract)
        page_first_para = _first_paragraph_from_extract(extract, best_page)

        # ---- 4. Missing-lemma diff (with synonym coverage)
        missing_title = missing_lemmas_after_synonyms(
            q.query_text, page_title, TOURISM_SYNONYMS,
        )
        missing_h1 = missing_lemmas_after_synonyms(
            q.query_text, page_h1, TOURISM_SYNONYMS,
        )
        missing_h2 = missing_lemmas_after_synonyms(
            q.query_text, page_h2_text, TOURISM_SYNONYMS,
        )
        missing_first = missing_lemmas_after_synonyms(
            q.query_text, page_first_para, TOURISM_SYNONYMS,
        )

        # Gap qualifying rule: title OR H1 must have a missing lemma.
        # The H2 / first-paragraph diffs are informational only — a page
        # that has the lemma in title is already "covered" enough for
        # us to leave it alone (strengthen-only philosophy).
        if not missing_title and not missing_h1:
            continue

        # Synonym-in-title flag — useful for UI to soften the wording
        # ("title is missing «джиппинг» but has the synonym «джип»").
        has_syn_in_title = _has_any_synonym_for_query(
            q.query_text, page_title, TOURISM_SYNONYMS,
        )

        # ---- 5. Current avg position for (page, query)
        current_position = positions.get((best_page.id, q.id))

        # ---- 7. Filter on position
        if current_position is not None and current_position < min_position_for_strengthen:
            # Already in the top-5 — strengthening title here would be
            # a low-leverage change. Skip.
            continue

        # ---- 6. Click uplift
        uplift = expected_clicks_uplift(
            volume=q.wordstat_volume or 0,
            current_position=current_position,
            target_position=target_position,
        )
        if uplift <= 0:
            continue

        # ---- Seasonality flags
        peak_3mo, is_off_season = _seasonality_flags(
            q.wordstat_volume or 0, q.wordstat_trend,
        )

        gaps.append(KeywordGap(
            site_id=site_id,
            page_id=best_page.id,
            page_url=best_page.url,
            page_current_title=page_title,
            page_current_h1=page_h1,
            query=q.query_text,
            query_id=q.id,
            wordstat_volume=q.wordstat_volume or 0,
            wordstat_volume_peak_3mo=peak_3mo,
            is_off_season=is_off_season,
            current_position=current_position,
            expected_clicks_per_month=uplift,
            missing_in_title_lemmas=missing_title,
            missing_in_h1_lemmas=missing_h1,
            missing_in_h2_lemmas=missing_h2,
            missing_in_first_para_lemmas=missing_first,
            has_synonym_in_title=has_syn_in_title,
            decision_tree_action="strengthen",
        ))

    # ---- 8. Sort by uplift descending
    gaps.sort(key=lambda g: g.expected_clicks_per_month, reverse=True)
    return gaps


def summarize_gaps(
    gaps: list[KeywordGap],
    site_id: UUID,
) -> KeywordGapsSummary:
    """Aggregate gaps for the brain summary card.

    Pure function, no DB I/O. Safe to call after `compute_keyword_gaps`
    in the same task / request without another await.
    """
    total_clicks = sum(g.expected_clicks_per_month for g in gaps)
    pages = {g.page_id for g in gaps}
    # gaps is already sorted by uplift desc in `compute_keyword_gaps`.
    # If a caller hands us an unsorted list (manual construction in
    # tests, etc.) we resort defensively here.
    top5 = sorted(
        gaps, key=lambda g: g.expected_clicks_per_month, reverse=True,
    )[:5]
    return KeywordGapsSummary(
        site_id=site_id,
        total_gaps=len(gaps),
        total_potential_clicks_per_month=total_clicks,
        pages_with_gaps=len(pages),
        top_5_by_uplift=top5,
    )


# ---------------------------------------------------------------------------
# Internal helpers — DB loaders
# ---------------------------------------------------------------------------


async def _load_candidate_queries(
    db: AsyncSession,
    site_id: UUID,
    min_volume: int,
) -> list[SearchQuery]:
    """All non-branded, non-spam queries with enough Wordstat volume."""
    res = await db.execute(
        select(SearchQuery)
        .where(
            SearchQuery.site_id == site_id,
            SearchQuery.is_branded.is_(False),
            SearchQuery.wordstat_volume.is_not(None),
            SearchQuery.wordstat_volume >= min_volume,
            SearchQuery.relevance != "spam",
        )
    )
    return list(res.scalars().all())


async def _load_pages(db: AsyncSession, site_id: UUID) -> list[Page]:
    """All pages for a site (used for fuzzy best-page matching).

    For sites with thousands of URLs this could be a memory hit; the
    pilot site has ~100 pages so this is fine. If the dataset grows,
    swap to a pre-scored materialized view.
    """
    res = await db.execute(
        select(Page).where(Page.site_id == site_id)
    )
    return list(res.scalars().all())


async def _load_target_query_mapping(
    db: AsyncSession,
    site_id: UUID,  # noqa: ARG001 — used once mapping lands; kept for API stability
) -> dict[UUID, UUID]:
    """Map query_id → page_id from `target_queries` (if any).

    Today this returns `{}` — the `target_queries` table has no
    `target_page_id` column (Phase A only). When Phase C wires Yandex
    Suggest into target_queries, this helper will start returning real
    mappings without callers having to change.
    """
    return {}


async def _load_latest_extracts(
    db: AsyncSession,
    site_id: UUID,
) -> dict[UUID, PageDeepExtract]:
    """Most recent PageDeepExtract row per page_id.

    Uses a DISTINCT ON (page_id) pattern — Postgres specific, but the
    project is Postgres-only so that's fine.
    """
    # Postgres DISTINCT ON: pick the row per page_id with the max
    # extracted_at. We achieve this with a window-less subquery via
    # ORDER BY (page_id, extracted_at DESC) + DISTINCT ON page_id.
    stmt = (
        select(PageDeepExtract)
        .where(
            PageDeepExtract.site_id == site_id,
            PageDeepExtract.is_competitor.is_(False),
            PageDeepExtract.page_id.is_not(None),
            PageDeepExtract.status == "completed",
        )
        .order_by(
            PageDeepExtract.page_id,
            PageDeepExtract.extracted_at.desc(),
        )
        .distinct(PageDeepExtract.page_id)
    )
    res = await db.execute(stmt)
    return {
        extr.page_id: extr
        for extr in res.scalars().all()
        if extr.page_id is not None
    }


async def _load_avg_positions(
    db: AsyncSession,
    site_id: UUID,
) -> dict[tuple[UUID, UUID], float]:
    """Webmaster avg_position per (page_id, query_id) over last 30 days.

    Today `daily_metrics` rows for queries store `dimension_id =
    SearchQuery.id` and do *not* carry a page_id — so we cannot
    compute a per-page position from this table alone. We expose the
    per-query average (used when the picked page = the page the query
    is observed against). Once page-level Webmaster data lands, this
    function can be widened without changing the caller.
    """
    cutoff = date.today() - timedelta(days=POSITION_LOOKBACK_DAYS)
    rows = await db.execute(
        select(
            DailyMetric.dimension_id,
            func.coalesce(
                func.sum(DailyMetric.avg_position * DailyMetric.impressions)
                / func.nullif(func.sum(DailyMetric.impressions), 0),
                func.avg(DailyMetric.avg_position),
            ).label("avg_position"),
        )
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == QUERY_METRIC_TYPE,
            DailyMetric.date >= cutoff,
            DailyMetric.dimension_id.is_not(None),
        )
        .group_by(DailyMetric.dimension_id)
    )
    # For now, we key by (any_page_id, query_id) — collapse to per-query
    # by returning the avg under every page lookup of that query. The
    # caller does .get((page_id, query_id)), so we build the map keyed
    # on query_id, then resolve in `_pick_best_page` results below.
    # Simpler: return a per-query-only map and have the caller key by it.
    per_query: dict[UUID, float] = {}
    for r in rows:
        if r.dimension_id is None or r.avg_position is None:
            continue
        per_query[r.dimension_id] = float(r.avg_position)
    # Caller does `positions.get((page_id, query_id))`. To keep that
    # signature stable while we have no per-page data, we expose a
    # dict subclass that ignores the page_id half of the key.
    return _PerQueryDict(per_query)


class _PerQueryDict(dict):  # type: ignore[type-arg]
    """Dict that accepts `(page_id, query_id)` tuple keys and resolves
    them by the query_id half. Bridge until daily_metrics carries
    per-page Webmaster data."""

    def __init__(self, per_query: dict[UUID, float]) -> None:
        super().__init__()
        self._per_query = per_query

    def __getitem__(self, key: tuple[UUID, UUID]) -> float:  # type: ignore[override]
        return self._per_query[key[1]]

    def get(self, key, default=None):  # type: ignore[override]
        if not isinstance(key, tuple) or len(key) != 2:
            return default
        return self._per_query.get(key[1], default)


# ---------------------------------------------------------------------------
# Internal helpers — page picking
# ---------------------------------------------------------------------------


def _pick_best_page(
    query: SearchQuery,
    pages: list[Page],
    target_mapping: dict[UUID, UUID],
) -> Page | None:
    """Pick the page most likely to own `query`.

    Strategy:
      * If `target_queries` has an explicit (query → page) row → use it.
      * Otherwise, fuzzy-score every page on the site:
          slug_hits * 3 + title_hits * 2 + h1_hits * 1
        where _hits_ = number of distinct query lemmas present in the
        respective surface.
      * Require score >= 2 (≈ at least one lemma in slug or title) —
        below that the candidate isn't really about this query and
        recommending a strengthen would be misleading.
    """
    pages_by_id = {p.id: p for p in pages}

    # Explicit mapping wins.
    if query.id in target_mapping:
        page = pages_by_id.get(target_mapping[query.id])
        if page is not None:
            return page

    query_lemmas = tokenize_phrase(query.query_text)
    if not query_lemmas:
        return None

    best: tuple[int, Page] | None = None
    for page in pages:
        score = _score_page(page, query_lemmas)
        if score < 2:
            continue
        if best is None or score > best[0]:
            best = (score, page)

    return best[1] if best is not None else None


def _score_page(page: Page, query_lemmas: set[str]) -> int:
    """Score = slug_hits*3 + title_hits*2 + h1_hits*1."""
    slug_lemmas = tokenize_phrase(_slug_text(page.path or page.url))
    title_lemmas = tokenize_phrase(page.title)
    h1_lemmas = tokenize_phrase(page.h1)

    slug_hits = len(query_lemmas & slug_lemmas)
    title_hits = len(query_lemmas & title_lemmas)
    h1_hits = len(query_lemmas & h1_lemmas)

    return slug_hits * 3 + title_hits * 2 + h1_hits * 1


def _slug_text(path_or_url: str | None) -> str:
    """Replace path separators / hyphens with spaces so the tokenizer
    treats `dzhip-tury-v-abkhazii` as a sequence of words."""
    if not path_or_url:
        return ""
    return (
        path_or_url
        .replace("/", " ")
        .replace("-", " ")
        .replace("_", " ")
    )


# ---------------------------------------------------------------------------
# Internal helpers — page-extract text accessors
# ---------------------------------------------------------------------------


def _h2_text_from_extract(extract: PageDeepExtract | None) -> str | None:
    """Concatenate H2 text from `headings_tree` JSON.

    `headings_tree` shape: list of {"level": int, "text": str, ...}.
    We tolerate missing fields silently — bad data should produce no
    gap signal, not a crash.
    """
    if extract is None or not extract.headings_tree:
        return None
    parts: list[str] = []
    for h in extract.headings_tree:
        if not isinstance(h, dict):
            continue
        if h.get("level") == 2:
            text = h.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return " ".join(parts) if parts else None


def _first_paragraph_from_extract(
    extract: PageDeepExtract | None,
    page: Page,
) -> str | None:
    """First ~500 chars of body text — heuristic "first paragraph".

    We don't have a structured "first paragraph" field; the deep
    extractor stores `full_text`. Taking the first chunk approximates
    what a reader (and Yandex's snippet generator) sees first.
    """
    text = None
    if extract is not None and extract.full_text:
        text = extract.full_text
    elif page.content_text:
        text = page.content_text
    if not text:
        return None
    # Trim to first paragraph or 500 chars, whichever is shorter.
    para_end = text.find("\n\n")
    if para_end == -1 or para_end > 500:
        return text[:500]
    return text[:para_end]


def _has_any_synonym_for_query(
    query_text: str,
    page_text: str | None,
    synonyms: dict[str, list[str]],
) -> bool:
    """True if at least one query lemma is covered by a synonym on the page.

    Differs from `has_synonym_coverage` in `tokenizer` — that one needs
    *every* lemma covered. Here we want the softer UI flag: "the page
    uses a related word, the gap may be wording rather than topical".
    """
    if not page_text:
        return False
    page_lemmas = tokenize_phrase(page_text)
    query_lemmas = tokenize_phrase(query_text)
    for ql in query_lemmas:
        if ql in page_lemmas:
            continue
        syns = synonyms.get(ql, [])
        if any(s in page_lemmas for s in syns):
            return True
    return False


# ---------------------------------------------------------------------------
# Internal helpers — seasonality
# ---------------------------------------------------------------------------


def _seasonality_flags(
    current_volume: int,
    trend: list[dict] | dict | None,
) -> tuple[int | None, bool]:
    """Return (peak_3mo_volume, is_off_season).

    Wordstat trend rows look like:
        [{"date": "2025-04-01", "count": 1234}, ...]
    sorted oldest → newest. We take the 3 most recent non-null counts
    as a proxy for the "next 3 months" peak (Wordstat doesn't forecast,
    so this is the best we have without a separate seasonality model).
    `is_off_season` is True when current is < 30% of that peak — the
    query exists but it's not the right time to chase it.
    """
    if not isinstance(trend, list) or not trend:
        return None, False

    counts: list[int] = []
    for row in trend:
        if not isinstance(row, dict):
            continue
        c = row.get("count")
        if isinstance(c, int) and c > 0:
            counts.append(c)

    if not counts:
        return None, False

    # Use the most recent 3 non-null counts as "near-future peak".
    # Order in the list is oldest→newest, so we take the tail.
    recent = counts[-3:]
    peak = max(recent)
    if peak <= 0:
        return peak, False
    is_off = current_volume < OFF_SEASON_RATIO * peak
    return peak, is_off
