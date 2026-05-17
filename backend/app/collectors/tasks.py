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
from app.security.crypto import decrypt_secret

logger = logging.getLogger(__name__)


def _site_oauth_token(site_token: str | None, fallback_token: str) -> str:
    return decrypt_secret(site_token) or fallback_token


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
            # 90 days = Yandex Webmaster API max look-back. Why so wide:
            # competitor-discovery skip-gate needs ≥5 «money queries» — that
            # threshold is starved by a 7-day window on slow-niche sites
            # (tourism off-season). 90 days catches the full year of
            # high-season tail without paying extra (one paginated call).
            result = await collector.collect_and_store(db, site["id"], days_back=90)
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
            result = await collector.collect_and_store(
                db,
                site["id"],
                days_back=7,
                site_domain=site.get("domain"),
            )
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
            "yandex_oauth_token": _site_oauth_token(
                s.yandex_oauth_token,
                settings.YANDEX_OAUTH_TOKEN,
            ),
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
                oauth_token=_site_oauth_token(
                    site.yandex_oauth_token,
                    settings.YANDEX_OAUTH_TOKEN,
                ),
                user_id=settings.YANDEX_WEBMASTER_USER_ID,
                host_id=site.yandex_webmaster_host_id or settings.YANDEX_WEBMASTER_HOST_ID,
            )
            try:
                # See _collect_webmaster_for_site for why 90.
                out = await collector.collect_and_store(db, site.id, days_back=90)
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
            crawler = SiteCrawler(domain=domain, base_url=base_url, max_pages=200)
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


@celery_app.task(name="crawl_single_page_site", bind=True, max_retries=0)
def crawl_single_page_task(
    self, site_id: str, page_id: str, run_id: str | None = None,
):
    """Re-fetch ONE page on demand. Used by /studio/pages/{id}/recrawl
    when owner edited a page and wants the system to see the new
    title/h1/meta without re-running the full site crawl."""
    from app.collectors.site_crawler import SiteCrawler
    from app.core_audit.activity import emit_terminal, log_event
    from app.models.page import Page

    async def _run():
        async with task_session() as db:
            site_row = await db.execute(
                select(Site).where(Site.id == UUID(site_id)),
            )
            site = site_row.scalar_one_or_none()
            if not site:
                await emit_terminal(
                    db, site_id, "page_recrawl", "failed",
                    "Сайт не найден.", run_id=run_id,
                )
                return {"status": "failed", "error": "site not found"}

            page_row = await db.execute(
                select(Page).where(
                    Page.id == UUID(page_id), Page.site_id == site.id,
                ),
            )
            page = page_row.scalar_one_or_none()
            if not page:
                await emit_terminal(
                    db, site_id, "page_recrawl", "failed",
                    "Страница не найдена.", run_id=run_id,
                    extra={"page_id": page_id},
                )
                return {"status": "failed", "error": "page not found"}

            await log_event(
                db, site_id, "page_recrawl", "started",
                f"Перезагружаю {page.path or page.url}…",
                extra={"page_id": page_id, "url": page.url},
                run_id=run_id,
            )

            domain = site.domain
            base_url = f"https://{domain}"
            crawler = SiteCrawler(domain=domain, base_url=base_url, max_pages=1)
            try:
                result = await crawler.crawl_single_page(db, site.id, page.url)
            except Exception as exc:  # noqa: BLE001
                await emit_terminal(
                    db, site_id, "page_recrawl", "failed",
                    f"Перезагрузка остановлена: {str(exc)[:200]}",
                    extra={"page_id": page_id},
                    run_id=run_id,
                )
                return {"status": "failed", "error": str(exc)}

            if result.get("status") == "failed":
                await emit_terminal(
                    db, site_id, "page_recrawl", "failed",
                    f"Не удалось загрузить {page.url}",
                    extra={"page_id": page_id},
                    run_id=run_id,
                )
                return result

            await emit_terminal(
                db, site_id, "page_recrawl", "done",
                (
                    f"Страница перезагружена · HTTP {result.get('http_status')}. "
                    f"Title/h1/meta обновлены."
                ),
                extra={"page_id": page_id, **result},
                run_id=run_id,
            )
            return result

    return _run_async(_run())


@celery_app.task(name="crawl_all_sites_weekly", bind=True, max_retries=0)
def crawl_all_sites_weekly(self):
    """Weekly re-crawl of every active site. Spaces sites by 60 seconds
    so one giant crawl doesn't hog the worker pool. Was monthly — that
    let title/h1/meta drift up to 30 days, and Reviewer skipped on
    `unchanged_hash` while UI showed cached month-old content."""
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
def collect_site_metrica(site_id: str, run_id: str | None = None):
    """Collect Metrica data for a specific site (manual trigger).

    Mirrors the `collect_site_webmaster` contract:
      - `started` event when fetching begins,
      - terminal `done` / `failed` / `skipped` event when finished.

    A non-CS_OK `counter_code_status` is treated as `skipped` (счётчик
    в обрыве — Метрика возвращает нули не потому что трафика нет, а
    потому что код не установлен / не отвечает). Real exceptions are
    `failed`. Otherwise `done`.
    """
    from app.config import settings
    from app.core_audit.activity import emit_terminal, log_event

    async def _run():
        async with task_session() as db:
            result = await db.execute(select(Site).where(Site.id == UUID(site_id)))
            site = result.scalar_one_or_none()
            if not site:
                await emit_terminal(
                    db, site_id, "metrica_collect", "failed",
                    "Сайт не найден в базе.",
                    run_id=run_id,
                )
                return {
                    "status": "failed",
                    "stage": "metrica_collect",
                    "error": "Site not found",
                }

            counter_id = site.yandex_metrica_counter_id or settings.YANDEX_METRICA_COUNTER_ID
            if not counter_id:
                await emit_terminal(
                    db, site_id, "metrica_collect", "skipped",
                    "Счётчик Метрики не привязан к сайту.",
                    extra={"reason": "no counter_id"},
                    run_id=run_id,
                )
                return {
                    "status": "skipped",
                    "stage": "metrica_collect",
                    "reason": "no counter_id",
                }

            await log_event(
                db, site_id, "metrica_collect", "started",
                "Тяну данные из Яндекс.Метрики…",
                run_id=run_id,
            )

            collector = MetricaCollector(
                oauth_token=_site_oauth_token(
                    site.yandex_oauth_token,
                    settings.YANDEX_OAUTH_TOKEN,
                ),
                counter_id=counter_id,
            )
            try:
                out = await collector.collect_and_store(
                    db,
                    site.id,
                    days_back=7,
                    site_domain=site.domain,
                )
            except Exception as exc:  # noqa: BLE001
                await emit_terminal(
                    db, site_id, "metrica_collect", "failed",
                    f"Метрика ответила ошибкой: {str(exc)[:200]}",
                    run_id=run_id,
                )
                return {
                    "status": "failed",
                    "stage": "metrica_collect",
                    "error": str(exc),
                }
            finally:
                await collector.close()

            # Counter-status check: non-CS_OK means the JS code isn't
            # firing on the site, so `visits=0` is «no data», not «no
            # traffic». Treat as `skipped` with a human-readable hint
            # so the owner fixes the install before retrying.
            counter_info = out.get("counter") if isinstance(out, dict) else None
            code_status = (
                counter_info.get("counter_code_status") if isinstance(counter_info, dict) else None
            )
            stats_summary = {
                "traffic_days": int(out.get("traffic_days", 0) or 0) if isinstance(out, dict) else 0,
                "landing_pages": int(out.get("landing_pages", 0) or 0) if isinstance(out, dict) else 0,
                "traffic_sources": int(out.get("traffic_sources", 0) or 0) if isinstance(out, dict) else 0,
                "goals": int(out.get("goals", 0) or 0) if isinstance(out, dict) else 0,
                "counter_code_status": code_status,
            }
            if code_status and code_status != "CS_OK":
                await emit_terminal(
                    db, site_id, "metrica_collect", "skipped",
                    "Счётчик Метрики в обрыве — проверьте установку кода "
                    f"на сайте (статус «{code_status}»).",
                    extra=stats_summary,
                    run_id=run_id,
                )
                return {
                    "status": "skipped",
                    "stage": "metrica_collect",
                    "reason": "counter_code_status",
                    "counter_code_status": code_status,
                    **stats_summary,
                }

            await emit_terminal(
                db, site_id, "metrica_collect", "done",
                (
                    f"Метрика: {stats_summary['traffic_days']} дней трафика, "
                    f"{stats_summary['landing_pages']} посадочных, "
                    f"{stats_summary['goals']} целей."
                ),
                extra=stats_summary,
                run_id=run_id,
            )
            return out

    return _run_async(_run())


# ── Keyword-gap matcher (Wordstat × page lemmas) ─────────────────────
#
# Stage name in `analysis_events` is `"keyword_gaps"` (plural) — that's
# also the JSONB cache key the studio /keyword-gaps endpoints read back.
# The task name is singular `keyword_match_for_site` to match the
# folder it lives next to (`core_audit/keyword_match/`).
#
# Idempotency: the stage is cached via the latest analysis_events row.
# Reading code (studio endpoints, brain card) always picks the most
# recent terminal row, so re-running the task just appends a fresher
# row — no UPDATE-in-place is needed and we avoid race conditions on
# concurrent triggers. (Pattern mirrors `robots_audit` and `metrica`.)

