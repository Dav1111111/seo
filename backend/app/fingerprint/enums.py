"""Enums for fingerprint lifecycle."""

from enum import Enum


class FingerprintStatus(str, Enum):
    pending = "pending"
    extracted = "extracted"
    fingerprinted = "fingerprinted"
    skipped_unchanged = "skipped_unchanged"
    skipped_thin = "skipped_thin"
    skipped_unsupported = "skipped_unsupported"
    failed = "failed"


class SkipReason(str, Enum):
    unchanged_hash = "unchanged_hash"
    stale_not_reached = "stale_not_reached"
    unsupported_content = "unsupported_content"
    extraction_failed = "extraction_failed"
    version_mismatch_skip = "version_mismatch_skip"
    thin_content = "thin_content"


class ExtractionStatus(str, Enum):
    ok = "ok"
    fallback_raw = "fallback_raw"
    failed = "failed"


class RecomputeReason(str, Enum):
    new = "new"
    changed = "changed"
    extraction_version_bump = "extraction_version_bump"
    lemmatization_version_bump = "lemmatization_version_bump"
    minhash_version_bump = "minhash_version_bump"
    ngram_version_bump = "ngram_version_bump"
    stale = "stale"
    unchanged = "unchanged"
    force = "force"
