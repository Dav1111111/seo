"""
Operating mode guard.

Modes:
  readonly     — detect + store issues, no actions
  recommend    — detect + store + show recommendations
  propose      — detect + store + create draft tasks
  autoexecute  — detect + store + create tasks + mark planned

Each level is a superset of the previous one.
"""

from __future__ import annotations

import logging
from enum import IntEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.site import Site

logger = logging.getLogger(__name__)


class OperatingLevel(IntEnum):
    READONLY = 0
    RECOMMEND = 1
    PROPOSE = 2
    AUTOEXECUTE = 3


MODE_MAP: dict[str, OperatingLevel] = {
    "readonly": OperatingLevel.READONLY,
    "recommend": OperatingLevel.RECOMMEND,
    "propose": OperatingLevel.PROPOSE,
    "autoexecute": OperatingLevel.AUTOEXECUTE,
}


class OperatingModeGuard:
    """Checks whether an action is permitted under the site's operating mode."""

    def __init__(self, mode: str):
        self.level = MODE_MAP.get(mode, OperatingLevel.READONLY)
        self.mode = mode

    def can_store_issues(self) -> bool:
        return self.level >= OperatingLevel.READONLY

    def can_show_recommendations(self) -> bool:
        return self.level >= OperatingLevel.RECOMMEND

    def can_create_tasks(self) -> bool:
        return self.level >= OperatingLevel.PROPOSE

    def can_auto_execute(self) -> bool:
        return self.level >= OperatingLevel.AUTOEXECUTE

    @classmethod
    async def for_site(cls, db: AsyncSession, site_id: UUID) -> OperatingModeGuard:
        result = await db.execute(
            select(Site.operating_mode).where(Site.id == site_id)
        )
        mode = result.scalar_one_or_none() or "readonly"
        return cls(mode)
