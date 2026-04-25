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
    """Run async coroutine from sync Celery task."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


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


@celery_app.task(name="crawl_site")
def crawl_site(site_id: str, run_id: str | None = None):
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
