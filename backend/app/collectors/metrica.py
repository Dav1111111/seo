"""
Yandex Metrica API collector.
https://yandex.ru/dev/metrika/doc/api2/api_v1/intro.html
"""

import logging
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlparse, unquote
import uuid
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import BaseCollector
from app.models.daily_metric import DailyMetric
from app.models.page import Page

logger = logging.getLogger(__name__)


SITE_TRAFFIC = "site_traffic"
LANDING_PAGE_TRAFFIC = "landing_page_traffic"
TRAFFIC_SOURCE = "traffic_source"
GOAL_CONVERSION = "goal_conversion"


def _metric_value(metrics: list[Any], index: int, default: float = 0.0) -> float:
    try:
        value = metrics[index]
    except (IndexError, TypeError):
        return default
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _bounce_decimal(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value / 100, 4)


def _normalise_host(host: str | None) -> str:
    host = (host or "").split(":", 1)[0].strip().lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return host


def _normalise_path(path: str | None) -> str:
    path = unquote(path or "/").strip() or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def _url_keys(raw_url: str | None, site_domain: str | None = None) -> tuple[str, str]:
    """Return stable (host+path, path) keys, ignoring query/fragment.

    Metrica can return Unicode domains while our crawl rows may be punycode;
    comparing through IDNA keeps both versions mapped to the same page.
    """
    raw = str(raw_url or "").strip()
    if not raw:
        return "", ""

    if raw.startswith("//"):
        raw = f"https:{raw}"
    elif raw.startswith("/"):
        raw = f"https://{site_domain or ''}{raw}"
    elif "://" not in raw:
        raw = f"https://{site_domain or ''}/{raw.lstrip('/')}"

    parsed = urlparse(raw)
    host = _normalise_host(parsed.netloc or site_domain)
    path = _normalise_path(parsed.path)
    return (f"{host}{path}" if host else path, path)


