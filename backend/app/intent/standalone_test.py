"""Standalone Value Test — Rule 1 gate.

Запрещено рекомендовать новую страницу, если задача решается усилением
существующей и если новая страница не несёт самостоятельной пользы.

New page must pass ≥3 of 5 criteria to be recommended:

  C1. Unique entity — страница про конкретную сущность
      (тур/достопримечательность/регион), не просто модификатор
  C2. Irreducible content — ≥400 слов, которые не дублируют родителя
  C3. Distinct user task — отличный от родительской CTA/действие
  C4. Distinct SERP — в топе Яндекса выделенные страницы, не категории
      (требует SERP API — skip в Phase 2C, возврат None)
  C5. Long-term demand — запросы стабильны ≥2 сезонов
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.intent.enums import IntentCode
from app.models.daily_metric import DailyMetric
from app.models.search_query import SearchQuery

logger = logging.getLogger(__name__)


@dataclass
class StandaloneTestResult:
    """Result of standalone value test."""
    c1_unique_entity: bool | None          # None = inconclusive
    c2_irreducible_content: bool | None
    c3_distinct_user_task: bool | None
    c4_distinct_serp: bool | None           # None if SERP data unavailable
    c5_long_term_demand: bool | None

    passed_count: int                       # how many passed (ignoring None)
    applicable_count: int                   # how many were checkable
    verdict_pass: bool                      # True if passed_count ≥ 3
    reasoning: list[str] = field(default_factory=list)


# ── Unique entity detection ───────────────────────────────────────────

# Known entities — things that deserve their own page
_UNIQUE_ENTITY_PATTERNS = [
    # Named tourist attractions
    r"\b(рица|гагра|пицунд|новый\s+афон|сухум|гегский\s+водопад|33\s+водопад|ахштырская|воронцовские|красная\s+поляна|роза\s+хутор|газпром\s+лаур|скайпарк|дендрарий|тисо-самшитов|имеретинская|мацеста)\b",
    # Specific pickup cities (real neighbourhoods)
    r"\b(лоо|адлер|хоста|кудепста|лазаревск|дагомыс|эсто-садок)\b",
    # Specific routes with identifiable names
    r"\b(золотое\s+кольцо|ведьмино\s+ущелье|мамедово\s+ущелье)\b",
]

# Generic modifiers — NOT unique entities
_GENERIC_MODIFIERS = [
    r"\b(недорого|дёшево|дешево|лучшие|топ|с\s+детьми|для\s+пенсионер|vip|недорогие)\b",
]


def check_c1_unique_entity(proposed_title: str, proposed_query: str | None = None) -> tuple[bool, str]:
    """C1: Does the proposed page target a unique entity, or just a modifier?"""
    text = f"{proposed_title} {proposed_query or ''}".lower()

    has_entity = any(re.search(p, text, re.I) for p in _UNIQUE_ENTITY_PATTERNS)
    only_modifier = all(
        not re.search(p, text, re.I) for p in _UNIQUE_ENTITY_PATTERNS
    ) and any(re.search(p, text, re.I) for p in _GENERIC_MODIFIERS)

    if has_entity:
        return True, "unique entity present in title/query"
    if only_modifier:
        return False, "only generic modifier, no unique entity"
    return True, "no generic modifier detected (assumed unique)"


def check_c2_irreducible_content(
    proposed_intent: IntentCode,
    parent_page_word_count: int | None = None,
) -> tuple[bool | None, str]:
    """C2: Can the topic support ≥400 unique words vs parent?

    Phase 2C heuristic:
      - Specific tour pages (COMM_MODIFIED, LOCAL_GEO): likely YES
      - Guide pages (INFO_DEST, INFO_LOGISTICS, INFO_PREP): likely YES if entity-specific
      - Category pages (COMM_CATEGORY): likely NO (overlaps existing catalog)
      - Compare pages (COMM_COMPARE): YES if head-to-head
      - Trust pages (TRUST_LEGAL): likely NO (reusable as section)
    """
    if proposed_intent in (
        IntentCode.COMM_MODIFIED,
        IntentCode.LOCAL_GEO,
        IntentCode.INFO_DEST,
        IntentCode.INFO_LOGISTICS,
    ):
        return True, f"intent {proposed_intent.value} typically supports 400+ unique words"

    if proposed_intent == IntentCode.COMM_CATEGORY:
        return False, "category pages usually overlap existing catalog"

    if proposed_intent in (IntentCode.TRUST_LEGAL, IntentCode.INFO_PREP):
        return False, f"intent {proposed_intent.value} better fits as section of parent"

    return None, "unclear"


def check_c3_distinct_user_task(
    proposed_intent: IntentCode,
    parent_intent: IntentCode | None = None,
) -> tuple[bool, str]:
    """C3: Does the new page enable a distinct user action?"""
    if parent_intent is None:
        return True, "no parent to overlap with"

    # Same intent as parent → user task is same → NO
    if proposed_intent == parent_intent:
        return False, f"same intent as parent ({parent_intent.value})"

    # Funnel stage differs → different user state (planning vs booking)
    if proposed_intent.funnel_stage != parent_intent.funnel_stage:
        return True, f"funnel stage differs ({parent_intent.funnel_stage} → {proposed_intent.funnel_stage})"

    return True, "different intent, likely distinct task"


def check_c4_distinct_serp(*args, **kwargs) -> tuple[None, str]:
    """C4: Does Yandex SERP prefer dedicated pages or aggregators?

    Requires SERP API — not available in Phase 2C.
    Returns None (inconclusive) so it doesn't count toward denominator.
    """
    return None, "SERP check skipped (Yandex XML Search API not configured)"


async def check_c5_long_term_demand(
    db: AsyncSession,
    *,
    site_id: UUID,
    query_cluster_keys: list[str] | None = None,
    min_days_active: int = 60,
) -> tuple[bool | None, str]:
    """C5: Are queries stable across ≥60 days of history?

    Without query-cluster mapping here, we use a proxy: check if queries
    in the target intent have impressions spread across ≥2 weekly buckets
    in the last 60 days.
    """
    today = date.today()
    cutoff = today - timedelta(days=min_days_active + 5)

    # Count distinct weeks with impressions
    wk_expr = func.date_trunc("week", DailyMetric.date)
    rows = await db.execute(
        select(
            wk_expr.label("wk"),
            func.sum(DailyMetric.impressions).label("imp"),
        )
        .where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.date >= cutoff,
        )
        .group_by(wk_expr)
        .order_by(wk_expr)
    )
    weeks_with_impressions = sum(1 for wk, imp in rows if imp and imp > 0)

    if weeks_with_impressions == 0:
        return None, "no historical data"
    if weeks_with_impressions >= 4:
        return True, f"{weeks_with_impressions} weeks with activity"

    return False, f"only {weeks_with_impressions} weeks with activity — possibly a spike"


async def run_standalone_test(
    db: AsyncSession,
    *,
    proposed_title: str,
    proposed_intent: IntentCode,
    site_id: UUID,
    proposed_query: str | None = None,
    parent_intent: IntentCode | None = None,
    parent_page_word_count: int | None = None,
    min_pass_count: int = 3,
) -> StandaloneTestResult:
    """Run all 5 criteria, aggregate verdict."""
    reasoning: list[str] = []

    # C1
    c1, r1 = check_c1_unique_entity(proposed_title, proposed_query)
    reasoning.append(f"C1 ({'PASS' if c1 else 'FAIL'}): {r1}")

    # C2
    c2, r2 = check_c2_irreducible_content(proposed_intent, parent_page_word_count)
    c2_str = "PASS" if c2 is True else ("FAIL" if c2 is False else "SKIP")
    reasoning.append(f"C2 ({c2_str}): {r2}")

    # C3
    c3, r3 = check_c3_distinct_user_task(proposed_intent, parent_intent)
    reasoning.append(f"C3 ({'PASS' if c3 else 'FAIL'}): {r3}")

    # C4 (skipped in Phase 2C)
    c4, r4 = check_c4_distinct_serp()
    reasoning.append(f"C4 (SKIP): {r4}")

    # C5
    c5, r5 = await check_c5_long_term_demand(db, site_id=site_id)
    c5_str = "PASS" if c5 is True else ("FAIL" if c5 is False else "SKIP")
    reasoning.append(f"C5 ({c5_str}): {r5}")

    # Count only applicable (not None)
    results = [c1, c2, c3, c4, c5]
    applicable = [r for r in results if r is not None]
    passed = sum(1 for r in applicable if r is True)
    applicable_count = len(applicable)

    verdict = passed >= min_pass_count

    return StandaloneTestResult(
        c1_unique_entity=c1,
        c2_irreducible_content=c2,
        c3_distinct_user_task=c3,
        c4_distinct_serp=c4,
        c5_long_term_demand=c5,
        passed_count=passed,
        applicable_count=applicable_count,
        verdict_pass=verdict,
        reasoning=reasoning,
    )
