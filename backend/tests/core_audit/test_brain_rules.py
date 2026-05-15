"""Tests for app.core_audit.brain.rules — Studio v2 etap 7.

The brain has TWO contracts that matter:

  1. **No fact appears in an Action body without backing in the
     snapshot.** Templates use `f""` substitution; if a rule reads a
     field the snapshot doesn't have, that's a regression — pin it.

  2. **Severity ladder is monotonic** in the count it watches. More
     missing landings ⇒ at least the same severity, never lower.

Pure functions, no DB, no asyncio.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core_audit.brain.rules import (
    Action,
    build_plan,
    _ru_plural,
)
from app.core_audit.brain.snapshot import (
    BrainSnapshot,
    IndexationFacts,
    QueriesFacts,
    ReviewFacts,
    MissingLandingsFacts,
    OutcomesFacts,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _snap(
    *,
    pages_total: int = 22,
    pages_in_index: int = 22,
    pages_excluded: int = 0,
    pages_unknown: int = 0,
    own: int = 0,
    adjacent: int = 0,
    disputed: int = 0,
    spam: int = 0,
    unclassified: int = 0,
    queries_with_volume: int = 0,
    pages_with_review: int = 22,
    recs_pending: int = 0,
    recs_high_priority_pending: int = 0,
    missing_items: list[dict] | None = None,
    applied_total: int = 0,
    applied_last_14d: int = 0,
    pending_followup: int = 0,
    sample_not_indexed_urls: list[str] | None = None,
    sample_excluded: list[dict[str, str]] | None = None,
    sample_harmful: list[dict[str, str | None]] | None = None,
    sample_own: list[str] | None = None,
    sample_unreviewed_urls: list[str] | None = None,
) -> BrainSnapshot:
    items = missing_items or []
    counts = {"high": 0, "medium": 0, "low": 0}
    for it in items:
        p = it.get("priority", "medium")
        if p in counts:
            counts[p] += 1
    queries_total = own + adjacent + disputed + spam + unclassified
    return BrainSnapshot(
        site_id="00000000-0000-0000-0000-000000000000",
        domain="example.ru",
        computed_at=datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
        indexation=IndexationFacts(
            pages_total=pages_total,
            pages_in_index=pages_in_index,
            pages_excluded=pages_excluded,
            pages_unknown=pages_unknown,
            coverage_pct=(pages_in_index / pages_total * 100.0) if pages_total else None,
            sample_not_indexed_urls=sample_not_indexed_urls or [],
            sample_excluded=sample_excluded or [],
        ),
        queries=QueriesFacts(
            total=queries_total,
            own=own, adjacent=adjacent, disputed=disputed,
            spam=spam, unclassified=unclassified,
            with_volume=queries_with_volume,
            classified_at=None,
            sample_harmful=sample_harmful or [],
            sample_own=sample_own or [],
        ),
        review=ReviewFacts(
            pages_with_review=pages_with_review,
            pages_without_review=max(0, pages_total - pages_with_review),
            recs_pending=recs_pending,
            recs_high_priority_pending=recs_high_priority_pending,
            sample_unreviewed_urls=sample_unreviewed_urls or [],
        ),
        missing_landings=MissingLandingsFacts(
            total=len(items),
            high_priority=counts["high"],
            medium_priority=counts["medium"],
            low_priority=counts["low"],
            items=items,
        ),
        outcomes=OutcomesFacts(
            applied_total=applied_total,
            applied_last_14d=applied_last_14d,
            pending_followup=pending_followup,
        ),
    )


def _by_id(actions: list[Action], aid: str) -> Action | None:
    return next((a for a in actions if a.id == aid), None)


# ── Pluralisation (foundation for all body templates) ────────────────


def test_ru_plural_handles_all_forms() -> None:
    assert _ru_plural(1, ("страница", "страницы", "страниц")) == "страница"
    assert _ru_plural(2, ("страница", "страницы", "страниц")) == "страницы"
    assert _ru_plural(5, ("страница", "страницы", "страниц")) == "страниц"
    assert _ru_plural(11, ("страница", "страницы", "страниц")) == "страниц"
    assert _ru_plural(21, ("страница", "страницы", "страниц")) == "страница"
    assert _ru_plural(0, ("страница", "страницы", "страниц")) == "страниц"


# ── Empty / no-data state ────────────────────────────────────────────


def test_pristine_site_yields_zero_actions() -> None:
    """Brand-new site with no queries, no pages, no reviews — there's
    nothing to act on, so the plan is empty. Diagnostics tell the
    owner what to do first."""
    snap = _snap(
        pages_total=0, pages_in_index=0, pages_with_review=0,
    )
    plan = build_plan(snap)
    assert plan.actions == []
    # At minimum we surface "no queries, no pages" diagnostics.
    assert any("Запрос" in d or "URL" in d or "Услуг" in d for d in plan.diagnostics)


# ── Indexation coverage ──────────────────────────────────────────────


def test_indexation_silent_when_per_url_data_absent() -> None:
    """If every page is `unknown`, the per-URL Webmaster check just
    didn't run yet. We don't pretend that's a coverage failure — it's
    a coverage of the COLLECTOR, surfaced via diagnostics, not as
    an action that says «23 страницы не в индексе»."""
    snap = _snap(pages_total=22, pages_in_index=0, pages_unknown=22)
    plan = build_plan(snap)
    assert _by_id(plan.actions, "indexation:not_indexed") is None
    assert any("URL" in d for d in plan.diagnostics)


def test_indexation_emits_when_real_gap() -> None:
    """4 pages confirmed not-indexed = real gap, fires."""
    snap = _snap(pages_total=22, pages_in_index=18)
    plan = build_plan(snap)
    a = _by_id(plan.actions, "indexation:not_indexed")
    assert a is not None
    assert a.evidence["not_indexed"] == 4
    assert a.evidence["in_index"] == 18
    assert "4" in a.title
    assert a.link_to == "/studio/indexation"
    # Phase A: action carries what_to_do_ru as a separate field, not
    # buried in body. Body explains «и что», what_to_do — «куда тыкать».
    assert a.what_to_do_ru
    assert "Открой" in a.what_to_do_ru or "открой" in a.what_to_do_ru


def test_indexation_severity_scales_with_gap() -> None:
    """3+ missing → critical. 1-2 missing on a ≥10-page site → high.
    The point is to grab the owner's attention proportionally — not
    to spam alarms when one page is just slow to index."""
    high = build_plan(_snap(pages_total=10, pages_in_index=9))
    crit = build_plan(_snap(pages_total=10, pages_in_index=5))
    h_a = _by_id(high.actions, "indexation:not_indexed")
    c_a = _by_id(crit.actions, "indexation:not_indexed")
    assert h_a and h_a.severity == "high"
    assert c_a and c_a.severity == "critical"


def test_indexation_silent_on_tiny_site_with_normal_latency() -> None:
    """Day-one site with 5 pages and 1-2 still un-indexed is normal
    Yandex latency, not an owner action. Stay quiet under the soft
    threshold so the brain doesn't cry wolf."""
    # 5 pages, 4 in index, 1 unindexed = normal latency.
    plan = build_plan(_snap(pages_total=5, pages_in_index=4))
    assert _by_id(plan.actions, "indexation:not_indexed") is None
    # 9 pages, 7 in index, 2 unindexed = still under threshold.
    plan = build_plan(_snap(pages_total=9, pages_in_index=7))
    assert _by_id(plan.actions, "indexation:not_indexed") is None


