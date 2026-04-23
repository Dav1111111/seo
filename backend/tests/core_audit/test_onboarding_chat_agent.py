"""Covers OnboardingChatAgent — initial message + refinement + validation.

Uses fake LLM callers so tests run offline and fast.
"""

from __future__ import annotations

from app.core_audit.onboarding.chat_agent import (
    CONFIRM_REGEX,
    MAX_ROUNDS,
    build_initial_message,
    refine_draft,
    validate_draft,
)


# ── Validation layer ─────────────────────────────────────────────────────


def test_validate_normalizes_services_to_lowercase_short_tokens():
    draft = validate_draft({
        "services": ["Багги", "МАРШРУТЫ", "полноразмерные багги-экспедиции"],
        "geo_primary": ["Абхазия", "Красная Поляна"],
        "geo_secondary": [],
        "narrative_ru": "Премиум активный туризм.",
    })
    # "полноразмерные багги-экспедиции" → truncated to 2 words = "полноразмерные багги".
    # Not perfect but defence-in-depth; LLM is already instructed to split.
    assert "багги" in draft["services"]
    assert "маршруты" in draft["services"]
    assert "красная поляна" in draft["geo_primary"]


def test_validate_splits_comma_separated_items():
    """If LLM stuffed multiple tokens in one string, we split."""
    draft = validate_draft({
        "services": ["багги, маршруты; яхты"],
        "geo_primary": ["сочи / адлер, абхазия"],
        "geo_secondary": [],
        "narrative_ru": "",
    })
    assert set(draft["services"]) >= {"багги", "маршруты", "яхты"}
    assert set(draft["geo_primary"]) >= {"сочи", "адлер", "абхазия"}


def test_validate_dedupes_and_caps_lengths():
    draft = validate_draft({
        "services": ["багги"] * 30 + ["яхты", "яхты"],
        "geo_primary": ["сочи"] * 20,
        "geo_secondary": [],
        "narrative_ru": "x" * 3000,
    })
    assert draft["services"].count("багги") == 1
    assert len(draft["services"]) <= 20  # MAX_SERVICES
    assert len(draft["geo_primary"]) <= 10  # MAX_GEO_PRIMARY
    assert len(draft["narrative_ru"]) <= 1200


def test_validate_primary_wins_on_geo_overlap():
    draft = validate_draft({
        "services": ["багги"],
        "geo_primary": ["абхазия", "сочи"],
        "geo_secondary": ["абхазия", "севастополь"],
        "narrative_ru": "",
    })
    assert "абхазия" in draft["geo_primary"]
    assert "абхазия" not in draft["geo_secondary"]
    assert "севастополь" in draft["geo_secondary"]


def test_validate_handles_malformed_input():
    # None, ints, missing keys — must not crash, returns empty draft.
    draft = validate_draft({"services": None, "geo_primary": 42})
    assert draft["services"] == []
    assert draft["geo_primary"] == []
    assert draft["narrative_ru"] == ""


# ── Confirm regex ────────────────────────────────────────────────────────


def test_confirm_regex_matches_common_phrases():
    for phrase in [
        "всё ок",
        "Всё верно",
        "подтверждаю",
        "поехали",
        "да, верно",
        "ok",
        "Готово",
    ]:
        assert CONFIRM_REGEX.match(phrase), f"should match: {phrase}"


def test_confirm_regex_rejects_edits():
    for phrase in [
        "добавь джип-туры",
        "нет, уберём прокат",
        "да, но исправь географию",  # qualified yes
        "ок, но сначала...",  # qualified ok
    ]:
        assert not CONFIRM_REGEX.match(phrase), f"should NOT match: {phrase}"


# ── Initial message ──────────────────────────────────────────────────────


def test_initial_empty_understanding_returns_error():
    result = build_initial_message(
        "example.com", None, {},
    )
    assert result.status == "empty_understanding"
    assert result.message_ru == ""


