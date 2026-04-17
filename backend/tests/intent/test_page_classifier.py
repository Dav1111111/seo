"""Unit tests for page intent classifier."""

from app.intent.enums import IntentCode
from app.intent.page_classifier import score_page_all_intents, score_page_for_intent


def test_tour_page_scores_comm_modified_high():
    scores = score_page_all_intents(
        path="/tours/33-waterfalls",
        title="Экскурсия на 33 водопада из Сочи 2026 от 2300₽",
        h1="33 водопада — джип-тур из Сочи",
        content_text=(
            "Программа тура: выезд в 8:00, дорога через горы, пасека, "
            "чайная плантация, обед. Что включено: трансфер, обед, гид. "
            "Забронировать можно онлайн. Отзывы туристов 4.8 из 5."
        ),
        word_count=800,
        has_schema=True,
        images_count=5,
    )
    # Tour page with TRANS_BOOK + COMM_MODIFIED signals
    assert scores[IntentCode.COMM_MODIFIED].score >= 2.0
    assert scores[IntentCode.TRANS_BOOK].score >= 2.0


def test_category_page_scores_comm_category():
    scores = score_page_all_intents(
        path="/tours",
        title="Все экскурсии в Сочи",
        h1="Каталог экскурсий",
        content_text="Все наши экскурсии с трансфером от отеля. Джиппинг, морские прогулки.",
        word_count=300,
        has_schema=False,
        images_count=10,
    )
    # Catalog page ranks well for COMM_CATEGORY due to URL pattern
    assert scores[IntentCode.COMM_CATEGORY].score >= 1.0


def test_reviews_page_scores_trust_legal():
    scores = score_page_all_intents(
        path="/reviews",
        title="Отзывы клиентов",
        h1="Отзывы туристов",
        content_text="Реальные отзывы наших клиентов об экскурсиях. Мария К. оставила отзыв...",
        word_count=1200,
        has_schema=True,
        images_count=0,
    )
    assert scores[IntentCode.TRUST_LEGAL].score >= 2.0


def test_blog_post_scores_info_prep():
    scores = score_page_all_intents(
        path="/blog/chto-vzyat-v-ekskursiyu",
        title="Что взять в экскурсию в горы",
        h1="Собираемся в горный тур — список вещей",
        content_text=(
            "Что взять с собой на экскурсию в горы: одежда, обувь, документы. "
            "Как одеться в горах. Советы туристам по подготовке к поездке."
        ),
        word_count=1500,
        has_schema=True,
        images_count=2,
    )
    assert scores[IntentCode.INFO_PREP].score >= 2.0


def test_empty_page_all_zero():
    scores = score_page_all_intents(
        path="/",
        title=None,
        h1=None,
        content_text=None,
        word_count=0,
        has_schema=False,
        images_count=0,
    )
    for intent, score in scores.items():
        assert score.score <= 1.0, f"{intent} scored too high for empty page: {score.score}"


def test_s3_structure_cap_applies():
    """If structure signal < 0.4, total score capped at 3.0 (Yandex commercial factor rule)."""
    # Page with great content but bad URL pattern (no /tours/, no /excursii/)
    s = score_page_for_intent(
        IntentCode.COMM_MODIFIED,
        path="/random-slug",
        title="Экскурсии из Сочи в Абхазию на 1 день от 2500",
        h1="Тур в Абхазию",
        content_text=(
            "Программа тура... что включено... забронировать..."
        ) * 20,
        word_count=800,
        has_schema=True,
        images_count=5,
    )
    assert s.s3_structure < 0.4
    assert s.score <= 3.0, f"expected cap at 3.0, got {s.score}"
