"""
Celery tasks for data collection.
"""

import asyncio
import logging
from uuid import UUID

from sqlalchemy import select

from app.workers.celery_app import celery_app
from app.workers.db_session import task_session
from app.models.site import Site
from app.collectors.webmaster import WebmasterCollector
from app.collectors.metrica import MetricaCollector

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run async coroutine from sync Celery task.

    Uses `asyncio.run` so async generators get `aclose`-d and the
    default executor is shut down before the loop closes — the
    hand-rolled `new_event_loop`/`close` pattern leaks both.
    """
    return asyncio.run(coro)


def _format_webmaster_result(out: dict) -> tuple[str, dict, str]:
    """Turn collector stats into a human-readable activity row.

    Returns `(message, extra, terminal_status)`.
    """
    queries = int(out.get("queries", 0) or 0)
    metrics = int(out.get("metrics", 0) or 0)
    indexing = int(out.get("indexing", 0) or 0)
    window_start = out.get("window_start")
    window_end = out.get("window_end")
    extra = {
        "queries": queries,
        "metrics": metrics,
        "indexing": indexing,
        "window_start": window_start,
        "window_end": window_end,
    }

    if out.get("status") == "host_not_loaded":
        return (
            "Вебмастер: хост ещё не загружен в интерфейсе Яндекса. "
            "Открой Webmaster UI и загрузите хост вручную.",
            {**extra, "host_id": out.get("host_id")},
            "skipped",
        )

    if queries == 0 and metrics == 0 and indexing == 0:
        window = (
            f"{window_start} → {window_end}"
            if window_start and window_end
            else "текущее окно сбора"
        )
        return (
            f"Вебмастер: Яндекс не вернул новых данных за окно {window}.",
            {**extra, "empty_window": True},
            "done",
        )

    return (
        (
            f"Вебмастер: {queries} запросов, "
            f"{metrics} замеров, {indexing} индекс-событий."
        ),
        extra,
        "done",
    )


async def _collect_webmaster_for_site(site: dict) -> dict:
    """Collect Webmaster data for a single site."""
    collector = WebmasterCollector(
        oauth_token=site["yandex_oauth_token"],
        user_id=site["webmaster_user_id"],
        host_id=site["yandex_webmaster_host_id"],
    )
    try:
        async with task_session() as db:
            result = await collector.collect_and_store(db, site["id"], days_back=7)
        return result
    finally:
        await collector.close()


async def _collect_metrica_for_site(site: dict) -> dict:
    """Collect Metrica data for a single site."""
    if not site.get("yandex_metrica_counter_id"):
        return {"status": "skipped", "reason": "no counter_id"}

    collector = MetricaCollector(
        oauth_token=site["yandex_oauth_token"],
        counter_id=site["yandex_metrica_counter_id"],
    )
    try:
        async with task_session() as db:
            result = await collector.collect_and_store(db, site["id"], days_back=7)
        return result
    finally:
        await collector.close()


async def _get_active_sites() -> list[dict]:
    """Get all active sites with their credentials."""
    from app.config import settings

    async with task_session() as db:
        result = await db.execute(
            select(Site).where(Site.is_active == True)  # noqa: E712
        )
        sites = result.scalars().all()

    site_list = []
    for s in sites:
        site_list.append({
            "id": s.id,
            "domain": s.domain,
            "yandex_oauth_token": s.yandex_oauth_token or settings.YANDEX_OAUTH_TOKEN,
            "webmaster_user_id": settings.YANDEX_WEBMASTER_USER_ID,
            "yandex_webmaster_host_id": s.yandex_webmaster_host_id or settings.YANDEX_WEBMASTER_HOST_ID,
            "yandex_metrica_counter_id": s.yandex_metrica_counter_id or settings.YANDEX_METRICA_COUNTER_ID,
        })
    return site_list


@celery_app.task(name="collect_webmaster_all", bind=True, max_retries=2)
def collect_webmaster_all(self):
    """Collect Webmaster data for all active sites."""
    logger.info("Starting Webmaster collection for all sites")
    sites = _run_async(_get_active_sites())

    results = {}
    for site in sites:
        if not site.get("yandex_webmaster_host_id"):
            logger.warning("Skipping %s — no webmaster host_id", site["domain"])
            continue
        try:
            result = _run_async(_collect_webmaster_for_site(site))
            results[site["domain"]] = result
            logger.info("✓ %s: %s", site["domain"], result)
        except Exception as exc:
            logger.error("✗ %s: %s", site["domain"], exc)
            results[site["domain"]] = {"error": str(exc)}

    return results


@celery_app.task(name="collect_metrica_all", bind=True, max_retries=2)
def collect_metrica_all(self):
    """Collect Metrica data for all active sites."""
    logger.info("Starting Metrica collection for all sites")
    sites = _run_async(_get_active_sites())

    results = {}
    for site in sites:
        try:
            result = _run_async(_collect_metrica_for_site(site))
            results[site["domain"]] = result
            logger.info("✓ %s: %s", site["domain"], result)
        except Exception as exc:
            logger.error("✗ %s: %s", site["domain"], exc)
            results[site["domain"]] = {"error": str(exc)}

    return results


@celery_app.task(name="collect_site_webmaster")
def collect_site_webmaster(site_id: str, run_id: str | None = None):
    """Collect Webmaster data for a specific site (for manual trigger)."""
    from app.config import settings
    from app.core_audit.activity import emit_terminal, log_event

    async def _run():
        async with task_session() as db:
            result = await db.execute(select(Site).where(Site.id == UUID(site_id)))
            site = result.scalar_one_or_none()
            if not site:
                await emit_terminal(
                    db, site_id, "webmaster", "failed",
                    "Сайт не найден в базе.",
                    run_id=run_id,
                )
                return {
                    "status": "failed",
                    "stage": "webmaster",
                    "error": "Site not found",
                }

            await log_event(
                db, site_id, "webmaster", "started",
                "Тяну данные из Яндекс.Вебмастера…",
                run_id=run_id,
            )
            collector = WebmasterCollector(
                oauth_token=site.yandex_oauth_token or settings.YANDEX_OAUTH_TOKEN,
                user_id=settings.YANDEX_WEBMASTER_USER_ID,
                host_id=site.yandex_webmaster_host_id or settings.YANDEX_WEBMASTER_HOST_ID,
            )
            try:
                out = await collector.collect_and_store(db, site.id, days_back=7)
            except Exception as exc:  # noqa: BLE001
                await emit_terminal(
                    db, site_id, "webmaster", "failed",
                    f"Вебмастер ответил ошибкой: {str(exc)[:200]}",
                    run_id=run_id,
                )
                return {
                    "status": "failed",
                    "stage": "webmaster",
                    "error": str(exc),
                }
            finally:
                await collector.close()

            message, extra, terminal_status = _format_webmaster_result(out)
            await emit_terminal(
                db, site_id, "webmaster", terminal_status, message,
                extra=extra,
                run_id=run_id,
            )
            return out

    return _run_async(_run())


@celery_app.task(name="crawl_site", bind=True, max_retries=0)
def crawl_site(self, site_id: str, run_id: str | None = None):
    """Crawl a site — fetch sitemap + all pages, extract SEO data.

    After crawl completes, automatically chains fingerprint_site with 10s countdown.
    """
    from app.collectors.site_crawler import SiteCrawler
    from app.core_audit.activity import emit_terminal, log_event
    from app.fingerprint.tasks import fingerprint_site

    async def _run():
        async with task_session() as db:
            result = await db.execute(select(Site).where(Site.id == UUID(site_id)))
            site = result.scalar_one_or_none()
            if not site:
                await emit_terminal(
                    db, site_id, "crawl", "failed",
                    "Сайт не найден в базе.",
                    run_id=run_id,
                )
                return {
                    "status": "failed",
                    "stage": "crawl",
                    "error": "Site not found",
                }

            await log_event(
                db, site_id, "crawl", "started",
                "Обхожу sitemap и собираю HTML страниц…",
                run_id=run_id,
            )

            domain = site.domain
            base_url = f"https://{domain}" if not domain.startswith("xn--") and "." in domain else f"https://{domain}"
            crawler = SiteCrawler(domain=domain, base_url=base_url, max_pages=50)
            try:
                out = await crawler.crawl_and_store(db, site.id)
            except Exception as exc:  # noqa: BLE001
                await emit_terminal(
                    db, site_id, "crawl", "failed",
                    f"Краулинг остановлен с ошибкой: {str(exc)[:200]}",
                    run_id=run_id,
                )
                return {
                    "status": "failed",
                    "stage": "crawl",
                    "error": str(exc),
                }

            await emit_terminal(
                db, site_id, "crawl", "done",
                (
                    f"Краулинг: {out.get('pages_crawled', 0)} страниц, "
                    f"{out.get('pages_failed', 0)} ошибок, "
                    f"sitemap: {out.get('sitemap_urls', 0)} URL."
                ),
                extra={
                    "pages_crawled": out.get("pages_crawled", 0),
                    "pages_failed": out.get("pages_failed", 0),
                    "sitemap_urls": out.get("sitemap_urls", 0),
                },
                run_id=run_id,
            )
            return out

    result = _run_async(_run())

    # Chain fingerprinting after successful crawl
    if isinstance(result, dict) and result.get("status") != "failed" and "error" not in result:
        fingerprint_site.apply_async(args=[site_id], countdown=10)
        result["fingerprint_queued"] = True
        # IndexNow push runs regardless of verification state — the
        # task itself decides to skip if the key isn't verified yet,
        # emits a "skipped" event instead of crashing, so owner sees
        # "not configured" in the activity feed without us gating here.
        indexnow_ping_site.apply_async(
            args=[site_id],
            kwargs={"run_id": run_id},
            countdown=30,
        )
        result["indexnow_queued"] = True

    return result


@celery_app.task(name="crawl_all_sites_monthly", bind=True, max_retries=0)
def crawl_all_sites_monthly(self):
    """Monthly re-crawl of every active site. Spaces sites by 60 seconds
    so one giant crawl doesn't hog the worker pool."""
    logger.info("Starting monthly crawl for all active sites")
    sites = _run_async(_get_active_sites())
    queued = []
    for i, site in enumerate(sites):
        if site.get("id"):
            crawl_site.apply_async(
                args=[str(site["id"])],
                countdown=i * 60,
            )
            queued.append(site["domain"])
    return {"queued": queued}


