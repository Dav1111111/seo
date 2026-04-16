"""Back-compat shim — DecisionTree forwards to profile-driven core.

Default profile = tourism/tour_operator when caller doesn't pass one.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.decision_tree import (
    MIN_IMPRESSIONS_COMMERCIAL,
    MIN_IMPRESSIONS_INFO,
    MIN_QUERIES_COMMERCIAL,
    MIN_QUERIES_INFO,
    STRONG_SCORE,
    WEAK_SCORE_MIN,
    DecisionOutput,
    DecisionTree as _DecisionTreeCore,
)
from app.intent.coverage import IntentClusterReport
from app.profiles.tourism import TOURISM_TOUR_OPERATOR


class DecisionTree:
    """Shim that binds to tourism profile. New call sites should use
    app.core_audit.decision_tree.DecisionTree(...).decide(..., profile)."""

    def __init__(self) -> None:
        self._inner = _DecisionTreeCore()

    async def decide(
        self,
        db: AsyncSession,
        report: IntentClusterReport,
        site_id: UUID,
    ) -> DecisionOutput:
        return await self._inner.decide(db, report, site_id, TOURISM_TOUR_OPERATOR)


__all__ = [
    "MIN_IMPRESSIONS_COMMERCIAL",
    "MIN_IMPRESSIONS_INFO",
    "MIN_QUERIES_COMMERCIAL",
    "MIN_QUERIES_INFO",
    "STRONG_SCORE",
    "WEAK_SCORE_MIN",
    "DecisionOutput",
    "DecisionTree",
]
