"""Public API for downstream modules (Module 2, 3, 4).

Convention: all functions are async, first arg is AsyncSession.
Similarity primitives are sync (pure bytes → number).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fingerprint.dto import (
    FingerprintResult,
    PageSnapshot,
    SimilarityAlgorithm,
    SimilarityResult,
)
from app.fingerprint.enums import FingerprintStatus
from app.fingerprint.minhash import jaccard as minhash_jaccard
from app.fingerprint.models import PageFingerprint
from app.fingerprint.ngrams import cosine as ngram_cosine_fn
from app.models.page import Page


async def get_fingerprint(db: AsyncSession, page_id: UUID) -> PageFingerprint | None:
    """Get raw fingerprint row (for downstream compute)."""
    r = await db.execute(
        select(PageFingerprint).where(PageFingerprint.page_id == page_id)
    )
    return r.scalar_one_or_none()


async def get_page_snapshot(db: AsyncSession, page_id: UUID) -> PageSnapshot | None:
    """Combined read-model of page + fingerprint."""
    r = await db.execute(
        select(Page, PageFingerprint).outerjoin(
            PageFingerprint, PageFingerprint.page_id == Page.id
        ).where(Page.id == page_id)
    )
    row = r.first()
    if not row:
        return None
    page, fp = row
    return _build_snapshot(page, fp)


async def get_site_snapshots(
    db: AsyncSession, site_id: UUID, include_missing_fp: bool = True
) -> list[PageSnapshot]:
    q = select(Page, PageFingerprint).outerjoin(
        PageFingerprint, PageFingerprint.page_id == Page.id
    ).where(Page.site_id == site_id)
    rows = await db.execute(q)
    out: list[PageSnapshot] = []
    for page, fp in rows:
        if fp is None and not include_missing_fp:
            continue
        out.append(_build_snapshot(page, fp))
    return out


async def find_exact_duplicates(
    db: AsyncSession, site_id: UUID
) -> list[list[UUID]]:
    """Groups of page_ids sharing the same content_hash on one site."""
    rows = await db.execute(
        select(PageFingerprint.content_hash, PageFingerprint.page_id)
        .where(
            PageFingerprint.site_id == site_id,
            PageFingerprint.status == FingerprintStatus.fingerprinted.value,
        )
        .order_by(PageFingerprint.content_hash)
    )
    groups: dict[str, list[UUID]] = {}
    for content_hash, page_id in rows:
        groups.setdefault(content_hash, []).append(page_id)
    return [g for g in groups.values() if len(g) > 1]


async def find_similar_pages(
    db: AsyncSession,
    page_id: UUID,
    algorithm: SimilarityAlgorithm = "hybrid",
    threshold: float = 0.7,
    limit: int = 20,
    same_site_only: bool = True,
) -> list[SimilarityResult]:
    """Find pages with similarity >= threshold. O(n) over site candidates."""
    target = await get_fingerprint(db, page_id)
    if not target or target.status != FingerprintStatus.fingerprinted.value:
        return []

    # Fetch candidates (same site, fingerprinted)
    conds = [
        PageFingerprint.page_id != page_id,
        PageFingerprint.status == FingerprintStatus.fingerprinted.value,
    ]
    if same_site_only:
        conds.append(PageFingerprint.site_id == target.site_id)

    candidates_q = select(PageFingerprint, Page.url).join(
        Page, Page.id == PageFingerprint.page_id
    ).where(and_(*conds))
    rows = await db.execute(candidates_q)

    results: list[SimilarityResult] = []
    for fp, url in rows:
        j = None
        c = None
        versions_ok = True

        # MinHash — seed and num_perm must match
        if target.minhash_signature and fp.minhash_signature:
            try:
                j = minhash_jaccard(target.minhash_signature, fp.minhash_signature)
            except Exception:
                versions_ok = False

        # Ngram — format_version and shape must match
        if target.ngram_hash_vector and fp.ngram_hash_vector and \
           target.ngram_format_version == fp.ngram_format_version and \
           target.ngram_n_features == fp.ngram_n_features and \
           target.ngram_ngram_range == fp.ngram_ngram_range:
            try:
                c = ngram_cosine_fn(target.ngram_hash_vector, fp.ngram_hash_vector)
            except Exception:
                versions_ok = False
        else:
            # Version mismatch — can't compute
            if target.ngram_hash_vector and fp.ngram_hash_vector:
                versions_ok = False

        # Compose score based on algorithm
        if algorithm == "minhash":
            score = j if j is not None else 0.0
        elif algorithm == "ngram":
            score = c if c is not None else 0.0
        else:  # hybrid
            if j is not None and c is not None:
                score = 0.5 * j + 0.5 * c
            elif j is not None:
                score = j
            elif c is not None:
                score = c
            else:
                score = 0.0

        if score < threshold:
            continue

        results.append(SimilarityResult(
            page_id=fp.page_id,
            url=url,
            algorithm=algorithm,
            score=round(score, 6),
            jaccard=round(j, 6) if j is not None else None,
            ngram_cosine=round(c, 6) if c is not None else None,
            exact_duplicate=(target.content_hash == fp.content_hash),
            title_equal=(target.title_normalized == fp.title_normalized)
                        if target.title_normalized else False,
            versions_compatible=versions_ok,
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:limit]


def compute_jaccard(sig_a: bytes, sig_b: bytes) -> float:
    """Pure function — exposed for Module 3/4."""
    return minhash_jaccard(sig_a, sig_b)


def compute_ngram_cosine(vec_a: bytes, vec_b: bytes) -> float:
    return ngram_cosine_fn(vec_a, vec_b)


async def invalidate_fingerprint(db: AsyncSession, page_id: UUID) -> None:
    """Mark fingerprint as invalid so next run recomputes (sets content_hash to empty)."""
    fp = await get_fingerprint(db, page_id)
    if fp:
        fp.content_hash = ""
        await db.flush()


# ── Helpers ────────────────────────────────────────────────────────────

def _build_snapshot(page: Page, fp: PageFingerprint | None) -> PageSnapshot:
    return PageSnapshot(
        page_id=page.id,
        site_id=page.site_id,
        url=page.url,
        path=page.path,
        normalized_url=(fp.normalized_url if fp else None),
        title=page.title,
        h1=page.h1,
        meta_description=page.meta_description,
        word_count=page.word_count,
        http_status=page.http_status,
        last_crawled_at=page.last_crawled_at,
        content_hash=(fp.content_hash if fp else None),
        fingerprint_schema_version=(fp.fingerprint_schema_version if fp else None),
        fingerprint_status=FingerprintStatus(fp.status) if fp else None,
        content_length_tokens=(fp.content_length_tokens if fp else None),
        last_fingerprinted_at=(fp.last_fingerprinted_at if fp else None),
        title_normalized=(fp.title_normalized if fp else None),
        h1_normalized=(fp.h1_normalized if fp else None),
    )