@celery_app.task(name="collect_site_metrica")
def collect_site_metrica(site_id: str):
    """Collect Metrica data for a specific site (for manual trigger)."""
    from app.config import settings

    async def _run():
        async with task_session() as db:
            result = await db.execute(select(Site).where(Site.id == UUID(site_id)))
            site = result.scalar_one_or_none()
            if not site:
                return {"error": "Site not found"}

            counter_id = site.yandex_metrica_counter_id or settings.YANDEX_METRICA_COUNTER_ID
            if not counter_id:
                return {"status": "skipped", "reason": "no counter_id"}

            collector = MetricaCollector(
                oauth_token=site.yandex_oauth_token or settings.YANDEX_OAUTH_TOKEN,
                counter_id=counter_id,
            )
            try:
                return await collector.collect_and_store(db, site.id, days_back=7)
            finally:
                await collector.close()

    return _run_async(_run())


@celery_app.task(name="indexnow_ping_site", bind=True, max_retries=1)
def indexnow_ping_site(self, site_id: str, run_id: str | None = None):
    """Submit the site's known URLs to Yandex via IndexNow.

    Pre-conditions checked inside:
      - Site exists and has IndexNow key stored in target_config.
      - Key file at `<host>/<key>.txt` is reachable and matches.

    URL source: Page rows we've crawled, limited to pages that looked
    alive during the last crawl (http_status 200). We prefer our own
    crawl list over sitemap.xml because crawl results verify the URLs
    actually render; sitemap.xml sometimes lists dead URLs.

    Called explicitly from the admin endpoint and chained after every
    successful crawl so fresh URLs hit Yandex within minutes of us
    discovering them.
    """
    from app.collectors.indexnow import ping_urls, verify_key_file
    from app.core_audit.activity import emit_terminal, log_event
    from app.models.page import Page

    async def _run():
        async with task_session() as db:
            result = await db.execute(select(Site).where(Site.id == UUID(site_id)))
            site = result.scalar_one_or_none()
            if not site:
                await emit_terminal(
                    db, site_id, "indexnow", "failed",
                    "Сайт не найден.",
                    run_id=run_id,
                )
                return {"error": "Site not found"}

            cfg = site.target_config or {}
            indexnow_cfg = cfg.get("indexnow") or {}
            key = indexnow_cfg.get("key")
            if not key or not indexnow_cfg.get("verified_at"):
                await emit_terminal(
                    db, site_id, "indexnow", "skipped",
                    "IndexNow не настроен: сначала загрузи файл ключа на домен и подтверди.",
                    extra={"reason": "not_verified"},
                    run_id=run_id,
                )
                return {"status": "skipped", "reason": "not_verified"}

            pages_res = await db.execute(
                select(Page.url)
                .where(Page.site_id == site.id, Page.http_status == 200)
                .order_by(Page.last_crawled_at.desc())
            )
            urls = [row[0] for row in pages_res.all() if row[0]]
            if not urls:
                await emit_terminal(
                    db, site_id, "indexnow", "skipped",
                    "Нет списка страниц — запусти краулинг сайта перед пингом.",
                    extra={"reason": "no_pages"},
                    run_id=run_id,
                )
                return {"status": "skipped", "reason": "no_pages"}

            await log_event(
                db, site_id, "indexnow", "started",
                f"Отправляю {len(urls)} URL в Яндекс через IndexNow…",
                run_id=run_id,
            )

            import anyio
            out = await anyio.to_thread.run_sync(ping_urls, site.domain, key, urls)

            if out.accepted:
                message = (
                    f"Яндекс принял {out.url_count} URL. Краулинг обычно "
                    "происходит в течение 24 часов — проверь индексацию завтра."
                )
                status = "done"
            else:
                message = (
                    f"IndexNow отказал: {out.error or 'неизвестная ошибка'} "
                    f"(HTTP {out.status_code}). Проверь файл ключа."
                )
                status = "failed"

            await emit_terminal(
                db, site_id, "indexnow", status, message,
                extra=out.to_dict(),
                run_id=run_id,
            )

            # Update last_pinged_at so UI can show "sent N minutes ago".
            from app.core_audit.sites.locks import lock_site_target_config
            from datetime import datetime, timezone
            await lock_site_target_config(db, site_id)
            await db.refresh(site)
            cfg2 = dict(site.target_config or {})
            idx = dict(cfg2.get("indexnow") or {})
            idx["last_pinged_at"] = datetime.now(timezone.utc).isoformat()
            idx["last_result"] = out.to_dict()
            cfg2["indexnow"] = idx
            site.target_config = cfg2
            await db.commit()

            return out.to_dict()

    return _run_async(_run())


