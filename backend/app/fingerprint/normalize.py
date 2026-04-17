"""Text and URL normalization utilities — deterministic, stable across runs."""

import re
import unicodedata
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# Zero-width and formatting chars to strip
_ZW_RE = re.compile(r"[\u200b-\u200f\ufeff\u2028\u2029]")
# Tracking/analytics query params to drop during URL normalization
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "yclid", "_ga", "mc_cid", "mc_eid", "ref", "ref_src",
})


def normalize_text_for_hash(text: str) -> str:
    """Stable, aggressive normalization for content hashing.

    NFKC → lowercase → strip zero-width → collapse whitespace → strip.
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = _ZW_RE.sub("", t)
    t = t.lower()
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalize_text_for_display(text: str) -> str:
    """Light normalization for title/h1 — preserves case for readability."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = _ZW_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalize_url(url: str) -> str:
    """Deterministic URL normalization.

    - NFKC on host
    - lowercase scheme and host
    - drop fragment
    - drop tracking params (utm_*, fbclid, gclid, yclid, etc.)
    - strip trailing slash (except root)
    - sort remaining query params alphabetically
    """
    if not url:
        return ""
    parsed = urlparse(url.strip())

    scheme = parsed.scheme.lower() or "https"
    netloc = unicodedata.normalize("NFKC", parsed.netloc).lower()
    path = parsed.path or "/"

    # Drop trailing slash except root
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    # Filter + sort query params
    params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
              if k.lower() not in _TRACKING_PARAMS]
    params.sort()
    query = urlencode(params)

    return urlunparse((scheme, netloc, path, "", query, ""))
