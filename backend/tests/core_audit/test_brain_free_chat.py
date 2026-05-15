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
    MAX_BATTLE_PLAN_REPLY_TOKENS,
    MAX_DISCUSSION_REPLY_TOKENS,
    MAX_HISTORY_MESSAGES,
    MAX_REPLY_TOKENS,
    MAX_USER_MESSAGE_CHARS,
    SYSTEM_PROMPT,
    _normalise_mode,
    build_user_message,
    free_chat,
)
from app.core_audit.brain.rules import Action, Plan
from app.core_audit.brain.snapshot import (
    BrainSnapshot,
    CompetitorFacts,
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
        competitors=CompetitorFacts(
            domains=["abhazia-buggy.ru", "adventure-sochi.ru"],
            profile_available=True,
            queries_probed=8,
            queries_with_results=6,
            unique_domains_seen=14,
            cost_usd=0.024,
            top_competitors=[
                {
                    "domain": "abhazia-buggy.ru",
                    "serp_hits": 4,
                    "best_position": 2,
                    "avg_position": 4.5,
                    "example_query": "багги абхазия",
                    "example_url": "https://abhazia-buggy.ru/tours",
                    "example_title": "Багги-туры по Абхазии",
                },
            ],
            deep_dive_available=True,
            self_signals={
                "url": "https://grandtourspirit.ru/",
                "status": "ok",
                "title": "Grand Tour Spirit",
                "has_price": False,
                "has_booking_cta": True,
                "has_reviews": False,
                "has_phone": True,
                "has_telegram": True,
                "has_whatsapp": False,
                "schema_types": ["Organization"],
            },
            deep_dive_competitors=[
                {
                    "domain": "abhazia-buggy.ru",
                    "has_price": True,
                    "has_booking_cta": True,
                    "has_reviews": True,
                    "has_phone": True,
                    "has_telegram": True,
                    "has_whatsapp": True,
                    "schema_types": ["Product", "FAQPage"],
                    "pages": [
                        {
                            "url": "https://abhazia-buggy.ru/tours",
                            "status": "ok",
                            "title": "Багги-туры по Абхазии",
                            "h1": "Багги-туры",
                            "word_count": 920,
                        },
                    ],
                },
            ],
            growth_opportunities=[
                {
                    "source": "feature_diff",
                    "category": "on_page_feature",
                    "priority": "high",
                    "title_ru": "Покажи цены на страницах услуг",
                    "reasoning_ru": "1 из 1 найденных конкурентов имеет цены.",
                    "evidence": {
                        "feature": "has_price",
                        "competitors_with": ["abhazia-buggy.ru"],
                    },
                },
            ],
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


def test_user_message_carries_full_snapshot_all_sections() -> None:
    """Free chat must see ALL snapshot sections — owner can ask
    about any of them. Per-action chat slices; free chat does NOT.
    Pin each section header + at least one fact per section."""
    msg = build_user_message(
        domain="grandtourspirit.ru",
        target_config={},
        understanding={},
        snap=_snap(),
        plan=_plan(),
        history=[],
        new_message="?",
    )
    assert "Индексация:" in msg
    assert "в индексе Яндекса: 18" in msg

    assert "Запросы:" in msg
    assert "спам: 26" in msg

    assert "Конкуренты:" in msg
    assert "abhazia-buggy.ru" in msg
    assert "Покажи цены на страницах услуг" in msg

    assert "Ревью страниц:" in msg
    assert "без ревью: 20" in msg

    assert "Услуги без отдельной страницы:" in msg
    assert "всего: 5" in msg

    assert "Применённые правки и замеры:" in msg
    assert "ждут замера через 14 дней: 0" in msg


def test_user_message_marks_recommendations_as_full_list_when_uncapped() -> None:
    snap = _snap()
    snap.review.recs_pending = 2
    snap.review.recs_high_priority_pending = 1
    snap.review.top_pending_recommendations = [
        {
            "rec_id": "1",
            "priority": "high",
            "category": "title",
            "url": "https://x/a",
            "reasoning_ru": "current title",
        },
        {
            "rec_id": "2",
            "priority": "medium",
            "category": "h1",
            "url": "https://x/b",
            "reasoning_ru": "current h1",
        },
    ]

    msg = build_user_message(
        domain="x", target_config={}, understanding={},
        snap=snap, plan=_plan(), history=[],
        new_message="покажи рекомендации",
    )

    assert "источник рекомендаций: последние завершённые ревью" in msg
    assert "все ожидающие рекомендации в контексте: 2 из 2" in msg
    assert "это полный список, а не примеры" in msg
    assert "current title" in msg
    assert "current h1" in msg


def test_user_message_marks_old_review_when_fresh_snapshot_exists() -> None:
    snap = _snap()
    snap.review.recs_pending = 1
    snap.review.recs_high_priority_pending = 1
    snap.review.recs_with_fresh_snapshot_after_review = 1
    snap.review.top_pending_recommendations = [
        {
            "rec_id": "1",
            "priority": "high",
            "category": "schema",
            "url": "https://x/a",
            "reasoning_ru": "Добавить Product",
            "current_snapshot": {
                "extracted_at": "2026-05-15T10:00:00+00:00",
                "after_review": True,
                "title": "Свежий title",
                "h1": "Свежий H1",
                "schema_types": ["TouristTrip", "Offer", "FAQPage"],
                "schema_issue_codes": ["info:schema.tourist_trip.offer_hint"],
                "lcp_ms": 3276,
                "js_error_count": 0,
                "freshness_warning": "latest_browser_snapshot_is_newer_than_review",
            },
        },
    ]
    snap.review.recommendation_groups = [
        {
            "priority": "high",
            "category": "schema",
            "count": 1,
            "reasoning_sample": "Добавить Product",
            "sample_urls": ["https://x/a"],
            "fresh_snapshot_after_review_count": 1,
        },
    ]

    msg = build_user_message(
        domain="x", target_config={}, understanding={},
        snap=snap, plan=_plan(), history=[],
        new_message="это ещё актуально?",
    )

    assert "переданных рекомендаций со свежим браузерным снимком после ревью: 1" in msg
    assert "их нельзя называть текущими проблемами без повторного ревью" in msg
    assert "свежий снимок после ревью: 2026-05-15T10:00:00+00:00" in msg
    assert "schema сейчас: TouristTrip, Offer, FAQPage" in msg
    assert "вывод: старое ревью, нужна перепроверка" in msg


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


def test_user_message_carries_discussion_mode_instruction() -> None:
    """Discussion mode changes answer style without changing the data
    contract: still factual, but more collaborative."""
    msg = build_user_message(
        domain="x", target_config={}, understanding={},
        snap=_snap(), plan=_plan(), history=[],
        new_message="давай разберём приоритеты",
        mode="discussion",
    )
    assert "РЕЖИМ ОТВЕТА: ОБСУЖДЕНИЕ" in msg
    assert "что видно по фактам" in msg
    assert "максимум 2 вопроса" in msg


def test_user_message_carries_battle_plan_mode_instruction() -> None:
    msg = build_user_message(
        domain="x", target_config={}, understanding={},
        snap=_snap(), plan=_plan(), history=[],
        new_message="собери боевой SEO-план",
        mode="battle_plan",
    )
    assert _normalise_mode("battle_plan") == "battle_plan"
    assert "РЕЖИМ ОТВЕТА: БОЕВОЙ SEO-ПЛАН" in msg
    assert "максимум 5 действий" in msg
    assert "причина, конкретная правка, ожидаемый" in msg
    assert "Не обещай гарантированный топ-5" in msg


def test_user_message_unknown_mode_falls_back_to_answer() -> None:
    """Mode is URL/user-input adjacent, so garbage must not leak into
    the prompt contract."""
    msg = build_user_message(
        domain="x", target_config={}, understanding={},
        snap=_snap(), plan=_plan(), history=[],
        new_message="?",
        mode="yolo",
    )
    assert _normalise_mode("yolo") == "answer"
    assert "РЕЖИМ ОТВЕТА: РАЗВЁРНУТЫЙ КОРОТКИЙ ОТВЕТ" in msg
    assert "РЕЖИМ ОТВЕТА: ОБСУЖДЕНИЕ" not in msg


def test_system_prompt_pins_mode_and_honesty_rules() -> None:
    """Keep stable anchors for the rules that protect free chat from
    bad indexation and discussion-mode answers."""
    assert "7. КОГДА ОБСУЖДАЕМ ИНДЕКСАЦИЮ" in SYSTEM_PROMPT
    assert "8. КОГДА НЕ ЗНАЕШЬ" in SYSTEM_PROMPT
    assert "9. РЕЖИМ ОБСУЖДЕНИЯ" in SYSTEM_PROMPT
    assert "РЕЖИМ БОЕВОГО SEO-ПЛАНА" in SYSTEM_PROMPT


def test_system_prompt_forbids_citing_internal_ids() -> None:
    """`source_finding_id` and similar Python-check identifiers travel
    in the snapshot context so the LLM knows WHICH detector fired.
    They must never be cited back to the owner — the prompt has to
    explicitly forbid that."""
    prompt_lower = SYSTEM_PROMPT.lower()
    # The forbidden term itself appears at least once (in the rule).
    assert "source_finding_id" in prompt_lower
    # And the rule must explicitly say "do not cite / do not show".
    assert "не цитируй" in prompt_lower or "не показывай" in prompt_lower
    # And it must mention the concept of an internal identifier so the
    # rule generalises beyond just `source_finding_id`.
    assert "идентификатор" in prompt_lower or "внутренние коды" in prompt_lower


def test_user_message_adds_competitor_question_instruction() -> None:
    """Competitor questions get an extra guardrail: split SERP,
    deep-dive and computed opportunities, and point to the module when
    data is missing."""
    msg = build_user_message(
        domain="x", target_config={}, understanding={},
        snap=_snap(), plan=_plan(), history=[],
        new_message="что видно по конкурентам?",
    )
    assert "Для вопроса про конкурентов ответь слоями" in msg
    assert "кто реально виден в выдаче" in msg
    assert "/studio/competitors" in msg


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
    assert mock_call.call_args.kwargs["max_tokens"] == MAX_REPLY_TOKENS
    # User message contains the actual question.
    assert "что у меня самое слабое место?" in mock_call.call_args.kwargs["user_message"]


def test_free_chat_unknown_mode_uses_answer_budget() -> None:
    fake_usage = {
        "model": "h", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0,
    }
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
            new_message="?",
            mode="garbage",
        )
    assert mock_call.call_args.kwargs["max_tokens"] == MAX_REPLY_TOKENS
    assert "РЕЖИМ ОТВЕТА: РАЗВЁРНУТЫЙ КОРОТКИЙ ОТВЕТ" in mock_call.call_args.kwargs["user_message"]


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


def test_free_chat_discussion_mode_uses_larger_reply_budget() -> None:
    """Discussion mode may need a little more room for alternatives
    and a next-step question."""
    fake_usage = {
        "model": "h", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0,
    }
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
            new_message="давай обсудим порядок работ",
            mode="discussion",
        )
    assert mock_call.call_args.kwargs["max_tokens"] == MAX_DISCUSSION_REPLY_TOKENS
    assert "РЕЖИМ ОТВЕТА: ОБСУЖДЕНИЕ" in mock_call.call_args.kwargs["user_message"]