@celery_app.task(name="check_site_indexation", bind=True, max_retries=1)
def check_site_indexation(self, site_id: str, run_id: str | None = None):
    """Probe Yandex `site:domain` to answer "is this site in the index?".

    Runs independently of Webmaster — uses Yandex Cloud Search API
    directly, so it answers the question even when the Webmaster host
    is stuck at HOST_NOT_LOADED. Result goes into activity feed as
    an `indexation` stage event so the UI can surface the honest
    status instead of showing a blank Webmaster card.
    """
    from app.collectors.yandex_serp import check_indexation
    from app.core_audit.activity import emit_terminal, log_event

    async def _run():
        async with task_session() as db:
            result = await db.execute(select(Site).where(Site.id == UUID(site_id)))
            site = result.scalar_one_or_none()
            if not site:
                await emit_terminal(
                    db, site_id, "indexation", "failed",
                    "Сайт не найден в базе.",
                    run_id=run_id,
                )
                return {"error": "Site not found"}

            domain = site.domain
            await log_event(
                db, site_id, "indexation", "started",
                f"Проверяю индексацию в Яндексе по запросу site:{domain}…",
                run_id=run_id,
            )

            # Sync call — Celery task, no event loop juggling needed.
            # The SERP client itself blocks on urllib inside this thread;
            # wrap in a thread executor to avoid holding the asyncio loop.
            import anyio
            out = await anyio.to_thread.run_sync(check_indexation, domain)

            pages = [
                {"url": p.url, "title": p.title, "position": p.position}
                for p in out.pages[:20]
            ]
            extra = {
                "pages_found": out.pages_found,
                "pages": pages,
                "query": f"site:{out.domain}",
            }

            if out.error:
                await emit_terminal(
                    db, site_id, "indexation", "failed",
                    f"Search API вернул ошибку: {out.error}",
                    extra={**extra, "error": out.error},
                    run_id=run_id,
                )
                return out.to_dict()

            if out.pages_found == 0:
                message = (
                    f"Сайт {domain} не найден в индексе Яндекса. "
                    "Это не наша ошибка — Яндекс пока не добавил его в поиск. "
                    "Проверь в Вебмастере: загружен ли хост и нет ли запретов в robots.txt."
                )
                status = "skipped"
            else:
                message = (
                    f"В индексе Яндекса: {out.pages_found} страниц "
                    f"(показываю первые {min(len(pages), 20)})."
                )
                status = "done"

            await emit_terminal(
                db, site_id, "indexation", status, message,
                extra=extra, run_id=run_id,
            )
            return out.to_dict()

    return _run_async(_run())


# ── Wordstat refresh (Studio /queries) ───────────────────────────────────

# Per-fetch sleep so we don't burn the AI Studio quota or trip rate
# limits. 1 req/sec is conservative — Yandex documents far higher
# limits but the dynamics endpoint is heavier than search.
WORDSTAT_INTER_QUERY_SLEEP_SEC = 1.0


