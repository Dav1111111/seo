"""Fingerprint admin/internal API — manual rebuild + stats + similarity search."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.fingerprint import api as fp_api
from app.fingerprint.enums import FingerprintStatus
from app.fingerprint.models import PageFingerprint

router = APIRouter()


class QueuedResponse(BaseModel):
    task_id: str
    status: str


@router.post("/fingerprint/sites/{site_id}/rebuild", response_model=QueuedResponse)
async def trigger_site_rebuild(site_id: uuid.UUID, force: bool = False):
    from app.fingerprint.tasks import fingerprint_site, fingerprint_site_force
    task = (fingerprint_site_force if force else fingerprint_site).delay(str(site_id))
    return QueuedResponse(task_id=task.id, status="queued")


@router.post("/fingerprint/pages/{page_id}/rebuild", response_model=QueuedResponse)
async def trigger_page_rebuild(page_id: uuid.UUID, force: bool = True):
    from app.fingerprint.tasks import fingerprint_page
    task = fingerprint_page.delay(str(page_id), force)
    return QueuedResponse(task_id=task.id, status="queued")


@router.get("/fingerprint/pages/{page_id}")
async def get_page_fingerprint(
    page_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    fp = await fp_api.get_fingerprint(db, page_id)
    if not fp:
        raise HTTPException(status_code=404, detail="Fingerprint not found")
    return {
        "page_id": str(fp.page_id),
        "site_id": str(fp.site_id),
        "normalized_url": fp.normalized_url,
        "status": fp.status,
        "skip_reason": fp.skip_reason,
        "content_hash": fp.content_hash,
        "content_length_chars": fp.content_length_chars,
        "content_length_tokens": fp.content_length_tokens,
        "boilerplate_ratio": fp.boilerplate_ratio,
        "extraction_status": fp.extraction_status,
        "extraction_error": fp.extraction_error,
        "versions": {
            "extraction": fp.extraction_version,
            "lemmatization": fp.lemmatization_version,
            "minhash": fp.minhash_version,
            "ngram": fp.ngram_version,
            "ngram_format": fp.ngram_format_version,
            "schema": fp.fingerprint_schema_version,
        },
        "last_fingerprinted_at": fp.last_fingerprinted_at.isoformat() if fp.last_fingerprinted_at else None,
        "last_status_at": fp.last_status_at.isoformat() if fp.last_status_at else None,
    }


@router.get("/fingerprint/sites/{site_id}/stats")
async def site_fingerprint_stats(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    # Count by status
    rows = await db.execute(
        select(PageFingerprint.status, func.count())
        .where(PageFingerprint.site_id == site_id)
        .group_by(PageFingerprint.status)
    )
    by_status = {row[0]: row[1] for row in rows}

    total = (await db.execute(
        select(func.count(PageFingerprint.page_id)).where(PageFingerprint.site_id == site_id)
    )).scalar() or 0

    oldest = (await db.execute(
        select(func.min(PageFingerprint.last_fingerprinted_at))
        .where(PageFingerprint.site_id == site_id)
    )).scalar()

    newest = (await db.execute(
        select(func.max(PageFingerprint.last_fingerprinted_at))
        .where(PageFingerprint.site_id == site_id)
    )).scalar()

    # Boilerplate ratio QA flags
    high_boilerplate = (await db.execute(
        select(func.count())
        .where(
            PageFingerprint.site_id == site_id,
            PageFingerprint.boilerplate_ratio > 0.7,
        )
    )).scalar() or 0

    return {
        "total": total,
        "by_status": by_status,
        "oldest_fingerprint": oldest.isoformat() if oldest else None,
        "newest_fingerprint": newest.isoformat() if newest else None,
        "high_boilerplate_count": high_boilerplate,
    }


@router.get("/fingerprint/sites/{site_id}/duplicates")
async def list_duplicates(
    site_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List exact-duplicate groups (same content_hash) on a site."""
    groups = await fp_api.find_exact_duplicates(db, site_id)
    return {
        "groups": [[str(pid) for pid in g] for g in groups],
        "count": len(groups),
    }


@router.get("/fingerprint/pages/{page_id}/similar")
async def similar_pages(
    page_id: uuid.UUID,
    algorithm: str = Query(default="hybrid", pattern="^(minhash|ngram|hybrid)$"),
    threshold: float = Query(default=0.7, ge=0.0, le=1.0),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    results = await fp_api.find_similar_pages(
        db, page_id, algorithm=algorithm, threshold=threshold, limit=limit,
    )
    return {
        "count": len(results),
        "items": [r.model_dump(mode="json") for r in results],
    }
