"""Pydantic v2 DTOs for fingerprinting."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.fingerprint.enums import (
    ExtractionStatus,
    FingerprintStatus,
    RecomputeReason,
    SkipReason,
)

SimilarityAlgorithm = Literal["minhash", "ngram", "hybrid"]


class FingerprintInput(BaseModel):
    """What the crawler / scheduler hands to the fingerprinter."""
    model_config = ConfigDict(frozen=True)

    page_id: UUID
    site_id: UUID
    url: str = Field(max_length=2048)
    title: str | None = Field(default=None, max_length=500)
    h1: str | None = Field(default=None, max_length=500)
    content_text: str | None = None
    last_crawled_at: datetime
    force: bool = False


class FingerprintResult(BaseModel):
    """Return of FingerprintService.compute_one()."""
    page_id: UUID
    site_id: UUID
    normalized_url: str

    status: FingerprintStatus
    skip_reason: SkipReason | None = None
    recompute_reason: RecomputeReason

    # Extraction
    extraction_status: ExtractionStatus
    extraction_error: str | None = None
    content_language: str = "ru"
    boilerplate_ratio: float = Field(ge=0.0, le=1.0, default=0.0)

    # Fingerprint payload metadata
    content_hash: str = Field(min_length=64, max_length=64)
    minhash_num_perm: int = 128
    shingle_size: int = 5
    content_length_chars: int = 0
    content_length_tokens: int = 0

    # Versioning
    extraction_version: str
    lemmatization_version: str
    minhash_version: str
    ngram_version: str
    ngram_format_version: str

    last_fingerprinted_at: datetime


class SimilarityResult(BaseModel):
    """One hit from a similarity search."""
    page_id: UUID
    url: str
    algorithm: SimilarityAlgorithm
    score: float = Field(ge=0.0, le=1.0)
    jaccard: float | None = Field(default=None, ge=0.0, le=1.0)
    ngram_cosine: float | None = Field(default=None, ge=0.0, le=1.0)
    exact_duplicate: bool = False
    title_equal: bool = False
    versions_compatible: bool = True


class PageSnapshot(BaseModel):
    """Aggregated read-model of a page's state."""
    model_config = ConfigDict(frozen=True)

    page_id: UUID
    site_id: UUID
    url: str
    path: str
    normalized_url: str | None = None

    # Crawl state
    title: str | None = None
    h1: str | None = None
    meta_description: str | None = None
    word_count: int | None = None
    http_status: int | None = None
    last_crawled_at: datetime | None = None

    # Fingerprint state
    content_hash: str | None = None
    fingerprint_schema_version: str | None = None
    fingerprint_status: FingerprintStatus | None = None
    content_length_tokens: int | None = None
    last_fingerprinted_at: datetime | None = None

    title_normalized: str | None = None
    h1_normalized: str | None = None


class FingerprintBatchStats(BaseModel):
    """What `fingerprint_site` returns."""
    site_id: UUID
    pages_total: int
    pages_computed: int
    pages_skipped_unchanged: int
    pages_skipped_thin: int
    pages_errored: int
    db_errors: int
    duration_ms: int
