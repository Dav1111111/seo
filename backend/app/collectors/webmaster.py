"""
Yandex Webmaster API v4 collector.
https://yandex.ru/dev/webmaster/doc/dg/reference/
"""

import logging
from datetime import date, timedelta
from urllib.parse import quote
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import BaseCollector, HostNotLoadedError
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
        params = [
            ("date_from", date_from.isoformat()),
            ("date_to", date_to.isoformat()),
            ("query_indicator", "TOTAL_SHOWS"),
            ("query_indicator", "TOTAL_CLICKS"),
            ("query_indicator", "AVG_SHOW_POSITION"),
        ]
        return await self.get(
            f"{self._host_prefix}/search-queries/{query_id}/history",
            params=params,
        )

    # ── Indexing ───────────────────────────────────────────────────────

    async def fetch_indexing_history(
        self,
        date_from: date,
        date_to: date,
    ) -> dict:
        """Fetch indexing status history (2xx, 3xx, 4xx, 5xx counts).

        API returns: {"indicators": {"HTTP_2XX": [{"date": "..T..", "value": N}], ...}}
        We unwrap "indicators" so callers get {"HTTP_2XX": [...], ...} directly.
        """
        data = await self.get(
            f"{self._host_prefix}/indexing/history",
            params={
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
            },
        )
        return data.get("indicators", data)

    # ── Search Events ──────────────────────────────────────────────────

    async def fetch_search_events(
        self,
        date_from: date,
        date_to: date,
    ) -> dict:
        """Fetch appeared/removed from search events.

        API returns: {"indicators": {"APPEARED_IN_SEARCH": [...], "REMOVED_FROM_SEARCH": [...]}}
        We unwrap "indicators" so callers get the inner dict directly.
        """
        data = await self.get(
            f"{self._host_prefix}/search-urls/events/history",
            params={
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
            },
        )
        return data.get("indicators", data)

    # ── Sitemaps ───────────────────────────────────────────────────────

    async def fetch_sitemaps(self) -> list[dict]:
        """Fetch sitemap list with errors."""
        data = await self.get(f"{self._host_prefix}/sitemaps/")
        return data.get("sitemaps", [])

    # ── Per-URL index status (Studio v2 etap 1+2 deep) ─────────────────
    #
    # Two endpoints, both return paginated samples:
    #
    #   /search-urls/in-search/samples         indexed URLs
    #   /search-urls/excluded/samples           excluded URLs (with reason)
    #
    # Each item has `url`, `last-access`, plus reason for excluded.
    # Endpoint exposes max 100 per request — we paginate via offset.

    async def fetch_indexed_urls(
        self, *, max_pages: int = 50, page_size: int = 100,
    ) -> list[dict]:
        """All URLs Yandex considers indexed for this host.

        Returns list of {"url": str, "last-access": str-iso}. Stops when
        the API returns less than `page_size` (end of list) or after
        `max_pages` to bound runtime on big sites (50 × 100 = 5000 URLs).
        """
        all_items: list[dict] = []
        for offset in range(0, max_pages * page_size, page_size):
            data = await self.get(
                f"{self._host_prefix}/search-urls/in-search/samples",
                params={"offset": str(offset), "limit": str(page_size)},
            )
            samples = data.get("samples") or []
            if not samples:
                break
            all_items.extend(samples)
            if len(samples) < page_size:
                break
        return all_items

    async def fetch_excluded_urls(
        self, *, max_pages: int = 50, page_size: int = 100,
    ) -> list[dict]:
        """All URLs Yandex excluded from index, with `removal-reason`.

        Reason values verbatim from the API: NOT_FOUND, BAD_HTTP_STATUS,
        META_NO_INDEX, ROBOTS_TXT_HOST, NOT_CANONICAL, EXCLUDED_FROM_SEARCH,
        DUPLICATE_PAGE, etc.

        Soft-fail on 404 / RESOURCE_NOT_FOUND: this endpoint is sometimes
        unavailable per-host (verified live on grandtourspirit.ru —
        Yandex returns 404 here while `in-search/samples` works fine).
        Returns [] in that case so callers can keep the indexed list
        and just don't get reasons.
        """
        all_items: list[dict] = []
        for offset in range(0, max_pages * page_size, page_size):
            try:
                data = await self.get(
                    f"{self._host_prefix}/search-urls/excluded/samples",
                    params={"offset": str(offset), "limit": str(page_size)},
                )
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if "404" in msg or "RESOURCE_NOT_FOUND" in msg:
                    logger.info(
                        "webmaster.excluded_samples_404 host=%s — endpoint "
                        "unavailable for this host, returning empty",
                        self.host_id,
                    )
                    return []
                raise
            samples = data.get("samples") or []
            if not samples:
                break
            all_items.extend(samples)
            if len(samples) < page_size:
                break
        return all_items

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

        stats = {
            "queries": 0,
            "metrics": 0,
            "indexing": 0,
            "window_start": start_date.isoformat(),
            "window_end": end_date.isoformat(),
            # Per-step failures we used to only log; now surfaced so
            # the caller's terminal event can be honest about partial
            # data. Empty list on success.
            "errors": [],
        }

        # 1. Popular queries
        logger.info("Fetching popular queries %s → %s", start_date, end_date)
        try:
            queries = await self.fetch_popular_queries(start_date, end_date)
        except HostNotLoadedError:
            logger.warning(
                "Host %s not loaded in Yandex Webmaster — skipping collection. "
                "Open Webmaster UI and load this host first.",
                self.host_id,
            )
            return {
                "status": "host_not_loaded",
                "host_id": self.host_id,
                **stats,
            }
        stats["queries"] = len(queries)

        for q in queries:
            query_id = q.get("query_id", "")
            query_text = q.get("query_text", "")
            if not query_text:
                continue

            # Upsert search_query and grab the id back in one round-trip
            # via RETURNING. The previous version did INSERT ... ON
            # CONFLICT then a separate SELECT to find the id, which
            # doubled the queries-per-popular-query count.
            stmt = (
                pg_insert(SearchQuery)
                .values(
                    site_id=site_id,
                    query_text=query_text,
                    yandex_query_id=str(query_id),
                    last_seen_at=today,
                )
                .on_conflict_do_update(
                    index_elements=["site_id", "query_text"],
                    set_={"yandex_query_id": str(query_id), "last_seen_at": today},
                )
                .returning(SearchQuery.id)
            )
            sq_id = (await db.execute(stmt)).scalar_one_or_none()
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
                # Daily breakdown — dates may be full ISO timestamps, extract date part
                shows_map = {d["date"][:10]: d["value"] for d in shows_raw}
                clicks_map = {d["date"][:10]: d["value"] for d in (clicks_raw if isinstance(clicks_raw, list) else [])}
                pos_map = {d["date"][:10]: d["value"] for d in (pos_raw if isinstance(pos_raw, list) else [])}

                for date_str in shows_map:
                    impressions = int(shows_map.get(date_str, 0))
                    clicks = int(clicks_map.get(date_str, 0))
                    position = pos_map.get(date_str)
                    ctr = (clicks / impressions) if impressions > 0 else 0

                    metric_date = date.fromisoformat(date_str[:10])
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

            # Build per-date index data (API dates are full ISO timestamps, extract date part)
            dates_seen: set[str] = set()
            for series in [http_2xx, http_4xx, http_5xx]:
                for point in series:
                    dates_seen.add(point["date"][:10])

            idx_2xx = {d["date"][:10]: d["value"] for d in http_2xx}
            idx_4xx = {d["date"][:10]: d["value"] for d in http_4xx}
            idx_5xx = {d["date"][:10]: d["value"] for d in http_5xx}

            for date_str in dates_seen:
                metric_date = date.fromisoformat(date_str[:10])
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
                    index_elements=["site_id", "date", "metric_type"],
                    index_where=DailyMetric.dimension_id.is_(None),
                    set_={
                        "pages_indexed": pages_ok,
                        "extra": {"http_4xx": pages_4xx, "http_5xx": pages_5xx},
                    },
                )
                await db.execute(stmt)
                stats["indexing"] += 1
        except Exception as exc:
            logger.warning("Indexing history fetch failed: %s", exc)
            stats["errors"].append({"step": "indexing", "error": str(exc)[:200]})

        # 3. Search events (appeared/removed)
        logger.info("Fetching search events")
        try:
            events = await self.fetch_search_events(start_date, end_date)
            appeared = events.get("APPEARED_IN_SEARCH", [])
            removed = events.get("REMOVED_FROM_SEARCH", [])

            # API dates are full ISO timestamps — extract date part and deduplicate
            app_by_date = {d["date"][:10]: d["value"] for d in appeared}
            rem_by_date = {d["date"][:10]: d["value"] for d in removed}
            all_dates = set(app_by_date) | set(rem_by_date)

            for date_str in all_dates:
                metric_date = date.fromisoformat(date_str)
                app_count = app_by_date.get(date_str, 0)
                rem_count = rem_by_date.get(date_str, 0)

                stmt = pg_insert(DailyMetric).values(
                    site_id=site_id,
                    date=metric_date,
                    metric_type="search_events",
                    dimension_id=None,
                    pages_in_search=app_count,
                    extra={"removed_from_search": rem_count},
                ).on_conflict_do_update(
                    index_elements=["site_id", "date", "metric_type"],
                    index_where=DailyMetric.dimension_id.is_(None),
                    set_={
                        "pages_in_search": app_count,
                        "extra": {"removed_from_search": rem_count},
                    },
                )
                await db.execute(stmt)
        except Exception as exc:
            logger.warning("Search events fetch failed: %s", exc)
            stats["errors"].append({"step": "search_events", "error": str(exc)[:200]})

        await db.commit()
        logger.info("Webmaster collection done: %s", stats)
        return stats
