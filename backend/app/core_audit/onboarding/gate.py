"""Onboarding gate helpers — skip nightly pipelines for sites still in wizard.

Rule: scheduled batch tasks (nightly, weekly) iterate only over sites whose
`onboarding_step == "active"`. A site that is mid-onboarding (pending_analyze
or confirm_*) collects raw data (Webmaster, crawler) but does NOT get its
recommendations regenerated — otherwise the plan would be built on top of
unconfirmed target_config assumptions.

Manual triggers (per-site API endpoints like POST /sites/{id}/pipeline)
bypass this gate: the user asked, they get it.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.site import Site


ACTIVE_STEP = "active"


async def onboarded_site_ids(db: AsyncSession) -> list[UUID]:
    """Return IDs of sites past onboarding (scheduled-pipeline eligible)."""
    stmt = select(Site.id).where(
        Site.is_active.is_(True),
        Site.onboarding_step == ACTIVE_STEP,
    )
    return [r[0] for r in await db.execute(stmt)]


async def onboarded_site_ids_with(db: AsyncSession, extra_col) -> list:
    """Same as onboarded_site_ids but selects an extra Site column alongside id.

    Useful for callers that want e.g. `(Site.id, Site.vertical)`.
    """
    stmt = select(Site.id, extra_col).where(
        Site.is_active.is_(True),
        Site.onboarding_step == ACTIVE_STEP,
    )
    return list(await db.execute(stmt))


__all__ = ["onboarded_site_ids", "onboarded_site_ids_with", "ACTIVE_STEP"]