def test_indexation_fires_on_tiny_site_with_significant_gap() -> None:
    """Even on a small site, ≥3 unindexed is a real gap, not latency."""
    plan = build_plan(_snap(pages_total=5, pages_in_index=2))
    a = _by_id(plan.actions, "indexation:not_indexed")
    assert a is not None
    assert a.severity == "critical"  # 3 missing


def test_indexation_subtracts_unknown_from_not_indexed() -> None:
    """`unknown` (Webmaster hasn't reported yet) is NOT «не в индексе».
    Earlier rule fired «12 не в индексе» when 8 were just unknown.
    Math: not_indexed = total - in_index - excluded - unknown.

    pages_total=22, in_index=10, unknown=8 ⇒ confirmed not_indexed=4
    (NOT 12 as the old buggy formula computed).
    """
    snap = _snap(pages_total=22, pages_in_index=10, pages_unknown=8)
    plan = build_plan(snap)
    a = _by_id(plan.actions, "indexation:not_indexed")
    assert a is not None
    assert a.evidence["not_indexed"] == 4
    assert a.evidence["unknown"] == 8
    assert "4" in a.title


# ── Harmful visibility ──────────────────────────────────────────────


def test_harmful_silent_when_no_classifier_run() -> None:
    """All-unclassified is the «classifier hasn't run» state. We
    don't surface harmful actions until we know what's harmful."""
    snap = _snap(unclassified=50)
    plan = build_plan(snap)
    assert _by_id(plan.actions, "queries:harmful") is None
    assert any("классиф" in d.lower() for d in plan.diagnostics)


