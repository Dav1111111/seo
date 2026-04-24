"""Postgres advisory locks scoped to a single site.

Background
----------
`sites.target_config` is a JSONB bag shared by several writers:
BusinessTruth persistence, competitor_profile, growth_opportunities,
onboarding chat finalize, owner-side overrides. Each one does the
classic read-modify-write:

    cfg = dict(site.target_config or {})
    cfg["some_key"] = ...
    site.target_config = cfg
    await db.commit()

Under concurrency (two Celery tasks on the same site) the later commit
stomps every key the earlier writer just added. Example pre-lock
incident: BusinessTruth rebuild and competitors_deep_dive finished
within ~200ms, the later one won, `target_config.business_truth` was
lost until the nightly rebuild.

The fix is a Postgres advisory lock keyed by site_id, acquired at the
top of every write transaction. It's transaction-scoped
(`pg_advisory_xact_lock`) so there's no release code to forget — it
unlocks on COMMIT or ROLLBACK.

We use the two-argument form `(namespace, key)` to avoid collisions
with advisory locks used elsewhere in the codebase or by vendor tools.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# Fixed namespace — any caller that locks on a site-scoped resource in a
# different category should pick a different integer. 0x5170 is an
# arbitrary marker for "target_config writers".
_TARGET_CONFIG_LOCK_NAMESPACE = 0x5170


async def lock_site_target_config(
    db: AsyncSession, site_id: UUID | str
) -> None:
    """Serialize writers on sites.target_config for one site.

    Idempotent within a transaction — Postgres allows re-acquiring the
    same lock from the same session without deadlock.

    Usage: call this BEFORE reading site.target_config. Any code that
    plans to modify and persist target_config must go through it.
    """
    key = str(site_id)
    await db.execute(
        text(
            "SELECT pg_advisory_xact_lock(:ns, hashtext(:k))"
        ),
        {"ns": _TARGET_CONFIG_LOCK_NAMESPACE, "k": key},
    )


async def try_lock_site_target_config(
    db: AsyncSession, site_id: UUID | str
) -> bool:
    """Non-blocking variant — returns True if the caller got the lock.

    Intended for readers that can bail out cheaply rather than wait.
    Writers should prefer `lock_site_target_config`.
    """
    key = str(site_id)
    result = await db.execute(
        text(
            "SELECT pg_try_advisory_xact_lock(:ns, hashtext(:k))"
        ),
        {"ns": _TARGET_CONFIG_LOCK_NAMESPACE, "k": key},
    )
    got: Any = result.scalar_one()
    return bool(got)


__all__ = [
    "lock_site_target_config",
    "try_lock_site_target_config",
]
