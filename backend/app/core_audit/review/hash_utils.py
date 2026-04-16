"""Composite idempotency hash for a review.

`composite_hash = sha256(content_hash | title | meta | h1 | target_intent)`

Normalization (applied to title / meta / h1):
  NFKC → casefold → collapse internal whitespace → strip.

Rules:
  - `target_intent_code` is included so two intents on the same page produce
    independent idempotency keys (one review per (page, intent) pair).
  - Punctuation is NOT stripped — "Title!" vs "Title." is a real editorial
    change that should trigger a new review.
  - `word_count` / `has_schema` are NOT part of the hash. They are derived
    from `content_hash` (for text) or are structural HTML signals that the
    fingerprint pipeline already folds into `content_hash`.
"""

from __future__ import annotations

import hashlib
import unicodedata

HASH_FIELD_SEPARATOR = "|"


def _normalize_for_hash(value: str | None) -> str:
    """NFKC → casefold → whitespace collapse → strip. None → ''."""
    if value is None:
        return ""
    v = unicodedata.normalize("NFKC", value).casefold()
    v = " ".join(v.split())
    return v.strip()


def compute_composite_hash(
    content_hash: str,
    title: str | None,
    meta_description: str | None,
    h1: str | None,
    target_intent_code: str,
) -> str:
    """Return 64-char lowercase hex sha256 over the review's identifying fields."""
    parts = [
        content_hash or "",
        _normalize_for_hash(title),
        _normalize_for_hash(meta_description),
        _normalize_for_hash(h1),
        (target_intent_code or "").lower(),
    ]
    payload = HASH_FIELD_SEPARATOR.join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
