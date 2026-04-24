"""Outcome follow-up — measure real-world effect 14 days post-apply.

Runs daily. Picks up outcome_snapshots where:
  - applied_at is between 14 and 45 days ago
  - followup_at is null (not yet measured)

For each, pulls last-7-days metrics of the site, computes delta vs
baseline, writes followup_metrics + delta + followup_at. That closes
the loop: owner sees "ты применил 18 дней назад, показы +32%".
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, func, select

from app.core_audit.activity import log_event
from app.models.daily_metric import DailyMetric
from app.models.outcome_snapshot import OutcomeSnapshot
from app.workers.celery_app import celery_app
from app.workers.db_session import task_session

log = logging.getLogger(__name__)


def _pct(new: float | int | None, old: float | int | None) -> float | None:
    try:
        if old is None or float(old) == 0:
            return None
        return round((float(new or 0) - float(old)) / float(old) * 100, 1)
    except Exception:  # noqa: BLE001
        return None


@celery_app.task(name="outcomes_followup_daily", bind=True, max_retries=1)
def outcomes_followup_daily_task(self) -> dict:
    """Fill followup metrics for all snapshots that matured today."""
    import asyncio

    async def _inner() -> dict:
        processed = 0
        async with task_session() as db:
            cutoff_min = datetime.now(timezone.utc) - timedelta(days=45)
            cutoff_max = datetime.now(timezone.utc) - timedelta(days=14)

            rows = (await db.execute(
                select(OutcomeSnapshot).where(
                    and_(
                        OutcomeSnapshot.applied_at <= cutoff_max,
                        OutcomeSnapshot.applied_at >= cutoff_min,
                        OutcomeSnapshot.followup_at.is_(None),
                    )
                )
            )).scalars().all()

            for snap in rows:
                # Fresh last-7-days window for the site
                today = date.today()
                week_ago = today - timedelta(days=7)
                row = (await db.execute(
                    select(
                        func.coalesce(func.sum(DailyMetric.impressions), 0).label("imp"),
                        func.coalesce(func.sum(DailyMetric.clicks), 0).label("clk"),
                        func.avg(DailyMetric.avg_position).label("pos"),
                    ).where(
                        DailyMetric.site_id == snap.site_id,
                        DailyMetric.metric_type == "query_performance",
                        DailyMetric.date.between(week_ago, today),
                    )
                )).first()

                followup = {
                    "impressions_7d": int(row.imp or 0) if row else 0,
                    "clicks_7d": int(row.clk or 0) if row else 0,
                    "avg_position": float(row.pos) if row and row.pos else None,
                }
                base = snap.baseline_metrics or {}
                delta = {
                    "impressions_pct": _pct(
                        followup["impressions_7d"], base.get("impressions_7d"),
                    ),
                    "clicks_pct": _pct(
                        followup["clicks_7d"], base.get("clicks_7d"),
                    ),
                    "position_delta": (
                        None
                        if base.get("avg_position") is None
                        or followup["avg_position"] is None
                        else round(
                            float(base["avg_position"]) - followup["avg_position"], 2,
                        )
                    ),
                }
                snap.followup_metrics = followup
                snap.delta = delta
                snap.followup_at = datetime.now(timezone.utc)

                await log_event(
                    db, snap.site_id, "outcome", "done",
                    (
                        f"Итог 14 дней по «{snap.recommendation_id}»: показы "
                        f"{_fmt_pct(delta['impressions_pct'])}, клики "
                        f"{_fmt_pct(delta['clicks_pct'])}."
                    ),
                    extra={"delta": delta, "recommendation_id": snap.recommendation_id},
                )
                processed += 1

            await db.commit()
        return {"status": "ok", "processed": processed}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_inner())
    finally:
        loop.close()


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "нет данных"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v}%"


__all__ = ["outcomes_followup_daily_task"]
