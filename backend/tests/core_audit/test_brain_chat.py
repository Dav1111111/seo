"""Tests for app.core_audit.brain.chat — Phase B chat endpoint.

Two surfaces pinned:

  build_user_message — every action becomes a structured prompt block
  the LLM can ground its answer in. We don't ship its prose to the
  model directly — we ship action title + body + examples + slice of
  the snapshot tied to the action id. Pin the format so prompt drift
  doesn't sneak in.

  chat_about_action — verifies the wrapper passes the right context,
  trims overlong messages, and respects history caps. We mock
  `call_plain` so no network touches.

Pattern matches `tests/core_audit/test_brain_rules.py` — pure functions,
no DB, no asyncio.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.core_audit.brain.chat import (
    MAX_HISTORY_MESSAGES,
    MAX_USER_MESSAGE_CHARS,
    build_user_message,
    chat_about_action,
)
from app.core_audit.brain.rules import Action
from app.core_audit.brain.snapshot import (
    BrainSnapshot,
    IndexationFacts,
    QueriesFacts,
    ReviewFacts,
    MissingLandingsFacts,
    OutcomesFacts,
)


# ── Fixtures ─────────────────────────────────────────────────────────


def _action(
    *,
    id: str = "queries:harmful",
    title: str = "Яндекс не понимает кто ты",
    body: str = "Из 45 запросов 37 — про чужую тему.",
    what_to_do: str = "Открой Вредная видимость.",
    examples: list[dict] | None = None,
    evidence: dict | None = None,
) -> Action:
    return Action(
        id=id,
        severity="critical",
        title=title,
        body_ru=body,
        what_to_do_ru=what_to_do,
        link_to="/studio/queries/harmful",
        link_label="К отчёту",
        examples=examples or [
            {"label": "джинсы багги", "kind": "spam", "hint": "про одежду"},
            {"label": "polaris цена", "kind": "spam", "hint": "не твой бренд"},
        ],
        evidence=evidence or {"spam": 26, "disputed": 11, "share_pct": 82.2},
    )


def _snap() -> BrainSnapshot:
    return BrainSnapshot(
        site_id="00000000-0000-0000-0000-000000000000",
        domain="grandtourspirit.ru",
        computed_at=datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
        indexation=IndexationFacts(
            pages_total=22, pages_in_index=18,
            pages_excluded=0, pages_unknown=4,
            coverage_pct=81.8,
            sample_not_indexed_urls=[],
            sample_excluded=[],
        ),
        queries=QueriesFacts(
            total=45, own=4, adjacent=4, disputed=11, spam=26,
            unclassified=0, with_volume=0, classified_at=None,
            sample_harmful=[
                {"query_text": "джинсы багги", "relevance": "spam", "reason_ru": "одежда"},
            ],
            sample_own=["багги абхазия", "экспедиции на багги"],
        ),
        review=ReviewFacts(
            pages_with_review=1, pages_without_review=21,
            recs_pending=0, recs_high_priority_pending=0,
            sample_unreviewed_urls=[],
        ),
        missing_landings=MissingLandingsFacts(
            total=5, high_priority=4, medium_priority=1, low_priority=0,
            items=[
                {"service_name": "Крым", "priority": "high"},
            ],
        ),
        outcomes=OutcomesFacts(
            applied_total=0, applied_last_14d=0, pending_followup=0,
        ),
    )


# ── build_user_message — prompt-snapshot tests ───────────────────────


def test_user_message_carries_action_title_body_and_examples() -> None:
    """The model must SEE the action it's being asked about — title,
    body, examples and evidence. Earlier we considered just sending
    the action id and reconstructing on the LLM side; that was a
    foot-gun (model can't know the body). Pin the inclusion."""
    a = _action()
    msg = build_user_message(
        action=a, snap=_snap(), history=[],
        new_message="а почему именно эти?",
    )
    assert "Яндекс не понимает кто ты" in msg
    assert "Из 45 запросов 37 — про чужую тему" in msg
    assert "джинсы багги" in msg
    assert "polaris цена" in msg
    assert "spam=26" in msg
    assert "disputed=11" in msg
    assert "а почему именно эти?" in msg


def test_snapshot_slice_is_per_action_not_dump() -> None:
    """For action_id=queries:harmful we surface ONLY query facts, not
    indexation / outcomes / review. Earlier we dumped everything and
    the model started latching on irrelevant numbers."""
    a = _action(id="queries:harmful")
    msg = build_user_message(
        action=a, snap=_snap(), history=[],
        new_message="что мне делать?",
    )
    # Query slice present.
    assert "запросов всего: 45" in msg
    assert "«мои»: 4" in msg
    assert "спам: 26" in msg
    # Indexation slice MUST NOT leak into a queries-action prompt.
    assert "в индексе Яндекса" not in msg
    assert "ждут замера через 14 дней" not in msg


def test_snapshot_slice_picks_indexation_facts_for_indexation_action() -> None:
    """For action_id=indexation:not_indexed we send indexation facts
    instead. Same selector logic, mirror test."""
    a = _action(
        id="indexation:not_indexed",
        title="Яндекс не видит N страниц",
        body="...",
        what_to_do="Открой Индексацию.",
        examples=[],
        evidence={"pages_total": 22, "in_index": 18},
    )
    msg = build_user_message(
        action=a, snap=_snap(), history=[],
        new_message="почему?",
    )
    assert "в индексе Яндекса: 18" in msg
    assert "пока неизвестно: 4" in msg
    # No query slice for indexation action.
    assert "запросов всего:" not in msg


def test_history_appears_in_message_with_role_tags() -> None:
    """History is folded into the user message (we don't use
    Anthropic's multi-turn API on purpose — keeps prompt-cache
    semantics simple). Pin that tags are present."""
    a = _action()
    history = [
        {"role": "user", "content": "а это правда вредно?"},
        {"role": "assistant", "content": "да, потому что Яндекс…"},
    ]
    msg = build_user_message(
        action=a, snap=_snap(), history=history,
        new_message="а как починить?",
    )
    assert "ВЛАДЕЛЕЦ: а это правда вредно?" in msg
    assert "ТЫ: да, потому что Яндекс…" in msg
    assert "ВЛАДЕЛЕЦ СЕЙЧАС СПРАШИВАЕТ: а как починить?" in msg


def test_history_truncates_to_max_window() -> None:
    """Long convos shouldn't blow input. We keep last
    MAX_HISTORY_MESSAGES turns."""
    a = _action()
    long_history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i}"}
        for i in range(MAX_HISTORY_MESSAGES + 5)
    ]
    msg = build_user_message(
        action=a, snap=_snap(), history=long_history,
        new_message="x",
    )
    # The earliest messages drop out.
    assert "msg-0" not in msg
    assert "msg-1" not in msg
    # The last MAX_HISTORY_MESSAGES are kept.
    last_idx = MAX_HISTORY_MESSAGES + 4
    assert f"msg-{last_idx}" in msg


