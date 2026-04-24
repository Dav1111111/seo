from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.fingerprint.models import PageFingerprint
from app.fingerprint.repository import touch_last_fingerprinted
from app.models.page import Page
from app.models.site import Site


async def test_touch_last_fingerprinted_inserts_required_shape(db, test_site: Site):
    # Page.last_crawled_at is TIMESTAMP WITHOUT TIME ZONE (naive UTC).
    # Strip tzinfo so asyncpg doesn't refuse the mixed-aware comparison.
    crawled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    page = Page(
        site_id=test_site.id,
        url="https://example.test/tours",
        path="/tours",
        title="Tours",
        content_text="content",
        last_crawled_at=crawled_at,
    )
    db.add(page)
    await db.flush()

    await touch_last_fingerprinted(
        db,
        page_id=page.id,
        site_id=test_site.id,
        normalized_url="https://example.test/tours",
        content_hash="0" * 64,
        status="skipped_unchanged",
        skip_reason="unchanged_hash",
        source_crawl_at=crawled_at,
    )

    row = (await db.execute(
        select(PageFingerprint).where(PageFingerprint.page_id == page.id)
    )).scalar_one()
    assert row.site_id == test_site.id
    assert row.normalized_url == "https://example.test/tours"
    assert row.content_hash == "0" * 64
    assert row.status == "skipped_unchanged"

