"""Section 6 — Technical SEO Snapshot.

Indexation + non-200 responses + stale fingerprints.
"""

from __future__ import annotations

from datetime import date, timedelta, timezone, datetime
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.report.dto import TechnicalSection
from app.fingerprint.models import PageFingerprint
from app.models.page import Page


STALE_DAYS = 30


async def build_technical(
    db: AsyncSession, site_id: UUID, week_end: date,
) -> TechnicalSection:
    total_row = await db.execute(
        select(func.count())
        .select_from(Page)
        .where(Page.site_id == site_id)
    )
    pages_total = int(total_row.scalar() or 0)

    indexed_row = await db.execute(
        select(func.count())
        .select_from(Page)
        .where(Page.site_id == site_id, Page.in_index == True)  # noqa: E712
    )
    pages_indexed = int(indexed_row.scalar() or 0)

    non200_row = await db.execute(
        select(func.count())
        .select_from(Page)
        .where(
            Page.site_id == site_id,
            Page.http_status.is_not(None),
            Page.http_status >= 400,
        )
    )
    pages_non_200 = int(non200_row.scalar() or 0)

    # Suspected duplicates = content_hash groups with count > 1
    dup_row = await db.execute(
        select(PageFingerprint.content_hash, func.count().label("c"))
        .where(
            PageFingerprint.site_id == site_id,
            PageFingerprint.status == "fingerprinted",
        )
        .group_by(PageFingerprint.content_hash)
        .having(func.count() > 1)
    )
    duplicates_suspected = sum(int(c) - 1 for _, c in dup_row)

    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)
    stale_row = await db.execute(
        select(func.count())
        .select_from(PageFingerprint)
        .where(
            PageFingerprint.site_id == site_id,
            PageFingerprint.last_fingerprinted_at < stale_cutoff,
        )
    )
    stale_count = int(stale_row.scalar() or 0)

    indexation_rate = (pages_indexed / pages_total) if pages_total else 0.0

    warning = None
    if pages_total == 0:
        warning = "Краулер не нашёл ни одной страницы. Запустите /api/v1/collectors/crawl."

    return TechnicalSection(
        pages_total=pages_total,
        pages_indexed=pages_indexed,
        pages_non_200=pages_non_200,
        indexation_rate=round(indexation_rate, 3),
        duplicates_suspected=duplicates_suspected,
        fingerprint_stale_count=stale_count,
        warning_ru=warning,
    )
