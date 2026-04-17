"""Content hashing — sha256 over normalized main text."""

import hashlib

from app.fingerprint.normalize import normalize_text_for_hash


def compute_content_hash(main_text: str) -> str:
    """SHA256 hex of normalized main text.

    Input: main_text as returned by boilerplate.extract_main_content
    Normalization: NFKC + lowercase + whitespace collapse (see normalize_text_for_hash)
    Output: 64-char hex string
    """
    normalized = normalize_text_for_hash(main_text or "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