@celery_app.task(name="wordstat_refresh_site", bind=True, max_retries=1)
def wordstat_refresh_site(self, site_id: str, run_id: str | None = None):
    """Refresh `wordstat_volume` + `wordstat_trend` for every SearchQuery
    of a site.

    Studio /queries module is the primary consumer. Runs as a Celery
    task (not inline) because each site has dozens to hundreds of
    queries and at 1 req/sec a refresh can take minutes.

    Per CONCEPT.md §5: writes only the wordstat_* columns. Does NOT
    update positions, impressions, cluster, or anything else. If a
    single query's fetch fails, log it and move on — partial progress
    is more useful than aborting the whole batch.
    """
    import time
    import anyio
    from app.collectors.wordstat import fetch_volume
    from app.core_audit.activity import emit_terminal, log_event
    from app.models.search_query import SearchQuery

    async def _run():
        async with task_session() as db:
            site = (await db.execute(
                select(Site).where(Site.id == UUID(site_id))
            )).scalar_one_or_none()
            if not site:
                await emit_terminal(
                    db, site_id, "wordstat", "failed",
                    "Сайт не найден в базе.",
                    run_id=run_id,
                )
                return {
                    "status": "failed",
                    "stage": "wordstat",
                    "error": "Site not found",
                }

            queries = (await db.execute(
                select(SearchQuery).where(SearchQuery.site_id == site.id)
            )).scalars().all()
            if not queries:
                await emit_terminal(
                    db, site_id, "wordstat", "skipped",
                    "Нет запросов для обновления — сначала запусти "
                    "сбор Webmaster или поиск новых запросов.",
                    extra={"reason": "no_queries"},
                    run_id=run_id,
                )
                return {"status": "skipped", "reason": "no_queries"}

            await log_event(
                db, site_id, "wordstat", "started",
                f"Обновляю объёмы Wordstat для {len(queries)} запросов "
                f"(~{len(queries)} сек, ходим по 1 запросу/сек).",
                extra={"queries_total": len(queries)},
                run_id=run_id,
            )

            updated = 0
            empty = 0
            failed = 0

            for i, q in enumerate(queries):
                # Off-load the blocking urllib call so the event loop
                # stays free between queries — same pattern as the
                # existing indexation task.
                try:
                    volume = await anyio.to_thread.run_sync(
                        fetch_volume, q.query_text,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "wordstat.refresh_query_failed query=%r err=%s",
                        q.query_text, exc,
                    )
                    failed += 1
                else:
                    if volume is None:
                        empty += 1
                    else:
                        q.wordstat_volume = volume.count
                        q.wordstat_trend = volume.to_dict()["trend"]
                        q.wordstat_updated_at = volume.fetched_at
                        updated += 1

                # Commit in batches of 25 so partial progress survives
                # if the worker is killed mid-run.
                if (i + 1) % 25 == 0:
                    await db.commit()

                await anyio.to_thread.run_sync(
                    time.sleep, WORDSTAT_INTER_QUERY_SLEEP_SEC,
                )

            # Final commit catches the last <25 rows.
            await db.commit()

            stats = {
                "queries_total": len(queries),
                "updated": updated,
                "empty": empty,
                "failed": failed,
            }
            if updated == 0:
                message = (
                    f"Wordstat не отдал данных ни по одному из {len(queries)} "
                    "запросов. Проверь YANDEX_SEARCH_API_KEY на /studio/connections."
                )
                terminal = "failed"
            else:
                message = (
                    f"Wordstat обновлён: {updated} запросов получили объёмы, "
                    f"{empty} вернули пусто (редкие фразы), {failed} упали с "
                    "ошибкой."
                )
                terminal = "done"

            await emit_terminal(
                db, site_id, "wordstat", terminal, message,
                extra=stats, run_id=run_id,
            )
            return {"status": terminal, **stats}

    return _run_async(_run())


# Cap on how many seed phrases we'll send to /topRequests in a single
# discovery run. Verified with the actual 429 body:
#   "search-api.wordstatRequestsPerHour.rate rate quota limit exceed:
#    allowed 100 requests"
# So the hard ceiling is 100 calls/hour shared across ALL sites on the
# same Cloud key. 10 seeds per run leaves headroom for refreshing two
# sites + the dynamics-based wordstat-refresh task without burning the
# whole budget on one click.
WORDSTAT_DISCOVER_MAX_SEEDS = 10
# How many phrases to keep per seed. /topRequests returns up to ~200 in
# practice; we keep the top N to keep the queries table manageable.
WORDSTAT_DISCOVER_TOP_N_PER_SEED = 30
# Sleep between /topRequests calls. 100 req/hour = 1 req per 36 sec
# average. 40 sec gives a small safety margin so a single back-off
# doesn't blow past the window. Yes, this means a 10-seed run takes
# ~7 minutes — that's the API talking, not us.
WORDSTAT_TOP_REQUESTS_SLEEP_SEC = 40.0


