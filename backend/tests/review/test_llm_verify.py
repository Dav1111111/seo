"""Verify() must drop hallucinations + off-whitelist items without poisoning
the rest of the enrichment."""

from __future__ import annotations

from uuid import uuid4

from app.core_audit.intent_codes import IntentCode
from app.core_audit.review import LinkCandidate, ReviewInput
from app.core_audit.review.llm.base import (
    LLMEnrichment,
    LLMH2Draft,
    LLMLinkProposal,
    LLMRewrite,
)
from app.core_audit.review.llm.verify import verify


def _ri(**overrides) -> ReviewInput:
    defaults = dict(
        page_id=uuid4(),
        site_id=uuid4(),
        coverage_decision_id=uuid4(),
        target_intent=IntentCode.COMM_MODIFIED,
        path="/tours/rica",
        url="https://example.com/tours/rica",
        title="Тур на Рицу",
        meta_description="Однодневный тур",
        h1="Экскурсия на Рицу",
        content_text="Тур на озеро Рица. Отправление из Адлера.",
        word_count=20,
        has_schema=False,
        images_count=0,
        content_hash="hash",
        composite_hash="xyz",
        top_queries=("тур на рицу",),
        link_candidates=(
            LinkCandidate(url="/tours/gagra", anchor_hint="Гагра", similarity=0.8),
        ),
    )
    defaults.update(overrides)
    return ReviewInput(**defaults)


def _enrichment_base() -> LLMEnrichment:
    return LLMEnrichment(
        rewrites=(
            LLMRewrite(
                finding_id="title_length",
                before_text="Тур на Рицу",
                after_text="Тур на Рицу из Адлера",
                reasoning_ru="Добавлен город выезда из queries",
            ),
        ),
    )


def test_verify_passes_clean_rewrite():
    ri = _ri()
    en = _enrichment_base()
    v = verify(en, ri, sent_finding_ids={"title_length"})
    assert len(v.rewrites) == 1


def test_verify_drops_unknown_finding_id():
    ri = _ri()
    en = LLMEnrichment(
        rewrites=(
            LLMRewrite(finding_id="bogus_signal", before_text=None, after_text="x", reasoning_ru="y"),
        ),
    )
    v = verify(en, ri, sent_finding_ids={"title_length"})
    assert v.rewrites == ()


def test_verify_drops_phone_hallucination():
    # Phone not in content_text
    ri = _ri(content_text="Тур на Рицу без телефонов в тексте.")
    en = LLMEnrichment(rewrites=(
        LLMRewrite(
            finding_id="title_length",
            before_text=None,
            after_text="Звоните +7 (999) 123-45-67",
            reasoning_ru="added phone",
        ),
    ))
    v = verify(en, ri, sent_finding_ids={"title_length"})
    assert v.rewrites == ()


def test_verify_keeps_phone_when_present_in_content():
    ri = _ri(content_text="Тур на Рицу. Звоните +7 (999) 123-45-67 для брони.")
    en = LLMEnrichment(rewrites=(
        LLMRewrite(
            finding_id="title_length",
            before_text=None,
            after_text="Тур Рица — +7 (999) 123-45-67",
            reasoning_ru="phone was in page",
        ),
    ))
    v = verify(en, ri, sent_finding_ids={"title_length"})
    assert len(v.rewrites) == 1


def test_verify_drops_price_hallucination():
    ri = _ri(content_text="Тур на Рицу. Программа.")
    en = LLMEnrichment(rewrites=(
        LLMRewrite(
            finding_id="title_length", before_text=None,
            after_text="Тур за 5000 руб", reasoning_ru="made-up price",
        ),
    ))
    v = verify(en, ri, sent_finding_ids={"title_length"})
    assert v.rewrites == ()


def test_verify_drops_city_not_in_source():
    # "Адлер" not in content_text / queries / title / h1
    ri = _ri(
        content_text="Тур на Рицу. Отправление.",
        title="Тур Рица",
        h1="Экскурсия",
        top_queries=("тур на рицу",),
    )
    en = LLMEnrichment(rewrites=(
        LLMRewrite(
            finding_id="title_length", before_text=None,
            after_text="Тур на Рицу из Адлера", reasoning_ru="bad",
        ),
    ))
    v = verify(en, ri, sent_finding_ids={"title_length"})
    assert v.rewrites == ()


def test_verify_drops_title_over_65_chars():
    long = "Очень длинный title на много-много символов сверх шестидесяти пяти знаков максимум"
    assert len(long) > 65
    ri = _ri(content_text=long)   # text contains same words so no fact-leak
    en = LLMEnrichment(rewrites=(
        LLMRewrite(
            finding_id="title_length",
            before_text=None, after_text=long, reasoning_ru="bad",
        ),
    ))
    v = verify(en, ri, sent_finding_ids={"title_length"})
    assert v.rewrites == ()


def test_verify_link_whitelist():
    ri = _ri()
    en = LLMEnrichment(
        link_proposals=(
            LLMLinkProposal(
                anchor_ru="Гагра",
                target_url="/tours/gagra",           # in candidates → keep
                reasoning_ru="related",
            ),
            LLMLinkProposal(
                anchor_ru="Выдуманная",
                target_url="/hallucinated/page",    # not in candidates → drop
                reasoning_ru="made-up",
            ),
        ),
    )
    v = verify(en, ri, sent_finding_ids=set())
    assert len(v.link_proposals) == 1
    assert v.link_proposals[0].target_url == "/tours/gagra"


def test_verify_cargo_cult_enum_filter():
    en = LLMEnrichment(
        detected_cargo_cult_schemas=("TouristTrip", "Product", "BogusType"),
    )
    v = verify(en, _ri(), sent_finding_ids=set())
    # Product is NOT cargo-cult; BogusType not in known list; only TouristTrip kept
    assert v.detected_cargo_cult_schemas == ("TouristTrip",)


def test_verify_h2_draft_hallucination():
    ri = _ri(content_text="Короткий текст страницы про Рицу.")
    en = LLMEnrichment(h2_drafts=(
        LLMH2Draft(
            block_title="Цены",
            draft_ru="Цена тура 12000 руб. ИНН: 1234567890",
            word_count=10,
        ),
    ))
    v = verify(en, ri, sent_finding_ids=set())
    assert v.h2_drafts == ()