def test_harmful_severity_scales() -> None:
    """26 spam + 11 disputed on grandtourspirit (37 of 41 ⇒ ~90% of
    classified) → critical. A small handful of bad on a healthy site
    → low (below medium's count and share thresholds). New ladder
    (2026-05-13): critical requires BOTH bad≥20 AND share≥40 (or
    share≥70 on classified≥5); high needs bad≥8 OR share≥30; medium
    needs bad≥5 OR share≥20; everything else is low."""
    big = build_plan(_snap(own=4, spam=26, disputed=11))
    # 3 bad / 50 classified = 6% — well below every threshold.
    small = build_plan(_snap(own=47, spam=2, disputed=1))
    big_a = _by_id(big.actions, "queries:harmful")
    small_a = _by_id(small.actions, "queries:harmful")
    assert big_a and big_a.severity == "critical"
    assert small_a and small_a.severity == "low"
    assert big_a.evidence["spam"] == 26
    assert big_a.evidence["disputed"] == 11
    # Phase A: title is conversational («Яндекс не понимает кто ты»),
    # the count «37» moves into the body. Pin counts in body instead.
    assert "37" in big_a.body_ru


def test_harmful_min_total_downgrades_tiny_samples() -> None:
    """Earlier rule: 4 spam on a 10-query site = 40% = critical.
    That's noise, not a problem — small samples have noisy ratios.
    With min_total=15 guard, severity drops to medium so the action
    is still surfaced (so the owner CAN act on it) but doesn't
    dominate the plan."""
    # 10 queries total: 4 spam, 6 own. Old rule: critical (40% share).
    # New rule: classified=10 < 15 ⇒ severity downgraded.
    plan = build_plan(_snap(own=6, spam=4, disputed=0))
    a = _by_id(plan.actions, "queries:harmful")
    assert a is not None
    # Below the min_total guard, even with high share, we never go
    # critical or high — just medium (or low if very few bad).
    assert a.severity in ("medium", "low")


def test_harmful_share_basis_is_classified_not_total() -> None:
    """Earlier bug: share = bad / total (including unclassified).
    On half-classified sites this lied: spam=5, total=100,
    unclassified=85 ⇒ share said 5%, real share among classified
    was 33%. Pin the new contract: evidence carries `classified`,
    body uses `classified` (15) as the basis, not `total` (100)."""
    # spam=5, own=10, unclassified=85, total=100, classified=15.
    # bad/classified = 5/15 = 33.3%. Old (buggy) bad/total = 5%.
    snap = _snap(own=10, spam=5, disputed=0, unclassified=85)
    plan = build_plan(snap)
    a = _by_id(plan.actions, "queries:harmful")
    assert a is not None
    assert a.evidence["classified"] == 15
    assert a.evidence["total"] == 100
    # share_pct must be the meaningful number, not the diluted one.
    assert a.evidence["share_pct"] >= 30.0
    # Body must use the classified count (15), not the total (100).
    # If body printed «из 100» that would be the old buggy basis.
    assert "15" in a.body_ru
    assert "из 100" not in a.body_ru


# ── Missing landings ────────────────────────────────────────────────


