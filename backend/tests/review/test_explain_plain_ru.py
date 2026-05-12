"""Tests for the plain-Russian recommendation explainer.

Covers:

  * `translate_to_plain_ru` returns a non-empty string and propagates
    the LLM usage stats (cost_usd in particular — we roll it up into
    the backfill total and into agent_runs).
  * The /admin/studio/recommendations/{rec_id}/explain endpoint hits
    the cache when `plain_ru` is already filled — the LLM mock MUST
    NOT be invoked, otherwise every owner click costs money for no
    reason.

The LLM client is mocked at the import site
(`app.core_audit.review.explain.call_with_tool`) so we never make a
real network call from the suite. Real-call coverage lives in the
explicit integration tier that pytest skips by default.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.studio import explain_recommendation
from app.core_audit.review.explain import translate_to_plain_ru
from app.core_audit.review.models import PageReview, PageReviewRecommendation
from app.models.page import Page
from app.models.site import Site


pytestmark = pytest.mark.asyncio


async def _make_review_and_rec(
    db: AsyncSession,
    test_site: Site,
    *,
    plain_ru: str | None = None,
) -> PageReviewRecommendation:
    """Insert one Page + PageReview + PageReviewRecommendation for the test.

    Returns the recommendation so the test can assert against its id.
    Helper rather than a fixture: most tests need slightly different
    rec shapes (with/without plain_ru), so a parametrised builder is
    less fragile than 3 fixtures.
    """
    page = Page(
        site_id=test_site.id,
        url="https://example.com/tours/rica",
        path="/tours/rica",
    )
    db.add(page)
    await db.flush()

    review = PageReview(
        site_id=test_site.id,
        page_id=page.id,
        target_intent_code="commercial_modified",
        composite_hash=f"test-{uuid.uuid4().hex[:8]}",
        status="completed",
    )
    db.add(review)
    await db.flush()

    rec_kwargs: dict = dict(
        site_id=test_site.id,
        review_id=review.id,
        category="title",
        priority="high",
        user_status="pending",
        before_text="Тур на Рицу — самый интересный",
        after_text="Тур на озеро Рица из Адлера — программа и цены",
        reasoning_ru="Title не содержит города выезда из top-queries.",
    )
    if plain_ru is not None:
        # Setting via kwarg works only after the migration is applied.
        # In the test environment that's a precondition of the suite;
        # locally without it pytest collection still works because the
        # column is referenced only inside this branch.
        rec_kwargs["plain_ru"] = plain_ru
    rec = PageReviewRecommendation(**rec_kwargs)
    db.add(rec)
    await db.flush()
    return rec


async def test_translate_to_plain_ru_returns_text_and_cost(
    db: AsyncSession, test_site: Site,
) -> None:
    """`translate_to_plain_ru` should hand the rec to call_with_tool,
    unwrap the `plain_ru` field, and bubble up the usage stats so the
    caller can charge it to the budget."""
    rec = await _make_review_and_rec(db, test_site)

    fake_tool_input = {"plain_ru": "Простое объяснение для владельца сайта."}
    fake_usage = {
        "cost_usd": 0.00042,
        "input_tokens": 220,
        "output_tokens": 35,
        "model": "claude-haiku-4-5-20251001",
    }

    with patch(
        "app.core_audit.review.explain.call_with_tool",
        return_value=(fake_tool_input, fake_usage),
    ) as mock_call:
        plain_ru, usage = translate_to_plain_ru(rec)

    assert plain_ru == "Простое объяснение для владельца сайта."
    assert usage["cost_usd"] == pytest.approx(0.00042)
    assert mock_call.call_count == 1
    # System prompt must mention the «без жаргона» constraint — that's
    # the contract owners rely on, so guard it with a regression test.
    kwargs = mock_call.call_args.kwargs
    assert "Без жаргона" in kwargs["system"]
    assert kwargs["model_tier"] == "cheap"


async def test_translate_to_plain_ru_truncates_over_600_chars(
    db: AsyncSession, test_site: Site,
) -> None:
    """Defensive truncation: even if the provider ignores the schema's
    maxLength, the column has a real cap and the UI tooltip would look
    awful past ~600 chars."""
    rec = await _make_review_and_rec(db, test_site)

    long_text = "А" * 1200
    with patch(
        "app.core_audit.review.explain.call_with_tool",
        return_value=({"plain_ru": long_text}, {"cost_usd": 0.0001}),
    ):
        plain_ru, _ = translate_to_plain_ru(rec)

    assert len(plain_ru) <= 600


async def test_explain_endpoint_cache_hit_skips_llm_call(
    db: AsyncSession, test_site: Site,
) -> None:
    """Critical money-path: rec already has plain_ru → the endpoint
    must return it verbatim and NEVER invoke the LLM. We assert on the
    mock's call count, not on the return value — because if the mock
    is called, we'd be paying for every owner click."""
    rec = await _make_review_and_rec(
        db, test_site, plain_ru="Уже переведённое объяснение",
    )

    with patch(
        "app.core_audit.review.explain.call_with_tool",
    ) as mock_call:
        result = await explain_recommendation(rec_id=rec.id, db=db)

    assert mock_call.call_count == 0
    assert result.cached is True
    assert result.cost_usd == 0.0
    assert result.plain_ru == "Уже переведённое объяснение"
    assert result.id == rec.id


async def test_explain_endpoint_404_for_unknown_rec(
    db: AsyncSession,
) -> None:
    """No row → 404, not 500 or 200 with empty body."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await explain_recommendation(rec_id=uuid.uuid4(), db=db)
    assert exc.value.status_code == 404
