"""Tourism-level brand tokens.

Most brands are configured per-site via Site.display_name; these are fallback
tokens shared across all tourism sites until the site-specific list is set.
"""

from __future__ import annotations

# Short tokens (<4 chars) are dangerous: whole-word matching still triggers on
# legitimate queries like "юк сочи" or "ук-фактор" — better to leave them
# unclassified than misbrand them.
TOURISM_BRAND_TOKENS: frozenset[str] = frozenset({
    "южный континент",
    "южный-континент",
    "grand tour spirit",
    "grand tour",
    "гранд тур",
    "гранд тур спирит",
    # "гтс" / "gts" dropped — 3-char acronyms over-match too easily
    # ("gts" appears in many car/electronics queries; "гтс" in legal
    # docs). Configure per-site via Site.display_name when needed.
})


def _validate_brand_tokens(tokens: frozenset[str]) -> None:
    """Refuse to import a misbranded fallback list.

    Every entry must be ≥4 chars and contain no whitespace-only or
    leading/trailing whitespace. Multi-word brand names ARE allowed,
    but single short tokens are not (see module comment).
    """
    for tok in tokens:
        if not isinstance(tok, str):
            raise ValueError(f"brand token must be str, got {type(tok)!r}: {tok!r}")
        if tok != tok.strip():
            raise ValueError(f"brand token has leading/trailing whitespace: {tok!r}")
        if len(tok) < 4:
            raise ValueError(
                f"brand token too short (<4 chars), would over-match: {tok!r}"
            )


_validate_brand_tokens(TOURISM_BRAND_TOKENS)