def test_missing_landings_quotes_real_service_names() -> None:
    """Phase A: actual service names move OUT of body_ru into the
    `examples` array — UI renders them as a separate list. Body keeps
    counts only. This is the «no LLM rewriting» guarantee: we copy
    verbatim from the validated payload, structure is owner-friendly."""
    items = [
        {"service_name": "Крым", "priority": "high",
         "evidence_quote": "набор на экспедиции в Крым"},
        {"service_name": "Яхты", "priority": "high",
         "evidence_quote": "флот яхт от 30 до 50 футов"},
        {"service_name": "Вертолёты", "priority": "high",
         "evidence_quote": "вертолётные туры над Кавказом"},
    ]
    snap = _snap(missing_items=items)
    plan = build_plan(snap)
    a = _by_id(plan.actions, "missing_landings:create")
    assert a is not None
    # Counts present in body, names in examples (no LLM, just data).
    assert a.evidence["high"] == 3
    assert a.severity == "critical"
    labels = [ex["label"] for ex in a.examples]
    assert "Крым" in labels
    assert "Яхты" in labels
    assert "Вертолёты" in labels
    # Each example carries its priority as `kind` and the validated
    # quote as `hint` — UI renders «label + hint as quoted excerpt».
    crimea = next(ex for ex in a.examples if ex["label"] == "Крым")
    assert crimea["kind"] == "high"
    assert "Крым" in (crimea.get("hint") or "")


def test_missing_landings_medium_when_only_low_priority() -> None:
    """One low-priority item alone ⇒ medium severity — not loud."""
    items = [{"service_name": "X", "priority": "low"}]
    plan = build_plan(_snap(missing_items=items))
    a = _by_id(plan.actions, "missing_landings:create")
    assert a is not None
    assert a.severity == "medium"


# ── Reviews & recommendations ───────────────────────────────────────


def test_unreviewed_pages_emits_medium() -> None:
    snap = _snap(pages_total=22, pages_with_review=1)
    plan = build_plan(snap)
    a = _by_id(plan.actions, "review:unreviewed")
    assert a is not None
    assert a.severity == "medium"
    assert a.evidence["pages_without_review"] == 21


def test_pending_recs_high_when_high_priority_present() -> None:
    snap = _snap(recs_pending=10, recs_high_priority_pending=3)
    plan = build_plan(snap)
    a = _by_id(plan.actions, "review:pending_recs")
    assert a is not None
    assert a.severity == "high"
    assert "3" in a.body_ru


def test_pending_recs_medium_when_no_high_priority() -> None:
    snap = _snap(recs_pending=4, recs_high_priority_pending=0)
    plan = build_plan(snap)
    a = _by_id(plan.actions, "review:pending_recs")
    assert a is not None
    assert a.severity == "medium"


# ── Plan-level guarantees ───────────────────────────────────────────


def test_plan_caps_at_max_actions() -> None:
    """Even when every rule fires, we surface only the top N — owner
    gets a *plan*, not a wall of items. Default cap is 5."""
    snap = _snap(
        pages_total=10, pages_in_index=5,
        own=4, spam=10, disputed=10,
        pages_with_review=1,
        recs_pending=5, recs_high_priority_pending=2,
        missing_items=[{"service_name": f"x{i}", "priority": "high"} for i in range(3)],
        pending_followup=2,
    )
    plan = build_plan(snap)
    assert len(plan.actions) <= 5


def test_plan_sorted_by_severity_first() -> None:
    """Critical before high before medium before low. Within the same
    severity, deterministic by id."""
    snap = _snap(
        pages_total=22, pages_in_index=15,  # critical (7 missing)
        own=4, spam=26, disputed=11,        # critical (harmful 37/41)
        pages_with_review=1,                # medium (review)
        pending_followup=2,                 # low (followup)
    )
    plan = build_plan(snap)
    severities = [a.severity for a in plan.actions]
    # No descent allowed: each subsequent severity must be ≥ previous
    # in the ordering critical→low.
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    for i in range(1, len(severities)):
        assert rank[severities[i]] >= rank[severities[i - 1]]


def test_phase_a_every_action_has_what_to_do_and_link() -> None:
    """Phase A: Every action MUST split «и что» (body_ru) from
    «куда тыкать» (what_to_do_ru). Earlier we mashed both into one
    paragraph and the owner had to dig for the imperative.

    Pin: every emitted action has non-empty `what_to_do_ru` and a
    sane link_to/link_label."""
    snap = _snap(
        pages_total=22, pages_in_index=18,           # indexation fires
        own=4, spam=26, disputed=11,                 # harmful fires
        pages_with_review=1,                         # review fires
        recs_pending=5, recs_high_priority_pending=2,
        missing_items=[                              # missing_landings fires
            {"service_name": "X", "priority": "high"},
        ],
        pending_followup=2,                          # followup fires
    )
    plan = build_plan(snap, max_actions=10)
    assert plan.actions  # something fired
    for a in plan.actions:
        assert a.what_to_do_ru, f"action {a.id} has no what_to_do_ru"
        assert a.link_to.startswith("/studio/"), a.id
        assert a.link_label, a.id
        # Phase A contract: title is short, body is the real prose.
        # Body is at least a couple of sentences, not a one-word title.
        assert len(a.body_ru) > len(a.title), a.id