def test_initial_ok_with_understanding():
    understanding = {
        "narrative_ru": "Премиум-клуб активного отдыха в Сочи.",
        "detected_niche": "премиум активный туризм",
        "detected_usp": "ежедневные экспедиции в Абхазию",
        "detected_positioning": "малые группы, экспедиционный формат",
        "observed_facts": [
            {"fact": "Багги-экспедиции 1–5 дней", "page_ref": "/abkhazia"},
        ],
        "inferences": ["Аудитория премиум"],
        "uncertainties": [],
    }

    def fake_caller(**kwargs):
        return (
            "Здравствуйте! Я посмотрел ваш сайт grandtourspirit.ru. "
            "Предлагаю такое описание... Всё верно?",
            {"cost_usd": 0.003},
        )

    result = build_initial_message(
        "grandtourspirit.ru", "Grand Tour Spirit", understanding,
        caller=fake_caller,
    )
    assert result.status == "ok"
    assert "grandtourspirit.ru" in result.message_ru
    assert result.cost_usd == 0.003


def test_initial_llm_failure_returns_error():
    def fake_caller(**kwargs):
        raise RuntimeError("network down")

    result = build_initial_message(
        "example.com", None, {"narrative_ru": "x"},
        caller=fake_caller,
    )
    assert result.status == "llm_failed"
    assert "network down" in (result.error or "")


# ── Refinement ───────────────────────────────────────────────────────────


def test_refine_short_circuits_on_obvious_confirm():
    """When user says 'поехали', skip the LLM round."""
    current = {
        "services": ["багги"],
        "geo_primary": ["абхазия"],
        "geo_secondary": [],
        "narrative_ru": "Test",
    }
    result = refine_draft(
        current=current, history=[], latest_user_message="поехали",
        round_number=3,
        caller=lambda **_: (_ for _ in ()).throw(RuntimeError("must not call")),
    )
    assert result.status == "short_circuit"
    assert result.needs_more_info is False
    assert result.draft["services"] == ["багги"]


def test_refine_applies_patch_and_normalizes():
    current = {
        "services": ["багги"],
        "geo_primary": ["абхазия"],
        "geo_secondary": [],
        "narrative_ru": "старый нарратив",
    }

    def fake_caller(**kwargs):
        return (
            {
                "reply_ru": "Принято, добавил джип-туры.",
                "understanding_patch": {
                    "services": ["Багги", "Джип-туры", "Маршруты"],
                    "geo_primary": ["Абхазия", "Сочи"],
                    "geo_secondary": ["Крым"],
                    "narrative_ru": "обновлённый",
                },
                "needs_more_info": True,
            },
            {"cost_usd": 0.004},
        )

    result = refine_draft(
        current=current, history=[{"role": "assistant", "content": "first"}],
        latest_user_message="добавь джип-туры",
        round_number=1,
        caller=fake_caller,
    )
    assert result.status == "ok"
    assert result.needs_more_info is True
    # Normalization applied
    assert "багги" in result.draft["services"]
    assert "сочи" in result.draft["geo_primary"]
    # "Джип-туры" has 1 word in Russian (hyphen preserved).
    assert "джип-туры" in result.draft["services"]


def test_refine_caps_at_max_rounds():
    """Past MAX_ROUNDS the LLM is never called — we force-close."""
    result = refine_draft(
        current={"services": ["багги"], "geo_primary": [], "geo_secondary": [], "narrative_ru": "x"},
        history=[],
        latest_user_message="ещё правка",
        round_number=MAX_ROUNDS + 1,
        caller=lambda **_: (_ for _ in ()).throw(RuntimeError("must not call")),
    )
    assert result.status == "capped"
    assert result.needs_more_info is False


def test_refine_handles_llm_returning_non_dict():
    """Malformed LLM output doesn't crash — returns status='malformed'."""
    def fake_caller(**kwargs):
        return "unexpected string", {"cost_usd": 0.001}

    result = refine_draft(
        current={"services": [], "geo_primary": [], "geo_secondary": [], "narrative_ru": ""},
        history=[],
        latest_user_message="добавь что-нибудь",
        round_number=1,
        caller=fake_caller,
    )
    assert result.status == "malformed"


def test_refine_handles_llm_exception():
    def fake_caller(**kwargs):
        raise RuntimeError("timeout")

    result = refine_draft(
        current={"services": [], "geo_primary": [], "geo_secondary": [], "narrative_ru": ""},
        history=[],
        latest_user_message="правка",
        round_number=1,
        caller=fake_caller,
    )
    assert result.status == "llm_failed"
    assert "timeout" in (result.error or "")
