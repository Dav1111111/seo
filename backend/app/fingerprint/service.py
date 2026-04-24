"""FingerprintService — orchestrates the full per-page pipeline.

Steps (from spec Section 2.2):
  1. LOAD       — input dto (caller provides PageRow data)
  2. CLEAN      — trafilatura extract main content
  3. HASH       — sha256 of normalized main_text
  4. CHANGE     — compare with existing row
  5. LEMMATIZE  — pymorphy3 tokens (only if recomputing)
  6. SHINGLE+MINHASH
  7. NGRAM TF-IDF HASH
  8. UPSERT
  9. RECORD (log + return result)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.fingerprint.boilerplate import compute_boilerplate_ratio, extract_main_content
from app.fingerprint.change_detection import CURRENT_VERSIONS, decide_recompute
from app.fingerprint.dto import FingerprintInput, FingerprintResult
from app.fingerprint.enums import (
    ExtractionStatus,
    FingerprintStatus,
    RecomputeReason,
    SkipReason,
)
from app.fingerprint.hashing import compute_content_hash
from app.fingerprint.lemmatize import lemmatize_tokens, normalize_heading, tokenize
from app.fingerprint.minhash import build_minhash
from app.fingerprint.ngrams import build_ngram_vector, get_format_version
from app.fingerprint.normalize import normalize_text_for_hash, normalize_url
from app.fingerprint.repository import (
    get_existing,
    touch_last_fingerprinted,
    upsert_fingerprint,
)
from app.fingerprint.shingles import word_shingles
from app.fingerprint.version import (
    FINGERPRINT_SCHEMA_VERSION,
    MINHASH_NUM_PERM,
    NGRAM_N_FEATURES,
    NGRAM_RANGE,
    SHINGLE_SIZE,
    THIN_CONTENT_CHARS,
)

logger = logging.getLogger(__name__)


class FingerprintService:
    async def compute_one(
        self, db: AsyncSession, inp: FingerprintInput
    ) -> FingerprintResult:
        t0 = time.monotonic()
        now = datetime.now(timezone.utc)

        normalized_url = normalize_url(inp.url)
        raw_text = inp.content_text or ""

        # Step 2 — CLEAN
        main_text, extraction_status, extraction_error = extract_main_content(raw_text)
        boilerplate_ratio = compute_boilerplate_ratio(raw_text, main_text)

        # Check thin content
        main_normalized_len = len(normalize_text_for_hash(main_text))
        if main_normalized_len < THIN_CONTENT_CHARS:
            # Still record a skipped_thin row so we know we looked at it
            await touch_last_fingerprinted(
                db,
                page_id=inp.page_id,
                site_id=inp.site_id,
                normalized_url=normalized_url,
                content_hash="0" * 64,
                status=FingerprintStatus.skipped_thin.value,
                skip_reason=SkipReason.thin_content.value,
                source_crawl_at=inp.last_crawled_at,
                extraction_status=extraction_status.value,
            )
            return self._skipped_result(
                inp, normalized_url, now,
                status=FingerprintStatus.skipped_thin,
                skip_reason=SkipReason.thin_content,
                recompute_reason=RecomputeReason.new,
                extraction_status=extraction_status,
                extraction_error=extraction_error,
                boilerplate_ratio=boilerplate_ratio,
                content_length_chars=main_normalized_len,
                content_hash="0" * 64,
            )

        # Step 3 — HASH
        content_hash = compute_content_hash(main_text)

        # Step 4 — CHANGE DETECTION
        existing = await get_existing(db, inp.page_id)
        should_recompute, reason = decide_recompute(
            existing, content_hash, force=inp.force, now=now
        )

        if not should_recompute:
            # No rebuild — just bump timestamps
            await touch_last_fingerprinted(
                db,
                page_id=inp.page_id,
                site_id=inp.site_id,
                normalized_url=normalized_url,
                content_hash=content_hash,
                status=FingerprintStatus.skipped_unchanged.value,
                skip_reason=SkipReason.unchanged_hash.value,
                source_crawl_at=inp.last_crawled_at,
                extraction_status=extraction_status.value,
            )
            return self._unchanged_result(
                inp, normalized_url, now, existing, content_hash,
                extraction_status=extraction_status,
                extraction_error=extraction_error,
                boilerplate_ratio=boilerplate_ratio,
                content_length_chars=main_normalized_len,
            )

        # Step 5 — LEMMATIZE
        tokens = tokenize(main_text)
        lemmas = lemmatize_tokens(tokens, drop_stopwords=True)
        title_norm = normalize_heading(inp.title)
        h1_norm = normalize_heading(inp.h1)

        # Step 6 — SHINGLE + MINHASH
        shingles = word_shingles(lemmas, k=SHINGLE_SIZE)
        minhash_blob = build_minhash(shingles, num_perm=MINHASH_NUM_PERM)

        # Step 7 — NGRAM TF-IDF HASH
        # Use normalized (but not lemmatized) main_text for char n-grams.
        # char_wb captures morphological variations natively.
        ngram_input = normalize_text_for_hash(main_text)
        ngram_blob = build_ngram_vector(ngram_input)

        # Step 8 — UPSERT
        await upsert_fingerprint(db, {
            "page_id": inp.page_id,
            "site_id": inp.site_id,
            "normalized_url": normalized_url,
            # Extraction
            "content_text_length": len(raw_text),
            "content_language": "ru",
            "main_content_extracted_at": now,
            "extraction_status": extraction_status.value,
            "extraction_error": extraction_error,
            # Core
            "content_hash": content_hash,
            "minhash_signature": minhash_blob,
            "minhash_num_perm": MINHASH_NUM_PERM,
            "shingle_size": SHINGLE_SIZE,
            "ngram_hash_vector": ngram_blob,
            "ngram_n_features": NGRAM_N_FEATURES,
            "ngram_ngram_range": f"{NGRAM_RANGE[0]},{NGRAM_RANGE[1]}",
            "ngram_format_version": get_format_version(),
            "title_normalized": title_norm,
            "h1_normalized": h1_norm,
            # Metrics
            "content_length_chars": main_normalized_len,
            "content_length_tokens": len(lemmas),
            "boilerplate_ratio": boilerplate_ratio,
            # Versioning
            "extraction_version": CURRENT_VERSIONS.extraction,
            "lemmatization_version": CURRENT_VERSIONS.lemmatization,
            "minhash_version": CURRENT_VERSIONS.minhash,
            "ngram_version": CURRENT_VERSIONS.ngram,
            "fingerprint_schema_version": FINGERPRINT_SCHEMA_VERSION,
            # Lifecycle
            "status": FingerprintStatus.fingerprinted.value,
            "skip_reason": None,
            "last_status_at": now,
            # Timing
            "source_crawl_at": inp.last_crawled_at,
            "last_fingerprinted_at": now,
        })

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "fingerprinted page_id=%s url=%s reason=%s tokens=%d elapsed=%dms",
            inp.page_id, inp.url, reason.value, len(lemmas), elapsed_ms,
        )

        return FingerprintResult(
            page_id=inp.page_id,
            site_id=inp.site_id,
            normalized_url=normalized_url,
            status=FingerprintStatus.fingerprinted,
            skip_reason=None,
            recompute_reason=reason,
            extraction_status=extraction_status,
            extraction_error=extraction_error,
            content_language="ru",
            boilerplate_ratio=boilerplate_ratio,
            content_hash=content_hash,
            minhash_num_perm=MINHASH_NUM_PERM,
            shingle_size=SHINGLE_SIZE,
            content_length_chars=main_normalized_len,
            content_length_tokens=len(lemmas),
            extraction_version=CURRENT_VERSIONS.extraction,
            lemmatization_version=CURRENT_VERSIONS.lemmatization,
            minhash_version=CURRENT_VERSIONS.minhash,
            ngram_version=CURRENT_VERSIONS.ngram,
            ngram_format_version=get_format_version(),
            last_fingerprinted_at=now,
        )

    # ── Helpers ────────────────────────────────────────────────────────

    def _skipped_result(
        self, inp: FingerprintInput, normalized_url: str, now: datetime, **kwargs
    ) -> FingerprintResult:
        return FingerprintResult(
            page_id=inp.page_id,
            site_id=inp.site_id,
            normalized_url=normalized_url,
            content_length_tokens=0,
            minhash_num_perm=MINHASH_NUM_PERM,
            shingle_size=SHINGLE_SIZE,
            extraction_version=CURRENT_VERSIONS.extraction,
            lemmatization_version=CURRENT_VERSIONS.lemmatization,
            minhash_version=CURRENT_VERSIONS.minhash,
            ngram_version=CURRENT_VERSIONS.ngram,
            ngram_format_version=get_format_version(),
            last_fingerprinted_at=now,
            **kwargs,
        )

    def _unchanged_result(
        self, inp: FingerprintInput, normalized_url: str, now: datetime,
        existing, content_hash: str, extraction_status, extraction_error,
        boilerplate_ratio: float, content_length_chars: int,
    ) -> FingerprintResult:
        return FingerprintResult(
            page_id=inp.page_id,
            site_id=inp.site_id,
            normalized_url=normalized_url,
            status=FingerprintStatus.skipped_unchanged,
            skip_reason=SkipReason.unchanged_hash,
            recompute_reason=RecomputeReason.unchanged,
            extraction_status=extraction_status,
            extraction_error=extraction_error,
            content_language=(existing.content_language if existing else "ru"),
            boilerplate_ratio=boilerplate_ratio,
            content_hash=content_hash,
            minhash_num_perm=(existing.minhash_num_perm if existing else MINHASH_NUM_PERM),
            shingle_size=(existing.shingle_size if existing else SHINGLE_SIZE),
            content_length_chars=content_length_chars,
            content_length_tokens=(existing.content_length_tokens if existing else 0),
            extraction_version=(existing.extraction_version if existing else CURRENT_VERSIONS.extraction),
            lemmatization_version=(existing.lemmatization_version if existing else CURRENT_VERSIONS.lemmatization),
            minhash_version=(existing.minhash_version if existing else CURRENT_VERSIONS.minhash),
            ngram_version=(existing.ngram_version if existing else CURRENT_VERSIONS.ngram),
            ngram_format_version=(existing.ngram_format_version if existing else get_format_version()),
            last_fingerprinted_at=now,
        )