@celery_app.task(name="wordstat_discover_site", bind=True, max_retries=1)
def wordstat_discover_site(self, site_id: str, run_id: str | None = None):
    """Discover new search phrases people enter around the site's
    actual product, using Wordstat `/topRequests`.

    Anchored discovery — each seed always contains `target_config.primary_product`
    so we never expand on off-topic words that may have leaked into
    `services` (e.g. site's profile listed "прокат" alongside "багги":
    without an anchor we'd pull tons of unrelated rental phrases).

    Seed strategy
    -------------
    1. Always: `<primary_product> <geo>` for each geo_primary.
       That's the high-signal layer.
    2. If `secondary_products` is non-empty: also
       `<secondary> <primary_product> <geo>` to capture co-occurrence
       phrases like "маршруты багги сочи".
    3. Fallback for legacy profiles WITHOUT primary_product: use
       `services × geo` — old behaviour, kept for backwards compat.

    Idempotent on (site_id, query_text) via the table's unique constraint.
    Per CONCEPT.md §5: only writes wordstat_volume + updated_at, never
    overwrites cluster, is_branded, last_seen_at on existing rows.
    """
    import time
    import anyio
    from datetime import datetime, timezone
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.collectors.wordstat import fetch_top_requests
    from app.core_audit.activity import emit_terminal, log_event
    from app.models.search_query import SearchQuery

    async def _run():
        async with task_session() as db:
            site = (await db.execute(
                select(Site).where(Site.id == UUID(site_id))
            )).scalar_one_or_none()
            if not site:
                await emit_terminal(
                    db, site_id, "wordstat_discover", "failed",
                    "Сайт не найден в базе.",
                    run_id=run_id,
                )
                return {"status": "failed", "error": "Site not found"}

            cfg = site.target_config or {}
            primary = (cfg.get("primary_product") or "").strip()
            secondaries = [
                s.strip() for s in (cfg.get("secondary_products") or [])
                if s and s.strip() and s.strip() != primary
            ]
            services = [
                s.strip() for s in (cfg.get("services") or [])
                if s and s.strip()
            ]
            geos = [
                g.strip() for g in (cfg.get("geo_primary") or [])
                if g and g.strip()
            ]

            if not geos:
                await emit_terminal(
                    db, site_id, "wordstat_discover", "skipped",
                    "В профиле сайта нет geo_primary — "
                    "сначала заполни регионы в настройках.",
                    extra={"reason": "no_geos"},
                    run_id=run_id,
                )
                return {"status": "skipped", "reason": "no_geos"}

            if not primary and not services:
                await emit_terminal(
                    db, site_id, "wordstat_discover", "skipped",
                    "В профиле сайта нет ни primary_product, ни services — "
                    "сначала заполни услуги в настройках.",
                    extra={"reason": "no_anchor"},
                    run_id=run_id,
                )
                return {"status": "skipped", "reason": "no_anchor"}

            seeds: list[str] = []
            anchor_mode: str
            if primary:
                anchor_mode = "primary_anchored"
                # Layer 1: primary alone — picks up wide-context phrases
                # ("багги тур", "багги отзывы", "багги техника").
                seeds.append(primary)
                # Layer 2: primary × geo — narrows to local intent
                # ("багги сочи", "багги абхазия").
                for geo in geos:
                    if len(seeds) >= WORDSTAT_DISCOVER_MAX_SEEDS:
                        break
                    seeds.append(f"{primary} {geo}")
                # NOTE: secondary × primary × geo expansion was tried
                # and produced near-empty results (Wordstat /topRequests
                # is shallow — phrases must literally contain the seed,
                # so a 3-word seed almost never has children). Skip.
            else:
                anchor_mode = "services_legacy"
                for svc in services:
                    for geo in geos:
                        if len(seeds) >= WORDSTAT_DISCOVER_MAX_SEEDS:
                            break
                        seeds.append(f"{svc} {geo}")
                    if len(seeds) >= WORDSTAT_DISCOVER_MAX_SEEDS:
                        break

            anchor_descr = (
                f"привязка к «{primary}»" if primary
                else f"услуги без привязки ({len(services)} шт.)"
            )
            est_sec = int(len(seeds) * WORDSTAT_TOP_REQUESTS_SLEEP_SEC)
            await log_event(
                db, site_id, "wordstat_discover", "started",
                f"Ищу новые запросы через Wordstat: {len(seeds)} "
                f"seed-фраз ({anchor_descr}, регионов: {len(geos)}). "
                f"Лимит API — 100 запросов/час, идём по {int(WORDSTAT_TOP_REQUESTS_SLEEP_SEC)} сек/запрос. "
                f"Оценка: ~{est_sec // 60} мин {est_sec % 60} сек.",
                extra={
                    "seeds_total": len(seeds),
                    "anchor_mode": anchor_mode,
                    "primary": primary,
                    "geos": len(geos),
                    "est_sec": est_sec,
                },
                run_id=run_id,
            )

            phrases_total = 0
            phrases_unique: set[str] = set()
            failed = 0
            now = datetime.now(timezone.utc)

            for i, seed in enumerate(seeds):
                try:
                    rows = await anyio.to_thread.run_sync(
                        fetch_top_requests, seed,
                    )
                except Exception as exc:  # noqa: BLE001
                    # fetch_top_requests already swallows urllib errors
                    # internally — this catches only truly unexpected
                    # crashes (corrupted module state, etc.).
                    logger.warning(
                        "wordstat.discover_crashed seed=%r err=%s",
                        seed, exc,
                    )
                    rows = None

                # `None` from fetch_top_requests means API failure
                # (429, HTTP error, network). Empty result is `[]`,
                # distinct. Count Nones as failed so the terminal
                # message can be honest about hitting the hourly quota.
                if rows is None:
                    failed += 1

                if rows:
                    # Trim to top-N to keep the table reasonable.
                    rows = sorted(rows, key=lambda r: r.count, reverse=True)
                    rows = rows[:WORDSTAT_DISCOVER_TOP_N_PER_SEED]

                    for r in rows:
                        # ON CONFLICT upsert — site_id + query_text is unique.
                        # Only touch wordstat_volume / updated_at; do NOT
                        # overwrite is_branded, cluster, last_seen_at etc.
                        stmt = pg_insert(SearchQuery).values(
                            site_id=site.id,
                            query_text=r.phrase,
                            wordstat_volume=r.count,
                            wordstat_updated_at=now,
                            is_branded=False,
                        ).on_conflict_do_update(
                            index_elements=["site_id", "query_text"],
                            set_={
                                "wordstat_volume": r.count,
                                "wordstat_updated_at": now,
                            },
                        )
                        await db.execute(stmt)
                        phrases_total += 1
                        phrases_unique.add(r.phrase)

                if (i + 1) % 5 == 0:
                    await db.commit()

                await anyio.to_thread.run_sync(
                    time.sleep, WORDSTAT_TOP_REQUESTS_SLEEP_SEC,
                )

            await db.commit()

            stats = {
                "seeds_total": len(seeds),
                "phrases_seen": phrases_total,
                "phrases_unique": len(phrases_unique),
                "failed_seeds": failed,
            }
            if not phrases_unique:
                if failed >= len(seeds):
                    message = (
                        f"Wordstat вернул 429 на все {len(seeds)} запросов — "
                        "часовой лимит исчерпан (100 запросов/час, делится "
                        "со всеми сайтами на этом ключе). Подожди час и "
                        "попробуй снова."
                    )
                    terminal = "failed"
                elif failed:
                    message = (
                        f"Wordstat частично 429-ил ({failed} из {len(seeds)} "
                        "seed-фраз). Часовой лимит на исходе. Подожди час "
                        "и перезапусти, либо смирись с тем что пришло."
                    )
                    terminal = "failed"
                else:
                    message = (
                        f"Wordstat не нашёл связанных фраз ни для одного из "
                        f"{len(seeds)} seed-запросов. Проверь, что в профиле "
                        "указан реальный primary_product."
                    )
                    terminal = "done"
            else:
                tail = ""
                if failed:
                    tail = (
                        f" {failed} seed-фраз 429-ило (часовой лимит Wordstat "
                        "близок), результат частичный."
                    )
                message = (
                    f"Wordstat-discovery: {len(phrases_unique)} уникальных "
                    f"фраз с объёмами добавлено/обновлено в БД "
                    f"(seed-фраз обработано: {len(seeds)}).{tail}"
                )
                terminal = "done"

            await emit_terminal(
                db, site_id, "wordstat_discover", terminal, message,
                extra=stats, run_id=run_id,
            )
            return {"status": terminal, **stats}

    return _run_async(_run())


