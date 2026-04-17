"""Thin async DB layer for PageFingerprint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.fingerprint.models import PageFingerprint


async def get_existing(db: AsyncSession, page_id: UUID) -> PageFingerprint | None:
    result = await db.execute(
        select(PageFingerprint).where(PageFingerprint.page_id == page_id)
    )
    return result.scalar_one_or_none()


async def upsert_fingerprint(db: AsyncSession, values: dict[str, Any]) -> None:
    """Idempotent upsert by page_id. Updates only provided fields on conflict."""
    # Ensure timestamps
    now = datetime.now(timezone.utc)
    values.setdefault("last_status_at", now)
    values.setdefault("last_fingerprinted_at", now)
    values.setdefault("created_at", now)
    values.setdefault("updated_at", now)

    stmt = pg_insert(PageFingerprint).values(**values)
    update_cols = {
        col: stmt.excluded[col]
        for col in values.keys()
        if col not in ("page_id", "created_at")
    }
    update_cols["updated_at"] = now

    stmt = stmt.on_conflict_do_update(
        index_elements=["page_id"],
        set_=update_cols,
    )
    await db.execute(stmt)


async def touch_last_fingerprinted(
    db: AsyncSession, page_id: UUID, status: str, skip_reason: str | None
) -> None:
    """When nothing changed, just bump timestamps + status."""
    now = datetime.now(timezone.utc)
    stmt = pg_insert(PageFingerprint).values(
        page_id=page_id,
        last_fingerprinted_at=now,
        last_status_at=now,
        updated_at=now,
        status=status,
        skip_reason=skip_reason,
    ).on_conflict_do_update(
        index_elements=["page_id"],
        set_={
            "last_fingerprinted_at": now,
            "last_status_at": now,
            "updated_at": now,
            "status": status,
            "skip_reason": skip_reason,
        },
    )
    await db.execute(stmt)


async def list_site_pages_for_fingerprinting(
    db: AsyncSession, site_id: UUID
) -> list[dict]:
    """Join pages + their existing fingerprint for batch processing."""
    from app.models.page import Page

    rows = await db.execute(
        select(
            Page.id, Page.site_id, Page.url, Page.path, Page.title, Page.h1,
            Page.meta_description, Page.content_text, Page.last_crawled_at,
            Page.http_status,
        ).where(Page.site_id == site_id)
    )
    return [dict(r._mapping) for r in rows]
