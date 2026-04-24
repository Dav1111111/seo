"""Connector registry — one declarative entry per external integration.

Each connector has a `check()` that actually hits the real endpoint and
reports `{ok, latency_ms, sample_data, error}`. The entire value of
this module is that nothing here is fake: if the check returns
`ok=True`, we really got a usable response back — not "the env var is
set" or "the code path compiled".

Why a registry and not ad-hoc checks per page
---------------------------------------------
The owner needs ONE place to see "platform healthy vs degraded". A
registry lets the UI group by category, run on-demand tests, and a
future scheduler can iterate the whole list on a cron without caring
about the check's implementation.

Keep checks fast (< 3 s) and idempotent (no side effects). Anything
longer goes into a Celery task and the UI polls.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from app.config import settings


log = logging.getLogger(__name__)


@dataclasses.dataclass
class CheckResult:
    """Outcome of a single connector probe.

    `sample_data` holds a SMALL fragment (one row, one field) proving
    we actually got data back — not the full payload. The UI surfaces
    this so the owner can see the proof with their own eyes, not just
    a green dot.
    """

    ok: bool
    latency_ms: int
    sample_data: dict | None = None
    error: str | None = None
    checked_at: datetime = dataclasses.field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "sample_data": self.sample_data,
            "error": self.error,
            "checked_at": self.checked_at.isoformat(),
        }


@dataclasses.dataclass
class Connector:
    """One external integration we depend on."""

    id: str                          # "yandex_cloud.wordstat.dynamics"
    category: str                    # "llm" | "yandex_cloud" | "yandex_oauth" | "infra" | "protocol"
    name: str                        # "Wordstat · Dynamics" — human label
    description_ru: str              # one-line purpose
    # check is a no-arg callable returning CheckResult. We don't pass
    # settings or db here — each check closes over whatever it needs
    # at module scope. Keeps the registry declarative.
    check: Callable[[], CheckResult]
    # If any of these env/settings values are empty, we short-circuit
    # the check and mark the connector as "not configured" rather than
    # "failed" — different UX.
    requires: tuple[str, ...] = ()


# ── Helper: time a block ────────────────────────────────────────────────

def _timed(fn: Callable[[], tuple[bool, dict | None, str | None]]) -> CheckResult:
    """Run a check body and wrap the boolean/sample/error triple into a
    CheckResult with latency."""
    start = time.monotonic()
    try:
        ok, sample, err = fn()
    except Exception as exc:  # noqa: BLE001
        log.warning("connector_check.exception err=%s", exc)
        return CheckResult(
            ok=False,
            latency_ms=int((time.monotonic() - start) * 1000),
            error=f"exception: {type(exc).__name__}: {str(exc)[:200]}",
        )
    return CheckResult(
        ok=ok,
        latency_ms=int((time.monotonic() - start) * 1000),
        sample_data=sample,
        error=err,
    )


def _requires_missing(keys: tuple[str, ...]) -> str | None:
    """Return the first missing setting name, or None if all set."""
    for k in keys:
        if not getattr(settings, k, None):
            return k
    return None


# ── Infrastructure ─────────────────────────────────────────────────────

def _check_postgres() -> CheckResult:
    def body():
        import psycopg  # psycopg3 — already a dep for Alembic
        # SQLAlchemy uses dialect prefixes like "postgresql+psycopg://"
        # and "postgresql+asyncpg://". psycopg.connect() wants the raw
        # libpq form, so strip any dialect suffix.
        raw = settings.DATABASE_URL_SYNC or settings.DATABASE_URL
        url = raw.replace("+asyncpg", "").replace("+psycopg", "")
        conn = psycopg.connect(url, connect_timeout=3)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT current_database(), "
                "(SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='public')",
            )
            row = cur.fetchone()
            db_name = row[0] if row else None
            table_count = int(row[1]) if row else 0
            return True, {"database": db_name, "tables": table_count}, None
        finally:
            conn.close()

    return _timed(body)


def _check_redis() -> CheckResult:
    def body():
        import redis
        client = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2)
        client.ping()
        depth = int(client.llen("celery") or 0)
        return True, {"celery_queue_depth": depth}, None

    return _timed(body)


def _check_celery_workers() -> CheckResult:
    def body():
        from app.workers.celery_app import celery_app
        # 1.5s timeout because broadcast to workers can be slow during
        # startup but 1.5s is plenty once steady-state.
        inspect = celery_app.control.inspect(timeout=1.5)
        active = inspect.ping() or {}
        if not active:
            return False, {"workers": 0}, "no_workers_responded"
        return True, {"workers": len(active), "names": list(active.keys())[:5]}, None

    return _timed(body)


# ── Anthropic LLM ──────────────────────────────────────────────────────

def _check_anthropic(model_name: str) -> CheckResult:
    def body():
        miss = _requires_missing(("ANTHROPIC_API_KEY",))
        if miss:
            return False, None, f"missing:{miss}"
        from anthropic import Anthropic
        client_kwargs: dict[str, Any] = {"api_key": settings.ANTHROPIC_API_KEY}
        if settings.ANTHROPIC_BASE_URL:
            client_kwargs["base_url"] = settings.ANTHROPIC_BASE_URL
        client = Anthropic(**client_kwargs)
        # Smallest possible real call: 1 token out, trivial prompt.
        # This proves auth + network path + proxy (if any) + model
        # all work end-to-end. Cost: fractions of a cent.
        resp = client.messages.create(
            model=model_name,
            max_tokens=4,
            messages=[{"role": "user", "content": "Say 'ok'"}],
            timeout=8.0,
        )
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        ).strip()
        return True, {
            "model": resp.model,
            "output": text[:50],
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        }, None

    return _timed(body)


def _check_anthropic_sonnet() -> CheckResult:
    return _check_anthropic(settings.AI_COMPLEX_MODEL)


def _check_anthropic_haiku() -> CheckResult:
    return _check_anthropic(settings.AI_DAILY_MODEL)


# ── Yandex Cloud (AI Studio) ───────────────────────────────────────────

def _yc_post(path: str, body: dict, *, timeout: float = 8.0) -> tuple[int, dict | None, str | None]:
    """POST to searchapi.api.cloud.yandex.net with our Api-Key.

    Returns (http_code, parsed_json, err_str). Kept generic so each
    wordstat endpoint check reuses the transport without duplication.
    """
    key = settings.YANDEX_SEARCH_API_KEY
    if not key:
        return 0, None, "missing:YANDEX_SEARCH_API_KEY"
    url = f"https://searchapi.api.cloud.yandex.net{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Api-Key {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        return exc.code, None, f"http_{exc.code}: {err_body}"
    except urllib.error.URLError as exc:
        return 0, None, f"network: {exc.reason}"


def _check_yc_search_api() -> CheckResult:
    def body():
        folder = settings.YANDEX_CLOUD_FOLDER_ID
        code, data, err = _yc_post(
            "/v2/web/searchAsync",
            {
                "query": {"searchType": "SEARCH_TYPE_RU", "queryText": "site:yandex.ru"},
                "groupSpec": {"groupMode": "GROUP_MODE_FLAT", "groupsOnPage": 1, "docsInGroup": 1},
                "folderId": folder,
            },
        )
        if err:
            return False, None, err
        return True, {"operation_id": (data or {}).get("id"), "http": code}, None

    return _timed(body)


def _check_yc_wordstat_dynamics() -> CheckResult:
    """The only stable wordstat endpoint as of 2026-04-24.

    `/regions` returns 400 with opaque enum errors on the `region`
    field — the valid values are not documented and vary by time
    (tested REGION_RUSSIA / COUNTRY / ALL_REGIONS etc — all rejected
    today, some accepted yesterday). `/queries` 404s on the same
    base path for the same body.

    If you need regional distribution: use this dynamics endpoint
    with a phrase and extract monthly counts. Aggregate by region on
    our side from per-region calls instead of trusting the broken
    `/regions` summariser.
    """
    def body():
        folder = settings.YANDEX_CLOUD_FOLDER_ID
        # 12 months back — enough history to prove seasonality works,
        # short enough that the request stays fast.
        from_date = (
            datetime.now(timezone.utc).replace(day=1) - timedelta(days=365)
        ).replace(day=1).strftime("%Y-%m-%dT%H:%M:%SZ")
        code, data, err = _yc_post(
            "/v2/wordstat/dynamics",
            {
                "phrase": "купить квартиру",   # reliably has high count
                "folderId": folder,
                "region": "REGION_RUSSIA",
                "devices": ["DEVICE_ALL"],
                "period": "PERIOD_MONTHLY",
                "fromDate": from_date,
            },
        )
        if err:
            return False, None, err
        # Yandex returns the monthly series under `results`, not `items`
        # — verified empirically against the live endpoint on 2026-04-25.
        items = (data or {}).get("results", []) or (data or {}).get("items", [])
        non_empty = [x for x in items if "count" in x and x.get("count")]
        if not non_empty:
            return False, {
                "months_returned": len(items),
                "hint": "empty_count_data",
            }, "wordstat_returned_empty_counts"
        latest = non_empty[-1]
        return True, {
            "phrase": "купить квартиру",
            "months_returned": len(items),
            "months_with_data": len(non_empty),
            "latest_month": latest.get("date"),
            "latest_count": int(latest.get("count", 0)),
        }, None

    return _timed(body)


# ── Yandex OAuth services (Webmaster / Metrica) ────────────────────────

def _check_yandex_webmaster() -> CheckResult:
    def body():
        miss = _requires_missing(("YANDEX_OAUTH_TOKEN",))
        if miss:
            return False, None, f"missing:{miss}"
        req = urllib.request.Request(
            "https://api.webmaster.yandex.net/v4/user",
            method="GET",
            headers={"Authorization": f"OAuth {settings.YANDEX_OAUTH_TOKEN}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return True, {"user_id": data.get("user_id")}, None
        except urllib.error.HTTPError as exc:
            return False, None, f"http_{exc.code}"
        except urllib.error.URLError as exc:
            return False, None, f"network: {exc.reason}"

    return _timed(body)


def _check_yandex_webmaster_hosts() -> CheckResult:
    def body():
        miss = _requires_missing(("YANDEX_OAUTH_TOKEN", "YANDEX_WEBMASTER_USER_ID"))
        if miss:
            return False, None, f"missing:{miss}"
        url = (
            f"https://api.webmaster.yandex.net/v4/user/"
            f"{settings.YANDEX_WEBMASTER_USER_ID}/hosts"
        )
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"Authorization": f"OAuth {settings.YANDEX_OAUTH_TOKEN}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                hosts = data.get("hosts", [])
                return True, {
                    "hosts_count": len(hosts),
                    "sample_host": hosts[0].get("host_id") if hosts else None,
                    "verification_state": hosts[0].get("verified") if hosts else None,
                }, None
        except urllib.error.HTTPError as exc:
            return False, None, f"http_{exc.code}"
        except urllib.error.URLError as exc:
            return False, None, f"network: {exc.reason}"

    return _timed(body)


def _check_yandex_metrica() -> CheckResult:
    def body():
        miss = _requires_missing(("YANDEX_OAUTH_TOKEN", "YANDEX_METRICA_COUNTER_ID"))
        if miss:
            return False, None, f"missing:{miss}"
        counter_id = settings.YANDEX_METRICA_COUNTER_ID
        url = f"https://api-metrika.yandex.net/management/v1/counter/{counter_id}"
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"Authorization": f"OAuth {settings.YANDEX_OAUTH_TOKEN}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                counter = data.get("counter", {})
                return True, {
                    "counter_id": counter.get("id"),
                    "name": counter.get("name"),
                    "site": counter.get("site"),
                    "status": counter.get("status"),
                }, None
        except urllib.error.HTTPError as exc:
            return False, None, f"http_{exc.code}"
        except urllib.error.URLError as exc:
            return False, None, f"network: {exc.reason}"

    return _timed(body)


def _check_yandex_metrica_visits() -> CheckResult:
    def body():
        miss = _requires_missing(("YANDEX_OAUTH_TOKEN", "YANDEX_METRICA_COUNTER_ID"))
        if miss:
            return False, None, f"missing:{miss}"
        counter_id = settings.YANDEX_METRICA_COUNTER_ID
        date_to = date.today()
        date_from = date_to - timedelta(days=7)
        params = urllib.parse.urlencode({
            "ids": counter_id,
            "metrics": "ym:s:visits",
            "date1": date_from.isoformat(),
            "date2": date_to.isoformat(),
        })
        url = f"https://api-metrika.yandex.net/stat/v1/data?{params}"
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"Authorization": f"OAuth {settings.YANDEX_OAUTH_TOKEN}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                totals = data.get("totals", [])
                return True, {
                    "date_range": f"{date_from}..{date_to}",
                    "visits_7d": int(totals[0][0]) if totals and totals[0] else 0,
                }, None
        except urllib.error.HTTPError as exc:
            return False, None, f"http_{exc.code}"
        except urllib.error.URLError as exc:
            return False, None, f"network: {exc.reason}"

    return _timed(body)


# ── Protocols ──────────────────────────────────────────────────────────

def _check_indexnow_endpoint() -> CheckResult:
    """Reach the IndexNow endpoint without submitting real URLs.

    Sends a deliberately malformed body so Yandex responds 400 quickly
    — proves the endpoint is reachable + accepting our region, without
    "using up" a submission for a real site. `accepted=False` here is
    the SUCCESS state: we got a protocol-level response.
    """

    def body():
        url = "https://yandex.com/indexnow"
        data = json.dumps({"ping": "healthcheck"}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "Host": "yandex.com"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return True, {"http": resp.getcode(), "note": "endpoint_reachable"}, None
        except urllib.error.HTTPError as exc:
            # 400/422 == endpoint alive and rejecting bad body — that
            # is exactly what we want to prove.
            if exc.code in (400, 422):
                return True, {"http": exc.code, "note": "endpoint_reachable_rejects_bad_body"}, None
            return False, None, f"http_{exc.code}"
        except urllib.error.URLError as exc:
            return False, None, f"network: {exc.reason}"

    return _timed(body)


# ── Registry ───────────────────────────────────────────────────────────

CONNECTORS: list[Connector] = [
    # Infrastructure
    Connector(
        id="infra.postgres",
        category="infra",
        name="PostgreSQL",
        description_ru="База данных приложения — хранит сайты, запросы, рекомендации, activity events.",
        check=_check_postgres,
        requires=("DATABASE_URL",),
    ),
    Connector(
        id="infra.redis",
        category="infra",
        name="Redis",
        description_ru="Брокер Celery и кэш — без него воркеры не получают задачи.",
        check=_check_redis,
        requires=("REDIS_URL",),
    ),
    Connector(
        id="infra.celery",
        category="infra",
        name="Celery Workers",
        description_ru="Фоновые воркеры — выполняют pipeline, crawl, LLM-задачи.",
        check=_check_celery_workers,
        requires=("REDIS_URL",),
    ),

    # LLM
    Connector(
        id="llm.anthropic.sonnet",
        category="llm",
        name="Anthropic · Sonnet",
        description_ru="Главная LLM для онбординг-чата и BusinessTruth — понимает бизнес на русском.",
        check=_check_anthropic_sonnet,
        requires=("ANTHROPIC_API_KEY",),
    ),
    Connector(
        id="llm.anthropic.haiku",
        category="llm",
        name="Anthropic · Haiku",
        description_ru="Дешёвая LLM для массовых задач — кластеризация, классификация, краткие тексты.",
        check=_check_anthropic_haiku,
        requires=("ANTHROPIC_API_KEY",),
    ),

    # Yandex Cloud (AI Studio) — single API key covers all
    Connector(
        id="yandex_cloud.search_api",
        category="yandex_cloud",
        name="Search API · Web Search",
        description_ru="Поиск Яндекса — используем для site:домен проверки индексации и для SERP-конкурентов.",
        check=_check_yc_search_api,
        requires=("YANDEX_SEARCH_API_KEY",),
    ),
    Connector(
        id="yandex_cloud.wordstat.dynamics",
        category="yandex_cloud",
        name="Wordstat · Динамика",
        description_ru="Частотность запроса по месяцам — реальные объёмы и сезонность. Единственный стабильный Wordstat endpoint; /regions и /queries пока не добавлены из-за нестабильного поведения Яндекс API.",
        check=_check_yc_wordstat_dynamics,
        requires=("YANDEX_SEARCH_API_KEY",),
    ),

    # Yandex OAuth services
    Connector(
        id="yandex_oauth.webmaster.user",
        category="yandex_oauth",
        name="Webmaster · User",
        description_ru="Проверка что OAuth-токен Вебмастера валиден.",
        check=_check_yandex_webmaster,
        requires=("YANDEX_OAUTH_TOKEN",),
    ),
    Connector(
        id="yandex_oauth.webmaster.hosts",
        category="yandex_oauth",
        name="Webmaster · Hosts",
        description_ru="Список хостов пользователя в Вебмастере — показывает, какие сайты реально привязаны к аккаунту.",
        check=_check_yandex_webmaster_hosts,
        requires=("YANDEX_OAUTH_TOKEN", "YANDEX_WEBMASTER_USER_ID"),
    ),
    Connector(
        id="yandex_oauth.metrica.counter",
        category="yandex_oauth",
        name="Metrica · Counter Info",
        description_ru="Метаданные счётчика Метрики — имя, сайт, статус.",
        check=_check_yandex_metrica,
        requires=("YANDEX_OAUTH_TOKEN", "YANDEX_METRICA_COUNTER_ID"),
    ),
    Connector(
        id="yandex_oauth.metrica.visits",
        category="yandex_oauth",
        name="Metrica · Visits (7d)",
        description_ru="Реальные визиты из Метрики за последние 7 дней — доказательство что счётчик собирает данные.",
        check=_check_yandex_metrica_visits,
        requires=("YANDEX_OAUTH_TOKEN", "YANDEX_METRICA_COUNTER_ID"),
    ),

    # Protocols
    Connector(
        id="protocol.indexnow",
        category="protocol",
        name="IndexNow Endpoint",
        description_ru="Канал, через который мы просим Яндекс переобойти URL — работает без Вебмастера.",
        check=_check_indexnow_endpoint,
    ),
]


CONNECTORS_BY_ID: dict[str, Connector] = {c.id: c for c in CONNECTORS}


def describe_connector(c: Connector) -> dict:
    """Metadata only — for listing endpoint, without running the check."""
    missing = _requires_missing(c.requires) if c.requires else None
    return {
        "id": c.id,
        "category": c.category,
        "name": c.name,
        "description_ru": c.description_ru,
        "configured": missing is None,
        "missing_setting": missing,
    }


__all__ = [
    "CheckResult",
    "Connector",
    "CONNECTORS",
    "CONNECTORS_BY_ID",
    "describe_connector",
]