@celery_app.task(name="studio_indexation_run", bind=True, max_retries=1)
def studio_indexation_run(self, site_id: str, run_id: str | None = None):
    """Studio /indexation module trigger — probe + diagnose in one shot.

    The pipeline-internal `check_site_indexation` task only does the
    SERP probe (it's part of the broader pipeline chain). Studio adds
    a second leg: when the probe finds < LOW_INDEX_THRESHOLD pages, run
    the same 3 inspections that `playground.indexation` runs (sitemap,
    robots.txt, homepage rendering) and synthesise a single diagnostic
    verdict so the owner sees ONE root cause + ONE fix instead of a
    five-step wizard.

    Stage stays "indexation" — same activity feed entry as the pipeline
    check, just with `extra.diagnosis` populated when applicable. The
    GET endpoint reads the latest indexation event regardless of which
    task wrote it.
    """
    import anyio
    from app.collectors.yandex_serp import check_indexation
    from app.core_audit.activity import emit_terminal, log_event
    from app.playground.scenarios import (
        LOW_INDEX_THRESHOLD,
        _inspect_homepage_rendering,
        _inspect_robots,
        _inspect_sitemap,
        _synthesise_diagnosis,
    )

    async def _run():
        async with task_session() as db:
            site = (await db.execute(
                select(Site).where(Site.id == UUID(site_id))
            )).scalar_one_or_none()
            if not site:
                await emit_terminal(
                    db, site_id, "indexation", "failed",
                    "Сайт не найден в базе.",
                    run_id=run_id,
                )
                return {"error": "Site not found"}

            domain = site.domain
            await log_event(
                db, site_id, "indexation", "started",
                f"Проверяю индексацию: site:{domain} + диагностика "
                f"причины (если страниц мало).",
                run_id=run_id,
            )

            # Step 1: SERP probe.
            out = await anyio.to_thread.run_sync(check_indexation, domain)

            pages = [
                {"url": p.url, "title": p.title, "position": p.position}
                for p in out.pages[:20]
            ]

            if out.error:
                await emit_terminal(
                    db, site_id, "indexation", "failed",
                    f"Search API вернул ошибку: {out.error}",
                    extra={
                        "pages_found": out.pages_found,
                        "pages": pages,
                        "query": f"site:{out.domain}",
                        "diagnosis": None,
                        "error": out.error,
                    },
                    run_id=run_id,
                )
                return out.to_dict()

            # Step 2: diagnose IF coverage is low.
            diagnosis: dict | None = None
            inspections: dict | None = None
            if out.pages_found < LOW_INDEX_THRESHOLD:
                sitemap = await anyio.to_thread.run_sync(_inspect_sitemap, domain)
                robots = await anyio.to_thread.run_sync(_inspect_robots, domain)
                homepage = await anyio.to_thread.run_sync(
                    _inspect_homepage_rendering, domain,
                )
                diagnosis = _synthesise_diagnosis(
                    out.pages_found, sitemap, robots, homepage,
                )
                inspections = {
                    "sitemap": sitemap,
                    "robots": robots,
                    "homepage": homepage,
                }

            extra = {
                "pages_found": out.pages_found,
                "pages": pages,
                "query": f"site:{out.domain}",
                "diagnosis": diagnosis,
                "inspections": inspections,
            }

            if out.pages_found == 0:
                # Honest skipped: not our error, Yandex hasn't crawled.
                # The diagnostic verdict (if any) explains WHY.
                base = (
                    f"Сайт {domain} не найден в индексе Яндекса. "
                )
                if diagnosis:
                    message = base + f"Корневая причина: {diagnosis['verdict']}."
                else:
                    message = base + (
                        "Яндекс просто ещё не добавил его — отправь sitemap "
                        "в Вебмастер и проверь robots.txt."
                    )
                status = "skipped"
            elif out.pages_found < LOW_INDEX_THRESHOLD and diagnosis:
                message = (
                    f"В индексе всего {out.pages_found} страниц — это мало. "
                    f"Корневая причина: {diagnosis['verdict']}. "
                    f"{diagnosis['action_ru']}"
                )
                status = "done"
            else:
                message = (
                    f"В индексе Яндекса: {out.pages_found} страниц "
                    f"(показываю первые {min(len(pages), 20)})."
                )
                status = "done"

            await emit_terminal(
                db, site_id, "indexation", status, message,
                extra=extra, run_id=run_id,
            )
            return {**out.to_dict(), "diagnosis": diagnosis}

    return _run_async(_run())


