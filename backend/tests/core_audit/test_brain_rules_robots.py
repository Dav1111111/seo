"""Tests for `_rule_robots_critical` — robots.txt critical issues.

Three contracts pinned here:

  1. The rule stays silent when `robots_critical_issues == 0`. A
     never-ran or clean audit must not generate a brain action.
  2. With `n >= 1` it emits an Action with `severity == "critical"`,
     a Russian title that mentions the count, and the standard
     /studio/indexation deep-link the other indexation rules use.
  3. The Action respects the same `in_focus` / `evidence` / `link_to`
     contract as the rest of `rules._RULES` — no extra fields, no
     missing ones, so the focus-aware sort and the UI renderer keep
     working unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core_audit.brain.rules import Action, _rule_robots_critical
from app.core_audit.brain.snapshot import (
    BrainSnapshot,
    IndexationFacts,
    MissingLandingsFacts,
    OutcomesFacts,
    QueriesFacts,
    ReviewFacts,
)


def _snap(
    *,
    robots_critical_issues: int = 0,
    robots_valid_for_yandex: bool = True,
) -> BrainSnapshot:
    """Bare-minimum snapshot — only the robots fields matter here."""
    return BrainSnapshot(
        site_id="00000000-0000-0000-0000-000000000000",
        domain="example.ru",
        computed_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
        indexation=IndexationFacts(
            pages_total=10,
            pages_in_index=10,
            pages_excluded=0,
            pages_unknown=0,
            coverage_pct=100.0,
        ),
        queries=QueriesFacts(
            total=0, own=0, adjacent=0, disputed=0, spam=0,
            unclassified=0, with_volume=0, classified_at=None,
        ),
        review=ReviewFacts(
            pages_with_review=10,
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
        robots_critical_issues=robots_critical_issues,
        robots_valid_for_yandex=robots_valid_for_yandex,
    )


def test_rule_robots_critical_silent_when_no_issues() -> None:
    """No critical issues → no action. The rule must NOT fire on
    healthy or never-audited sites, otherwise the «what to do this
    week» plan would be polluted with noise."""
    snap = _snap(robots_critical_issues=0, robots_valid_for_yandex=True)
    assert _rule_robots_critical(snap, focus_tokens=[]) is None


def test_rule_robots_critical_emits_action() -> None:
    """`n=2` critical issues → a `critical` Action whose title carries
    the count verbatim in Russian. We also pin the link target so the
    UI renderer can route the button without sniffing for the id."""
    snap = _snap(robots_critical_issues=2, robots_valid_for_yandex=True)
    action = _rule_robots_critical(snap, focus_tokens=[])

    assert isinstance(action, Action)
    assert action.severity == "critical"
    assert "2" in action.title
    # Russian copy must mention robots and Yandex — owner needs to
    # recognise what's being talked about.
    assert "robots.txt" in action.title
    assert "Яндекс" in action.title or "яндекс" in action.title.lower()
    assert action.link_to == "/studio/indexation"
    # `evidence` carries the receipt for the count.
    assert action.evidence.get("critical_issues") == 2


def test_rule_robots_critical_in_focus_field() -> None:
    """robots.txt is site-wide — there's no per-focus signal. The
    Action must therefore declare `in_focus=False` regardless of
    focus tokens, and still expose the standard CTA contract."""
    snap = _snap(robots_critical_issues=1, robots_valid_for_yandex=False)

    # Even with rich focus tokens, robots criticals are site-wide.
    action = _rule_robots_critical(snap, focus_tokens=["абхазия", "багги"])
    assert isinstance(action, Action)
    assert action.in_focus is False
    # Standard Action contract: every rule populates these strings.
    assert action.id == "robots:critical"
    assert action.link_label
    assert action.what_to_do_ru
    assert action.body_ru
    # When the audit found the file invalid, the body should not lie
    # about cleanliness — it must reference the unavailable/invalid
    # state. We accept any of several wordings.
    body_lower = action.body_ru.lower()
    assert any(
        marker in body_lower
        for marker in ("недоступ", "не распарс", "не парсится")
    ), action.body_ru
    # `evidence` carries the validity flag verbatim so downstream
    # consumers (battle plan, free chat) can branch on it.
    assert action.evidence.get("valid_for_yandex") is False
