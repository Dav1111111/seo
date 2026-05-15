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

Tri-state semantics (2026-05-15)
--------------------------------
The previous contract collapsed three very different outcomes into
`None`:

  - API returned 200 with no rows for a legitimately-rare phrase
    (e.g. "багги сочи гранд")
  - API returned 4xx/5xx or the network blew up (transient)
  - Caller passed a URL or empty string by mistake (data-quality bug)

That collapse caused the weekly Celery beat to retry legitimately-
empty phrases forever — `wordstat_updated_at` was never written, so
the row stayed in the «never fetched» bucket and got picked up by the
next beat. Live probe on 2026-05-15: 9 of 13 queries on prod were
stuck in this loop.

The new return type `WordstatFetchOutcome` distinguishes:

  - status="ok"             — API returned data, volume > 0 (or 0 if
                              every monthly count was zero or null)
  - status="empty"          — 200 with no rows. Caller SHOULD write
                              volume=0 and stamp `wordstat_updated_at`
                              so the row exits the retry loop.
  - status="error"          — HTTP error, network failure, malformed
                              JSON. Caller SHOULD NOT touch the row
                              and will retry on the next beat.
  - status="invalid_phrase" — input is a URL, empty, or otherwise
                              unusable. Caller SHOULD stamp
                              `wordstat_updated_at` (to stop the loop)
                              and surface a data-quality warning.

Design
------
- stdlib urllib (matches yandex_serp.py style — no httpx dep dance).
- Synchronous; called from Celery worker threads via anyio executor.
- Tri-state result via `WordstatFetchOutcome`. The legacy
  `WordstatVolume` dataclass is preserved as an alias for any out-of-
  tree code that still imports it.
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
WORDSTAT_TOP_REQUESTS_ENDPOINT = (
    "https://searchapi.api.cloud.yandex.net/v2/wordstat/topRequests"
)
DEFAULT_REGION = "REGION_RUSSIA"
DEFAULT_DEVICES = ("DEVICE_ALL",)
TREND_MONTHS = 12

REQUEST_TIMEOUT_SEC = 12.0


# Tri-state status values for WordstatFetchOutcome.
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_ERROR = "error"
STATUS_INVALID_PHRASE = "invalid_phrase"


@dataclasses.dataclass(frozen=True)
class WordstatFetchOutcome:
    """Tri-state result of a single `fetch_volume` call.

    See module docstring for the four `status` values and what the
    caller should do for each.

    Fields:
      phrase       — the cleaned phrase that was queried (or attempted)
      status       — one of {"ok","empty","error","invalid_phrase"}
      volume       — total trend-window volume; 0 if status != "ok"
      trend        — per-month series for "ok"; [] otherwise
      from_date    — RFC3339 of the latest data month for "ok"; None
                     otherwise. Kept for backwards compat with old
                     `WordstatVolume.from_date`.
      latest_date  — alias of `from_date` for readability at call sites
      fetched_at   — when the fetch was attempted (always populated;
                     callers stamp `wordstat_updated_at` from this)
      error        — short human-readable error string for
                     status in {"error","invalid_phrase"}; None for ok
      http_code    — HTTP status code when we got one (None for network
                     failures and non-HTTP errors)
    """

    phrase: str
    status: str
    volume: int
    trend: list[dict]
    from_date: str | None
    latest_date: str | None
    fetched_at: datetime
    error: str | None = None
    http_code: int | None = None

    @property
    def is_actionable(self) -> bool:
        """True if the caller should write something to the DB.

        For "ok"/"empty" we have a real answer (a real volume or a
        verified zero) and should stamp `wordstat_updated_at`. For
        "error" the failure may be transient — leave the row alone so
        the next beat retries. "invalid_phrase" is a borderline case:
        the caller should also stamp the timestamp (to break the retry
        loop) but should NOT trust `volume`. Hence we return True for
        invalid as well — callers distinguish via `status`.
        """
        return self.status in (STATUS_OK, STATUS_EMPTY, STATUS_INVALID_PHRASE)

    def to_dict(self) -> dict:
        return {
            "phrase": self.phrase,
            "status": self.status,
            "volume": self.volume,
            "trend": list(self.trend),
            "from_date": self.from_date,
            "latest_date": self.latest_date,
            "fetched_at": self.fetched_at.isoformat(),
            "error": self.error,
            "http_code": self.http_code,
        }

    # ── Backwards-compat aliases ──────────────────────────────────
    # The old `WordstatVolume.count` field was the same number as
    # `volume`. Keep a property so any straggling caller that still
    # reads `.count` keeps working.
    @property
    def count(self) -> int:
        return self.volume


