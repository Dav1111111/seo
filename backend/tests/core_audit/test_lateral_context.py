"""Lateral v2 context — anti-cannibalization + own-brand guard tests.

These tests pin the contract that `build_context` surfaces the site's
own URL inventory and own brand tokens to the LLM. Without these, the
LLM can propose queries an existing page already targets (cannibalization)
or queries containing the site's own brand (which would skew Wordstat
volume and waste budget).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.lateral.context import (
    OWN_PAGES_TOP_N,
    build_context,
)
from app.intent.models import PageIntentScore
from app.models.page import Page


@pytest.mark.asyncio
async def test_brand_strings_derived_from_domain(
    db: AsyncSession, test_site,
) -> None:
    """When target_config has no brand_name, the domain root is the fallback.

    The fixture site is `test-<hex>.example` — we expect `test-<hex>` as
    the brand token (lowercased, no TLD).
    """
    ctx = await build_context(db, test_site)

    expected_root = test_site.domain.split(".", 1)[0].lower()
    assert expected_root in ctx.brand_strings
    # All tokens are lowercase, deduped, non-empty.
    assert all(t == t.lower() and t for t in ctx.brand_strings)
    assert len(ctx.brand_strings) == len(set(ctx.brand_strings))


@pytest.mark.asyncio
async def test_brand_strings_use_target_config_brand_name(
    db: AsyncSession, test_site,
) -> None:
    """target_config.brand_name + display_name + domain root all show up."""
    test_site.target_config = {
        **(test_site.target_config or {}),
        "brand_name": "GrandTourSpirit",
    }
    test_site.display_name = "Grand Tour Spirit"
    await db.flush()

    ctx = await build_context(db, test_site)

    assert "grandtourspirit" in ctx.brand_strings
    assert "grand tour spirit" in ctx.brand_strings
    # Domain root still present.
    assert test_site.domain.split(".", 1)[0].lower() in ctx.brand_strings


@pytest.mark.asyncio
async def test_own_pages_loaded_with_intent_code(
    db: AsyncSession, test_site,
) -> None:
    """Pages and their top intent score surface in own_pages."""
    now = datetime.now(timezone.utc)
    page = Page(
        site_id=test_site.id,
        url=f"https://{test_site.domain}/buggy-abkhazia/",
        path="/buggy-abkhazia/",
        title="Багги-туры в Абхазию",
        h1="Багги в Абхазию из Сочи",
        last_seen_at=now,
    )
    db.add(page)
    await db.flush()

    db.add(
        PageIntentScore(
            page_id=page.id,
            site_id=test_site.id,
            intent_code="commercial_local",
            score=4.2,
            scored_at=now,
        )
    )
    await db.flush()

    ctx = await build_context(db, test_site)

    assert len(ctx.own_pages) == 1
    row = ctx.own_pages[0]
    assert row["url"].endswith("/buggy-abkhazia/")
    assert row["title"] == "Багги-туры в Абхазию"
    assert row["h1"] == "Багги в Абхазию из Сочи"
    assert row["intent_code"] == "commercial_local"


@pytest.mark.asyncio
async def test_own_pages_capped_at_50(
    db: AsyncSession, test_site,
) -> None:
    """Pin the cap — owners with 1000 pages shouldn't blow the prompt."""
    now = datetime.now(timezone.utc)
    # Insert 60 pages; expect only OWN_PAGES_TOP_N (50) back.
    for i in range(60):
        db.add(
            Page(
                site_id=test_site.id,
                url=f"https://{test_site.domain}/page-{i}/",
                path=f"/page-{i}/",
                title=f"Page {i}",
                last_seen_at=now,
            )
        )
    await db.flush()

    ctx = await build_context(db, test_site)

    assert OWN_PAGES_TOP_N == 50
    assert len(ctx.own_pages) == OWN_PAGES_TOP_N


@pytest.mark.asyncio
async def test_own_pages_empty_when_no_pages_crawled(
    db: AsyncSession, test_site,
) -> None:
    """A fresh site (no crawl yet) must not break build_context."""
    ctx = await build_context(db, test_site)

    assert ctx.own_pages == []
    # And brand_strings still derived from the domain.
    assert ctx.brand_strings
