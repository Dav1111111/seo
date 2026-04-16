"""Site-level ranking + weekly-plan diversification.

Pure functions over ranked PrioritizedItem lists — no DB.
"""

from __future__ import annotations

from collections import defaultdict, deque

from app.core_audit.priority.dto import PrioritizedItem, WeeklyPlan


MAX_PER_PAGE_DEFAULT = 2                          # SEO guidance for tourism


def rank(items: list[PrioritizedItem]) -> list[PrioritizedItem]:
    """Sort by priority_score DESC, tiebreak on impact, then ease."""
    return sorted(
        items,
        key=lambda x: (x.priority_score, x.impact, x.ease),
        reverse=True,
    )


def weekly_plan(
    items: list[PrioritizedItem],
    *,
    top_n: int = 10,
    max_per_page: int = MAX_PER_PAGE_DEFAULT,
) -> WeeklyPlan:
    """Round-robin pick top-N across pages, capped per page.

    Algorithm:
      1. Sort all items by priority_score DESC
      2. Bucket by page_id preserving order
      3. Rotate through buckets popping the highest-scored rec each round
      4. Skip a page once it hits max_per_page
      5. Fall back to top-scored regardless of cap if still short
    """
    if not items:
        return WeeklyPlan(items=(), total_in_backlog=0, max_per_page=max_per_page, pages_represented=0)

    ordered = rank(items)
    buckets: dict = defaultdict(deque)
    for item in ordered:
        buckets[item.page_id].append(item)

    picks: list[PrioritizedItem] = []
    picks_per_page: dict = defaultdict(int)
    page_ids = list(buckets.keys())

    while len(picks) < top_n and any(buckets[p] for p in page_ids):
        progressed = False
        for pid in page_ids:
            if len(picks) >= top_n:
                break
            if not buckets[pid]:
                continue
            if picks_per_page[pid] >= max_per_page:
                continue
            picks.append(buckets[pid].popleft())
            picks_per_page[pid] += 1
            progressed = True
        if not progressed:
            break  # all remaining pages hit cap — fall through to filler

    # Filler: if we still have room and some pages have recs left, allow
    # extras beyond max_per_page to reach top_n.
    if len(picks) < top_n:
        remaining_sorted = [it for pid in page_ids for it in buckets[pid]]
        remaining_sorted = rank(remaining_sorted)
        for it in remaining_sorted:
            if len(picks) >= top_n:
                break
            picks.append(it)

    return WeeklyPlan(
        items=tuple(picks),
        total_in_backlog=len(items),
        max_per_page=max_per_page,
        pages_represented=len({p.page_id for p in picks}),
    )