def test_phase_a_harmful_examples_carry_real_query_text() -> None:
    """Harmful action's `examples` array must surface actual query
    text from `sample_harmful` so the owner sees «джинсы багги»,
    not just a count. Without examples the «37 вредных» feels
    abstract and the owner can't validate the claim."""
    sample = [
        {"query_text": "джинсы багги", "relevance": "spam",
         "reason_ru": "это про одежду, не про машины"},
        {"query_text": "багги мото", "relevance": "spam",
         "reason_ru": "это про мотоциклы"},
        {"query_text": "прокат сочи", "relevance": "disputed",
         "reason_ru": "ты не прокат"},
    ]
    snap = _snap(
        own=10, spam=20, disputed=10,
        sample_harmful=sample,
        sample_own=["багги абхазия", "экспедиции на багги"],
    )
    plan = build_plan(snap)
    a = _by_id(plan.actions, "queries:harmful")
    assert a is not None
    labels = [ex["label"] for ex in a.examples]
    assert "джинсы багги" in labels
    assert "прокат сочи" in labels
    # `kind` lets UI render different badges for spam vs disputed.
    kinds = {ex["label"]: ex["kind"] for ex in a.examples}
    assert kinds["джинсы багги"] == "spam"
    assert kinds["прокат сочи"] == "disputed"
    # owner-recognised counter-examples («что МОЁ») weave into body.
    assert "багги абхазия" in a.body_ru


def test_phase_a_indexation_examples_are_real_urls() -> None:
    """Indexation action's `examples` carry the actual URL strings
    we found via SQL, so the owner sees «вот эти 3 страницы
    конкретно» rather than just a number."""
    urls = [
        "https://example.ru/page-a",
        "https://example.ru/page-b",
        "https://example.ru/page-c",
    ]
    snap = _snap(
        pages_total=22, pages_in_index=18,
        sample_not_indexed_urls=urls,
    )
    plan = build_plan(snap)
    a = _by_id(plan.actions, "indexation:not_indexed")
    assert a is not None
    labels = [ex["label"] for ex in a.examples]
    assert all(u in labels for u in urls)
    # All URL examples carry kind="url" so UI renders them clickable.
    assert all(ex["kind"] == "url" for ex in a.examples)


# ── New ladder + hysteresis regressions (2026-05-13) ───────────────


def test_harmful_six_of_fifteen_is_not_critical() -> None:
    """6 spam / 15 classified (40% share) used to ring as critical
    because share alone (≥40%) was enough. With the new AND-gated
    critical rule plus the small-sample clamp, 6-of-15 is medium —
    a real but proportionate signal. Crucially: NOT critical."""
    # own=9, spam=6: bad=6, classified=15, share≈40%
    plan = build_plan(_snap(own=9, spam=6))
    a = _by_id(plan.actions, "queries:harmful")
    assert a is not None
    assert a.severity == "medium"
    assert a.severity != "critical"


def test_harmful_full_small_site_is_high_not_medium() -> None:
    """5 of 5 classified queries are spam — 100% harm on a 5-query
    site. Old rule clamped this to medium because classified<15.
    New rule: when share≥70% on classified≥5, allow `high` (but
    cap at high — small absolute count still keeps it out of
    critical territory)."""
    plan = build_plan(_snap(spam=5))
    a = _by_id(plan.actions, "queries:harmful")
    assert a is not None
    assert a.severity == "high"


def test_pending_recs_in_focus_from_url() -> None:
    """When at least one pending rec lives on a focus-matching URL,
    the action picks up `in_focus=True` so the focus-aware sort
    promotes it ahead of out-of-focus items at the same severity."""
    snap = _snap(
        recs_pending=2,
        recs_high_priority_pending=1,
    )
    # Snapshot helper doesn't expose top_pending_recommendations —
    # set it directly on the review facts so the rule can read URLs.
    snap.review.top_pending_recommendations = [
        {"url": "https://example.ru/excursions/krym/buggy", "rec_id": "1"},
        {"url": "https://example.ru/about", "rec_id": "2"},
    ]
    plan = build_plan(
        snap,
        target_config={
            "strategic_focus": {"products": ["крым"], "regions": [], "query_signals": []},
        },
    )
    a = _by_id(plan.actions, "review:pending_recs")
    assert a is not None
    assert a.in_focus is True