class MetricaCollector(BaseCollector):
    base_url = "https://api-metrika.yandex.net"

    def __init__(self, oauth_token: str, counter_id: str):
        super().__init__(oauth_token)
        self.counter_id = counter_id

    async def fetch_counter_info(self) -> dict:
        """Counter metadata: active/deleted state, traffic status, mirrors."""
        data = await self.get(f"/management/v1/counter/{self.counter_id}")
        counter = data.get("counter")
        return counter if isinstance(counter, dict) else {}

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
                "accuracy": "full",
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
                "accuracy": "full",
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
                "sort": "-ym:s:visits",
                "limit": 50,
                "accuracy": "full",
                "lang": "ru",
            },
        )
        return data.get("data", [])

    async def fetch_goals(self) -> list[dict]:
        """Configured goals for this counter."""
        data = await self.get(f"/management/v1/counter/{self.counter_id}/goals")
        goals = data.get("goals")
        return goals if isinstance(goals, list) else []

    async def fetch_goal_totals(
        self,
        goal_id: int | str,
        date_from: date,
        date_to: date,
    ) -> dict[str, float]:
        """Goal reaches/conversion for a single goal over the collection window."""
        data = await self.get(
            "/stat/v1/data",
            params={
                "id": self.counter_id,
                "metrics": (
                    f"ym:s:goal{goal_id}reaches,"
                    f"ym:s:goal{goal_id}conversionRate,"
                    f"ym:s:goal{goal_id}visits"
                ),
                "date1": date_from.isoformat(),
                "date2": date_to.isoformat(),
                "accuracy": "full",
            },
        )
        metrics = data.get("totals")
        if not isinstance(metrics, list):
            rows = data.get("data") or []
            metrics = rows[0].get("metrics", []) if rows else []
        return {
            "reaches": _metric_value(metrics, 0),
            "conversion_rate": _metric_value(metrics, 1),
            "target_visits": _metric_value(metrics, 2),
        }

    async def _page_lookup(
        self,
        db: AsyncSession,
        site_id: UUID,
        site_domain: str | None,
    ) -> tuple[dict[str, UUID], dict[str, UUID]]:
        rows = (await db.execute(
            select(Page.id, Page.url, Page.path).where(Page.site_id == site_id)
        )).all()

        by_url: dict[str, UUID] = {}
        by_path: dict[str, UUID] = {}
        for page_id, url, path in rows:
            host_path, path_key = _url_keys(url, site_domain)
            if host_path:
                by_url[host_path] = page_id
            if path_key:
                by_path[path_key] = page_id
            if path:
                _, crawl_path = _url_keys(path, site_domain)
                if crawl_path:
                    by_path[crawl_path] = page_id
        return by_url, by_path

    async def collect_and_store(
        self,
        db: AsyncSession,
        site_id: UUID,
        days_back: int = 7,
        site_domain: str | None = None,
    ) -> dict:
        """Main entry: fetch Metrica data and persist."""
        if not self.counter_id:
            logger.warning("Metrica counter_id not set — skipping")
            return {"status": "skipped", "reason": "no counter_id"}

        today = date.today()
        end_date = today - timedelta(days=1)  # yesterday (Metrica available same day)
        start_date = end_date - timedelta(days=days_back - 1)

        stats = {
            "traffic_days": 0,
            "landing_pages": 0,
            "traffic_sources": 0,
            "goals": 0,
            "window_start": start_date.isoformat(),
            "window_end": end_date.isoformat(),
            "counter": {},
            "errors": [],
        }

        counter_extra: dict[str, Any] = {}
        try:
            counter = await self.fetch_counter_info()
            counter_extra = {
                "counter_id": self.counter_id,
                "counter_status": counter.get("status"),
                "counter_activity_status": counter.get("activity_status"),
                "counter_code_status": counter.get("code_status"),
                "counter_site": counter.get("site") or counter.get("site2"),
            }
            stats["counter"] = {k: v for k, v in counter_extra.items() if v is not None}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Metrica counter info fetch failed: %s", exc)
            stats["errors"].append({"step": "counter_info", "error": str(exc)[:200]})

        # 1. Site-level daily traffic
        logger.info("Fetching Metrica traffic %s → %s", start_date, end_date)
        try:
            raw_data = await self.get(
                "/stat/v1/data/bytime",
                params={
                    "id": self.counter_id,
                    "metrics": "ym:s:visits,ym:s:pageviews,ym:s:bounceRate,ym:s:avgVisitDurationSeconds",
                    "date1": start_date.isoformat(),
                    "date2": end_date.isoformat(),
                    "group": "day",
                    "accuracy": "full",
                },
            )

            # bytime response structure:
            #   time_intervals: [["2026-04-12","2026-04-12"], ...]  ← top-level
            #   data: [{ dimensions: [], metrics: [[visits...], [pv...], [bounce...], [dur...]] }]
            time_intervals = raw_data.get("time_intervals", [])
            data_rows = raw_data.get("data", [])

            if data_rows and time_intervals:
                metrics_list = data_rows[0].get("metrics", [])
                # metrics_list: [[visits_day1, visits_day2, ...], [pv_day1, pv_day2, ...], ...]
                if len(metrics_list) >= 4:
                    visits_arr = metrics_list[0]
                    pv_arr = metrics_list[1]
                    bounce_arr = metrics_list[2]
                    dur_arr = metrics_list[3]

                    for i, interval in enumerate(time_intervals):
                        # interval is ["2026-04-12", "2026-04-12"]
                        if isinstance(interval, list) and len(interval) >= 1:
                            date_str = interval[0]
                        else:
                            continue

                        metric_date = date.fromisoformat(date_str[:10])
                        visits = int(visits_arr[i]) if i < len(visits_arr) else 0
                        pvs = int(pv_arr[i]) if i < len(pv_arr) else 0
                        bounce = round(bounce_arr[i] / 100, 4) if i < len(bounce_arr) and bounce_arr[i] else None
                        dur = round(dur_arr[i], 2) if i < len(dur_arr) and dur_arr[i] else None
                        extra = {
                            "window_start": start_date.isoformat(),
                            "window_end": end_date.isoformat(),
                            **counter_extra,
                        }

                        stmt = pg_insert(DailyMetric).values(
                            site_id=site_id,
                            date=metric_date,
                            metric_type=SITE_TRAFFIC,
                            dimension_id=None,
                            visits=visits,
                            pageviews=pvs,
                            bounce_rate=bounce,
                            avg_duration=dur,
                            extra=extra,
                        ).on_conflict_do_update(
                            index_elements=["site_id", "date", "metric_type"],
                            index_where=DailyMetric.dimension_id.is_(None),
                            set_={
                                "visits": visits,
                                "pageviews": pvs,
                                "bounce_rate": bounce,
                                "avg_duration": dur,
                                "extra": extra,
                            },
                        )
                        await db.execute(stmt)
                        stats["traffic_days"] += 1
                else:
                    # Defensive: Metrica is supposed to always return 4
                    # metrics in the order we asked. If shape changes
                    # (API revision, partial outage) we must not
                    # silently report «0 traffic days».
                    logger.warning(
                        "metrica.site_traffic: unexpected metrics shape, "
                        "got %d metric arrays, expected >=4",
                        len(metrics_list),
                    )
                    stats["errors"].append({
                        "step": "site_traffic",
                        "error": (
                            f"unexpected API shape: metrics list has "
                            f"{len(metrics_list)} entries, expected >=4"
                        ),
                    })
            else:
                # Same reasoning: an empty `data`/`time_intervals`
                # means «API returned nothing» — sometimes that's
                # «counter has no data this week», but it can also be
                # a transient error or a misconfigured request. Log
                # so the owner / admin can investigate; the surfaced
                # error keeps the bytime path from being a silent
                # zero.
                logger.warning(
                    "metrica.site_traffic: unexpected API shape, "
                    "data_rows=%s, time_intervals=%s",
                    bool(data_rows), bool(time_intervals),
                )
                stats["errors"].append({
                    "step": "site_traffic",
                    "error": "unexpected API shape: missing data_rows or time_intervals",
                })
        except Exception as exc:
            logger.error("Metrica traffic fetch failed: %s", exc)
            stats["errors"].append({"step": "site_traffic", "error": str(exc)[:200]})

        # 2. Landing pages mapped back to our Page rows where possible.
        logger.info("Fetching Metrica landing pages %s → %s", start_date, end_date)
        try:
            by_url, by_path = await self._page_lookup(db, site_id, site_domain)
            landing_rows = await self.fetch_landing_pages(start_date, end_date, limit=200)
            for row in landing_rows:
                dimensions = row.get("dimensions") or []
                landing_url = dimensions[0].get("name") if dimensions else ""
                metrics = row.get("metrics") or []
                visits = int(_metric_value(metrics, 0))
                pageviews = int(_metric_value(metrics, 1))
                bounce = _bounce_decimal(_metric_value(metrics, 2)) if len(metrics) > 2 else None
                duration = round(_metric_value(metrics, 3), 2) if len(metrics) > 3 else None
                host_path, path_key = _url_keys(landing_url, site_domain)
                page_id = by_url.get(host_path) or by_path.get(path_key)
                dimension_id = page_id or uuid.uuid5(site_id, f"metrica:landing:{host_path or landing_url}")
                extra = {
                    "landing_url": landing_url,
                    "normalized_url": host_path,
                    "path": path_key,
                    "mapped_page_id": str(page_id) if page_id else None,
                    "window_start": start_date.isoformat(),
                    "window_end": end_date.isoformat(),
                    **counter_extra,
                }
                stmt = pg_insert(DailyMetric).values(
                    site_id=site_id,
                    date=end_date,
                    metric_type=LANDING_PAGE_TRAFFIC,
                    dimension_id=dimension_id,
                    visits=visits,
                    pageviews=pageviews,
                    bounce_rate=bounce,
                    avg_duration=duration,
                    extra=extra,
                ).on_conflict_do_update(
                    index_elements=["site_id", "date", "metric_type", "dimension_id"],
                    set_={
                        "visits": visits,
                        "pageviews": pageviews,
                        "bounce_rate": bounce,
                        "avg_duration": duration,
                        "extra": extra,
                    },
                )
                await db.execute(stmt)
                stats["landing_pages"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Metrica landing pages fetch failed: %s", exc)
            stats["errors"].append({"step": "landing_pages", "error": str(exc)[:200]})

        # 3. Traffic sources, used for SEO-vs-direct/social interpretation.
        logger.info("Fetching Metrica traffic sources %s → %s", start_date, end_date)
        try:
            source_rows = await self.fetch_traffic_sources(start_date, end_date)
            for row in source_rows:
                dimensions = row.get("dimensions") or []
                dim = dimensions[0] if dimensions else {}
                source_id = str(dim.get("id") or dim.get("name") or "unknown")
                source_name = str(dim.get("name") or source_id)
                metrics = row.get("metrics") or []
                visits = int(_metric_value(metrics, 0))
                pageviews = int(_metric_value(metrics, 1))
                dimension_id = uuid.uuid5(site_id, f"metrica:source:{source_id}")
                extra = {
                    "source_id": source_id,
                    "source": source_name,
                    "window_start": start_date.isoformat(),
                    "window_end": end_date.isoformat(),
                    **counter_extra,
                }
                stmt = pg_insert(DailyMetric).values(
                    site_id=site_id,
                    date=end_date,
                    metric_type=TRAFFIC_SOURCE,
                    dimension_id=dimension_id,
                    visits=visits,
                    pageviews=pageviews,
                    extra=extra,
                ).on_conflict_do_update(
                    index_elements=["site_id", "date", "metric_type", "dimension_id"],
                    set_={
                        "visits": visits,
                        "pageviews": pageviews,
                        "extra": extra,
                    },
                )
                await db.execute(stmt)
                stats["traffic_sources"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Metrica traffic sources fetch failed: %s", exc)
            stats["errors"].append({"step": "traffic_sources", "error": str(exc)[:200]})

        # 4. Configured goals + factual conversions. Fail-soft per goal:
        # broken/unsupported goal metrics must not erase traffic data.
        logger.info("Fetching Metrica goals %s → %s", start_date, end_date)
        try:
            goals = await self.fetch_goals()
            for goal in goals:
                goal_id = goal.get("id")
                if goal_id is None:
                    continue
                try:
                    totals = await self.fetch_goal_totals(goal_id, start_date, end_date)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Metrica goal %s totals fetch failed: %s", goal_id, exc)
                    stats["errors"].append({
                        "step": "goal_conversion",
                        "goal_id": goal_id,
                        "error": str(exc)[:200],
                    })
                    continue

                reaches = int(totals["reaches"])
                target_visits = int(totals["target_visits"])
                conversion_rate = round(float(totals["conversion_rate"]), 4)
                dimension_id = uuid.uuid5(site_id, f"metrica:goal:{goal_id}")
                extra = {
                    "goal_id": str(goal_id),
                    "name": goal.get("name"),
                    "type": goal.get("type"),
                    "goal_source": goal.get("goal_source"),
                    "is_favorite": goal.get("is_favorite"),
                    "reaches": reaches,
                    "target_visits": target_visits,
                    "conversion_rate": conversion_rate,
                    "window_start": start_date.isoformat(),
                    "window_end": end_date.isoformat(),
                    **counter_extra,
                }
                stmt = pg_insert(DailyMetric).values(
                    site_id=site_id,
                    date=end_date,
                    metric_type=GOAL_CONVERSION,
                    dimension_id=dimension_id,
                    visits=target_visits,
                    extra=extra,
                ).on_conflict_do_update(
                    index_elements=["site_id", "date", "metric_type", "dimension_id"],
                    set_={
                        "visits": target_visits,
                        "extra": extra,
                    },
                )
                await db.execute(stmt)
                stats["goals"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Metrica goals fetch failed: %s", exc)
            stats["errors"].append({"step": "goals", "error": str(exc)[:200]})

        await db.commit()
        logger.info("Metrica collection done: %s", stats)
        return stats
