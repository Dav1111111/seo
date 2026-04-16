"""Back-compat shim — forwards Standalone Value Test to profile-driven core.

Callers without an explicit profile default to tourism/tour_operator.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.intent_codes import IntentCode
from app.core_audit.standalone_test import (
    StandaloneTestResult,
    check_c1_unique_entity as _c1_core,
    check_c2_irreducible_content,
    check_c3_distinct_user_task,
    check_c4_distinct_serp,
    check_c5_long_term_demand,
    run_standalone_test as _run_core,
)
from app.profiles.tourism import TOURISM_TOUR_OPERATOR


def check_c1_unique_entity(proposed_title: str, proposed_query: str | None = None) -> tuple[bool, str]:
    return _c1_core(proposed_title, TOURISM_TOUR_OPERATOR, proposed_query)


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
    return await _run_core(
        db,
        TOURISM_TOUR_OPERATOR,
        proposed_title=proposed_title,
        proposed_intent=proposed_intent,
        site_id=site_id,
        proposed_query=proposed_query,
        parent_intent=parent_intent,
        parent_page_word_count=parent_page_word_count,
        min_pass_count=min_pass_count,
    )


__all__ = [
    "StandaloneTestResult",
    "check_c1_unique_entity",
    "check_c2_irreducible_content",
    "check_c3_distinct_user_task",
    "check_c4_distinct_serp",
    "check_c5_long_term_demand",
    "run_standalone_test",
]
