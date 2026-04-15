"""
Yandex Webmaster API v4 collector.
https://yandex.ru/dev/webmaster/doc/dg/reference/
"""

import logging
from datetime import date, timedelta
from urllib.parse import quote
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import BaseCollector
from app.models.search_query import SearchQuery
from app.models.daily_metric import DailyMetric
from app.models.page import Page

logger = logging.getLogger(__name__)


class WebmasterCollector(BaseCollector):
    base_url = "https://api.webmaster.yandex.net/v4"

    def __init__(self, oauth_token: str, user_id: str, host_id: str):
        super().__init__(oauth_token)
        self.user_id = user_id
        self.host_id = host_id

    @property
    def _host_prefix(self) -> str:
        # host_id contains colons (e.g. "https:www.site.ru:443") — must URL-encode
        encoded = quote(self.host_id, safe="")
        return f"/user/{self.user_id}/hosts/{encoded}"

    # ── Search Queries ─────────────────────────────────────────────────

    async def fetch_popular_queries(
        self,
        date_from: date,
        date_to: date,
        limit: int = 500,
    ) -> list[dict]:
        """Fetch top search queries by impressions."""
        # query_indicator must be repeated params, not comma-separated
        params = [
            ("order_by", "TOTAL_SHOWS"),
            ("date_from", date_from.isoformat()),
            ("date_to", date_to.isoformat()),
            ("query_indicator", "TOTAL_SHOWS"),
            ("query_indicator", "TOTAL_CLICKS"),
            ("query_indicator", "AVG_SHOW_POSITION"),
            ("limit", str(limit)),
        ]
        data = await self.get(f"{self._host_prefix}/search-queries/popular", params=params)
        return data.get("queries", [])

    async def fetch_query_history(
        self,
        query_id: str,
        date_from: date,
        date_to: date,
    ) -> dict:
        """Fetch daily history for a specific query."""
        return await self.get(
            f"{self._host_prefix}/search-queries/{query_id}/history",
            params={
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "query_indicator": "TOTAL_SHOWS,TOTAL_CLICKS,AVG_SHOW_POSITION",
            },
        )

    # ── Indexing ───────────────────────────────────────────────────────

    async def fetch_indexing_history(
        self,
        date_from: date,
        date_to: date,
    ) -> dict:
        """Fetch indexing status history (2xx, 3xx, 4xx, 5xx counts)."""
        return await self.get(
            f"{self._host_prefix}/indexing/history",
            params={
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
            },
        )

    # ── Search Events ──────────────────────────────────────────────────

    async def fetch_search_events(
        self,
        date_from: date,
        date_to: date,
    ) -> dict:
        """Fetch appeared/removed from search events."""
        return await self.get(
            f"{self._host_prefix}/search-urls/events/history",
            params={
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
            },
        )

    # ── Sitemaps ───────────────────────────────────────────────────────

    async def fetch_sitemaps(self) -> list[dict]:
        """Fetch sitemap list with errors."""
        data = await self.get(f"{self._host_prefix}/sitemaps/")
        return data.get("sitemaps", [])

    # ── Persist to DB ──────────────────────────────────────────────────

    async def collect_and_store(
        self,
        db: AsyncSession,
        site_id: UUID,
        days_back: int = 7,
    ) -> dict:
        """Main entry: fetch all data and persist."""
        today = date.today()
        # Webmaster data has ~5-10 day lag for query data
        end_date = today - timedelta(days=5)
        start_date = end_date - timedelta(days=max(days_back, 30) - 1)

        stats = {"queries": 0, "metrics": 0, "indexing": 0}

        # 1. Popular queries
        logger.info("Fetching popular queries %s → %s", start_date, end_date)
        queries = await self.fetch_popular_queries(start_date, end_date)
        stats["queries"] = len(queries)

        for q in queries:
            query_id = q.get("query_id", "")
            query_text = q.get("query_text", "")
            if not query_text:
                continue

            # Upsert search_query
            stmt = pg_insert(SearchQuery).values(
                site_id=site_id,
                query_text=query_text,
                yandex_query_id=str(query_id),
                last_seen_at=today,
            ).on_conflict_do_update(
                index_elements=["site_id", "query_text"],
                set_={"yandex_query_id": str(query_id), "last_seen_at": today},
            )
            result = await db.execute(stmt)

            # Get the search_query id
            sq_row = await db.execute(
                select(SearchQuery.id).where(
                    SearchQuery.site_id == site_id,
                    SearchQuery.query_text == query_text,
                )
            )
            sq_id = sq_row.scalar_one_or_none()
            if not sq_id:
                logger.warning("SearchQuery not found after upsert: %s", query_text)
                continue

            # Extract indicator values — API returns either:
            # - Aggregated numbers: {"TOTAL_SHOWS": 5.0, "TOTAL_CLICKS": 1.0}
            # - Daily arrays: {"TOTAL_SHOWS": [{"date": "2026-04-06", "value": 3}]}
            indicators = q.get("indicators", {})
            shows_raw = indicators.get("TOTAL_SHOWS", 0)
            clicks_raw = indicators.get("TOTAL_CLICKS", 0)
            pos_raw = indicators.get("AVG_SHOW_POSITION", 0)

            if isinstance(shows_raw, list):
                # Daily breakdown
                shows_map = {d["date"]: d["value"] for d in shows_raw}
                clicks_map = {d["date"]: d["value"] for d in (clicks_raw if isinstance(clicks_raw, list) else [])}
                pos_map = {d["date"]: d["value"] for d in (pos_raw if isinstance(pos_raw, list) else [])}

                for date_str in shows_map:
                    impressions = int(shows_map.get(date_str, 0))
                    clicks = int(clicks_map.get(date_str, 0))
                    position = pos_map.get(date_str)
                    ctr = (clicks / impressions) if impressions > 0 else 0

                    metric_date = date.fromisoformat(date_str)
                    stmt = pg_insert(DailyMetric).values(
                        site_id=site_id,
                        date=metric_date,
                        metric_type="query_performance",
                        dimension_id=sq_id,
                        impressions=impressions,
                        clicks=clicks,
                        ctr=round(ctr, 4),
                        avg_position=round(position, 2) if position else None,
                    ).on_conflict_do_update(
                        index_elements=["site_id", "date", "metric_type", "dimension_id"],
                        set_={
                            "impressions": impressions,
                            "clicks": clicks,
                            "ctr": round(ctr, 4),
                            "avg_position": round(position, 2) if position else None,
                        },
                    )
                    await db.execute(stmt)
                    stats["metrics"] += 1
            else:
                # Aggregated totals for the period — store as single row
                impressions = int(shows_raw)
                clicks = int(clicks_raw)
                position = float(pos_raw) if pos_raw else None
                ctr = (clicks / impressions) if impressions > 0 else 0

                stmt = pg_insert(DailyMetric).values(
                    site_id=site_id,
                    date=end_date,
                    metric_type="query_performance",
                    dimension_id=sq_id,
                    impressions=impressions,
                    clicks=clicks,
                    ctr=round(ctr, 4),
                    avg_position=round(position, 2) if position else None,
                ).on_conflict_do_update(
                    index_elements=["site_id", "date", "metric_type", "dimension_id"],
                    set_={
                        "impressions": impressions,
                        "clicks": clicks,
                        "ctr": round(ctr, 4),
                        "avg_position": round(position, 2) if position else None,
                    },
                )
                await db.execute(stmt)
                stats["metrics"] += 1

        # 2. Indexing history
        logger.info("Fetching indexing history")
        try:
            indexing = await self.fetch_indexing_history(start_date, end_date)
            http_2xx = indexing.get("HTTP_2XX", [])
            http_4xx = indexing.get("HTTP_4XX", [])
            http_5xx = indexing.get("HTTP_5XX", [])

            # Build per-date index data
            dates_seen: set[str] = set()
            for series in [http_2xx, http_4xx, http_5xx]:
                for point in series:
                    dates_seen.add(point["date"])

            idx_2xx = {d["date"]: d["value"] for d in http_2xx}
            idx_4xx = {d["date"]: d["value"] for d in http_4xx}
            idx_5xx = {d["date"]: d["value"] for d in http_5xx}

            for date_str in dates_seen:
                metric_date = date.fromisoformat(date_str)
                pages_ok = idx_2xx.get(date_str, 0)
                pages_4xx = idx_4xx.get(date_str, 0)
                pages_5xx = idx_5xx.get(date_str, 0)

                stmt = pg_insert(DailyMetric).values(
                    site_id=site_id,
                    date=metric_date,
                    metric_type="indexing",
                    dimension_id=None,
                    pages_indexed=pages_ok,
                    extra={"http_4xx": pages_4xx, "http_5xx": pages_5xx},
                ).on_conflict_do_update(
                    index_elements=["site_id", "date", "metric_type", "dimension_id"],
                    set_={
                        "pages_indexed": pages_ok,
                        "extra": {"http_4xx": pages_4xx, "http_5xx": pages_5xx},
                    },
                )
                await db.execute(stmt)
                stats["indexing"] += 1
        except Exception as exc:
            logger.warning("Indexing history fetch failed: %s", exc)

        # 3. Search events (appeared/removed)
        logger.info("Fetching search events")
        try:
            events = await self.fetch_search_events(start_date, end_date)
            appeared = events.get("APPEARED_IN_SEARCH", [])
            removed = events.get("REMOVED_FROM_SEARCH", [])

            for date_str in set(d["date"] for d in appeared + removed):
                metric_date = date.fromisoformat(date_str)
                app_count = next((d["value"] for d in appeared if d["date"] == date_str), 0)
                rem_count = next((d["value"] for d in removed if d["date"] == date_str), 0)

                stmt = pg_insert(DailyMetric).values(
                    site_id=site_id,
                    date=metric_date,
                    metric_type="search_events",
                    dimension_id=None,
                    pages_in_search=app_count,
                    extra={"removed_from_search": rem_count},
                ).on_conflict_do_update(
                    index_elements=["site_id", "date", "metric_type", "dimension_id"],
                    set_={
                        "pages_in_search": app_count,
                        "extra": {"removed_from_search": rem_count},
                    },
                )
                await db.execute(stmt)
        except Exception as exc:
            logger.warning("Search events fetch failed: %s", exc)

        await db.commit()
        logger.info("Webmaster collection done: %s", stats)
        return stats
