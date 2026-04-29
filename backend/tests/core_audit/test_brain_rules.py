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
        ),
        queries=QueriesFacts(
            total=queries_total,
            own=own, adjacent=adjacent, disputed=disputed,
            spam=spam, unclassified=unclassified,
            with_volume=queries_with_volume,
            classified_at=None,
        ),
        review=ReviewFacts(
            pages_with_review=pages_with_review,
            pages_without_review=max(0, pages_total - pages_with_review),
            recs_pending=recs_pending,
            recs_high_priority_pending=recs_high_priority_pending,
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


def test_indexation_severity_scales_with_gap() -> None:
    """3+ missing → critical. 1-2 missing → high. The point is to
    grab the owner's attention proportionally — not to spam alarms
    when one page is just slow to index."""
    high = build_plan(_snap(pages_total=10, pages_in_index=9))
    crit = build_plan(_snap(pages_total=10, pages_in_index=5))
    h_a = _by_id(high.actions, "indexation:not_indexed")
    c_a = _by_id(crit.actions, "indexation:not_indexed")
    assert h_a and h_a.severity == "high"
    assert c_a and c_a.severity == "critical"


# ── Harmful visibility ──────────────────────────────────────────────


def test_harmful_silent_when_no_classifier_run() -> None:
    """All-unclassified is the «classifier hasn't run» state. We
    don't surface harmful actions until we know what's harmful."""
    snap = _snap(unclassified=50)
    plan = build_plan(snap)
    assert _by_id(plan.actions, "queries:harmful") is None
    assert any("классиф" in d.lower() for d in plan.diagnostics)


def test_harmful_severity_scales() -> None:
    """26 spam + 11 disputed on grandtourspirit (37 of 45 = 82%) ⇒
    critical. 5 spam total ⇒ medium. The thresholds are deliberately
    set so a clean site stays calm."""
    big = build_plan(_snap(own=4, spam=26, disputed=11))
    small = build_plan(_snap(own=20, spam=3, disputed=2))
    big_a = _by_id(big.actions, "queries:harmful")
    small_a = _by_id(small.actions, "queries:harmful")
    assert big_a and big_a.severity == "critical"
    assert small_a and small_a.severity == "medium"
    assert big_a.evidence["spam"] == 26
    assert big_a.evidence["disputed"] == 11
    assert "37" in big_a.title  # spam + disputed


# ── Missing landings ────────────────────────────────────────────────


def test_missing_landings_quotes_real_service_names() -> None:
    """The body must include actual service names from the items.
    This is the «no LLM rewriting» guarantee — we copy verbatim from
    the validated payload."""
    items = [
        {"service_name": "Крым", "priority": "high"},
        {"service_name": "Яхты", "priority": "high"},
        {"service_name": "Вертолёты", "priority": "high"},
    ]
    snap = _snap(missing_items=items)
    plan = build_plan(snap)
    a = _by_id(plan.actions, "missing_landings:create")
    assert a is not None
    assert "«Крым»" in a.body_ru
    assert "«Яхты»" in a.body_ru
    assert a.evidence["high"] == 3
    assert a.severity == "critical"


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
