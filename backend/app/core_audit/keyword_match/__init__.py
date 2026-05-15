"""Keyword-gap analysis: deterministic Wordstat → page matcher.

For each search query the site already tracks (Wordstat volume > N), the
module finds the best-matching page on the site, lemmatizes both, and
emits a `KeywordGap` whenever the page's title / H1 / H2 / first paragraph
is missing query lemmas that aren't covered by domain synonyms.

This is a **pure data layer** — no LLM, no fabrication. Every gap is a
verifiable claim a human (or an LLM that consumes this output) can check
against the actual page text.

Other modules read `KeywordGap` and `KeywordGapsSummary`; they are the
contract. See `dto.py` for the frozen field list.
"""

from app.core_audit.keyword_match.dto import (
    KeywordGap,
    KeywordGapsSummary,
)
from app.core_audit.keyword_match.matcher import (
    compute_keyword_gaps,
    summarize_gaps,
)

__all__ = [
    "KeywordGap",
    "KeywordGapsSummary",
    "compute_keyword_gaps",
    "summarize_gaps",
]