# Legacy alias — out-of-tree code may still `from … import WordstatVolume`.
# A successful fetch is structurally compatible: it has `count`,
# `from_date`, `trend`, `fetched_at` (the historical surface).
WordstatVolume = WordstatFetchOutcome


def _twelve_months_ago_iso() -> str:
    """First day of the month, 12 months before today, RFC3339 UTC."""
    base = datetime.now(timezone.utc).replace(day=1)
    target = (base - timedelta(days=365)).replace(day=1)
    return target.strftime("%Y-%m-%dT%H:%M:%SZ")


def _looks_like_url(phrase: str) -> bool:
    """True if the phrase is clearly a URL or domain rather than a
    search phrase. Wordstat will 400 these every time, so reject at
    boundary to keep them out of the error counter."""
    p = phrase.strip().lower()
    if not p:
        return False
    if "://" in p:
        return True
    if p.startswith(("http://", "https://", "www.")):
        return True
    return False


def _post(
    body: dict,
    api_key: str,
    timeout: float,
    *,
    endpoint: str = WORDSTAT_DYNAMICS_ENDPOINT,
) -> tuple[int, dict | None, str | None]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
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


def _outcome(
    *,
    phrase: str,
    status: str,
    volume: int = 0,
    trend: list[dict] | None = None,
    latest_date: str | None = None,
    error: str | None = None,
    http_code: int | None = None,
) -> WordstatFetchOutcome:
    return WordstatFetchOutcome(
        phrase=phrase,
        status=status,
        volume=volume,
        trend=trend or [],
        from_date=latest_date,
        latest_date=latest_date,
        fetched_at=datetime.now(timezone.utc),
        error=error,
        http_code=http_code,
    )


def fetch_volume(
    phrase: str,
    *,
    region: str = DEFAULT_REGION,
    devices: Sequence[str] = DEFAULT_DEVICES,
    timeout: float = REQUEST_TIMEOUT_SEC,
    api_key: str | None = None,
    folder_id: str | None = None,
) -> WordstatFetchOutcome:
    """Pull 12-month dynamics for `phrase`.

    Always returns a `WordstatFetchOutcome` — never `None`. Inspect
    `outcome.status` to dispatch:

      - "ok"              — outcome.volume / outcome.trend populated
      - "empty"           — API said zero demand for this phrase
      - "error"           — transient failure (HTTP/network)
      - "invalid_phrase"  — input rejected without hitting the API

    See module docstring for the full contract.
    """
    cleaned = (phrase or "").strip()
    if not cleaned:
        log.warning("wordstat.invalid_phrase reason=empty")
        return _outcome(
            phrase="",
            status=STATUS_INVALID_PHRASE,
            error="phrase is empty or whitespace",
        )

    if _looks_like_url(cleaned):
        log.warning("wordstat.invalid_phrase phrase=%r reason=url_shape", cleaned)
        return _outcome(
            phrase=cleaned,
            status=STATUS_INVALID_PHRASE,
            error="phrase looks like a URL, not a search phrase",
        )

    key = api_key or settings.YANDEX_SEARCH_API_KEY
    folder = folder_id or settings.YANDEX_CLOUD_FOLDER_ID
    if not key or not folder:
        log.warning(
            "wordstat.no_credentials phrase=%r — missing API key or folder",
            cleaned,
        )
        return _outcome(
            phrase=cleaned,
            status=STATUS_ERROR,
            error="missing YANDEX_SEARCH_API_KEY or YANDEX_CLOUD_FOLDER_ID",
        )

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
        log.warning(
            "wordstat.fetch_failed phrase=%r code=%s err=%s",
            cleaned, code, err,
        )
        return _outcome(
            phrase=cleaned,
            status=STATUS_ERROR,
            error=err,
            http_code=code if code else None,
        )

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
        # 200 with empty or all-null `results` — Wordstat is telling us
        # this phrase has no measurable demand. That's a real answer:
        # callers should record volume=0 and stamp `wordstat_updated_at`
        # so the weekly beat doesn't retry forever.
        log.warning(
            "wordstat.empty_result phrase=%r rows=%d total=%d",
            cleaned,
            len(rows),
            total,
        )
        return _outcome(
            phrase=cleaned,
            status=STATUS_EMPTY,
            volume=0,
            trend=trend,  # may still contain null-count rows for UI
            latest_date=None,
            http_code=code,
        )

    return _outcome(
        phrase=cleaned,
        status=STATUS_OK,
        volume=total,
        trend=trend,
        latest_date=latest_date,
        http_code=code,
    )


