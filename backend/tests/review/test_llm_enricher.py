"""Integration tests for the LLM enricher with mocked llm_client.call_with_tool."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from app.core_audit.intent_codes import IntentCode
from app.core_audit.review import (
    LinkCandidate,
    ReviewInput,
    enrich_with_llm,
    run_python_checks_with_findings,
)
from app.profiles.tourism import TOURISM_TOUR_OPERATOR


def _ri(**overrides) -> ReviewInput:
    defaults = dict(
        page_id=uuid4(),
        site_id=uuid4(),
        coverage_decision_id=uuid4(),
        target_intent=IntentCode.COMM_MODIFIED,
        path="/tours/tur-na-ricu",
        url="https://example.com/tours/tur-na-ricu",
        title="Очень длинный title выходящий далеко за 70 символов чтобы сработал флаг length",
        meta_description="",
        h1="Экскурсия",
        content_text="Программа тура. Забираем из отеля. Цена 2500 руб.",
        word_count=20,
        has_schema=True,
        images_count=0,
        content_hash="hash1",
        composite_hash="composite1",
        top_queries=("тур на рицу",),
        link_candidates=(
            LinkCandidate(url="/tours/gagra", anchor_hint="Гагра", similarity=0.8),
        ),
    )
    defaults.update(overrides)
    return ReviewInput(**defaults)


def test_enricher_falls_back_when_llm_unavailable():
    """llm_client fails to import or raises — ReviewResult stays Python-only."""
    ri = _ri()
    out = run_python_checks_with_findings(ri, TOURISM_TOUR_OPERATOR)

    def boom(**kwargs):
        raise RuntimeError("no API key")

    with patch("app.core_audit.review.llm.runner.call_with_tool", side_effect=boom, create=True):
        enriched = enrich_with_llm(out.result, ri, out.findings)

    assert enriched.reviewer_model == "python-only"
    assert enriched.cost_usd == 0.0
    # Recommendations unchanged
    assert len(enriched.recommendations) == len(out.result.recommendations)


def test_enricher_merges_llm_rewrite_into_matching_rec():
    ri = _ri()
    out = run_python_checks_with_findings(ri, TOURISM_TOUR_OPERATOR)
    # Find the title_length finding id the enricher will send
    assert any(r.source_finding_id == "title_length" for r in out.result.recommendations)

    fake_tool_input = {
        "rewrites": [{
            "finding_id": "title_length",
            "before_text": ri.title,
            "after_text": "Тур на Рицу — цены и программа",
            "reasoning_ru": "Сокращено до 30 символов, ключ в начале.",
        }],
        "h2_drafts": [],
        "link_proposals": [],
        "detected_cargo_cult_schemas": [],
    }
    fake_usage = {
        "cost_usd": 0.003,
        "input_tokens": 800,
        "output_tokens": 120,
        "model": "claude-haiku-4-5-20251001",
    }

    with patch(
        "app.core_audit.review.llm.runner.call_with_tool",
        return_value=(fake_tool_input, fake_usage),
        create=True,
    ):
        enriched = enrich_with_llm(out.result, ri, out.findings)

    title_rec = next(r for r in enriched.recommendations if r.source_finding_id == "title_length")
    assert title_rec.after == "Тур на Рицу — цены и программа"
    assert "Сокращено до 30" in title_rec.reasoning_ru
    assert enriched.cost_usd == 0.003
    assert "claude-haiku-4-5" in enriched.reviewer_model


def test_enricher_appends_link_proposal_as_new_rec():
    ri = _ri()
    out = run_python_checks_with_findings(ri, TOURISM_TOUR_OPERATOR)

    fake = {
        "rewrites": [],
        "h2_drafts": [],
        "link_proposals": [{
            "anchor_ru": "Похожий тур в Гагру",
            "target_url": "/tours/gagra",
            "reasoning_ru": "Семантически близкая страница",
            "placement_hint": "body",
        }],
        "detected_cargo_cult_schemas": [],
    }
    with patch(
        "app.core_audit.review.llm.runner.call_with_tool",
        return_value=(fake, {"cost_usd": 0.002, "input_tokens": 500, "output_tokens": 60, "model": "haiku"}),
        create=True,
    ):
        enriched = enrich_with_llm(out.result, ri, out.findings)

    link_recs = [r for r in enriched.recommendations if r.source_finding_id and r.source_finding_id.startswith("llm_internal_link:")]
    assert len(link_recs) == 1
    assert "/tours/gagra" in (link_recs[0].after or "")


def test_enricher_drops_hallucinated_link():
    ri = _ri()
    out = run_python_checks_with_findings(ri, TOURISM_TOUR_OPERATOR)

    fake = {
        "rewrites": [],
        "h2_drafts": [],
        "link_proposals": [{
            "anchor_ru": "Выдумка",
            "target_url": "/pages/hallucinated",   # not in candidates
            "reasoning_ru": "z",
        }],
        "detected_cargo_cult_schemas": [],
    }
    with patch(
        "app.core_audit.review.llm.runner.call_with_tool",
        return_value=(fake, {"cost_usd": 0.002, "input_tokens": 500, "output_tokens": 60, "model": "haiku"}),
        create=True,
    ):
        enriched = enrich_with_llm(out.result, ri, out.findings)

    # No link rec should have been added
    assert not any(
        r.source_finding_id and r.source_finding_id.startswith("llm_internal_link:")
        for r in enriched.recommendations
    )


def test_enricher_returns_input_when_no_findings():
    ri = _ri()
    out = run_python_checks_with_findings(ri, TOURISM_TOUR_OPERATOR)
    enriched = enrich_with_llm(out.result, ri, findings=[])
    assert enriched is out.result
