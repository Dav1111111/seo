"""Celery task — BusinessUnderstandingAgent run (Этап 1 step 1).

Triggered via the admin onboarding endpoint; writes the result into
`sites.understanding` JSONB and advances `sites.onboarding_step` from
`pending_analyze` to `confirm_business` so the wizard can proceed.

Race-safe via pg_try_advisory_lock on a key derived from site_id —
prevents two concurrent runs from clobbering each other when the user
impatiently double-clicks "Analyze".

Fail-open: any internal error is caught, persisted into
`sites.understanding.error`, and returned as a structured dict.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy import select, text

from app.core_audit.onboarding.business_understanding import (
    understand_business,
)
from app.models.page import Page
from app.models.site import Site
from app.workers.celery_app import celery_app
from app.workers.db_session import task_session

log = logging.getLogger(__name__)


# Derive a stable advisory-lock key from the site UUID. PG locks are
# 64-bit ints; we fold the UUID hash into a signed 64-bit range.
def _advisory_key(site_id: UUID) -> int:
    raw = int(site_id.hex[:16], 16)  # first 64 bits of the UUID
    # Shift to signed 64-bit (PG bigint) — no semantic meaning, just
    # avoid the upper-range warning.
    return raw - (1 << 63)


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="onboarding_understand_site", bind=True, max_retries=0)
def onboarding_understand_site_task(self, site_id: str) -> dict:
    """Analyze the business from crawled pages and persist understanding.

    Advances onboarding_step to "confirm_business" on success. If the
    site is already past confirm_business, re-runs in-place but does not
    regress the step — the user may have explicitly re-triggered from a
    later step.
    """

    async def _inner() -> dict:
        try:
            async with task_session() as db:
                lock_key = _advisory_key(UUID(site_id))
                locked = (await db.execute(
                    text("SELECT pg_try_advisory_lock(:k)"),
                    {"k": lock_key},
                )).scalar_one()
                if not locked:
                    return {
                        "status": "skipped",
                        "reason": "concurrent_run",
                        "site_id": site_id,
                    }

                try:
                    site = await db.get(Site, UUID(site_id))
                    if site is None:
                        return {
                            "status": "skipped",
                            "reason": "site_not_found",
                            "site_id": site_id,
                        }

                    # Top-N crawled pages by word_count desc. Homepage
                    # first so the agent anchors on it.
                    pages_q = await db.execute(
                        select(Page)
                        .where(Page.site_id == site.id)
                        .where(Page.content_text.isnot(None))
                        .where(Page.word_count.isnot(None))
                        .order_by(
                            (Page.path == "/").desc(),
                            Page.word_count.desc().nullslast(),
                        )
                        .limit(25)
                    )
                    pages = [
                        {
                            "url": p.url,
                            "title": p.title,
                            "h1": p.h1,
                            "content_text": p.content_text,
                        }
                        for p in pages_q.scalars()
                    ]

                    result = understand_business(
                        site_domain=site.domain,
                        site_display_name=site.display_name,
                        pages=pages,
                    )

                    site.understanding = result.to_jsonb()
                    # Only advance if still at the initial step — don't
                    # regress the wizard if the user is re-running from
                    # a later step.
                    if site.onboarding_step == "pending_analyze" and result.status == "ok":
                        site.onboarding_step = "confirm_business"

                    await db.commit()

                    return {
                        "status": result.status,
                        "site_id": site_id,
                        "pages_analyzed": result.pages_analyzed,
                        "cost_usd": result.cost_usd,
                        "onboarding_step": site.onboarding_step,
                    }
                finally:
                    await db.execute(
                        text("SELECT pg_advisory_unlock(:k)"),
                        {"k": lock_key},
                    )
        except Exception as exc:  # noqa: BLE001 — fail-open
            log.warning(
                "onboarding.understanding.task_failed site=%s err=%s",
                site_id, exc,
            )
            return {"status": "error", "site_id": site_id, "err": str(exc)}

    return _run(_inner())


__all__ = ["onboarding_understand_site_task"]