@celery_app.task(name="keyword_match_for_site", bind=True, max_retries=1)
def keyword_match_for_site(self, site_id: str, run_id: str | None = None):
    """Recompute keyword gaps for a site and cache them as a single
    `analysis_events` row with `stage="keyword_gaps"`.

    Pipeline cascade invariant (CLAUDE.md rule 1): every code path
    emits a started + terminal event so the wrapper closes cleanly.

    Skip semantics:
      * `failed`  — site not found or compute_keyword_gaps raised.
      * `skipped` — site has zero SearchQuery rows with `wordstat_volume`
        (Wordstat has never been collected — running the matcher would
        just return []). UI tells the owner to refresh Wordstat first.
      * `done` with empty gaps — Wordstat has data but every query
        already ranks well / lemmas already match. «Всё в порядке».
      * `done` with N gaps — the typical case.

    The terminal `extra` JSON is the source of truth for the GET
    endpoints — they don't recompute, they read this row.
    """
    from app.core_audit.activity import emit_terminal, log_event
    from app.core_audit.keyword_match import (
        compute_keyword_gaps, summarize_gaps,
    )
    from app.models.search_query import SearchQuery
    from datetime import datetime, timezone

    def _gap_to_dict(g) -> dict:
        """Serialize KeywordGap to a JSONB-safe dict.

        UUIDs → strings (asyncpg can't store raw UUIDs in JSONB);
        everything else is already primitive.
        """
        return {
            "site_id": str(g.site_id),
            "page_id": str(g.page_id),
            "page_url": g.page_url,
            "page_current_title": g.page_current_title,
            "page_current_h1": g.page_current_h1,
            "query": g.query,
            "query_id": str(g.query_id),
            "wordstat_volume": g.wordstat_volume,
            "wordstat_volume_peak_3mo": g.wordstat_volume_peak_3mo,
            "is_off_season": g.is_off_season,
            "current_position": g.current_position,
            "expected_clicks_per_month": g.expected_clicks_per_month,
            "missing_in_title_lemmas": list(g.missing_in_title_lemmas or []),
            "missing_in_h1_lemmas": list(g.missing_in_h1_lemmas or []),
            "missing_in_h2_lemmas": list(g.missing_in_h2_lemmas or []),
            "missing_in_first_para_lemmas": list(
                g.missing_in_first_para_lemmas or []
            ),
            "has_synonym_in_title": g.has_synonym_in_title,
            "decision_tree_action": g.decision_tree_action,
        }

    async def _run():
        async with task_session() as db:
            site = (await db.execute(
                select(Site).where(Site.id == UUID(site_id))
            )).scalar_one_or_none()
            if site is None:
                await emit_terminal(
                    db, site_id, "keyword_gaps", "failed",
                    "Сайт не найден в базе.",
                    run_id=run_id,
                )
                return {
                    "status": "failed",
                    "stage": "keyword_gaps",
                    "error": "Site not found",
                }

            await log_event(
                db, site_id, "keyword_gaps", "started",
                "Сравниваю запросы Wordstat с леммами страниц…",
                run_id=run_id,
            )

            # Gate: if no SearchQuery rows carry a Wordstat volume yet,
            # the matcher returns []. Distinguish «не считали» from
            # «считали, дыр нет» so the UI shows the right CTA.
            from sqlalchemy import func as _sa_func
            queries_with_volume = (await db.execute(
                select(_sa_func.count(SearchQuery.id))
                .where(
                    SearchQuery.site_id == site.id,
                    SearchQuery.wordstat_volume.is_not(None),
                )
            )).scalar_one() or 0

            if queries_with_volume == 0:
                await emit_terminal(
                    db, site_id, "keyword_gaps", "skipped",
                    "Нет запросов с объёмом Wordstat — соберите его "
                    "сначала (кнопка «Обновить объёмы Wordstat»).",
                    extra={
                        "reason": "no_wordstat_volume",
                        "computed_at": datetime.now(timezone.utc).isoformat(),
                        "total_gaps": 0,
                        "total_potential_clicks_per_month": 0,
                        "pages_with_gaps": 0,
                        "gaps": [],
                    },
                    run_id=run_id,
                )
                return {
                    "status": "skipped",
                    "stage": "keyword_gaps",
                    "reason": "no_wordstat_volume",
                }

            try:
                gaps = await compute_keyword_gaps(db, site.id)
                summary = summarize_gaps(gaps, site.id)
            except Exception as exc:  # noqa: BLE001
                try:
                    await db.rollback()
                except Exception:  # noqa: BLE001
                    pass
                await emit_terminal(
                    db, site_id, "keyword_gaps", "failed",
                    f"Сравнение упало: {str(exc)[:200]}",
                    run_id=run_id,
                )
                return {
                    "status": "failed",
                    "stage": "keyword_gaps",
                    "error": str(exc),
                }

            payload = {
                "computed_at": datetime.now(timezone.utc).isoformat(),
                "total_gaps": int(summary.total_gaps),
                "total_potential_clicks_per_month": int(
                    summary.total_potential_clicks_per_month,
                ),
                "pages_with_gaps": int(summary.pages_with_gaps),
                "gaps": [_gap_to_dict(g) for g in gaps],
            }

            if not gaps:
                message = (
                    "Дыр по ключевым словам не найдено — все запросы "
                    "с объёмом либо в топ-5, либо уже покрыты леммами."
                )
            else:
                message = (
                    f"Нашёл {payload['total_gaps']} дыр на "
                    f"{payload['pages_with_gaps']} страницах · "
                    f"потенциал +{payload['total_potential_clicks_per_month']} "
                    "кликов/мес."
                )

            await emit_terminal(
                db, site_id, "keyword_gaps", "done", message,
                extra=payload, run_id=run_id,
            )
            return {
                "status": "done",
                "stage": "keyword_gaps",
                "total_gaps": payload["total_gaps"],
                "pages_with_gaps": payload["pages_with_gaps"],
            }

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
    """Probe Yandex `site:domain` to collect a public Search API sample.

    Runs independently of Webmaster — uses Yandex Cloud Search API
    directly, so it gives a visibility signal even when the Webmaster
    host is stuck at HOST_NOT_LOADED. It is not a full index inventory:
    exact per-URL status still comes from Webmaster.
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
                f"Проверяю видимость в Яндексе по выборке site:{domain}…",
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
                    f"Search API не показал URL сайта {domain} в выборке "
                    "site:domain. Это не точное доказательство полного "
                    "отсутствия в индексе — точный статус смотри по "
                    "per-URL данным Webmaster."
                )
                status = "skipped"
            else:
                message = (
                    f"Search API показал {out.pages_found} URL в выборке "
                    f"site:domain (показываю первые {min(len(pages), 20)})."
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


@celery_app.task(name="wordstat_refresh_all", bind=True, max_retries=0)
def wordstat_refresh_all(self):
    """Weekly fan-out for Wordstat volume refresh. Without this beat,
    monthly volumes only update on manual button click and the UI
    silently shows stale_30d+ status."""
    logger.info("Starting weekly Wordstat volume refresh for all sites")
    sites = _run_async(_get_active_sites())
    queued = []
    for i, site in enumerate(sites):
        if site.get("id"):
            wordstat_refresh_site.apply_async(
                args=[str(site["id"])],
                countdown=i * 60,  # generous spacing — Wordstat is slow
            )
            queued.append(site["domain"])
    return {"queued": queued}


@celery_app.task(name="wordstat_discover_all", bind=True, max_retries=0)
def wordstat_discover_all(self):
    """Weekly semantic demand discovery for all active sites.

    `wordstat_refresh_all` only updates phrases we already know. This
    task keeps expanding the market map from each site's profile so the
    assistant can discover indirect demand like «развлечения сочи» /
    «джиппинг абхазия» without the owner clicking a button every week.

    The Wordstat key is shared and limited, so fan-out is intentionally
    serialized with a large countdown between sites.
    """
    logger.info("Starting weekly Wordstat discovery for all sites")
    sites = _run_async(_get_active_sites())
    queued = []
    for i, site in enumerate(sites):
        if site.get("id"):
            wordstat_discover_site.apply_async(
                args=[str(site["id"])],
                countdown=i * 3600,  # one site per hour: 30 seeds at ~40s each
            )
            queued.append(site["domain"])
    return {"queued": queued, "count": len(queued)}


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
    from app.collectors.wordstat import (
        fetch_volume,
        STATUS_OK,
        STATUS_EMPTY,
        STATUS_ERROR,
        STATUS_INVALID_PHRASE,
    )
    from app.core_audit.activity import emit_terminal, log_event
    from app.models.search_query import SearchQuery

    # Cap on how many per-query error records we put into the terminal
    # event's `extra` JSONB. Beyond this the count is still accurate
    # but we drop the per-row detail to keep the events row small.
    MAX_ERROR_DETAILS = 50

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

            # Tri-state counters (mirroring fetch_volume's outcome.status):
            #   ok      — got real volume; row updated
            #   empty   — API said legitimately zero; row stamped with 0
            #             so next beat doesn't retry forever (bug fix
            #             2026-05-15: previously this counted as "empty"
            #             but `wordstat_updated_at` was NEVER written)
            #   invalid — phrase is a URL / empty / garbage; stamp the
            #             timestamp to exit the retry loop, surface in
            #             errors list as data-quality warning
            #   failed  — transient (HTTP/network); leave row alone so
            #             the next beat retries
            ok = 0
            empty = 0
            invalid = 0
            failed = 0
            error_details: list[dict] = []

            def _record_error(entry: dict) -> None:
                if len(error_details) < MAX_ERROR_DETAILS:
                    error_details.append(entry)

            for i, q in enumerate(queries):
                # Off-load the blocking urllib call so the event loop
                # stays free between queries — same pattern as the
                # existing indexation task. The new fetch_volume never
                # raises on normal failures (returns status="error"),
                # but keep the try/except as defense in depth.
                try:
                    outcome = await anyio.to_thread.run_sync(
                        fetch_volume, q.query_text,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "wordstat.refresh_query_failed query=%r err=%s",
                        q.query_text, exc,
                    )
                    failed += 1
                    _record_error({
                        "query": q.query_text,
                        "code": "exception",
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                else:
                    if outcome.status == STATUS_OK:
                        q.wordstat_volume = outcome.volume
                        q.wordstat_trend = outcome.trend
                        q.wordstat_updated_at = outcome.fetched_at
                        ok += 1
                    elif outcome.status == STATUS_EMPTY:
                        # Legit «no demand» — record 0 + stamp the
                        # timestamp so the weekly beat exits the loop.
                        # `wordstat_trend` may be [] or carry null-count
                        # rows; either is fine for the UI sparkline.
                        q.wordstat_volume = 0
                        q.wordstat_trend = outcome.trend
                        q.wordstat_updated_at = outcome.fetched_at
                        empty += 1
                    elif outcome.status == STATUS_INVALID_PHRASE:
                        # URL / empty string in `query_text` — data
                        # quality bug upstream. Stamp the timestamp to
                        # stop retrying; leave `wordstat_volume` as-is
                        # (likely NULL) so the UI shows «нет данных»
                        # rather than a fake 0.
                        q.wordstat_updated_at = outcome.fetched_at
                        invalid += 1
                        _record_error({
                            "query": q.query_text,
                            "code": "invalid_phrase",
                            "error": outcome.error,
                        })
                    else:  # STATUS_ERROR — transient
                        # DO NOT touch the row. Next beat will retry.
                        failed += 1
                        _record_error({
                            "query": q.query_text,
                            "code": "fetch_error",
                            "http_code": outcome.http_code,
                            "error": outcome.error,
                        })

                # Commit in batches of 25 so partial progress survives
                # if the worker is killed mid-run.
                if (i + 1) % 25 == 0:
                    await db.commit()

                await anyio.to_thread.run_sync(
                    time.sleep, WORDSTAT_INTER_QUERY_SLEEP_SEC,
                )

            # Final commit catches the last <25 rows.
            await db.commit()

            recorded = ok + empty + invalid
            stats: dict = {
                "queries_total": len(queries),
                "ok": ok,
                "empty": empty,
                "invalid": invalid,
                "failed": failed,
                # Keep the legacy `updated` key so existing dashboards /
                # alerts that read it from `agent_runs` extras continue
                # to work — it now means "ok" (real volume written).
                "updated": ok,
                "errors": error_details,
            }

            # Terminal classification:
            #   * everything errored             → "failed"
            #   * at least one row was recorded  → "done" (even if some
            #                                      transient errors —
            #                                      retry next beat)
            if recorded == 0 and failed > 0:
                message = (
                    f"Wordstat не отдал данных ни по одному из {len(queries)} "
                    "запросов. Проверь YANDEX_SEARCH_API_KEY на /studio/connections."
                )
                terminal = "failed"
            else:
                parts = [f"{ok} получили объёмы"]
                if empty:
                    parts.append(f"{empty} без спроса (записали 0)")
                if invalid:
                    parts.append(f"{invalid} некорректных фраз")
                if failed:
                    parts.append(f"{failed} временных ошибок (повторим)")
                message = "Wordstat обновлён: " + ", ".join(parts) + "."
                terminal = "done"

            await emit_terminal(
                db, site_id, "wordstat", terminal, message,
                extra=stats, run_id=run_id,
            )
            return {"status": terminal, **stats}

    result = _run_async(_run())

    # Chain keyword_match after a successful Wordstat refresh — the
    # matcher reads SearchQuery.wordstat_volume that we just wrote.
    # Pipeline cascade invariant (CLAUDE.md rule 1): if Wordstat
    # failed/skipped and `keyword_gaps` was pre-declared in the run's
    # queued list, the upstream pipeline reconciler needs a
    # `keyword_gaps:<terminal>` event. We dispatch the keyword_match
    # task in both success and skip-with-data paths; on hard failure
    # we emit a `keyword_gaps:skipped` so the wrapper closes cleanly.
    try:
        status = (result or {}).get("status") if isinstance(result, dict) else None
        if status == "done":
            # Fire-and-forget; the task emits its own terminal.
            from app.core_audit.pipeline.tasks import queue_keyword_match
            queue_keyword_match(site_id, run_id)
        elif status in ("failed", "skipped"):
            # Only mark the downstream stage as skipped when this run
            # was part of a pipeline (run_id is the only signal we
            # have at this layer — standalone refreshes pass None).
            if run_id:
                from app.core_audit.pipeline.tasks import (
                    skip_keyword_match_after_wordstat_failure,
                )

                async def _mark_kw_skipped() -> None:
                    async with task_session() as db:
                        await skip_keyword_match_after_wordstat_failure(
                            db, site_id, run_id,
                        )

                _run_async(_mark_kw_skipped())
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "wordstat.keyword_match_chain_dispatch_failed site=%s err=%s",
            site_id, exc,
        )

    return result


# Cap on how many seed phrases we'll send to /topRequests in a single
# discovery run. Verified with the actual 429 body:
#   "search-api.wordstatRequestsPerHour.rate rate quota limit exceed:
#    allowed 100 requests"
# The hard ceiling is 100 calls/hour shared across ALL sites on the same
# Cloud key. 30 seeds gives enough breadth for direct + adjacent tourism
# discovery while keeping one manual run around 20 minutes at 40s cadence.
WORDSTAT_DISCOVER_MAX_SEEDS = 30
# How many phrases to keep per seed. /topRequests returns up to ~200 in
# practice; we keep the top N to keep the queries table manageable.
WORDSTAT_DISCOVER_TOP_N_PER_SEED = 30
# Sleep between /topRequests calls. 100 req/hour = 1 req per 36 sec
# average. 40 sec gives a small safety margin so a single back-off
# doesn't blow past the window. Yes, this means a 10-seed run takes
# ~7 minutes — that's the API talking, not us.
WORDSTAT_TOP_REQUESTS_SLEEP_SEC = 40.0

_TOURISM_ADJACENT_INTENTS = (
    "экскурсии",
    "туры",
    "отдых",
    "активный отдых",
    "джип тур",
    "маршруты",
)
_PRODUCT_COMMERCIAL_MODIFIERS = (
    "цена",
    "стоимость",
    "забронировать",
    "отзывы",
)
_RU_FROM_CASE = {
    "сочи": "сочи",
    "адлер": "адлера",
    "абхазия": "абхазии",
    "крым": "крыма",
    "гагра": "гагры",
    "сухум": "сухума",
}
_RU_TO_CASE = {
    "сочи": "сочи",
    "адлер": "адлер",
    "абхазия": "абхазию",
    "крым": "крым",
    "гагра": "гагру",
    "сухум": "сухум",
    "красная поляна": "красную поляну",
}


def _clean_wordstat_seed(value: object) -> str:
    text = str(value or "").strip().lower()
    return " ".join(text.replace("ё", "е").split())


def _list_from_config(cfg: dict, key: str, *, limit: int = 20) -> list[str]:
    raw = cfg.get(key)
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _clean_wordstat_seed(item)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _geo_from_case(geo: str) -> str:
    return _RU_FROM_CASE.get(geo, geo)


def _geo_to_case(geo: str) -> str:
    return _RU_TO_CASE.get(geo, geo)


def _add_seed(plan: list[dict[str, str]], seen: set[str], seed: str, category: str) -> None:
    clean = _clean_wordstat_seed(seed)
    if not clean or clean in seen:
        return
    seen.add(clean)
    plan.append({"seed": clean, "category": category})


def _wordstat_geo_terms(cfg: dict) -> set[str]:
    terms: set[str] = set()
    for geo in (
        _list_from_config(cfg, "geo_primary", limit=20)
        + _list_from_config(cfg, "geo_secondary", limit=20)
    ):
        terms.add(geo)
        terms.add(_geo_from_case(geo))
        terms.add(_geo_to_case(geo))
        if geo.endswith("ия"):
            stem = geo[:-1]
            terms.add(f"{stem}и")
            terms.add(f"{stem}ю")
    return {term for term in terms if term}


def classify_wordstat_discovery_phrase(
    phrase: str,
    target_config: dict,
) -> tuple[bool, str, str]:
    """Deterministic funnel-aware relevance guard for Wordstat discovery.

    `/topRequests` is intentionally broad and happily returns homonyms,
    pop-culture noise and out-of-region demand for short seeds like
    «багги». We want a five-way verdict, not the legacy own/spam binary:

      * ``direct_product`` — primary product + your geo (or commercial
        intent strong enough that the buy-now signal dominates the lack
        of geo)
      * ``funnel_warm``    — tourism activity in your geo, or the
        primary product without explicit geo
      * ``funnel_top``     — discovery intent in your geo («что
        посмотреть», «развлечения сочи», «достопримечательности»)
      * ``out_of_market``  — the product / activity, but the geo is
        another Russian city. We still record it (returns
        ``accepted=True``) so the owner can see the demand, but the
        priority weight is zero downstream.
      * ``spam``           — empty, URL-shaped, homonym («джинсы багги»,
        «трансформеры»), automotive part, content (мульт, фильм…),
        transit timetable.

    ``accepted=False`` is only for spam. Out-of-market accepted but
    priced-zero in the scorer is the deliberate design — the owner
    needs to see it, the brain plan must not act on it.

    Returns ``(accepted, relevance, reason_ru)``.
    """
    from app.profiles.tourism.funnel_intents import detect_intent_layer
    from app.profiles.tourism.ru_cities import is_other_russian_geo

    cfg = target_config if isinstance(target_config, dict) else {}
    text = _clean_wordstat_seed(phrase)
    if not text:
        return False, "spam", "URL или пустая фраза, не запрос"

    # URL-shaped / domain-shaped junk that ingestion sometimes leaks.
    if "://" in text or text.startswith(("www.", "http")):
        return False, "spam", "URL или пустая фраза, не запрос"
    if "." in text and " " not in text and any(
        text.endswith(tld)
        for tld in (".ru", ".рф", ".com", ".org", ".net", ".su")
    ):
        return False, "spam", "URL или пустая фраза, не запрос"

    tokens = text.split()

    # ── Hard homonym / spam categories ──────────────────────────────
    #
    # Tackle the brand-killer cases first; everything that follows
    # assumes we're looking at something at least vaguely tourism-shaped.

    homonym_prefixes = (
        # Clothing — «джинсы багги», «штаны багги»
        "джинс", "штан", "брюк", "одежд", "женск", "мужск",
        "детск",
        # Pop-culture — «трансформеры багги», «мультфильм багги»
        "трансформ", "мультфильм", "мульт", "сериал", "фильм",
        "комикс", "персонаж", "актер", "актёр", "герой",
        # Software — «баг в программе», «починить баги»
        "программ", "софт", "приложен",
        # Toys & gaming
        "игруш", "лего",
    )
    if any(tok.startswith(homonym_prefixes) for tok in tokens):
        return False, "spam", "омоним не про туристическую услугу"

    # Wikipedia / reference / colouring pages — info but not buyer
    if any(tok.startswith(("википед", "раскраск")) for tok in tokens):
        return False, "spam", "омоним не про туристическую услугу"

    # ── Adjacent automotive (parts / repair, NOT vehicle rental) ─────
    auto_part_prefixes = (
        "карбюратор", "карбюра",
        "двигател", "мотор",
        "карданн", "трансмисс",
        "запчаст", "запчас",
        "руль",
    )
    if any(tok.startswith(auto_part_prefixes) for tok in tokens):
        return False, "spam", "автозапчасть, не туристическая услуга"
    auto_brand_tokens = {
        "тойота", "тойоту", "тойоте", "тойоты",
        "лада", "ладу", "ладе", "лады",
        "ваз", "уаз", "газ",
        "форд", "мерседес", "ауди", "бмв", "kia", "хендай",
    }
    if auto_brand_tokens.intersection(tokens):
        return False, "spam", "автозапчасть, не туристическая услуга"

    # ── Transit timetables ─────────────────────────────────────────
    transit_terms = {
        "автобус", "автобусы", "автобуса", "автобусом", "автобусов",
        "поезд", "поезда", "поездом",
        "электричка", "электрички",
        "ласточка", "ласточки",
        "вокзал",
    }
    has_transit_schedule = bool(transit_terms.intersection(tokens)) or any(
        tok.startswith("расписан") for tok in tokens
    )

    # ── Profile-derived flags ──────────────────────────────────────
    primary = _clean_wordstat_seed(cfg.get("primary_product"))
    services = [
        s for s in _list_from_config(cfg, "services", limit=20)
        if s and s != primary
    ]
    geo_terms = _wordstat_geo_terms(cfg)
    my_geos_norm: set[str] = set()
    for g in (
        _list_from_config(cfg, "geo_primary", limit=20)
        + _list_from_config(cfg, "geo_secondary", limit=20)
    ):
        my_geos_norm.add(g)
    # Include the inflected forms generated by `_wordstat_geo_terms` so
    # `is_other_russian_geo` correctly ignores «сочи»/«адлера»/etc.
    my_geos_norm.update(geo_terms)

    has_primary = bool(primary and primary in text)
    has_my_geo = any(term in text for term in geo_terms) if geo_terms else False

    other_geo_found, other_geo_name = is_other_russian_geo(
        tokens, my_geos_norm,
    )

    intent_layer = detect_intent_layer(tokens, text)

    # Transit schedule with no product is just «как доехать» — not what
    # an owner is hunting for. We even reject it when `intent_layer ==
    # "tourism"`, because the «маршрут» / «расписание» combo is what
    # carries the tourism prefix yet the query is clearly about
    # buses/trains, not excursions.
    strong_activity = any(
        tok.startswith((
            "экскурс", "отдых", "джип", "прокат", "экспедиц",
            "аренд", "поездк", "поход", "путешеств",
        ))
        for tok in tokens
    )
    if has_transit_schedule and not has_primary and not strong_activity:
        return False, "spam", "транспортное расписание, не туристическая услуга"

    # ── 6. Out of market: primary product in another Russian city ───
    if has_primary and other_geo_found and not has_my_geo:
        return (
            True,
            "out_of_market",
            f"продукт в чужом регионе ({other_geo_name})",
        )

    # Same rule for clearly-tourism intent (no primary product but
    # «экскурсии в москве», «отдых в крыму» when those aren't your geos)
    # — record it so the owner sees the noise; priority weight = 0.
    if other_geo_found and intent_layer in ("tourism", "commercial") and not has_my_geo:
        return (
            True,
            "out_of_market",
            f"туристический запрос в чужом регионе ({other_geo_name})",
        )

    # ── 7. Direct product (hot, ready-to-buy) ───────────────────────
    if has_primary and has_my_geo:
        return True, "direct_product", "продукт + твоё гео"

    # ── 8. Direct product without explicit geo but commercial intent
    if has_primary and intent_layer == "commercial":
        return (
            True,
            "direct_product",
            "коммерческий продуктовый запрос (без явной гео)",
        )

    # ── 9. Funnel top: tourist in your geo browsing what to do ──────
    if has_my_geo and intent_layer == "discovery":
        return (
            True,
            "funnel_top",
            "турист в твоём гео ищет чем заняться",
        )

    # ── 10. Funnel warm: tourist in your geo looking at activities ──
    if has_my_geo and intent_layer == "tourism":
        return (
            True,
            "funnel_warm",
            "турист в твоём гео ищет активность/тур",
        )

    # ── 10b. Funnel warm: commercial-but-no-product («забронировать
    #         экскурсию», «прокат лодки сочи») — still valuable demand
    if has_my_geo and intent_layer == "commercial":
        return (
            True,
            "funnel_warm",
            "коммерческий туристический запрос в твоём гео",
        )

    # ── 11. Funnel warm: primary product without any geo ────────────
    if has_primary and intent_layer in ("tourism", "none"):
        return (
            True,
            "funnel_warm",
            "запрос про продукт без явной географии",
        )

    # Profile service token (e.g. «экспедиция», «прокат») landing in
    # your geo with no product — still warm.
    service_tokens: set[str] = set()
    for s in services:
        service_tokens.add(s)
        if s.startswith("экспедиц"):
            service_tokens.add("экспедиц")
        if s.startswith("маршрут"):
            service_tokens.add("маршрут")
    has_service = any(s and s in text for s in service_tokens)
    if has_my_geo and has_service:
        return (
            True,
            "funnel_warm",
            "услуга из профиля + твоё гео",
        )

    # ── 12. Fallback: nothing matched, it's spam ────────────────────
    return False, "spam", "не подошёл ни под одну категорию"


def build_wordstat_seed_plan(
    target_config: dict,
    *,
    max_seeds: int = WORDSTAT_DISCOVER_MAX_SEEDS,
) -> list[dict[str, str]]:
    """Build a broad but deterministic Wordstat discovery plan.

    Previous logic used only `primary_product` + `geo_primary`, which
    made a tourism business with profile `{primary: "багги", geos:
    ["сочи","абхазия"]}` ask Wordstat just 3 seeds. This builder keeps
    direct product seeds, then adds service, route and adjacent-tourism
    intent seeds so the system can discover indirect demand like
    «экскурсии из сочи в абхазию» without an LLM inventing phrases.
    """
    cfg = target_config if isinstance(target_config, dict) else {}
    primary = _clean_wordstat_seed(cfg.get("primary_product"))
    services = _list_from_config(cfg, "services")
    secondaries = [
        s for s in _list_from_config(cfg, "secondary_products")
        if s != primary
    ]
    primary_geos = _list_from_config(cfg, "geo_primary", limit=8)
    secondary_geos = [
        geo for geo in _list_from_config(cfg, "geo_secondary", limit=8)
        if geo not in primary_geos
    ]
    geos = primary_geos + secondary_geos

    plan: list[dict[str, str]] = []
    seen: set[str] = set()

    if primary:
        _add_seed(plan, seen, primary, "direct_product")
        for geo in primary_geos:
            _add_seed(plan, seen, f"{primary} {geo}", "direct_product_geo")
            for mod in _PRODUCT_COMMERCIAL_MODIFIERS[:2]:
                _add_seed(plan, seen, f"{primary} {mod} {geo}", "commercial_product_geo")

        for service in services:
            if service == primary:
                continue
            _add_seed(plan, seen, f"{service} {primary}", "service_product")
            for geo in primary_geos[:4]:
                _add_seed(plan, seen, f"{service} {primary} {geo}", "service_product_geo")

        for secondary in secondaries:
            _add_seed(plan, seen, f"{secondary} {primary}", "secondary_product")
            for geo in primary_geos[:3]:
                _add_seed(plan, seen, f"{secondary} {primary} {geo}", "secondary_product_geo")
    else:
        for service in services:
            _add_seed(plan, seen, service, "service")
            for geo in primary_geos:
                _add_seed(plan, seen, f"{service} {geo}", "service_geo")

    # Tourism-specific adjacent intent: this is where we intentionally
    # look beyond literal "buggy" demand. Classifier/relevance logic
    # later filters irrelevant broad phrases; discovery must first see
    # the market.
    if len(primary_geos) >= 2:
        for origin in primary_geos:
            for dest in primary_geos:
                if origin == dest:
                    continue
                # For tourism demand, "из Сочи в Абхазию" is useful;
                # the reverse "из Абхазии в Сочи" is usually not the
                # owner's market and wastes scarce Wordstat quota.
                if origin not in {"сочи", "адлер"} and (
                    "сочи" in primary_geos or "адлер" in primary_geos
                ):
                    continue
                _add_seed(
                    plan,
                    seen,
                    f"экскурсии из {_geo_from_case(origin)} в {_geo_to_case(dest)}",
                    "route_adjacent",
                )
                _add_seed(
                    plan,
                    seen,
                    f"туры из {_geo_from_case(origin)} в {_geo_to_case(dest)}",
                    "route_adjacent",
                )

    for geo in primary_geos:
        for intent in _TOURISM_ADJACENT_INTENTS:
            _add_seed(plan, seen, f"{intent} {geo}", "tourism_adjacent_geo")

    # Secondary geos are useful, but they should not consume the first
    # 30 scarce Wordstat calls before broad primary-market intent
    # ("экскурсии из Сочи в Абхазию", "активный отдых Сочи") has been
    # probed. Add them late as quota/limit allows.
    if primary:
        for geo in secondary_geos:
            _add_seed(plan, seen, f"{primary} {geo}", "direct_product_secondary_geo")
            for mod in _PRODUCT_COMMERCIAL_MODIFIERS[:1]:
                _add_seed(
                    plan,
                    seen,
                    f"{primary} {mod} {geo}",
                    "commercial_product_secondary_geo",
                )
        for service in services:
            if service == primary:
                continue
            for geo in secondary_geos[:3]:
                _add_seed(
                    plan,
                    seen,
                    f"{service} {primary} {geo}",
                    "service_product_secondary_geo",
                )
    else:
        for service in services:
            for geo in secondary_geos:
                _add_seed(plan, seen, f"{service} {geo}", "service_secondary_geo")

    for geo in secondary_geos[:4]:
        for intent in _TOURISM_ADJACENT_INTENTS[:3]:
            _add_seed(
                plan,
                seen,
                f"{intent} {geo}",
                "tourism_adjacent_secondary_geo",
            )

    return plan[:max(0, max_seeds)]


@celery_app.task(
    name="wordstat_discover_site",
    bind=True,
    max_retries=1,
    soft_time_limit=2700,
    time_limit=3000,
)
def wordstat_discover_site(self, site_id: str, run_id: str | None = None):
    """Discover new search phrases people enter around the site's
    actual product, using Wordstat `/topRequests`.

    Discovery is intentionally broader than Webmaster: it expands from
    direct product phrases into service, route and adjacent tourism
    intents so a site can discover demand before it ranks for it.

    Idempotent on (site_id, query_text) via the table's unique constraint.
    Per CONCEPT.md §5: only writes wordstat_volume + updated_at, never
    overwrites cluster, is_branded, last_seen_at on existing rows.
    """
    import time
    import anyio
    from datetime import datetime, timezone
    from sqlalchemy import case
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.collectors.wordstat import (
        STATUS_EMPTY,
        STATUS_OK,
        STATUS_RATE_LIMITED,
        fetch_top_requests_outcome,
    )
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
            primary = _clean_wordstat_seed(cfg.get("primary_product"))
            services = _list_from_config(cfg, "services")
            geos = _list_from_config(cfg, "geo_primary")

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

            seed_plan = build_wordstat_seed_plan(
                cfg,
                max_seeds=WORDSTAT_DISCOVER_MAX_SEEDS,
            )
            seeds = [item["seed"] for item in seed_plan]
            if not seeds:
                await emit_terminal(
                    db, site_id, "wordstat_discover", "skipped",
                    "Не удалось построить seed-план Wordstat из профиля сайта.",
                    extra={"reason": "empty_seed_plan"},
                    run_id=run_id,
                )
                return {"status": "skipped", "reason": "empty_seed_plan"}

            anchor_descr = (
                f"продукт «{primary}» + услуги + смежный туризм" if primary
                else f"услуги без основного продукта ({len(services)} шт.)"
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
                    "anchor_mode": "semantic_profile",
                    "primary": primary,
                    "geos": len(geos),
                    "est_sec": est_sec,
                    "seed_plan": seed_plan,
                },
                run_id=run_id,
            )

            phrases_total = 0
            phrases_unique: set[str] = set()
            failed = 0
            empty = 0
            rejected = 0
            rejected_samples: list[dict] = []
            rate_limited = False
            rate_limited_seed: str | None = None
            seeds_attempted = 0
            seed_results: list[dict] = []
            now = datetime.now(timezone.utc)

            for i, seed in enumerate(seeds):
                try:
                    outcome = await anyio.to_thread.run_sync(
                        fetch_top_requests_outcome, seed,
                    )
                except Exception as exc:  # noqa: BLE001
                    # fetch_top_requests_outcome already swallows urllib
                    # errors internally — this catches only unexpected
                    # crashes (corrupted module state, etc.).
                    logger.warning(
                        "wordstat.discover_crashed seed=%r err=%s",
                        seed, exc,
                    )
                    failed += 1
                    seeds_attempted += 1
                    seed_results.append({
                        "seed": seed,
                        "status": "exception",
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    continue

                seeds_attempted += 1
                seed_results.append({
                    "seed": seed,
                    "status": outcome.status,
                    "count": len(outcome.requests),
                    "http_code": outcome.http_code,
                    "error": outcome.error,
                })

                if outcome.status == STATUS_RATE_LIMITED:
                    failed += 1
                    rate_limited = True
                    rate_limited_seed = seed
                    logger.warning(
                        "wordstat.discover_rate_limited seed=%r attempted=%s/%s",
                        seed, seeds_attempted, len(seeds),
                    )
                    break

                if outcome.status == STATUS_EMPTY:
                    empty += 1
                    rows = []
                elif outcome.status == STATUS_OK:
                    rows = outcome.requests
                else:
                    failed += 1
                    rows = []

                if rows:
                    # Filter homonyms/noise before trimming. A broad
                    # seed like "багги" returns "джинсы багги" and
                    # "трансформеры"; those should never reach the AI
                    # assistant as business opportunities.
                    rows = sorted(rows, key=lambda r: r.count, reverse=True)

                    accepted_for_seed = 0
                    for r in rows:
                        accepted, relevance, reason_ru = classify_wordstat_discovery_phrase(
                            r.phrase,
                            cfg,
                        )
                        if not accepted:
                            rejected += 1
                            if len(rejected_samples) < 20:
                                rejected_samples.append({
                                    "phrase": r.phrase,
                                    "count": r.count,
                                    "reason": reason_ru,
                                })
                            continue

                        # ON CONFLICT upsert — site_id + query_text is unique.
                        # Only touch wordstat_volume / updated_at; do NOT
                        # overwrite is_branded, cluster, last_seen_at etc.
                        # Relevance is rules-owned unless the user already
                        # manually set it.
                        stmt = pg_insert(SearchQuery).values(
                            site_id=site.id,
                            query_text=r.phrase,
                            wordstat_volume=r.count,
                            wordstat_updated_at=now,
                            is_branded=False,
                            relevance=relevance,
                            relevance_set_by="rules",
                            relevance_set_at=now,
                            relevance_reason_ru=reason_ru,
                        ).on_conflict_do_update(
                            index_elements=["site_id", "query_text"],
                            set_={
                                "wordstat_volume": r.count,
                                "wordstat_updated_at": now,
                                "relevance": case(
                                    (
                                        SearchQuery.relevance_set_by == "user",
                                        SearchQuery.relevance,
                                    ),
                                    else_=relevance,
                                ),
                                "relevance_set_by": case(
                                    (
                                        SearchQuery.relevance_set_by == "user",
                                        SearchQuery.relevance_set_by,
                                    ),
                                    else_="rules",
                                ),
                                "relevance_set_at": case(
                                    (
                                        SearchQuery.relevance_set_by == "user",
                                        SearchQuery.relevance_set_at,
                                    ),
                                    else_=now,
                                ),
                                "relevance_reason_ru": case(
                                    (
                                        SearchQuery.relevance_set_by == "user",
                                        SearchQuery.relevance_reason_ru,
                                    ),
                                    else_=reason_ru,
                                ),
                            },
                        )
                        await db.execute(stmt)
                        phrases_total += 1
                        phrases_unique.add(r.phrase)
                        accepted_for_seed += 1
                        if accepted_for_seed >= WORDSTAT_DISCOVER_TOP_N_PER_SEED:
                            break

                if (i + 1) % 5 == 0:
                    await db.commit()

                if i < len(seeds) - 1:
                    await anyio.to_thread.run_sync(
                        time.sleep, WORDSTAT_TOP_REQUESTS_SLEEP_SEC,
                    )

            await db.commit()

            stats = {
                "seeds_total": len(seeds),
                "seeds_attempted": seeds_attempted,
                "seeds_remaining": max(0, len(seeds) - seeds_attempted),
                "phrases_seen": phrases_total,
                "phrases_unique": len(phrases_unique),
                "failed_seeds": failed,
                "empty_seeds": empty,
                "rejected_phrases": rejected,
                "rejected_samples": rejected_samples,
                "rate_limited": rate_limited,
                "rate_limited_seed": rate_limited_seed,
                "seed_plan": seed_plan,
                "seed_results": seed_results[:50],
            }
            if not phrases_unique:
                if rate_limited:
                    message = (
                        f"Wordstat упёрся в часовой лимит на seed «{rate_limited_seed}»: "
                        f"обработано {seeds_attempted} из {len(seeds)}, "
                        f"осталось {max(0, len(seeds) - seeds_attempted)}. "
                        "Новые фразы не успели прийти — подожди час и запусти снова."
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
                        f"{len(seeds)} seed-запросов. Это не значит, что спроса "
                        "нет: проверь профиль и попробуй более широкие услуги/гео."
                    )
                    terminal = "done"
            else:
                tail = ""
                if rate_limited:
                    tail = (
                        f" Остановились на лимите Wordstat после {seeds_attempted} "
                        f"из {len(seeds)} seed-фраз; результат частичный, "
                        "следующий запуск продолжит разведку с новым лимитом."
                    )
                elif failed:
                    tail = (
                        f" {failed} seed-фраз 429-ило (часовой лимит Wordstat "
                        "близок), результат частичный."
                    )
                message = (
                    f"Wordstat-discovery: {len(phrases_unique)} уникальных "
                    f"фраз с объёмами добавлено/обновлено в БД "
                    f"(seed-фраз обработано: {seeds_attempted} из {len(seeds)}).{tail}"
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
                f"Проверяю выборку Яндекса: site:{domain} + диагностика "
                f"причины (если URL мало).",
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
                # Honest skipped: Search API sample is empty. Webmaster
                # per-URL is the source of truth for exact index status.
                base = (
                    f"Search API не показал URL сайта {domain} в выборке site:domain. "
                )
                if diagnosis:
                    message = base + f"Корневая причина: {diagnosis['verdict']}."
                else:
                    message = base + (
                        "Это не точное доказательство полного отсутствия в "
                        "индексе — проверь per-URL статус в Webmaster."
                    )
                status = "skipped"
            elif out.pages_found < LOW_INDEX_THRESHOLD and diagnosis:
                message = (
                    f"Search API показал всего {out.pages_found} URL в "
                    f"выборке site:domain — это мало. "
                    f"Корневая причина: {diagnosis['verdict']}. "
                    f"{diagnosis['action_ru']}"
                )
                status = "done"
            else:
                message = (
                    f"Search API показал {out.pages_found} URL в выборке "
                    f"site:domain (показываю первые {min(len(pages), 20)})."
                )
                status = "done"

            await emit_terminal(
                db, site_id, "indexation", status, message,
                extra=extra, run_id=run_id,
            )
            return {**out.to_dict(), "diagnosis": diagnosis}

    return _run_async(_run())


@celery_app.task(name="classify_queries_all", bind=True, max_retries=0)
def classify_queries_all(self):
    """Daily fan-out for query relevance classification. Runs after
    `collect_webmaster_all` so any newly observed SearchQuery rows
    get tagged the same morning instead of waiting for a manual click."""
    logger.info("Starting daily query relevance classification for all sites")
    sites = _run_async(_get_active_sites())
    queued = []
    for i, site in enumerate(sites):
        if site.get("id"):
            classify_queries_site_task.apply_async(
                args=[str(site["id"])],
                countdown=i * 30,
            )
            queued.append(site["domain"])
    return {"queued": queued}


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
        # Pipeline cascade invariant — every started stage MUST receive a
        # terminal event. The `_run` body can raise from many places
        # (OperationalError on session enter, anthropic SDK errors inside
        # classify_by_llm, malformed target_config, etc.). Without this
        # outer guard, Celery records FAILURE on the task itself but no
        # `classify_queries:failed` activity event is written, leaving
        # the pipeline reconciler unable to close the wrapper.
        #
        # Pattern mirrored from `pipeline_intent_then_review_task`
        # (core_audit/pipeline/tasks.py).
        try:
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
        except Exception as exc:  # noqa: BLE001
            # Best-effort terminal so the pipeline cascade can close.
            # Open a fresh session — the outer one may have rolled back
            # or never been entered.
            logger.exception(
                "classify_queries_site_task.unhandled site=%s err=%s",
                site_id, exc,
            )
            try:
                async with task_session() as db2:
                    await emit_terminal(
                        db2, site_id, "classify_queries", "failed",
                        f"Классификация остановлена: {str(exc)[:200]}",
                        run_id=run_id,
                    )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "classify_queries_site_task.terminal_emit_failed "
                    "site=%s", site_id,
                )
            # Re-raise so Celery still records FAILURE on the task itself.
            raise

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
        MatchedPageInfo,
        diagnose_one,
        find_matched_url,
        score_page_for_query,
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

            # Pre-load all pages for the site once — used by the
            # token-overlap fallback when Search API can't pin a URL.
            all_pages = (await db.execute(
                select(Page).where(Page.site_id == site.id)
            )).scalars().all()

            for q in candidates:
                matched = await anyio.to_thread.run_sync(
                    find_matched_url, q.query_text, site.domain,
                )
                page = None
                heuristic_match = False

                if matched is not None:
                    # SERP found us — look up the exact URL in Page table.
                    page = (await db.execute(
                        select(Page).where(
                            Page.site_id == site.id,
                            Page.url == matched.url,
                        )
                    )).scalar_one_or_none()
                else:
                    # Search API didn't pin us. Fall back to scoring all
                    # pages by token overlap with the query — better than
                    # nothing. Webmaster says we DO rank for this query
                    # over the period, just not in the SERP probe today.
                    best_page = None
                    best_score = 0
                    for p in all_pages:
                        s = score_page_for_query(q.query_text, p)
                        if s > best_score:
                            best_score = s
                            best_page = p
                    if best_page is not None and best_score > 0:
                        page = best_page
                        heuristic_match = True
                        # Synthetic match info — position 0 marks
                        # «не из SERP, эвристика по контенту».
                        matched = MatchedPageInfo(
                            url=best_page.url,
                            position=0,
                            title=best_page.title or "",
                            headline="",
                        )

                if matched is None:
                    no_match += 1
                    q.harmful_diagnosis = {
                        "matched_url": None,
                        "matched_position": None,
                        "cause_ru": (
                            "Не удалось найти страницу: ни Search API не "
                            "показал нас в топ-30 прямо сейчас, ни наш "
                            "crawler не имеет страницы с пересечением слов "
                            "запроса. Возможно, страница из индекса Яндекса "
                            "отсутствует у нас в БД — запусти crawl и "
                            "перепроверку индексации, потом перезапусти разбор."
                        ),
                        "fixes": {},
                        "model": None,
                        "diagnosed_at": datetime.now(timezone.utc).isoformat(),
                        "skipped": "no_match",
                    }
                    q.harmful_diagnosed_at = datetime.now(timezone.utc)
                    await db.commit()
                    continue

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
                            page_title=page.title,  # type: ignore[union-attr]
                            page_h1=page.h1,
                            page_meta=page.meta_description,
                            page_content=page.content_text,
                        )
                    )
                    llm_cost += float(diag.get("cost_usd") or 0.0)
                    if heuristic_match:
                        # Honest UI hint that the URL was inferred,
                        # not pinned by Search API.
                        diag["match_method"] = "content_overlap"
                        diag["matched_position"] = None
                    else:
                        diag["match_method"] = "search_api"
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

    result = _run_async(_run())

    # Translate the freshly-cached harmful_diagnosis fixes into
    # PageReviewRecommendation rows so the owner sees them on
    # /studio/pages — without this they only show up as text inside
    # the harmful-query card. No LLM call here, just DB transformation.
    if result.get("status") in ("done", "skipped"):
        try:
            from app.core_audit.harmful_fix.tasks import harmful_fix_materialize_task
            harmful_fix_materialize_task.apply_async(args=[site_id])
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "harmful_diagnose.materialize_dispatch_failed site=%s err=%s",
                site_id, exc,
            )
    return result


@celery_app.task(name="webmaster_url_indexation_all", bind=True, max_retries=0)
def webmaster_url_indexation_all(self):
    """Daily fan-out for per-URL Webmaster index status. Same pattern as
    `crawl_all_sites_monthly`: enqueues `webmaster_url_indexation_site`
    per active site spaced by 30s so one big site can't hog the worker.

    Without this beat job the per-URL state in Page.yandex_index_checked_at
    only updates on manual `/studio/indexation/refresh-urls` clicks, so the
    UI silently shows weeks-stale data."""
    logger.info("Starting daily per-URL Webmaster indexation for all sites")
    sites = _run_async(_get_active_sites())
    queued = []
    for i, site in enumerate(sites):
        if not site.get("yandex_webmaster_host_id"):
            continue
        if site.get("id"):
            webmaster_url_indexation_site_task.apply_async(
                args=[str(site["id"])],
                countdown=i * 30,
            )
            queued.append(site["domain"])
    return {"queued": queued}


@celery_app.task(name="webmaster_url_indexation_site", bind=True, max_retries=1)
def webmaster_url_indexation_site_task(
    self, site_id: str, run_id: str | None = None,
):
    """Studio v2 etap 1+2 deep — pull per-URL index status from
    Yandex Webmaster and write back to Page rows.

    Two endpoints:
      /search-urls/in-search/samples  — URLs Yandex considers indexed
      /search-urls/excluded/samples   — URLs excluded with reason

    For each Page row we set:
      in_yandex_index = True / False / None (none for unmatched stays None)
      yandex_excluded_reason = removal-reason from API for excluded
      yandex_index_checked_at = now

    Pages absent from BOTH lists keep `in_yandex_index = None`
    (Yandex sample endpoint caps at 5000 — for bigger sites the
    sampling is partial, and we don't want to claim false-«not-indexed»).
    """
    from datetime import datetime, timezone
    from app.collectors.webmaster import WebmasterCollector
    from app.config import settings as _settings
    from app.core_audit.activity import emit_terminal, log_event
    from app.models.page import Page

    async def _run():
        async with task_session() as db:
            site = (await db.execute(
                select(Site).where(Site.id == UUID(site_id))
            )).scalar_one_or_none()
            if not site:
                await emit_terminal(
                    db, site_id, "url_indexation", "failed",
                    "Сайт не найден в базе.",
                    run_id=run_id,
                )
                return {"status": "failed", "error": "Site not found"}

            host_id = (
                site.yandex_webmaster_host_id
                or _settings.YANDEX_WEBMASTER_HOST_ID
            )
            oauth = _site_oauth_token(
                site.yandex_oauth_token,
                _settings.YANDEX_OAUTH_TOKEN,
            )
            user_id = _settings.YANDEX_WEBMASTER_USER_ID
            if not host_id or not oauth or not user_id:
                await emit_terminal(
                    db, site_id, "url_indexation", "failed",
                    "Webmaster не подключён (нет host_id / oauth / user_id). "
                    "Проверь /studio/connections.",
                    run_id=run_id,
                )
                return {"status": "failed", "error": "Webmaster not configured"}

            await log_event(
                db, site_id, "url_indexation", "started",
                "Тяну per-URL индексацию из Webmaster: список "
                "проиндексированных + исключённых с причинами.",
                run_id=run_id,
            )

            collector = WebmasterCollector(
                oauth_token=oauth, user_id=user_id, host_id=host_id,
            )
            try:
                indexed = await collector.fetch_indexed_urls()
                excluded = await collector.fetch_excluded_urls()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "webmaster_url_indexation.fetch_failed err=%s", exc,
                )
                await emit_terminal(
                    db, site_id, "url_indexation", "failed",
                    f"Webmaster API ответил ошибкой: {str(exc)[:200]}.",
                    run_id=run_id,
                )
                await collector.close()
                return {"status": "failed", "error": str(exc)}
            finally:
                await collector.close()

            # URL normalisation — Webmaster sometimes adds trailing
            # slashes, sometimes not. Match on lowercased no-trailing-slash.
            def _norm(u: str) -> str:
                return (u or "").strip().rstrip("/").lower()

            indexed_urls = {_norm(it.get("url", "")) for it in indexed if it.get("url")}
            excluded_by_url: dict[str, str] = {}
            for it in excluded:
                u = _norm(it.get("url", ""))
                if not u:
                    continue
                reason = (
                    str(it.get("removal-reason") or "")
                    .strip()
                    .upper()[:40]
                )
                excluded_by_url[u] = reason or "UNKNOWN"

            pages = (await db.execute(
                select(Page).where(Page.site_id == site.id),
            )).scalars().all()

            now = datetime.now(timezone.utc)
            n_indexed = 0
            n_excluded = 0
            n_unknown = 0

            for p in pages:
                key = _norm(p.url)
                if key in indexed_urls:
                    p.in_yandex_index = True
                    p.yandex_excluded_reason = None
                    n_indexed += 1
                elif key in excluded_by_url:
                    p.in_yandex_index = False
                    p.yandex_excluded_reason = excluded_by_url[key]
                    n_excluded += 1
                else:
                    # Don't claim «not indexed» if Yandex sample didn't
                    # include this URL — could be sampling cap.
                    p.in_yandex_index = None
                    p.yandex_excluded_reason = None
                    n_unknown += 1
                p.yandex_index_checked_at = now

            await db.commit()

            stats = {
                "yandex_indexed_total": len(indexed_urls),
                "yandex_excluded_total": len(excluded_by_url),
                "matched_indexed": n_indexed,
                "matched_excluded": n_excluded,
                "no_match": n_unknown,
                "pages_total": len(pages),
            }
            message = (
                f"Webmaster ответил: {len(indexed_urls)} URL в индексе, "
                f"{len(excluded_by_url)} исключено. "
                f"В нашей БД совпало: {n_indexed} индексировано, "
                f"{n_excluded} исключено, {n_unknown} вне выборки Webmaster."
            )
            await emit_terminal(
                db, site_id, "url_indexation", "done", message,
                extra=stats, run_id=run_id,
            )
            return {"status": "done", **stats}

    return _run_async(_run())


@celery_app.task(name="studio_review_page", bind=True, max_retries=1)
def studio_review_page_task(
    self, site_id: str, page_id: str, run_id: str | None = None,
):
    """Studio v2 etap 3 — review one page on demand.

    Wraps the existing `Reviewer.review_page` so the Studio UI can
    trigger «Запустить ревью» from the page workspace without going
    through the global pipeline. Emits activity events so the page
    can show «идёт ревью…» state and auto-refresh on completion.

    Idempotency: the underlying `review_page` already has its own
    composite-hash dedup (skip if page content hasn't changed since
    last review), so re-clicking is cheap.
    """
    from app.core_audit.activity import emit_terminal, log_event
    from app.core_audit.review.reviewer import Reviewer
    from app.intent.models import CoverageDecision
    from app.models.page import Page

    async def _run():
        async with task_session() as db:
            page = (await db.execute(
                select(Page).where(Page.id == UUID(page_id))
            )).scalar_one_or_none()
            if page is None:
                await emit_terminal(
                    db, site_id, "page_review", "failed",
                    "Страница не найдена.",
                    run_id=run_id,
                )
                return {"status": "failed", "error": "page not found"}

            # Reviewer is decision-driven: it expects a strengthen-decision
            # to know «which intent are we improving the page for». A
            # manual UI trigger doesn't carry that id, so we look it up
            # ourselves — pick the open strengthen decision targeting
            # this page with the highest impressions, falling back to
            # the most recent. Without a decision Reviewer would skip
            # with `not_strengthen` even though the substrate exists.
            decision_row = (await db.execute(
                select(CoverageDecision)
                .where(
                    CoverageDecision.site_id == UUID(site_id),
                    CoverageDecision.target_page_id == UUID(page_id),
                    CoverageDecision.action == "strengthen",
                    CoverageDecision.status == "open",
                )
                .order_by(
                    CoverageDecision.total_impressions.desc(),
                    CoverageDecision.decided_at.desc(),
                )
                .limit(1)
            )).scalar_one_or_none()

            if decision_row is None:
                await emit_terminal(
                    db, site_id, "page_review", "skipped",
                    (
                        "Ревью не запускается: для этой страницы нет "
                        "решения «усилить» (strengthen). Это значит, "
                        "что система не нашла запросов, под которые "
                        "стоит докручивать именно её. Если уверен, что "
                        "нужно — сначала запусти Decisioner на сайте."
                    ),
                    extra={"page_id": page_id, "reason": "no_strengthen_decision"},
                    run_id=run_id,
                )
                return {
                    "status": "skipped",
                    "reason": "no_strengthen_decision",
                    "page_id": page_id,
                }

            await log_event(
                db, site_id, "page_review", "started",
                (
                    f"Запускаю ревью страницы {page.path or page.url} "
                    f"(intent: {decision_row.intent_code})."
                ),
                extra={
                    "page_id": page_id,
                    "decision_id": str(decision_row.id),
                    "intent_code": decision_row.intent_code,
                },
                run_id=run_id,
            )

            try:
                result = await Reviewer().review_page(
                    db, UUID(page_id), decision_row.id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "studio_review_page.failed page=%s err=%s", page_id, exc,
                )
                await emit_terminal(
                    db, site_id, "page_review", "failed",
                    f"Reviewer упал: {str(exc)[:200]}.",
                    extra={"page_id": page_id},
                    run_id=run_id,
                )
                return {"status": "failed", "error": str(exc)}

            recs = len(result.recommendations or [])
            skip_reason = result.skip_reason.value if result.skip_reason else None
            status_value = result.status.value if hasattr(result.status, "value") else str(result.status)

            if skip_reason == "content_unchanged":
                message = (
                    "Ревью пропущено: содержимое страницы не менялось с "
                    "прошлого раза. Если хочешь принудительно — обнови "
                    "page-content (rerun crawl) и нажми снова."
                )
                terminal = "skipped"
            elif skip_reason:
                message = f"Ревью пропущено: {skip_reason}."
                terminal = "skipped"
            elif status_value == "completed":
                message = (
                    f"Ревью готово: {recs} {'рекомендация' if recs == 1 else 'рекомендаций'} "
                    f"(модель {result.reviewer_model}, ${result.cost_usd:.4f})."
                )
                terminal = "done"
            else:
                message = f"Ревью завершилось со статусом {status_value}."
                terminal = "failed" if status_value == "failed" else "done"

            stats = {
                "page_id": page_id,
                "status": status_value,
                "skip_reason": skip_reason,
                "recommendations": recs,
                "reviewer_model": result.reviewer_model,
                "cost_usd": float(result.cost_usd or 0.0),
            }
            await emit_terminal(
                db, site_id, "page_review", terminal, message,
                extra=stats, run_id=run_id,
            )
            return {"status": terminal, **stats}

    return _run_async(_run())


@celery_app.task(name="missing_landings_scan", bind=True, max_retries=1)
def missing_landings_scan_task(self, site_id: str, run_id: str | None = None):
    """Studio v2 etap 6 — find services described in business narrative
    that lack a dedicated landing page.

    Reads `sites.understanding` (built earlier by BusinessUnderstanding)
    + `sites.target_config` + the Page table, asks Haiku to spot gaps,
    drops anything whose evidence_quote is not actually a substring of
    the narrative, and writes the survivors to
    `target_config.missing_landings` without touching other slots.
    """
    from app.core_audit.activity import emit_terminal, log_event
    from app.core_audit.missing_landings import find_missing_landings
    from app.core_audit.sites.locks import lock_site_target_config
    from app.models.page import Page

    async def _run():
        async with task_session() as db:
            site = (await db.execute(
                select(Site).where(Site.id == UUID(site_id))
            )).scalar_one_or_none()
            if site is None:
                await emit_terminal(
                    db, site_id, "missing_landings", "failed",
                    "Сайт не найден.",
                    run_id=run_id,
                )
                return {"status": "failed", "error": "site not found"}

            understanding = site.understanding or {}
            if not (understanding.get("narrative_ru") or "").strip():
                await emit_terminal(
                    db, site_id, "missing_landings", "skipped",
                    "Не запускаю: нет business understanding (narrative_ru пустой). "
                    "Сначала построй понимание бизнеса, потом возвращайся.",
                    extra={"reason": "no_understanding"},
                    run_id=run_id,
                )
                return {"status": "skipped", "reason": "no_understanding"}

            pages_res = await db.execute(
                select(Page)
                .where(Page.site_id == site.id)
                .order_by(Page.url)
            )
            page_rows = pages_res.scalars().all()
            page_dicts = [
                {
                    "path": p.path or p.url,
                    "url": p.url,
                    "title": p.title,
                    "h1": p.h1,
                    "meta_description": p.meta_description,
                    "content_snippet": (p.content_text or "")[:600],
                }
                for p in page_rows
            ]
            if not page_dicts:
                await emit_terminal(
                    db, site_id, "missing_landings", "skipped",
                    "Не запускаю: страниц сайта в базе ещё нет — запусти краулинг.",
                    extra={"reason": "no_pages"},
                    run_id=run_id,
                )
                return {"status": "skipped", "reason": "no_pages"}

            await log_event(
                db, site_id, "missing_landings", "started",
                f"Ищу услуги без посадочных среди {len(page_dicts)} страниц…",
                run_id=run_id,
            )

            try:
                import anyio
                result = await anyio.to_thread.run_sync(
                    lambda: find_missing_landings(
                        understanding=understanding,
                        target_config=site.target_config or {},
                        pages=page_dicts,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "missing_landings_scan.failed site=%s err=%s",
                    site_id, exc,
                )
                await emit_terminal(
                    db, site_id, "missing_landings", "failed",
                    f"LLM-ошибка: {str(exc)[:200]}",
                    run_id=run_id,
                )
                return {"status": "failed", "error": str(exc)}

            # Persist into target_config.missing_landings WITHOUT
            # touching the competitor module's growth_opportunities
            # slot. The LLM call above took ~30 s — during that
            # window other tasks (business_truth, deep_dive) may have
            # committed updates to target_config. We MUST re-SELECT
            # the site under the advisory lock instead of mutating the
            # stale ORM instance loaded before the LLM call. Otherwise
            # we'd stomp those concurrent commits.
            await lock_site_target_config(db, site_id)
            fresh_site = (await db.execute(
                select(Site).where(Site.id == UUID(site_id))
            )).scalar_one()
            new_cfg = dict(fresh_site.target_config or {})
            new_cfg["missing_landings"] = result
            fresh_site.target_config = new_cfg
            await db.commit()

            n_items = len(result["items"])
            n_rejected = result["rejected_no_evidence"]
            cost = result["cost_usd"]
            if n_items == 0:
                if n_rejected:
                    message = (
                        f"Не нашёл пропущенных посадочных. LLM предложил "
                        f"{n_rejected}, но evidence-фильтр отбросил всё — "
                        f"модель не сослалась на конкретный текст бизнеса."
                    )
                    terminal = "done"
                else:
                    summary = result.get("summary_ru") or "Все услуги покрыты страницами."
                    message = (
                        f"{summary} (LLM проверил {len(page_dicts)} страниц, "
                        f"стоимость ${cost:.4f}.)"
                    )
                    terminal = "done"
            else:
                priorities = [it["priority"] for it in result["items"]]
                high = priorities.count("high")
                tail = (
                    f", {n_rejected} отбросил без evidence" if n_rejected else ""
                )
                message = (
                    f"Нашёл {n_items} {'услугу' if n_items == 1 else 'услуг'} "
                    f"без отдельных страниц "
                    f"({high} высокого приоритета){tail}. "
                    f"Стоимость ${cost:.4f}."
                )
                terminal = "done"

            await emit_terminal(
                db, site_id, "missing_landings", terminal, message,
                extra={
                    "items": n_items,
                    "rejected_no_evidence": n_rejected,
                    "cost_usd": cost,
                    "model": result.get("model"),
                },
                run_id=run_id,
            )
            return {"status": terminal, "items": n_items, "cost_usd": cost}

    return _run_async(_run())



# ── Advice card auto-verification (post-«Применил») ─────────────────
#
# Stage in `analysis_events` is `"advice_verify"`. Pipeline cascade
# invariant: every started event gets a terminal (done/failed/skipped).
# Field contract on `advice_card_states`:
#   verification_status / verified_at / verification_evidence
# matches the frozen `VerificationResult` from
# `core_audit.advisor.verification.dispatcher`.

@celery_app.task(name="verify_advice_card_application", bind=True, max_retries=1)
def verify_advice_card_application(
    self, site_id: str, card_id: str, run_id: str | None = None,
):
    """Run deterministic technical re-check for one applied advice card.

    Re-runs the same Python check that produced the card, writes back
    `verification_status / verified_at / verification_evidence`, emits an
    activity event. Idempotent — replays overwrite the prior verdict
    with the freshest one.
    """
    from datetime import datetime as _dt, timezone as _tz
    from sqlalchemy import select as _select
    from app.core_audit.activity import emit_terminal, log_event
    from app.core_audit.advisor import collect_advice
    from app.core_audit.advisor.verification import verify_card
    from app.models.advice_card_state import AdviceCardState

    async def _run():
        async with task_session() as db:
            site_uuid = UUID(site_id)

            await log_event(
                db, site_uuid, "advice_verify", "started",
                f"Проверяю, применилась ли правка для совета «{card_id}»…",
                extra={"card_id": card_id}, run_id=run_id,
            )

            # Pull the card definition from a fresh aggregator pass so
            # we have its current category/link/source_module — the
            # state row alone doesn't carry that.
            feed = await collect_advice(db, site_uuid)
            card = next((c for c in feed.cards if c.id == card_id), None)
            if card is None:
                await emit_terminal(
                    db, site_uuid, "advice_verify", "skipped",
                    "Карточка совета больше не отдаётся системой — "
                    "проверять нечего.",
                    extra={"card_id": card_id, "reason": "card_disappeared"},
                    run_id=run_id,
                )
                return {"status": "skipped", "reason": "card_disappeared"}

            try:
                result = await verify_card(
                    db, site_uuid, card_id,
                    card_category=card.category,
                    card_link=card.link,
                    card_source_module=card.source_module,
                )
            except Exception as exc:  # noqa: BLE001 — defensive; dispatcher already wraps
                await emit_terminal(
                    db, site_uuid, "advice_verify", "failed",
                    f"Проверка сломалась: {str(exc)[:160]}",
                    extra={"card_id": card_id, "error": str(exc)[:300]},
                    run_id=run_id,
                )
                raise

            # Write verdict to state row (upsert if owner hasn't touched
            # it yet — happens for cards verified by sweep before any
            # explicit Применил).
            state = (await db.execute(
                _select(AdviceCardState).where(
                    AdviceCardState.site_id == site_uuid,
                    AdviceCardState.card_id == card_id,
                )
            )).scalar_one_or_none()
            if state is None:
                state = AdviceCardState(
                    site_id=site_uuid, card_id=card_id,
                    source_module=card.source_module,
                )
                db.add(state)

            state.verification_status = result.status
            state.verified_at = _dt.now(_tz.utc)
            state.verification_evidence = dict(result.evidence) if result.evidence else None

            terminal = (
                "done" if result.status in ("verified", "user_attested")
                else "failed" if result.status == "failed"
                else "skipped"  # not_yet_visible — re-runs from beat
            )
            await emit_terminal(
                db, site_uuid, "advice_verify", terminal,
                result.message_ru,
                extra={
                    "card_id": card_id,
                    "verification_status": result.status,
                    "evidence": result.evidence,
                },
                run_id=run_id,
            )

            return {
                "status": terminal,
                "card_id": card_id,
                "verification_status": result.status,
            }

    return _run_async(_run())


@celery_app.task(name="verify_unverified_daily", bind=True, max_retries=0)
def verify_unverified_daily(self):
    """Beat-only sweep: re-runs verify on every card stuck in
    `not_yet_visible` whose `applied_at` is within the last 14 days.
    Owner may have re-deployed late; this picks up the change without
    requiring a manual «Перепроверить» click.
    """
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    from sqlalchemy import select as _select
    from app.models.advice_card_state import AdviceCardState

    async def _run():
        async with task_session() as db:
            cutoff = _dt.now(_tz.utc) - _td(days=14)
            rows = (await db.execute(
                _select(
                    AdviceCardState.site_id, AdviceCardState.card_id,
                ).where(
                    AdviceCardState.verification_status == "not_yet_visible",
                    AdviceCardState.applied_at >= cutoff,
                )
                .limit(200)
            )).all()
        # Re-queue outside the session — verify writes its own session.
        for site_uuid, card_id in rows:
            verify_advice_card_application.apply_async(
                args=[str(site_uuid), card_id],
            )
        return {"requeued": len(rows)}

    return _run_async(_run())


# ── SERP probing + deterministic clustering (roadmap point 2) ──────────


# Hard cap so a single run can't burn the Yandex Cloud Search shared
# quota (≈100 calls/day across all sites). At 30 queries × 1 site we
# stay well under, but two sites onboarding the same day still fit.
SERP_INTEL_MAX_QUERIES_PER_RUN = 30


@celery_app.task(name="serp_intel_probe_for_site", bind=True, max_retries=1)
def serp_intel_probe_for_site(
    self, site_id: str, run_id: str | None = None,
):
    """Probe SERP for the site's top-N important queries, store snapshots.

    Stage = "serp_intel". CLAUDE.md rule 1 (pipeline cascade invariant):
    `started` + a terminal (`done` / `failed` / `skipped`) on every
    code path. CLAUDE.md rule 2: `task_session()` for the DB handle.
    CLAUDE.md rule 3: `run_id` propagates into every event for the
    activity feed.

    Heavy lifting lives in `core_audit.serp_intel.collect_serp_snapshot_for_site`;
    this task is just the Celery wrapper that wires up events.
    """
    from app.core_audit.activity import emit_terminal, log_event
    from app.core_audit.serp_intel import collect_serp_snapshot_for_site

    async def _run():
        async with task_session() as db:
            site = (await db.execute(
                select(Site).where(Site.id == UUID(site_id))
            )).scalar_one_or_none()
            if site is None:
                await emit_terminal(
                    db, site_id, "serp_intel", "failed",
                    "Сайт не найден в базе.",
                    run_id=run_id,
                )
                return {
                    "status": "failed",
                    "stage": "serp_intel",
                    "error": "Site not found",
                }

            await log_event(
                db, site_id, "serp_intel", "started",
                f"Снимаю SERP по самым ценным запросам "
                f"(до {SERP_INTEL_MAX_QUERIES_PER_RUN} штук)…",
                run_id=run_id,
            )

            result = await collect_serp_snapshot_for_site(
                db, UUID(site_id),
                max_queries=SERP_INTEL_MAX_QUERIES_PER_RUN,
            )

            await db.commit()

            total = result.queries_probed + result.queries_failed

            if total == 0:
                await emit_terminal(
                    db, site_id, "serp_intel", "skipped",
                    "Нет запросов для пробы — сначала классифицируй "
                    "запросы и собери Wordstat.",
                    extra={
                        "queries_probed": 0,
                        "queries_failed": 0,
                        "reason": "no_eligible_queries",
                    },
                    run_id=run_id,
                )
                return {
                    "status": "skipped",
                    "stage": "serp_intel",
                    "reason": "no_eligible_queries",
                }

            extra = {
                "queries_probed": result.queries_probed,
                "queries_failed": result.queries_failed,
                "queries_total": total,
            }

            if result.queries_probed == 0:
                # All probes failed — Search API likely unavailable.
                await emit_terminal(
                    db, site_id, "serp_intel", "failed",
                    f"Search API вернул ошибки на всех {total} запросах. "
                    f"Проверь YANDEX_SEARCH_API_KEY и квоту.",
                    extra=extra,
                    run_id=run_id,
                )
                return {
                    "status": "failed",
                    "stage": "serp_intel",
                    **extra,
                }

            in_top_n = sum(
                1 for s in result.snapshots
                if isinstance(s, dict) and s.get("our_position") is not None
            )
            extra["our_in_top_n_count"] = in_top_n
            message = (
                f"SERP собран по {result.queries_probed}/{total} "
                f"запросам, мы в топ-10 на {in_top_n}."
            )
            await emit_terminal(
                db, site_id, "serp_intel", "done", message,
                extra=extra,
                run_id=run_id,
            )
            return {
                "status": "done",
                "stage": "serp_intel",
                **extra,
            }

    return _run_async(_run())


@celery_app.task(name="assign_query_clusters", bind=True, max_retries=1)
def assign_query_clusters(
    self, site_id: str, run_id: str | None = None,
):
    """Recompute deterministic cluster_id for every SearchQuery on a site.

    Cheap (no API calls): the clustering signature is a pure-Python
    lemma normalisation. Idempotent — running twice with the same
    inputs produces the same `cluster` values.

    We only WRITE when the value changed, so an unchanged row doesn't
    bump `updated_at`. Counts both attempted rows and rows that
    actually got a new cluster id so the activity feed reflects the
    real diff.

    Stage = "cluster_queries". Started + terminal on every code path.
    """
    from app.core_audit.activity import emit_terminal, log_event
    from app.core_audit.clustering import cluster_id_for

    async def _run():
        async with task_session() as db:
            site = (await db.execute(
                select(Site).where(Site.id == UUID(site_id))
            )).scalar_one_or_none()
            if site is None:
                await emit_terminal(
                    db, site_id, "cluster_queries", "failed",
                    "Сайт не найден в базе.",
                    run_id=run_id,
                )
                return {
                    "status": "failed",
                    "stage": "cluster_queries",
                    "error": "Site not found",
                }

            await log_event(
                db, site_id, "cluster_queries", "started",
                "Раздаю запросам кластерные подписи по леммам…",
                run_id=run_id,
            )

            rows = (await db.execute(
                select(SearchQuery).where(SearchQuery.site_id == UUID(site_id))
            )).scalars().all()

            if not rows:
                await emit_terminal(
                    db, site_id, "cluster_queries", "skipped",
                    "Нет запросов для кластеризации.",
                    extra={"total": 0, "updated": 0, "clusters": 0},
                    run_id=run_id,
                )
                return {
                    "status": "skipped",
                    "stage": "cluster_queries",
                    "reason": "no_queries",
                }

            updated = 0
            cluster_set: set[str] = set()
            unclustered = 0
            for q in rows:
                new_cluster = cluster_id_for(q.query_text)
                if new_cluster is None:
                    unclustered += 1
                else:
                    cluster_set.add(new_cluster)
                if q.cluster != new_cluster:
                    q.cluster = new_cluster
                    updated += 1

            await db.commit()

            extra = {
                "total": len(rows),
                "updated": updated,
                "clusters": len(cluster_set),
                "unclustered": unclustered,
            }
            message = (
                f"Кластеризовал {len(rows)} запросов в {len(cluster_set)} "
                f"групп ({updated} изменений, {unclustered} без кластера)."
            )
            await emit_terminal(
                db, site_id, "cluster_queries", "done", message,
                extra=extra,
                run_id=run_id,
            )
            return {
                "status": "done",
                "stage": "cluster_queries",
                **extra,
            }

    return _run_async(_run())


@celery_app.task(name="serp_intel_probe_all", bind=True, max_retries=0)
def serp_intel_probe_all(self):
    """Thursday-morning fan-out: probe important queries for every
    active site. Spaced 60s apart so we don't burn the SERP quota in
    one minute (each per-site task itself sleeps between queries).
    """
    logger.info("Starting weekly SERP-intel probe for all active sites")
    sites = _run_async(_get_active_sites())
    queued: list[str] = []
    for i, site in enumerate(sites):
        sid = site.get("id")
        if not sid:
            continue
        serp_intel_probe_for_site.apply_async(
            args=[str(sid)],
            countdown=i * 60,
        )
        queued.append(site["domain"])
    return {"queued": queued}


@celery_app.task(name="assign_query_clusters_all", bind=True, max_retries=0)
def assign_query_clusters_all(self):
    """Daily fan-out for deterministic clustering. Cheap — runs in
    milliseconds per site — but we still stagger by 5 seconds so the
    activity feed is readable.
    """
    logger.info("Starting daily cluster reassignment for all active sites")
    sites = _run_async(_get_active_sites())
    queued: list[str] = []
    for i, site in enumerate(sites):
        sid = site.get("id")
        if not sid:
            continue
        assign_query_clusters.apply_async(
            args=[str(sid)],
            countdown=i * 5,
        )
        queued.append(site["domain"])
    return {"queued": queued}
