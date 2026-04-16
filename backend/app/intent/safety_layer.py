"""Back-compat shim — Safety Layer forwards to profile-driven core.

Defaults to tourism profile when callers don't pass one.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.intent_codes import IntentCode
from app.core_audit.safety_layer import (
    INTENT_OVERLAP_THRESHOLD,
    SIMILARITY_BLOCK_HIGH,
    SIMILARITY_BLOCK_MID,
    SIMILARITY_SAFE,
    CheckResult,
    SafetyVerdict,
    check_cannibalization,
    check_doorway_pattern as _doorway_core,
    check_duplicate_risk,
    check_thin_content_forecast,
    run_safety_checks as _run_core,
)
from app.profiles.tourism import TOURISM_TOUR_OPERATOR


async def check_doorway_pattern(
    db: AsyncSession,
    proposed_url_path: str,
    site_id: UUID,
) -> CheckResult:
    return await _doorway_core(db, proposed_url_path, site_id, TOURISM_TOUR_OPERATOR)


async def run_safety_checks(
    db: AsyncSession,
    *,
    proposed_title: str,
    proposed_url_path: str,
    proposed_intent: IntentCode,
    site_id: UUID,
    query_volume_14d: int = 0,
    queries_in_cluster: int = 0,
) -> SafetyVerdict:
    return await _run_core(
        db,
        TOURISM_TOUR_OPERATOR,
        proposed_title=proposed_title,
        proposed_url_path=proposed_url_path,
        proposed_intent=proposed_intent,
        site_id=site_id,
        query_volume_14d=query_volume_14d,
        queries_in_cluster=queries_in_cluster,
    )


__all__ = [
    "INTENT_OVERLAP_THRESHOLD",
    "SIMILARITY_BLOCK_HIGH",
    "SIMILARITY_BLOCK_MID",
    "SIMILARITY_SAFE",
    "CheckResult",
    "SafetyVerdict",
    "check_cannibalization",
    "check_doorway_pattern",
    "check_duplicate_risk",
    "check_thin_content_forecast",
    "run_safety_checks",
]