def test_free_chat_battle_plan_mode_uses_largest_reply_budget() -> None:
    fake_usage = {
        "model": "h", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0,
    }
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
            new_message="собери боевой план",
            mode="battle_plan",
        )
    assert mock_call.call_args.kwargs["max_tokens"] == MAX_BATTLE_PLAN_REPLY_TOKENS
    assert "РЕЖИМ ОТВЕТА: БОЕВОЙ SEO-ПЛАН" in mock_call.call_args.kwargs["user_message"]


def test_free_chat_rejects_empty_message() -> None:
    """Empty/whitespace-only message means accidental Send — don't
    burn an LLM call on it."""
    with pytest.raises(ValueError):
        free_chat(
            domain="x", target_config={}, understanding={},
            snap=_snap(), plan=_plan(), history=[],
            new_message="   ",
        )


def test_user_message_warns_about_broken_metrica_counter() -> None:
    """When counter_code_status is non-CS_OK, the user message must
    include a clear warning telling the LLM that visits=0 means «нет
    данных», not «нет трафика», and forbidding behavioral conclusions.

    Reproduces audit 2026-05-15: chat answers were quoting «у тебя 0
    визитов» as a fact even when the Metrica counter was reporting
    CS_ERR_UNKNOWN (code not installed)."""
    snap = _snap()
    snap.metrica.latest_date = datetime(2026, 5, 10, tzinfo=timezone.utc).date()
    snap.metrica.counter_code_status = "CS_ERR_UNKNOWN"
    snap.metrica.visits_7d = 0

    msg = build_user_message(
        domain="x", target_config={}, understanding={},
        snap=snap, plan=_plan(), history=[],
        new_message="как трафик?",
    )

    # The exact status code lands in the context so the LLM can name it
    # back if asked.
    assert "CS_ERR_UNKNOWN" in msg or "код Метрики" in msg
    # The warning makes explicit that this is «нет данных», not «нет трафика».
    assert "нет данных" in msg.lower() or "не отвечает" in msg.lower()
    # And explicitly forbids drawing conclusions from the broken
    # counter's zeros.
    assert "не делай" in msg.lower() or "недостоверн" in msg.lower()


