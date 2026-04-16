"""Section 2 — This Week's Action Plan.

Reuses Module 4 weekly_plan output. Suggested owner + ETA inferred from
category. Narrative text set by builder (LLM call).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.priority.service import PriorityService
from app.core_audit.report.dto import ActionPlanItem, ActionPlanSection


OWNER_BY_CATEGORY = {
    "title": "copywriter",
    "meta_description": "copywriter",
    "h1_structure": "copywriter",
    "over_optimization": "copywriter",
    "internal_linking": "seo",
    "commercial": "dev",
    "schema": "dev",
    "eeat": "legal",
}
DEFAULT_OWNER = "seo"


ETA_BY_PRIORITY = {
    "critical": "сегодня",
    "high": "эта неделя",
    "medium": "2 недели",
    "low": "месяц",
}


async def build_action_plan(
    db: AsyncSession, site_id: UUID, *, top_n: int = 10,
) -> ActionPlanSection:
    plan = await PriorityService().weekly_plan(db, site_id, top_n=top_n, max_per_page=2)

    items: list[ActionPlanItem] = []
    for it in plan.items:
        owner = OWNER_BY_CATEGORY.get(it.category, DEFAULT_OWNER)
        eta = ETA_BY_PRIORITY.get(it.priority, "эта неделя")
        items.append(ActionPlanItem(
            recommendation_id=it.recommendation_id,
            page_url=it.page_url,
            target_intent_code=it.target_intent_code,
            category=it.category,
            priority=it.priority,
            priority_score=it.priority_score,
            expected_lift_impressions=None,         # future: Module 6 outcomes
            suggested_owner=owner,
            eta_ru=eta,
            reasoning_ru=it.reasoning_ru,
            before_text=it.before_text,
            after_text=it.after_text,
        ))

    return ActionPlanSection(
        items=items,
        narrative_ru="",                            # filled by builder after LLM call
        narrative_source="template",
        pages_represented=plan.pages_represented,
        total_in_backlog=plan.total_in_backlog,
    )
