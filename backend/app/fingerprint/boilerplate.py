"""Boilerplate extraction — wraps trafilatura.

Contract:
- Input: raw content_text (already somewhat stripped by our crawler)
- Output: (main_text, extraction_status, error_message)

boilerplate_ratio formula (strictly documented):
    raw_chars  = len(raw_text.strip())
    main_chars = len(main_text.strip())
    ratio = 0.0 if raw_chars == 0 else max(0.0, 1.0 - main_chars/raw_chars)

Both sides measured BEFORE NFKC/lowercase normalization — we want to measure
structural reduction (what trafilatura removed), not post-normalization effects.
"""

import logging

try:
    import trafilatura
    _TRAFILATURA_AVAILABLE = True
except ImportError:
    _TRAFILATURA_AVAILABLE = False

from app.fingerprint.enums import ExtractionStatus

logger = logging.getLogger(__name__)


def extract_main_content(raw_text: str) -> tuple[str, ExtractionStatus, str | None]:
    """Extract main content using trafilatura text-only mode.

    Our crawler feeds us HTML-stripped text, but trafilatura can still identify
    and remove residual nav/footer/ads patterns via its heuristics.

    Returns:
        (main_text, status, error_message)
    """
    if not raw_text or not raw_text.strip():
        return "", ExtractionStatus.ok, None

    if not _TRAFILATURA_AVAILABLE:
        # Graceful degrade — use raw as-is, log once
        return raw_text, ExtractionStatus.fallback_raw, "trafilatura_not_installed"

    try:
        # text-only mode: input is already plain text from our crawler
        extracted = trafilatura.extract(
            raw_text,
            favor_recall=True,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
        )
        if extracted and extracted.strip():
            return extracted, ExtractionStatus.ok, None
        # Trafilatura returned None/empty — not an error, just means
        # content was too sparse. Fall back to raw.
        return raw_text, ExtractionStatus.fallback_raw, None
    except Exception as exc:
        logger.warning("trafilatura failed: %s", exc)
        return raw_text, ExtractionStatus.failed, str(exc)[:2000]


def compute_boilerplate_ratio(raw_text: str, main_text: str) -> float:
    """Strict formula (see module docstring)."""
    raw_chars = len((raw_text or "").strip())
    main_chars = len((main_text or "").strip())
    if raw_chars == 0:
        return 0.0
    if main_chars == 0:
        return 1.0
    ratio = 1.0 - (main_chars / raw_chars)
    return max(0.0, min(1.0, ratio))