@celery_app.task(name="classify_queries_site", bind=True, max_retries=1)
def classify_queries_site_task(self, site_id: str, run_id: str | None = None):
    """Studio v2 etap 4 — classify SearchQuery rows by relevance.

    Pipeline per site:

      1. Load site + ProfileSlice + narrative_ru.
      2. Pull every SearchQuery for the site WHERE
            relevance_set_by IS NULL OR relevance_set_by = 'rules'
         User-overridden rows (relevance_set_by='user') are ALWAYS
         skipped — owner's verdict wins forever.
      3. Apply rules first. Anything that returns a verdict gets
         written with set_by='rules' (cheap path).
      4. Whatever rules deferred goes to LLM in CLASSIFY_BATCH_SIZE
         batches. Verdicts written with set_by='llm'.
      5. Anything still missing after LLM (rare — model timeouts /
         malformed output) stays as 'unclassified'.

    Activity feed: stage="classify_queries", emit_terminal at the
    end with totals + cost.

    Idempotent: re-running on a site already classified just
    re-classifies the rules+llm rows, never user rows.
    """
    import anyio
    from datetime import datetime, timezone
    from sqlalchemy import or_

    from app.core_audit.activity import emit_terminal, log_event
    from app.core_audit.relevance import (
        ProfileSlice,
        classify_by_rules,
    )
    from app.core_audit.relevance_llm import (
        CLASSIFY_BATCH_SIZE,
        classify_by_llm,
    )
    from app.models.search_query import SearchQuery

    async def _run():
        async with task_session() as db:
            site = (await db.execute(
                select(Site).where(Site.id == UUID(site_id))
            )).scalar_one_or_none()
            if not site:
                await emit_terminal(
                    db, site_id, "classify_queries", "failed",
                    "Сайт не найден в базе.",
                    run_id=run_id,
                )
                return {"status": "failed", "error": "Site not found"}

            profile = ProfileSlice.from_target_config(site.target_config)
            narrative = (
                site.target_config.get("narrative_ru")
                if site.target_config else ""
            ) or ""

            if not profile.primary_product or not profile.geo_primary:
                await emit_terminal(
                    db, site_id, "classify_queries", "skipped",
                    "Профиль неполный — заполни primary_product и "
                    "geo_primary в /studio/profile перед классификацией.",
                    extra={"reason": "incomplete_profile"},
                    run_id=run_id,
                )
                return {"status": "skipped", "reason": "incomplete_profile"}

            # Rows we may overwrite — never user-set ones.
            rows = (await db.execute(
                select(SearchQuery).where(
                    SearchQuery.site_id == site.id,
                    or_(
                        SearchQuery.relevance_set_by.is_(None),
                        SearchQuery.relevance_set_by == "rules",
                        SearchQuery.relevance_set_by == "llm",
                    ),
                )
            )).scalars().all()

            if not rows:
                await emit_terminal(
                    db, site_id, "classify_queries", "done",
                    "Нет запросов для классификации.",
                    extra={"total": 0},
                    run_id=run_id,
                )
                return {"status": "done", "total": 0}

            await log_event(
                db, site_id, "classify_queries", "started",
                f"Классифицирую {len(rows)} запросов: правила, потом "
                f"LLM пакетами по {CLASSIFY_BATCH_SIZE}.",
                extra={"total": len(rows), "primary": profile.primary_product},
                run_id=run_id,
            )

            now = datetime.now(timezone.utc)

            # ── Pass 1: rules ──────────────────────────────────
            rules_hits = 0
            llm_pending: list[tuple[int, SearchQuery]] = []  # (idx-in-list, row)
            for r in rows:
                v = classify_by_rules(r.query_text, profile)
                if v is not None:
                    r.relevance = v.relevance
                    r.relevance_set_by = v.set_by
                    r.relevance_set_at = now
                    r.relevance_reason_ru = v.reason_ru
                    rules_hits += 1
                else:
                    llm_pending.append((len(llm_pending), r))

            await db.commit()

            # ── Pass 2: LLM in batches ────────────────────────
            llm_hits = 0
            llm_cost = 0.0
            llm_input_tokens = 0
            llm_output_tokens = 0
            llm_failures = 0

            for start in range(0, len(llm_pending), CLASSIFY_BATCH_SIZE):
                batch = llm_pending[start : start + CLASSIFY_BATCH_SIZE]
                batch_queries = [row.query_text for _, row in batch]

                try:
                    result = await anyio.to_thread.run_sync(
                        classify_by_llm,
                        batch_queries,
                        profile,
                        narrative,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "classify_queries.llm_batch_failed start=%d size=%d "
                        "err=%s",
                        start, len(batch), exc,
                    )
                    llm_failures += len(batch)
                    continue

                llm_cost += result.cost_usd
                llm_input_tokens += result.input_tokens
                llm_output_tokens += result.output_tokens

                for batch_idx, (_, row) in enumerate(batch):
                    verdict = result.verdicts.get(batch_idx)
                    if verdict is None:
                        # Model didn't return this index — leave row
                        # alone (will retry on next run).
                        continue
                    row.relevance = verdict.relevance
                    row.relevance_set_by = verdict.set_by
                    row.relevance_set_at = now
                    row.relevance_reason_ru = verdict.reason_ru
                    llm_hits += 1

                # Commit per-batch so partial progress survives a
                # worker crash mid-run.
                await db.commit()

            stats = {
                "total": len(rows),
                "rules_hits": rules_hits,
                "llm_hits": llm_hits,
                "llm_failures": llm_failures,
                "llm_batches": (len(llm_pending) + CLASSIFY_BATCH_SIZE - 1)
                    // CLASSIFY_BATCH_SIZE,
                "llm_cost_usd": round(llm_cost, 5),
                "llm_input_tokens": llm_input_tokens,
                "llm_output_tokens": llm_output_tokens,
            }

            unclassified_left = len(rows) - rules_hits - llm_hits
            if unclassified_left > 0:
                message = (
                    f"Классифицировано {rules_hits + llm_hits} из {len(rows)}: "
                    f"{rules_hits} правилами, {llm_hits} LLM. "
                    f"{unclassified_left} остались без класса "
                    f"(LLM вернул не на всё)."
                )
                terminal = "done"  # not a failure — partial is fine
            else:
                message = (
                    f"Классифицировано {len(rows)} запросов: "
                    f"{rules_hits} правилами, {llm_hits} LLM. "
                    f"Стоимость: ${llm_cost:.4f}."
                )
                terminal = "done"

            await emit_terminal(
                db, site_id, "classify_queries", terminal, message,
                extra=stats, run_id=run_id,
            )
            return {"status": terminal, **stats}

    return _run_async(_run())


