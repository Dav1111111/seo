"""Tourism-level brand tokens.

Most brands are configured per-site via Site.display_name; these are fallback
tokens shared across all tourism sites until the site-specific list is set.
"""

from __future__ import annotations

TOURISM_BRAND_TOKENS: frozenset[str] = frozenset({
    "южный континент",
    "южный-континент",
    "юк",
    "ук",
    "grand tour spirit",
    "grand tour",
    "гранд тур",
    "гранд тур спирит",
    "гтс",
    "gts",
})
