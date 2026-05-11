from __future__ import annotations

from app.core_audit.brain.battle_plan import (
    battle_plan_result,
    build_battle_plan_items,
    render_battle_plan_reply,
)
from tests.core_audit.test_brain_free_chat import _plan, _snap


def test_battle_plan_caps_actions_and_requires_grounded_fields() -> None:
    snap = _snap()
    snap.indexation.non_200_count = 1
    snap.indexation.sample_non_200 = [
        {"url": "https://example.com/broken", "http_status": 500},
    ]
    snap.review.recs_pending = 6
    snap.review.recs_high_priority_pending = 6
    snap.review.top_pending_recommendations = [
        {
            "rec_id": f"rec-{idx}",
            "priority": "high",
            "category": "title",
            "priority_score": 80 - idx,
            "url": f"https://example.com/page-{idx}",
            "reasoning_ru": f"reason {idx}",
            "before_text": f"old title {idx}",
            "after_text": f"action {idx}",
            "target_intent_code": "buggy_abkhazia",
        }
        for idx in range(8)
    ]

    items = build_battle_plan_items(snap, _plan(), limit=5)

    assert len(items) == 5
    assert items[0].id == "indexation:non_200"
    for item in items:
        assert item.source
        assert item.reason_ru
        assert item.action_ru
        assert item.expected_effect_ru
        assert item.verify_ru
        assert item.link_to
        assert item.evidence
    assert any("сейчас в данных" in item.detail_ru for item in items)


def test_battle_plan_reply_has_plan_verification_and_missing_data() -> None:
    snap = _snap()
    snap.competitors.profile_available = False
    snap.competitors.deep_dive_available = False
    snap.indexation.pages_unknown = 4

    reply = render_battle_plan_reply(snap, _plan())

    assert "## Боевой SEO-план" in reply
    assert "не гарантия топ-5" in reply
    assert "### Факты" in reply
    assert "### План действий" in reply
    assert "### Проверка результата" in reply
    assert "### Что добрать" in reply
    assert "Детали:" in reply
    assert "Куда открыть:" in reply
    assert "Проверенный per-URL статус Webmaster" in reply
    assert "unknown не считай ошибкой" in reply
    assert "Нет SERP-разведки конкурентов" in reply


def test_battle_plan_result_is_zero_cost_deterministic() -> None:
    result = battle_plan_result(_snap(), _plan())

    assert result["reply"]
    assert result["proposal"] is None
    assert result["cost_usd"] == 0.0
    assert result["model"] == "rules:battle-plan"
    assert result["input_tokens"] == 0
    assert result["output_tokens"] == 0
    assert result["truncated"] is False