def test_indexation_fallback_when_buckets_dont_sum() -> None:
    """If `pages_total` doesn't line up with the breakdown buckets
    we fall back to subtraction (clamped at 0) and log the
    discrepancy. The action still fires with a sane count rather
    than crashing or showing a negative number."""
    snap = _snap(pages_total=20, pages_in_index=5, pages_excluded=2, pages_unknown=3)
    plan = build_plan(snap)
    a = _by_id(plan.actions, "indexation:not_indexed")
    # Subtraction: 20 - 5 - 2 - 3 = 10
    assert a is not None
    assert a.evidence["not_indexed"] == 10


def test_decision_tree_hysteresis_at_strong_boundary() -> None:
    """Pages oscillating around score 4.0 should not flip LEAVE ↔
    STRENGTHEN day to day. Inside the borderline band (±0.25 of
    STRONG_SCORE) both directions resolve to LEAVE with
    `borderline=True` in evidence."""
    from app.core_audit.decision_tree import (
        BORDERLINE_BAND, STRONG_SCORE, WEAK_SCORE_MIN, _is_borderline,
    )

    # Hairline above and below the cutoff both count as borderline.
    assert _is_borderline(STRONG_SCORE + 0.1, STRONG_SCORE) is True
    assert _is_borderline(STRONG_SCORE - 0.1, STRONG_SCORE) is True
    # Far below the cutoff is not borderline.
    assert _is_borderline(STRONG_SCORE - 1.0, STRONG_SCORE) is False
    # Same band on the weak boundary.
    assert _is_borderline(WEAK_SCORE_MIN + 0.2, WEAK_SCORE_MIN) is True
    assert _is_borderline(WEAK_SCORE_MIN - 0.2, WEAK_SCORE_MIN) is True
    # Edge of band: exactly at BORDERLINE_BAND still counts.
    assert _is_borderline(STRONG_SCORE + BORDERLINE_BAND, STRONG_SCORE) is True
    # None propagates as not-borderline.
    assert _is_borderline(None, STRONG_SCORE) is False


def test_evidence_carries_real_counts_not_prose() -> None:
    """Evidence dict is the «receipt» — it must contain numbers from
    the snapshot, never re-derived strings. Pin one rule explicitly so
    a future refactor doesn't quietly switch evidence to free-form
    text."""
    snap = _snap(own=4, spam=26, disputed=11)
    plan = build_plan(snap)
    a = _by_id(plan.actions, "queries:harmful")
    assert a is not None
    assert isinstance(a.evidence["spam"], int)
    assert isinstance(a.evidence["disputed"], int)
    assert isinstance(a.evidence["share_pct"], float)
    assert a.evidence["spam"] == 26
    assert a.evidence["disputed"] == 11


# ── Funnel-coverage rules (Block C, added 2026-05-16) ────────────────


def _funnel_snap(
    *,
    direct_count: int = 0,
    direct_volume: int = 0,
    warm_count: int = 0,
    warm_volume: int = 0,
    top_count: int = 0,
    top_volume: int = 0,
    top_pages: int = 0,
    out_of_market: int = 0,
) -> BrainSnapshot:
    """Funnel-only snapshot. Indexation / queries / review stay at zero
    so the rules tested here are the ONLY ones that can fire."""
    from app.core_audit.brain.snapshot import FunnelFacts

    snap = _snap(pages_total=0, pages_in_index=0, pages_with_review=0)
    snap.funnel = FunnelFacts(
        direct_product_count=direct_count,
        direct_product_total_volume=direct_volume,
        funnel_warm_count=warm_count,
        funnel_warm_total_volume=warm_volume,
        funnel_top_count=top_count,
        funnel_top_total_volume=top_volume,
        funnel_top_total_volume_kmo=round(top_volume / 1000.0) if top_volume else 0,
        funnel_top_pages_count=top_pages,
        out_of_market_count=out_of_market,
    )
    return snap


