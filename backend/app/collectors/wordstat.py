"""Yandex Cloud Wordstat client — monthly volume + 12-month trend.

Why this exists
---------------
`SearchQuery.wordstat_volume` and `wordstat_trend` columns have lived
empty since they were added — we never had a working Wordstat path.
With the AI Studio Search API we now do (verified 2026-04-25, see
docs/studio/CONCEPT.md §3 and connectors registry).

This module is a tiny, dependency-free fetcher that the Studio
`/queries` module + Celery refresh task use to populate those two
columns + `wordstat_updated_at`.

Endpoint
--------
    POST https://searchapi.api.cloud.yandex.net/v2/wordstat/dynamics

Body shape (verified):
    { phrase, folderId, region, devices, period, fromDate (RFC3339) }

Response key is `results`, NOT `items` — historical landmine, see
commit 1cfded7 where we made the connector tolerant of either key.
We aggregate the monthly counts into a single 12-month volume and
expose the per-month trend as a JSON-friendly list.

Design
------
- stdlib urllib (matches yandex_serp.py style — no httpx dep dance).
- Synchronous; called from Celery worker threads via anyio executor.
- Fail-soft: every error path returns None instead of raising. The
  Celery task wraps each per-query call in try/except anyway, but a
  single-quote regression here shouldn't crash the entire batch.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Sequence

from app.config import settings


log = logging.getLogger(__name__)


WORDSTAT_DYNAMICS_ENDPOINT = (
    "https://searchapi.api.cloud.yandex.net/v2/wordstat/dynamics"
)
DEFAULT_REGION = "REGION_RUSSIA"
DEFAULT_DEVICES = ("DEVICE_ALL",)
TREND_MONTHS = 12

REQUEST_TIMEOUT_SEC = 12.0


@dataclasses.dataclass(frozen=True)
class WordstatVolume:
    """Result of a successful Wordstat dynamics fetch.

    `count` is the total search volume across the trend window
    (sum of all monthly counts that came back). `trend` is the per-
    month series, oldest → newest, suitable for plotting and for
    serialising into the `wordstat_trend` JSONB column.
    `from_date` is the timestamp of the latest month for which we
    have data — used as the freshness marker.
    """

    phrase: str
    count: int
    from_date: str  # RFC3339 of latest data month
    trend: list[dict]  # [{date: "2025-04-01T...", count: 12345}, ...]
    fetched_at: datetime

    def to_dict(self) -> dict:
        return {
            "phrase": self.phrase,
            "count": self.count,
            "from_date": self.from_date,
            "trend": list(self.trend),
            "fetched_at": self.fetched_at.isoformat(),
        }


def _twelve_months_ago_iso() -> str:
    """First day of the month, 12 months before today, RFC3339 UTC."""
    base = datetime.now(timezone.utc).replace(day=1)
    target = (base - timedelta(days=365)).replace(day=1)
    return target.strftime("%Y-%m-%dT%H:%M:%SZ")


def _post(body: dict, api_key: str, timeout: float) -> tuple[int, dict | None, str | None]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        WORDSTAT_DYNAMICS_ENDPOINT,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Api-Key {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (
                resp.getcode(),
                json.loads(resp.read().decode("utf-8")),
                None,
            )
    except urllib.error.HTTPError as exc:
        body_preview = ""
        try:
            body_preview = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        return exc.code, None, f"http_{exc.code}: {body_preview}"
    except urllib.error.URLError as exc:
        return 0, None, f"network: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        log.warning("wordstat.unexpected_error err=%s", exc)
        return 0, None, f"exception: {type(exc).__name__}"


def fetch_volume(
    phrase: str,
    *,
    region: str = DEFAULT_REGION,
    devices: Sequence[str] = DEFAULT_DEVICES,
    timeout: float = REQUEST_TIMEOUT_SEC,
    api_key: str | None = None,
    folder_id: str | None = None,
) -> WordstatVolume | None:
    """Pull 12-month dynamics for `phrase`. None on any failure.

    None semantics:
      - empty/blank phrase → caller bug, returns None silently
      - missing API key / folder → can't even try, returns None
      - HTTP 4xx/5xx, network error, malformed JSON → returns None
        (call sites that need to report the cause should look at logs)
      - 200 but `results` empty / no monthly counts → returns None
        (Wordstat sometimes returns empty for very rare phrases —
         that's data-absence, not an error, but for our purposes
         "no volume to record" is the same outcome)
    """
    cleaned = (phrase or "").strip()
    if not cleaned:
        return None

    key = api_key or settings.YANDEX_SEARCH_API_KEY
    folder = folder_id or settings.YANDEX_CLOUD_FOLDER_ID
    if not key or not folder:
        return None

    body = {
        "phrase": cleaned,
        "folderId": folder,
        "region": region,
        "devices": list(devices) or list(DEFAULT_DEVICES),
        "period": "PERIOD_MONTHLY",
        "fromDate": _twelve_months_ago_iso(),
    }

    code, data, err = _post(body, key, timeout)
    if err:
        log.info("wordstat.fetch_failed phrase=%r code=%s err=%s", cleaned, code, err)
        return None

    # Yandex returns the series under `results`. Be tolerant of both
    # keys in case it ever harmonises with /regions and /queries that
    # use `items` — pattern lifted from connectors._check_yc_wordstat_dynamics.
    rows = (data or {}).get("results") or (data or {}).get("items") or []

    trend: list[dict] = []
    total = 0
    latest_date: str | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = row.get("date")
        raw_count = row.get("count")
        if raw_count is None:
            # Months with no data come back as `{date: "..."}` only.
            # Skip from total but keep in the trend so the UI can
            # render gaps honestly.
            if isinstance(date, str):
                trend.append({"date": date, "count": None})
            continue
        try:
            n = int(raw_count)
        except (TypeError, ValueError):
            continue
        if n < 0:
            continue
        total += n
        if isinstance(date, str):
            trend.append({"date": date, "count": n})
            # Latest = max date. We append in API order; the API
            # returns oldest → newest in practice, but don't assume.
            if latest_date is None or date > latest_date:
                latest_date = date

    if not trend or total == 0 or latest_date is None:
        # Nothing useful came back — treat as no-data
        log.info(
            "wordstat.empty_result phrase=%r rows=%d total=%d",
            cleaned,
            len(rows),
            total,
        )
        return None

    return WordstatVolume(
        phrase=cleaned,
        count=total,
        from_date=latest_date,
        trend=trend,
        fetched_at=datetime.now(timezone.utc),
    )


__all__ = [
    "WordstatVolume",
    "fetch_volume",
    "WORDSTAT_DYNAMICS_ENDPOINT",
    "TREND_MONTHS",
]
