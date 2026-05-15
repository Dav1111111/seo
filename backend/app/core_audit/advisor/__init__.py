"""Advisor — unified advice center.

Pulls signals from every existing module (brain rules, health checks,
schema audit, keyword_match, robots audit, funnel coverage gaps,
Metrica health) and produces one ordered `AdviceFeed` of
`AdviceCard` objects.

Pure read + compose: NO DB WRITES, NO LLM CALLS. The owner opens
`/studio` home and sees one feed — critical/broken first, high SEO
impact second, info last.

Public surface — Agent 3 (frontend) consumes this:

    from app.core_audit.advisor import collect_advice, AdviceCard, AdviceFeed
    feed = await collect_advice(db, site_id)
"""

from app.core_audit.advisor.aggregator import collect_advice
from app.core_audit.advisor.dto import (
    AdviceCard,
    AdviceFeed,
    CATEGORY_BUMP,
    Category,
    Severity,
    SEVERITY_WEIGHT,
    compute_sort_score,
)

__all__ = [
    "collect_advice",
    "AdviceCard",
    "AdviceFeed",
    "Category",
    "Severity",
    "SEVERITY_WEIGHT",
    "CATEGORY_BUMP",
    "compute_sort_score",
]