@celery_app.task(name="diagnose_harmful_queries_site", bind=True, max_retries=1)
def diagnose_harmful_queries_site_task(
    self, site_id: str, run_id: str | None = None,
):
    """Studio v2 etap 5+ — diagnose WHY each harmful query ranks
    and recommend page edits.

    Pipeline per site:
      1. Pull queries WHERE relevance ∈ (spam, disputed) AND we have
         a position ≤ 30 (the filter that powers /queries/harmful).
      2. For each, probe Yandex SERP for the query → find OUR URL in
         top results. Cache in JSONB on the row.
      3. Look up the matched URL in `pages` to get the actual content
         that ranks.
      4. Call Haiku with profile + query + page content → structured
         cause + concrete edits.
      5. Persist on SearchQuery.harmful_diagnosis (overwrite — content
         may have changed since last diagnosis).

    Skips queries where `harmful_diagnosis` is already set so re-runs
    are cheap. Resetting requires the override path (UI button can
    «перезапросить диагноз», not yet implemented).
    """
    import anyio
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import desc

    from app.core_audit.activity import emit_terminal, log_event
    from app.core_audit.harmful_diagnoser import (
        SERP_DEPTH,
        diagnose_one,
        find_matched_url,
    )
    from app.models.daily_metric import DailyMetric
    from app.models.page import Page
    from app.models.search_query import SearchQuery

    HARMFUL_POSITION_THRESHOLD = 30

    async def _run():
        async with task_session() as db:
            site = (await db.execute(
                select(Site).where(Site.id == UUID(site_id))
            )).scalar_one_or_none()
            if not site:
                await emit_terminal(
                    db, site_id, "harmful_diagnose", "failed",
                    "Сайт не найден в базе.",
                    run_id=run_id,
                )
                return {"status": "failed", "error": "Site not found"}

            cfg = site.target_config or {}
            primary = (cfg.get("primary_product") or "").strip()
            geo = [
                str(g).strip()
                for g in (cfg.get("geo_primary") or [])
                if g and str(g).strip()
            ]
            narrative = str(cfg.get("narrative_ru") or "").strip()

            # Find harmful candidates: spam/disputed AND we have a recent position.
            metrics_cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).date()
            metric_rows = (await db.execute(
                select(DailyMetric)
                .where(
                    DailyMetric.site_id == site.id,
                    DailyMetric.metric_type == "query_performance",
                    DailyMetric.date >= metrics_cutoff,
                )
                .order_by(desc(DailyMetric.date))
                .limit(50000)
            )).scalars().all()
            latest_pos_by_qid: dict[UUID, float] = {}
            for m in metric_rows:
                if m.dimension_id is None:
                    continue
                if m.dimension_id not in latest_pos_by_qid:
                    if m.avg_position is not None:
                        latest_pos_by_qid[m.dimension_id] = float(m.avg_position)

            queries = (await db.execute(
                select(SearchQuery).where(
                    SearchQuery.site_id == site.id,
                    SearchQuery.relevance.in_(("spam", "disputed")),
                )
            )).scalars().all()

            candidates = [
                q for q in queries
                if (
                    latest_pos_by_qid.get(q.id) is not None
                    and latest_pos_by_qid[q.id] <= HARMFUL_POSITION_THRESHOLD
                    and q.harmful_diagnosis is None
                )
            ]

            if not candidates:
                await emit_terminal(
                    db, site_id, "harmful_diagnose", "skipped",
                    (
                        "Нет вредных запросов без диагноза. "
                        "Все уже разобраны или классификатор не нашёл "
                        "проблемных запросов."
                    ),
                    extra={"reason": "nothing_to_diagnose"},
                    run_id=run_id,
                )
                return {"status": "skipped", "reason": "nothing_to_diagnose"}

            await log_event(
                db, site_id, "harmful_diagnose", "started",
                f"Разбираю {len(candidates)} вредных "
                f"{'запрос' if len(candidates) == 1 else 'запросов'}: "
                f"для каждого SERP → находим нашу страницу → LLM "
                f"объясняет причину и даёт правки. ~10 сек на запрос.",
                extra={"total": len(candidates)},
                run_id=run_id,
            )

            diagnosed = 0
            no_match = 0
            no_page_content = 0
            llm_cost = 0.0

            for q in candidates:
                matched = await anyio.to_thread.run_sync(
                    find_matched_url, q.query_text, site.domain,
                )
                if matched is None:
                    no_match += 1
                    q.harmful_diagnosis = {
                        "matched_url": None,
                        "matched_position": None,
                        "cause_ru": (
                            "Не удалось найти страницу через Search API — "
                            "позиция могла измениться или Yandex не показал "
                            "наш домен в момент запроса. Попробуй позже или "
                            "проверь руками."
                        ),
                        "fixes": {},
                        "model": None,
                        "diagnosed_at": datetime.now(timezone.utc).isoformat(),
                        "skipped": "no_match",
                    }
                    q.harmful_diagnosed_at = datetime.now(timezone.utc)
                    await db.commit()
                    continue

                # Look up the URL in our Page table.
                page = (await db.execute(
                    select(Page).where(
                        Page.site_id == site.id,
                        Page.url == matched.url,
                    )
                )).scalar_one_or_none()

                if page is None:
                    no_page_content += 1
                    q.harmful_diagnosis = {
                        "matched_url": matched.url,
                        "matched_position": matched.position,
                        "cause_ru": (
                            f"Yandex показывает URL {matched.url} в выдаче "
                            f"по этому запросу, но в нашей базе сайта этой "
                            f"страницы нет — crawler её не видел. Это "
                            f"частая причина «вредной видимости»: страница "
                            f"существует, мы её не индексируем сами, "
                            f"контент мог быть устаревшим. Снэпет Яндекса: "
                            f"«{matched.headline[:200]}»."
                        ),
                        "fixes": {
                            "content_change_ru": (
                                "Сначала запусти crawl чтобы получить "
                                "содержимое страницы. Затем перезапусти "
                                "диагностику."
                            ),
                        },
                        "model": None,
                        "diagnosed_at": datetime.now(timezone.utc).isoformat(),
                        "skipped": "no_page_in_db",
                    }
                    q.harmful_diagnosed_at = datetime.now(timezone.utc)
                    await db.commit()
                    continue

                # Full LLM diagnosis.
                try:
                    diag = await anyio.to_thread.run_sync(
                        lambda: diagnose_one(
                            query=q.query_text,
                            relevance=q.relevance,
                            relevance_reason=q.relevance_reason_ru,
                            business_narrative=narrative,
                            business_primary=primary,
                            business_geo=geo,
                            matched=matched,
                            page_title=page.title,
                            page_h1=page.h1,
                            page_meta=page.meta_description,
                            page_content=page.content_text,
                        )
                    )
                    llm_cost += float(diag.get("cost_usd") or 0.0)
                    q.harmful_diagnosis = diag
                    q.harmful_diagnosed_at = datetime.now(timezone.utc)
                    diagnosed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "harmful_diagnose.llm_failed query=%r err=%s",
                        q.query_text, exc,
                    )

                await db.commit()

            stats = {
                "total_candidates": len(candidates),
                "diagnosed": diagnosed,
                "no_match_in_serp": no_match,
                "no_page_in_db": no_page_content,
                "llm_cost_usd": round(llm_cost, 5),
            }

            if diagnosed == 0:
                message = (
                    f"Ничего не получилось разобрать: "
                    f"{no_match} запросов без матча в SERP, "
                    f"{no_page_content} URL не в нашем crawler. "
                    f"Запусти crawl и перепроверку индексации, потом перезапусти."
                )
                terminal = "failed"
            else:
                message = (
                    f"Разобрано {diagnosed} вредных {'запрос' if diagnosed == 1 else 'запросов'}: "
                    f"причина + правки на странице. "
                    f"Стоимость LLM: ${llm_cost:.4f}. "
                    f"Открой /studio/queries/harmful чтобы увидеть детали."
                )
                terminal = "done"

            await emit_terminal(
                db, site_id, "harmful_diagnose", terminal, message,
                extra=stats, run_id=run_id,
            )
            return {"status": terminal, **stats}

    return _run_async(_run())