def test_rule_funnel_top_gap_fires_when_demand_no_pages() -> None:
    """50 funnel_top queries, 0 pages targeting → high-severity card."""
    snap = _funnel_snap(top_count=50, top_volume=30_000, top_pages=0)
    plan = build_plan(snap, max_actions=10)
    a = _by_id(plan.actions, "funnel:top_gap")
    assert a is not None
    assert a.severity == "high"
    assert a.evidence["funnel_top_count"] == 50
    assert a.evidence["funnel_top_pages_count"] == 0
    assert a.link_to == "/studio/queries?layer=funnel_top"
    # Title must mention the count + volume in human form.
    assert "50" in a.title
    assert "тыс" in a.title


def test_rule_funnel_top_gap_silent_when_pages_exist() -> None:
    """If the site already ranks on at least one funnel_top query,
    the gap rule stays silent — coverage exists, no need to nag."""
    snap = _funnel_snap(top_count=20, top_volume=10_000, top_pages=5)
    plan = build_plan(snap, max_actions=10)
    assert _by_id(plan.actions, "funnel:top_gap") is None


def test_rule_funnel_top_gap_silent_when_no_demand() -> None:
    """Zero funnel_top queries → silent (nothing to capture)."""
    snap = _funnel_snap(top_count=0, top_volume=0, top_pages=0)
    plan = build_plan(snap, max_actions=10)
    assert _by_id(plan.actions, "funnel:top_gap") is None


def test_rule_funnel_warm_underserved_fires_on_imbalance() -> None:
    """warm_volume > 2× direct_volume AND ≥10 warm queries → medium."""
    snap = _funnel_snap(
        direct_count=5, direct_volume=2_000,
        warm_count=30, warm_volume=12_000,    # 6× ratio
    )
    plan = build_plan(snap, max_actions=10)
    a = _by_id(plan.actions, "funnel:warm_underserved")
    assert a is not None
    assert a.severity == "medium"
    assert a.evidence["funnel_warm_count"] == 30
    assert a.evidence["direct_product_total_volume"] == 2_000


def test_rule_funnel_warm_underserved_silent_when_balanced() -> None:
    """warm ≤ 2× direct → silent."""
    snap = _funnel_snap(
        direct_count=5, direct_volume=5_000,
        warm_count=20, warm_volume=8_000,     # 1.6× ratio
    )
    plan = build_plan(snap, max_actions=10)
    assert _by_id(plan.actions, "funnel:warm_underserved") is None


def test_rule_funnel_warm_underserved_silent_below_min_count() -> None:
    """Fewer than 10 warm queries → silent (signal too small)."""
    snap = _funnel_snap(
        direct_count=2, direct_volume=500,
        warm_count=5, warm_volume=20_000,
    )
    plan = build_plan(snap, max_actions=10)
    assert _by_id(plan.actions, "funnel:warm_underserved") is None


def test_rule_funnel_warm_underserved_silent_with_no_direct() -> None:
    """No direct_product demand → the `top_gap` rule already covers
    that case; this rule stays silent to avoid double-firing."""
    snap = _funnel_snap(
        direct_count=0, direct_volume=0,
        warm_count=30, warm_volume=20_000,
    )
    plan = build_plan(snap, max_actions=10)
    assert _by_id(plan.actions, "funnel:warm_underserved") is None


def test_rule_out_of_market_summary_info_severity_only() -> None:
    """Summary card is `low` (the lowest brain severity) and links to
    the filtered query view. Wording must be transparent — owner sees
    we KNOW about those queries."""
    snap = _funnel_snap(out_of_market=12)
    plan = build_plan(snap, max_actions=10)
    a = _by_id(plan.actions, "funnel:out_of_market_summary")
    assert a is not None
    assert a.severity == "low"
    assert a.evidence["out_of_market_count"] == 12
    assert a.link_to == "/studio/queries?layer=out_of_market"
    # Body must explain WHY we excluded — not just a count.
    assert "регион" in a.body_ru.lower() or "рынок" in a.body_ru.lower()


def test_rule_out_of_market_summary_silent_when_zero() -> None:
    snap = _funnel_snap(out_of_market=0)
    plan = build_plan(snap, max_actions=10)
    assert _by_id(plan.actions, "funnel:out_of_market_summary") is None
