"""Tests for app.core_audit.brain.free_chat — Phase C free chat.

Two surfaces pinned:

  build_user_message — the prompt is the contract. We pin that the
  business profile + observed facts + ALL snapshot sections + plan
  appear in the message. If a future refactor accidentally drops one
  (e.g. forgets outcomes), the LLM will give worse answers and these
  tests catch it.

  free_chat — the wrapper sanitises overlong messages, refuses empty
  ones, and surfaces the LLM reply + cost. Mocked LLM, no network.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.core_audit.brain.free_chat import (
    MAX_HISTORY_MESSAGES,
    MAX_USER_MESSAGE_CHARS,
    build_user_message,
    free_chat,
)
from app.core_audit.brain.rules import Action, Plan
from app.core_audit.brain.snapshot import (
    BrainSnapshot,
    IndexationFacts,
    QueriesFacts,
    ReviewFacts,
    MissingLandingsFacts,
    OutcomesFacts,
)


# ── Fixtures ─────────────────────────────────────────────────────────


def _snap() -> BrainSnapshot:
    return BrainSnapshot(
        site_id="00000000-0000-0000-0000-000000000000",
        domain="grandtourspirit.ru",
        computed_at=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
        indexation=IndexationFacts(
            pages_total=22, pages_in_index=18,
            pages_excluded=0, pages_unknown=4,
            coverage_pct=81.8,
            sample_not_indexed_urls=[
                "https://grandtourspirit.ru/orphan-1",
                "https://grandtourspirit.ru/orphan-2",
            ],
            sample_excluded=[],
        ),
        queries=QueriesFacts(
            total=45, own=4, adjacent=4, disputed=11, spam=26,
            unclassified=0, with_volume=20, classified_at=None,
            sample_harmful=[
                {"query_text": "джинсы багги", "relevance": "spam",
                 "reason_ru": "это про одежду, не про твои туры"},
            ],
            sample_own=["багги абхазия", "экспедиции на багги"],
        ),
        review=ReviewFacts(
            pages_with_review=2, pages_without_review=20,
            recs_pending=0, recs_high_priority_pending=0,
            sample_unreviewed_urls=[
                "https://grandtourspirit.ru/stories/post-2",
            ],
        ),
        missing_landings=MissingLandingsFacts(
            total=5, high_priority=4, medium_priority=1, low_priority=0,
            items=[
                {
                    "service_name": "Багги-экспедиция в Крым",
                    "priority": "high",
                    "evidence_quote": "На сезон 2026 открыт набор",
                },
            ],
        ),
        outcomes=OutcomesFacts(
            applied_total=0, applied_last_14d=0, pending_followup=0,
        ),
    )


def _plan(actions: list[Action] | None = None) -> Plan:
    return Plan(
        site_id="00000000-0000-0000-0000-000000000000",
        domain="grandtourspirit.ru",
        actions=actions or [],
        diagnostics=[],
        computed_at=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc).isoformat(),
    )


def _action(id: str = "queries:harmful") -> Action:
    return Action(
        id=id, severity="critical",
        title="Яндекс не понимает кто ты",
        body_ru="…",
        what_to_do_ru="Открой Вредная видимость.",
        link_to="/studio/queries/harmful",
        link_label="К отчёту",
        examples=[],
        evidence={"spam": 26},
    )


def _target_config() -> dict:
    return {
        "primary_product": "багги-экспедиции",
        "services": ["экспедиции", "багги"],
        "secondary_products": ["вертолёты", "яхты"],
        "geo_primary": ["сочи", "абхазия"],
        "geo_secondary": ["крым"],
    }


def _understanding() -> dict:
    return {
        "narrative_ru": (
            "Премиальный клуб активного отдыха в Сочи. "
            "Багги-экспедиции по Абхазии и Черноморскому побережью."
        ),
        "observed_facts": [
            {
                "fact": "На главной странице 1200+ клиентов в месяц.",
                "page_ref": "https://grandtourspirit.ru/",
            },
            {
                "fact": "В 2026 открыт набор на экспедиции в Крым.",
                "page_ref": "https://grandtourspirit.ru/",
            },
        ],
    }


# ── build_user_message — context inclusion ───────────────────────────


def test_user_message_carries_business_profile() -> None:
    """Without business profile the LLM doesn't know «what is this
    site about». Pin presence of domain, primary product, services,
    regions, narrative, and observed facts."""
    msg = build_user_message(
        domain="grandtourspirit.ru",
        target_config=_target_config(),
        understanding=_understanding(),
        snap=_snap(),
        plan=_plan(),
        history=[],
        new_message="что мне делать?",
    )
    assert "САЙТ: grandtourspirit.ru" in msg
    assert "багги-экспедиции" in msg
    assert "экспедиции, багги" in msg
    assert "сочи, абхазия" in msg
    assert "Премиальный клуб активного отдыха" in msg
    # Observed fact (objective signal from crawl) makes it through.
    assert "1200+ клиентов в месяц" in msg


def test_user_message_carries_full_snapshot_all_five_sections() -> None:
    """Free chat must see ALL snapshot sections — owner can ask
    about any of them. Per-action chat slices; free chat does NOT.
    Pin each of the 5 section headers + at least one fact per section."""
    msg = build_user_message(
        domain="grandtourspirit.ru",
        target_config={},
        understanding={},
        snap=_snap(),
        plan=_plan(),
        history=[],
        new_message="?",
    )
    # Five sections + at least one bullet from each.
    assert "Индексация:" in msg
    assert "в индексе Яндекса: 18" in msg

    assert "Запросы:" in msg
    assert "спам: 26" in msg

    assert "Ревью страниц:" in msg
    assert "без ревью: 20" in msg

    assert "Услуги без отдельной страницы:" in msg
    assert "всего: 5" in msg

    assert "Применённые правки и замеры:" in msg
    assert "ждут замера через 14 дней: 0" in msg


def test_user_message_carries_examples_for_grounding() -> None:
    """Every section's sample_* fields surface so the LLM can quote
    real names instead of «один из спам-запросов»."""
    msg = build_user_message(
        domain="x", target_config={}, understanding={},
        snap=_snap(), plan=_plan(), history=[],
        new_message="?",
    )
    # Sample harmful query + reason
    assert "джинсы багги" in msg
    assert "это про одежду" in msg
    # Sample own queries
    assert "багги абхазия" in msg
    # Sample not-indexed URL
    assert "https://grandtourspirit.ru/orphan-1" in msg
    # Sample missing landing
    assert "Багги-экспедиция в Крым" in msg


def test_user_message_carries_plan_when_actions_present() -> None:
    """The plan tells the LLM «here's what's already been recommended»
    so it directs owner to those actions instead of inventing new
    ones. Pin presence of titles + severity tags + module links."""
    plan = _plan([
        _action(id="queries:harmful"),
        Action(
            id="missing_landings:create", severity="high",
            title="5 услуг живёт без своей страницы",
            body_ru="…", what_to_do_ru="…",
            link_to="/studio/competitors", link_label="К услугам",
            examples=[], evidence={"total": 5},
        ),
    ])
    msg = build_user_message(
        domain="x", target_config={}, understanding={},
        snap=_snap(), plan=plan, history=[],
        new_message="что важнее?",
    )
    assert "ТЕКУЩИЙ ПЛАН" in msg
    assert "Яндекс не понимает кто ты" in msg
    assert "5 услуг живёт без своей страницы" in msg
    assert "/studio/queries/harmful" in msg
    assert "/studio/competitors" in msg


def test_user_message_explains_empty_plan() -> None:
    """If plan is empty (clean site or modules not run) we say so —
    no need for the LLM to guess what plan items exist."""
    msg = build_user_message(
        domain="x", target_config={}, understanding={},
        snap=_snap(), plan=_plan(), history=[],
        new_message="?",
    )
    assert "ТЕКУЩИЙ ПЛАН: пусто" in msg


def test_user_message_history_uses_role_tags() -> None:
    """History folded into the message with explicit role tags so the
    LLM can read it as a transcript without us using SDK multi-turn."""
    history = [
        {"role": "user", "content": "<previous-question>"},
        {"role": "assistant", "content": "<previous-answer>"},
    ]
    msg = build_user_message(
        domain="x", target_config={}, understanding={},
        snap=_snap(), plan=_plan(), history=history,
        new_message="<current-question>",
    )
    assert "ВЛАДЕЛЕЦ: <previous-question>" in msg
    assert "ТЫ: <previous-answer>" in msg
    assert "ВЛАДЕЛЕЦ СЕЙЧАС СПРАШИВАЕТ: <current-question>" in msg


def test_user_message_history_truncates_at_cap() -> None:
    """Long convos kept to the last MAX_HISTORY_MESSAGES turns."""
    history = [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"<turn-{i:03d}>",
        }
        for i in range(MAX_HISTORY_MESSAGES + 5)
    ]
    msg = build_user_message(
        domain="x", target_config={}, understanding={},
        snap=_snap(), plan=_plan(), history=history,
        new_message="x",
    )
    assert "<turn-000>" not in msg
    assert "<turn-001>" not in msg
    last_idx = MAX_HISTORY_MESSAGES + 4
    assert f"<turn-{last_idx:03d}>" in msg


# ── free_chat — wrapper integration ─────────────────────────────────


def test_free_chat_returns_reply_and_usage() -> None:
    """Happy path — model picked plain text. Mocked
    call_with_optional_tools returns {text, tool_use=None} + usage;
    wrapper surfaces both. Pin the response shape."""
    fake_usage = {
        "model": "claude-haiku-4-5",
        "input_tokens": 1500, "output_tokens": 100,
        "cost_usd": 0.0042,
    }
    with patch(
        "app.core_audit.brain.free_chat.call_with_optional_tools",
        return_value=(
            {"text": "Вот мой ответ.", "tool_use": None},
            fake_usage,
        ),
    ) as mock_call:
        result = free_chat(
            domain="grandtourspirit.ru",
            target_config=_target_config(),
            understanding=_understanding(),
            snap=_snap(),
            plan=_plan(),
            history=[],
            new_message="что у меня самое слабое место?",
        )

    assert result["reply"] == "Вот мой ответ."
    assert result["proposal"] is None
    assert result["cost_usd"] == 0.0042
    assert result["model"] == "claude-haiku-4-5"
    assert result["input_tokens"] == 1500
    assert result["output_tokens"] == 100

    # The system prompt must contain the anti-fabrication rules.
    sys = mock_call.call_args.kwargs["system"]
    assert "не выдумывай" in sys.lower() or "не придумывай" in sys.lower()
    # User message contains the actual question.
    assert "что у меня самое слабое место?" in mock_call.call_args.kwargs["user_message"]


def test_free_chat_returns_proposal_when_tool_picked() -> None:
    """Phase E step 2 — when the LLM picks the
    propose_strategic_focus tool, the wrapper surfaces a structured
    proposal alongside (or instead of) plain text. Pin the shape so
    the API model + frontend modal stay in sync."""
    fake_usage = {
        "model": "claude-haiku-4-5",
        "input_tokens": 2100, "output_tokens": 150,
        "cost_usd": 0.0089,
    }
    tool_call = {
        "name": "propose_strategic_focus",
        "input": {
            "label": "Багги-экспедиции в Абхазию",
            "products": ["багги-экспедиции"],
            "regions": ["абхазия"],
            "query_signals": ["багги абхазия", "экскурсии абхазия"],
            "deprioritised": ["яхты", "вертолёты"],
            "exit_criterion": "топ-10 по «экскурсии абхазия»",
            "owner_note": "Сначала с этим, остальное потом.",
            "rationale": "Ты сам это сказал в чате.",
        },
    }
    with patch(
        "app.core_audit.brain.free_chat.call_with_optional_tools",
        return_value=(
            {"text": None, "tool_use": tool_call},
            fake_usage,
        ),
    ):
        result = free_chat(
            domain="x", target_config={}, understanding={},
            snap=_snap(), plan=_plan(), history=[],
            new_message="давай сосредоточимся на багги в Абхазию",
        )

    assert result["reply"] is None
    p = result["proposal"]
    assert p is not None
    assert p["label"] == "Багги-экспедиции в Абхазию"
    assert p["products"] == ["багги-экспедиции"]
    assert p["regions"] == ["абхазия"]
    assert "багги абхазия" in p["query_signals"]
    assert "яхты" in p["deprioritised"]
    assert p["exit_criterion"] == "топ-10 по «экскурсии абхазия»"
    assert p["rationale"] == "Ты сам это сказал в чате."


def test_free_chat_ignores_unknown_tool_calls() -> None:
    """Defensive: if the model somehow calls a tool we didn't define,
    the wrapper silently drops the proposal and treats the response
    as plain text. We never want UI to show a modal for garbage."""
    fake_usage = {
        "model": "h", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0,
    }
    with patch(
        "app.core_audit.brain.free_chat.call_with_optional_tools",
        return_value=(
            {
                "text": "ok",
                "tool_use": {"name": "rogue_tool", "input": {}},
            },
            fake_usage,
        ),
    ):
        result = free_chat(
            domain="x", target_config={}, understanding={},
            snap=_snap(), plan=_plan(), history=[],
            new_message="?",
        )
    assert result["proposal"] is None
    assert result["reply"] == "ok"


def test_free_chat_truncates_overlong_message() -> None:
    """A 10 000-char message is paste-noise. We trim with a marker so
    owner sees what got cut, but the model isn't drowned."""
    fake_usage = {
        "model": "h", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0,
    }
    long_msg = "x" * (MAX_USER_MESSAGE_CHARS + 1000)
    with patch(
        "app.core_audit.brain.free_chat.call_with_optional_tools",
        return_value=(
            {"text": "ok", "tool_use": None},
            fake_usage,
        ),
    ) as mock_call:
        free_chat(
            domain="x", target_config={}, understanding={},
            snap=_snap(), plan=_plan(), history=[],
            new_message=long_msg,
        )
    user_msg = mock_call.call_args.kwargs["user_message"]
    assert "[…обрезано]" in user_msg


def test_free_chat_rejects_empty_message() -> None:
    """Empty/whitespace-only message means accidental Send — don't
    burn an LLM call on it."""
    with pytest.raises(ValueError):
        free_chat(
            domain="x", target_config={}, understanding={},
            snap=_snap(), plan=_plan(), history=[],
            new_message="   ",
        )