def test_system_prompt_carries_broken_metrica_rule() -> None:
    """The behavioral rule must also live in SYSTEM_PROMPT so the LLM
    knows what to do even when the warning line in the user message
    happens to be paraphrased / truncated."""
    prompt_lower = SYSTEM_PROMPT.lower()
    # Mentions the broken-counter status family.
    assert "cs_ok" in prompt_lower or "cs_err" in prompt_lower
    # Instructs the model not to ground answers on Metrica numbers.
    assert "недостоверн" in prompt_lower or "не ссылайся" in prompt_lower


# ── Wordstat tri-state coverage (audit-2026-05-15) ───────────────────


def test_user_message_warns_about_partial_wordstat_coverage() -> None:
    """When wordstat coverage is low, the user message must include
    explicit guidance telling the LLM not to interpret missing volume
    as «no demand». Anchored at the field level (with_demand / total /
    never_fetched) so a future formatting change can't accidentally
    drop the anti-hallucination signal."""
    snap = _snap()
    snap.queries.total = 13
    snap.queries.with_volume_known = 4
    snap.queries.with_demand = 3
    snap.queries.never_fetched = 9
    # Keep the back-compat alias in sync so any older formatter path
    # also sees the same numbers.
    snap.queries.with_volume = 4

    msg = build_user_message(
        domain="x", target_config={}, understanding={},
        snap=snap, plan=_plan(), history=[], new_message="?",
    )

    # Tri-state counters surface verbatim.
    assert "3 из 13" in msg or "3 / 13" in msg
    # Explicit instruction NOT to read «no number» as «no demand».
    assert "не успели" in msg.lower() or "не опрашивали" in msg.lower()
    assert "не значит" in msg.lower() or "не означает" in msg.lower()


def test_system_prompt_carries_wordstat_anti_fabrication_rule() -> None:
    """Mirrors the user-message guard: the SYSTEM_PROMPT must instruct
    the LLM not to conflate «не успели собраться» with «нет спроса»
    even when the user-message formatting shifts."""
    # Collapse whitespace so multi-line prompt text matches regardless
    # of how the rule body is wrapped.
    collapsed = " ".join(SYSTEM_PROMPT.lower().split())
    assert "wordstat" in collapsed
    assert "не успели собраться" in collapsed
    # The rule must explicitly forbid the «no number = no demand» leap.
    assert "нет спроса" in collapsed