# ── chat_about_action — wrapper integration ─────────────────────────


def test_chat_about_action_returns_reply_and_cost() -> None:
    """Happy path. Mocked LLM returns a string + usage; the wrapper
    surfaces both. Pin the public shape."""
    fake_reply = "Это про одежду, к багги не относится."
    fake_usage = {
        "model": "claude-haiku-4-5",
        "input_tokens": 600,
        "output_tokens": 25,
        "cost_usd": 0.0028,
    }
    with patch(
        "app.core_audit.brain.chat.call_plain",
        return_value=(fake_reply, fake_usage),
    ) as mock_call:
        result = chat_about_action(
            action=_action(),
            snap=_snap(),
            history=[],
            new_message="что значит «джинсы багги» в моих запросах?",
        )

    assert result["reply"] == fake_reply
    assert result["cost_usd"] == 0.0028
    assert result["model"] == "claude-haiku-4-5"
    assert result["input_tokens"] == 600
    assert result["output_tokens"] == 25
    # The wrapper passes our composed user_message + the cached system
    # prompt — pin that the SDK was called with kwargs we expect.
    call_kwargs = mock_call.call_args.kwargs
    assert call_kwargs["model_tier"] == "cheap"
    assert "ты — внутренний помощник" in call_kwargs["system"].lower()
    assert "что значит «джинсы багги»" in call_kwargs["user_message"]


def test_chat_truncates_overlong_message() -> None:
    """A 10 000-character user message is almost always paste-noise.
    We cap at MAX_USER_MESSAGE_CHARS so the model isn't drowned —
    the cap message stays inline so owner sees what got cut."""
    fake_usage = {
        "model": "h", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0,
    }
    long_msg = "x" * (MAX_USER_MESSAGE_CHARS + 500)
    with patch(
        "app.core_audit.brain.chat.call_plain",
        return_value=("ok", fake_usage),
    ) as mock_call:
        chat_about_action(
            action=_action(), snap=_snap(),
            history=[], new_message=long_msg,
        )
    user_msg = mock_call.call_args.kwargs["user_message"]
    # The original 10 500-char message must be cut down. Cap is
    # MAX_USER_MESSAGE_CHARS + the "[...обрезано]" suffix.
    assert "[…обрезано]" in user_msg
    assert "x" * (MAX_USER_MESSAGE_CHARS + 100) not in user_msg


def test_chat_rejects_empty_message() -> None:
    """Empty message means owner clicked Send by accident — don't
    burn an LLM call on it."""
    import pytest
    with pytest.raises(ValueError):
        chat_about_action(
            action=_action(), snap=_snap(),
            history=[], new_message="   ",
        )
