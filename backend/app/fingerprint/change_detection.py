"""Change detection: decide whether to recompute fingerprint for a page."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.fingerprint.enums import RecomputeReason
from app.fingerprint.models import PageFingerprint
from app.fingerprint.version import (
    EXTRACTION_VERSION,
    LEMMATIZATION_VERSION,
    MINHASH_VERSION,
    NGRAM_VERSION,
    STALENESS_DAYS,
)


@dataclass(frozen=True)
class ComponentVersions:
    extraction: str
    lemmatization: str
    minhash: str
    ngram: str


CURRENT_VERSIONS = ComponentVersions(
    extraction=EXTRACTION_VERSION,
    lemmatization=LEMMATIZATION_VERSION,
    minhash=MINHASH_VERSION,
    ngram=NGRAM_VERSION,
)


def decide_recompute(
    existing: PageFingerprint | None,
    new_content_hash: str,
    force: bool = False,
    now: datetime | None = None,
) -> tuple[bool, RecomputeReason]:
    """Decide whether to recompute based on state and version diffs.

    Rules (evaluated in order):
      0. force=True             → force
      1. no existing row        → new
      2. hash differs           → changed
      3. extraction_version ≠   → extraction_version_bump
      4. lemmatization_version ≠ → lemmatization_version_bump
      5. minhash_version ≠      → minhash_version_bump
      6. ngram_version ≠        → ngram_version_bump
      7. last_fp_at < now - STALENESS_DAYS → stale
      8. otherwise              → unchanged
    """
    if force:
        return True, RecomputeReason.force

    if not existing:
        return True, RecomputeReason.new

    if existing.content_hash != new_content_hash:
        return True, RecomputeReason.changed

    if existing.extraction_version != CURRENT_VERSIONS.extraction:
        return True, RecomputeReason.extraction_version_bump
    if existing.lemmatization_version != CURRENT_VERSIONS.lemmatization:
        return True, RecomputeReason.lemmatization_version_bump
    if existing.minhash_version != CURRENT_VERSIONS.minhash:
        return True, RecomputeReason.minhash_version_bump
    if existing.ngram_version != CURRENT_VERSIONS.ngram:
        return True, RecomputeReason.ngram_version_bump

    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=STALENESS_DAYS)
    if existing.last_fingerprinted_at and existing.last_fingerprinted_at < cutoff:
        return True, RecomputeReason.stale

    return False, RecomputeReason.unchanged
