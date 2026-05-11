from datetime import datetime, timezone

from app.api.v1.studio import (
    _dedupe_recommendation_export_items,
    _render_recommendations_markdown,
)


def test_recommendations_export_deduplicates_same_page_action():
    items = [
        {
            "rec_id": "low",
            "url": "https://example.com/a",
            "category": "title",
            "priority": "low",
            "priority_score": 1.0,
            "before_text": "old",
            "after_text": "new",
            "reasoning_ru": "same",
        },
        {
            "rec_id": "high",
            "url": "https://example.com/a",
            "category": "title",
            "priority": "high",
            "priority_score": 9.0,
            "before_text": "old",
            "after_text": "new",
            "reasoning_ru": "same",
        },
        {
            "rec_id": "other-page",
            "url": "https://example.com/b",
            "category": "title",
            "priority": "high",
            "priority_score": 8.0,
            "before_text": "old",
            "after_text": "new",
            "reasoning_ru": "same",
        },
    ]

    unique = _dedupe_recommendation_export_items(items)

    assert [item["rec_id"] for item in unique] == ["high", "other-page"]


def test_recommendations_export_markdown_contains_counts_and_content():
    markdown = _render_recommendations_markdown(
        domain="example.com",
        items=[
            {
                "rec_id": "1",
                "url": "https://example.com/a",
                "category": "title",
                "priority": "high",
                "user_status": "pending",
                "priority_score": 7.5,
                "target_intent_code": "commercial",
                "before_text": "Старый title",
                "after_text": "Новый title",
                "reasoning_ru": "Title не попадает в спрос.",
            },
        ],
        total_before_dedupe=3,
        computed_at=datetime(2026, 5, 4, tzinfo=timezone.utc),
    )

    assert "# Рекомендации для example.com" in markdown
    assert "Собрано уникальных рекомендаций: 1." in markdown
    assert "Повторы убраны: 2." in markdown
    assert "Что сделать: Новый title" in markdown