@dataclasses.dataclass(frozen=True)
class WordstatTopRequest:
    """One row from `/v2/wordstat/topRequests` — a phrase that contains
    the seed (or strongly co-occurs with it), with monthly search volume.

    `count` is the integer monthly volume (Yandex returns it as a string;
    we parse it once here so callers don't have to).
    """

    phrase: str
    count: int

    def to_dict(self) -> dict:
        return {"phrase": self.phrase, "count": self.count}


def fetch_top_requests(
    seed: str,
    *,
    region: str = DEFAULT_REGION,
    devices: Sequence[str] = DEFAULT_DEVICES,
    timeout: float = REQUEST_TIMEOUT_SEC,
    api_key: str | None = None,
    folder_id: str | None = None,
    retry_on_429: bool = True,
) -> list[WordstatTopRequest] | None:
    """Discover phrases people search around `seed` (the «что ищут со словом X»
    column from manual wordstat.yandex.ru).

    None semantics:
      - empty seed / missing creds → returns None silently
      - HTTP error / network / malformed JSON → returns None
      - 200 with empty `results` → returns [] (valid "no related phrases")
        so callers can distinguish "no data" from "API failure"

    Rate-limit handling: `/topRequests` is much harsher than `/dynamics`
    — empirical limit ≈ 1 req per 8-12 sec. On HTTP 429 we sleep 30 sec
    and retry exactly once (still cheaper than losing the whole batch
    when one slow seed bunches up against the next call).

    Note this endpoint does NOT return per-month trend — only an
    aggregate volume. To populate `wordstat_trend` for these new
    phrases the caller can run `fetch_volume` afterwards.
    """
    import time as _time

    cleaned = (seed or "").strip()
    if not cleaned:
        return None

    key = api_key or settings.YANDEX_SEARCH_API_KEY
    folder = folder_id or settings.YANDEX_CLOUD_FOLDER_ID
    if not key or not folder:
        return None

    body = {
        "folderId": folder,
        "phrase": cleaned,
        "region": region,
        "devices": list(devices) or list(DEFAULT_DEVICES),
    }

    code, data, err = _post(
        body, key, timeout, endpoint=WORDSTAT_TOP_REQUESTS_ENDPOINT,
    )
    if code == 429 and retry_on_429:
        log.warning("wordstat.top_requests_429 seed=%r — backoff 30s", cleaned)
        _time.sleep(30.0)
        code, data, err = _post(
            body, key, timeout, endpoint=WORDSTAT_TOP_REQUESTS_ENDPOINT,
        )
    if err:
        log.warning(
            "wordstat.top_requests_failed seed=%r code=%s err=%s",
            cleaned, code, err,
        )
        return None

    rows = (data or {}).get("results") or []
    out: list[WordstatTopRequest] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        phrase = (row.get("phrase") or "").strip()
        raw_count = row.get("count")
        if not phrase or raw_count is None:
            continue
        try:
            n = int(raw_count)
        except (TypeError, ValueError):
            continue
        if n < 0:
            continue
        out.append(WordstatTopRequest(phrase=phrase, count=n))
    return out


__all__ = [
    "WordstatFetchOutcome",
    "WordstatVolume",  # legacy alias of WordstatFetchOutcome
    "WordstatTopRequest",
    "fetch_volume",
    "fetch_top_requests",
    "WORDSTAT_DYNAMICS_ENDPOINT",
    "WORDSTAT_TOP_REQUESTS_ENDPOINT",
    "TREND_MONTHS",
    "STATUS_OK",
    "STATUS_EMPTY",
    "STATUS_ERROR",
    "STATUS_INVALID_PHRASE",
]
