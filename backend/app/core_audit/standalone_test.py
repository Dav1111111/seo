"""Profile-driven Standalone Value Test (Rule 1 gate).

  C1. Unique entity — читает profile.unique_entity_patterns / generic_modifier_patterns
  C2. Irreducible content — универсальная логика по intent
  C3. Distinct user task — универсальная (funnel stage)
  C4. Distinct SERP — stub (requires SERP API)
  C5. Long-term demand — DB query

Only C1 depends on profile data; C2-C5 are universal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.intent_codes import IntentCode
from app.core_audit.profile_protocol import SiteProfile
from app.models.daily_metric import DailyMetric

logger = logging.getLogger(__name__)


@dataclass
class StandaloneTestResult:
    c1_unique_entity: bool | None
    c2_irreducible_content: bool | None
    c3_distinct_user_task: bool | None
    c4_distinct_serp: bool | None
    c5_long_term_demand: bool | None

    passed_count: int
    applicable_count: int
    verdict_pass: bool
    reasoning: list[str] = field(default_factory=list)


def check_c1_unique_entity(
    proposed_title: str,
    profile: SiteProfile,
    proposed_query: str | None = None,
) -> tuple[bool, str]:
    """C1: Does the proposal target a unique entity (not just a generic modifier)?"""
    text = f"{proposed_title} {proposed_query or ''}".lower()

    has_entity = any(p.search(text) for p in profile.unique_entity_patterns)
    only_modifier = (
        not has_entity
        and any(p.search(text) for p in profile.generic_modifier_patterns)
    )

    if has_entity:
        return True, "unique entity present in title/query"
    if only_modifier:
        return False, "only generic modifier, no unique entity"
    return True, "no generic modifier detected (assumed unique)"


def check_c2_irreducible_content(
    proposed_intent: IntentCode,
    parent_page_word_count: int | None = None,
) -> tuple[bool | None, str]:
    """C2: Can the topic support ≥400 unique words vs parent?"""
    if proposed_intent in (
        IntentCode.COMM_MODIFIED,
        IntentCode.LOCAL_GEO,
        IntentCode.INFO_DEST,
        IntentCode.INFO_LOGISTICS,
    ):
        return True, f"intent {proposed_intent.value} typically supports 400+ unique words"

    if proposed_intent is IntentCode.COMM_CATEGORY:
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

    if proposed_intent is parent_intent:
        return False, f"same intent as parent ({parent_intent.value})"

    if proposed_intent.funnel_stage != parent_intent.funnel_stage:
        return True, (
            f"funnel stage differs ({parent_intent.funnel_stage} → {proposed_intent.funnel_stage})"
        )

    return True, "different intent, likely distinct task"


def check_c4_distinct_serp(*args, **kwargs) -> tuple[None, str]:
    """C4: Inconclusive — requires SERP API."""
    return None, "SERP check skipped (Yandex XML Search API not configured)"


async def check_c5_long_term_demand(
    db: AsyncSession,
    *,
    site_id: UUID,
    query_cluster_keys: list[str] | None = None,
    min_days_active: int = 60,
) -> tuple[bool | None, str]:
    """C5: Stable query demand over ≥60 days? Weekly-bucket proxy."""
    today = date.today()
    cutoff = today - timedelta(days=min_days_active + 5)

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
    weeks_with_impressions = sum(1 for _wk, imp in rows if imp and imp > 0)

    if weeks_with_impressions == 0:
        return None, "no historical data"
    if weeks_with_impressions >= 4:
        return True, f"{weeks_with_impressions} weeks with activity"
    return False, f"only {weeks_with_impressions} weeks with activity — possibly a spike"


async def run_standalone_test(
    db: AsyncSession,
    profile: SiteProfile,
    *,
    proposed_title: str,
    proposed_intent: IntentCode,
    site_id: UUID,
    proposed_query: str | None = None,
    parent_intent: IntentCode | None = None,
    parent_page_word_count: int | None = None,
    min_pass_count: int = 3,
) -> StandaloneTestResult:
    reasoning: list[str] = []

    c1, r1 = check_c1_unique_entity(proposed_title, profile, proposed_query)
    reasoning.append(f"C1 ({'PASS' if c1 else 'FAIL'}): {r1}")

    c2, r2 = check_c2_irreducible_content(proposed_intent, parent_page_word_count)
    c2_str = "PASS" if c2 is True else ("FAIL" if c2 is False else "SKIP")
    reasoning.append(f"C2 ({c2_str}): {r2}")

    c3, r3 = check_c3_distinct_user_task(proposed_intent, parent_intent)
    reasoning.append(f"C3 ({'PASS' if c3 else 'FAIL'}): {r3}")

    c4, r4 = check_c4_distinct_serp()
    reasoning.append(f"C4 (SKIP): {r4}")

    c5, r5 = await check_c5_long_term_demand(db, site_id=site_id)
    c5_str = "PASS" if c5 is True else ("FAIL" if c5 is False else "SKIP")
    reasoning.append(f"C5 ({c5_str}): {r5}")

    results = [c1, c2, c3, c4, c5]
    applicable = [r for r in results if r is not None]
    passed = sum(1 for r in applicable if r is True)

    return StandaloneTestResult(
        c1_unique_entity=c1,
        c2_irreducible_content=c2,
        c3_distinct_user_task=c3,
        c4_distinct_serp=c4,
        c5_long_term_demand=c5,
        passed_count=passed,
        applicable_count=len(applicable),
        verdict_pass=passed >= min_pass_count,
        reasoning=reasoning,
    )
