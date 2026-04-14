"""
Yandex Metrica API collector.
https://yandex.ru/dev/metrika/doc/api2/api_v1/intro.html

Stub for Phase 2 — will be activated when Metrica counter is created for grandtourspirit.ru
"""

import logging
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import BaseCollector
from app.models.daily_metric import DailyMetric

logger = logging.getLogger(__name__)


class MetricaCollector(BaseCollector):
    base_url = "https://api-metrika.yandex.net"

    def __init__(self, oauth_token: str, counter_id: str):
        super().__init__(oauth_token)
        self.counter_id = counter_id

    async def fetch_site_traffic(self, date_from: date, date_to: date) -> list[dict]:
        """Daily site traffic: visits, pageviews, bounce_rate, avg_duration."""
        data = await self.get(
            "/stat/v1/data/bytime",
            params={
                "id": self.counter_id,
                "metrics": "ym:s:visits,ym:s:pageviews,ym:s:bounceRate,ym:s:avgVisitDurationSeconds",
                "date1": date_from.isoformat(),
                "date2": date_to.isoformat(),
                "group": "day",
            },
        )
        return data.get("data", [])

    async def fetch_landing_pages(self, date_from: date, date_to: date, limit: int = 200) -> list[dict]:
        """Top landing pages by visits."""
        data = await self.get(
            "/stat/v1/data",
            params={
                "id": self.counter_id,
                "metrics": "ym:s:visits,ym:s:pageviews,ym:s:bounceRate,ym:s:avgVisitDurationSeconds",
                "dimensions": "ym:s:startURL",
                "date1": date_from.isoformat(),
                "date2": date_to.isoformat(),
                "sort": "-ym:s:visits",
                "limit": limit,
            },
        )
        return data.get("data", [])

    async def fetch_traffic_sources(self, date_from: date, date_to: date) -> list[dict]:
        """Traffic by source: search, direct, referral, social."""
        data = await self.get(
            "/stat/v1/data",
            params={
                "id": self.counter_id,
                "metrics": "ym:s:visits,ym:s:pageviews",
                "dimensions": "ym:s:lastTrafficSource",
                "date1": date_from.isoformat(),
                "date2": date_to.isoformat(),
            },
        )
        return data.get("data", [])

    async def collect_and_store(
        self,
        db: AsyncSession,
        site_id: UUID,
        days_back: int = 7,
    ) -> dict:
        """Main entry: fetch Metrica data and persist."""
        if not self.counter_id:
            logger.warning("Metrica counter_id not set — skipping")
            return {"status": "skipped", "reason": "no counter_id"}

        today = date.today()
        end_date = today - timedelta(days=1)  # yesterday (Metrica available same day)
        start_date = end_date - timedelta(days=days_back - 1)

        stats = {"traffic_days": 0, "landing_pages": 0}

        # 1. Site-level daily traffic
        logger.info("Fetching Metrica traffic %s → %s", start_date, end_date)
        try:
            traffic_data = await self.fetch_site_traffic(start_date, end_date)
            # bytime returns one row with time_intervals array
            for row in traffic_data:
                time_intervals = row.get("dimensions", [])
                metrics_list = row.get("metrics", [])
                # metrics_list is [[visits_per_day], [pageviews_per_day], ...]
                if len(metrics_list) >= 4:
                    visits_arr = metrics_list[0]
                    pv_arr = metrics_list[1]
                    bounce_arr = metrics_list[2]
                    dur_arr = metrics_list[3]

                    for i, interval in enumerate(time_intervals):
                        # interval is like [{"from": "2026-04-10", "to": "2026-04-11"}]
                        if isinstance(interval, list) and interval:
                            date_str = interval[0].get("from", "")
                        elif isinstance(interval, dict):
                            date_str = interval.get("from", "")
                        else:
                            continue

                        if not date_str:
                            continue

                        metric_date = date.fromisoformat(date_str[:10])
                        stmt = pg_insert(DailyMetric).values(
                            site_id=site_id,
                            date=metric_date,
                            metric_type="site_traffic",
                            dimension_id=None,
                            visits=int(visits_arr[i]) if i < len(visits_arr) else 0,
                            pageviews=int(pv_arr[i]) if i < len(pv_arr) else 0,
                            bounce_rate=round(bounce_arr[i] / 100, 4) if i < len(bounce_arr) else None,
                            avg_duration=round(dur_arr[i], 2) if i < len(dur_arr) else None,
                        ).on_conflict_do_update(
                            index_elements=["site_id", "date", "metric_type", "dimension_id"],
                            set_={
                                "visits": int(visits_arr[i]) if i < len(visits_arr) else 0,
                                "pageviews": int(pv_arr[i]) if i < len(pv_arr) else 0,
                                "bounce_rate": round(bounce_arr[i] / 100, 4) if i < len(bounce_arr) else None,
                                "avg_duration": round(dur_arr[i], 2) if i < len(dur_arr) else None,
                            },
                        )
                        await db.execute(stmt)
                        stats["traffic_days"] += 1
        except Exception as exc:
            logger.error("Metrica traffic fetch failed: %s", exc)

        await db.commit()
        logger.info("Metrica collection done: %s", stats)
        return stats
