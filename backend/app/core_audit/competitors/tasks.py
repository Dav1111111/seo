"""Celery task — competitor discovery run.

Fetches top money queries for a site, probes each via Yandex Cloud
Search API, aggregates domains, persists result into
`sites.competitor_domains` (plain list of domain strings for the wizard
UI) and `sites.target_config.competitor_profile` (full dict for drill-
down).

Idempotency: `pg_try_advisory_lock` on the site UUID — double-clicks
become no-ops.
"""

from __future__ import annotations

import asyncio
import logging
import re
from uuid import UUID

from sqlalchemy import desc, func, select, text

from app.core_audit.activity import emit_terminal, log_event
from app.core_audit.competitors.discovery import (
    DEFAULT_MAX_QUERIES,
    DEFAULT_TOP_K,
    discover_competitors,
)
from app.core_audit.demand_map.models import TargetCluster
from app.models.daily_metric import DailyMetric
from app.models.page import Page
from app.models.search_query import SearchQuery
from app.models.site import Site
from app.workers.celery_app import celery_app
from app.workers.db_session import task_session

log = logging.getLogger(__name__)


def _advisory_key(site_id: UUID) -> int:
    """Signed 64-bit int derived from UUID for pg_try_advisory_lock."""
    return int(site_id.hex[:16], 16) - (1 << 63)


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _queue_review_chain(site_id: str, run_id: str | None) -> bool:
    """Continue full analysis into intent_decide -> review -> priorities -> report."""
    from app.core_audit.pipeline.tasks import pipeline_intent_then_review_task

    try:
        pipeline_intent_then_review_task.apply_async(
            args=[site_id],
            kwargs={"run_id": run_id},
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("competitors.review_chain_dispatch_failed site=%s err=%s", site_id, exc)
        return False


async def _mark_review_chain_dispatch_failed(
    db,
    site_id: str,
    run_id: str | None,
) -> None:
    await emit_terminal(
        db, site_id, "intent_decide", "skipped",
        "Решения по запросам пропущены — проверка страниц не запустилась.",
        run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "review", "failed",
        "Не удалось запустить проверку страниц.",
        run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "priorities", "skipped",
        "Приоритеты пропущены — проверка страниц не запустилась.",
        run_id=run_id,
    )
    await emit_terminal(
        db, site_id, "report", "skipped",
        "Отчёт пропущен — проверка страниц не запустилась.",
        run_id=run_id,
    )


async def _skip_after_competitor_stop(
    db,
    site_id: str,
    run_id: str | None,
    *,
    reason: str,
    include_deep_dive: bool = True,
) -> None:
    """Fill dependent terminals when competitor flow cannot continue."""
    extra = {"reason": reason}
    if include_deep_dive:
        await emit_terminal(
            db, site_id, "competitor_deep_dive", "skipped",
            "Глубокий анализ пропущен — нет свежей разведки конкурентов.",
            extra=extra, run_id=run_id,
        )
    await emit_terminal(
        db, site_id, "opportunities", "skipped",
        "Точки роста пропущены — нет свежего глубокого анализа конкурентов.",
        extra=extra, run_id=run_id,
    )


_GENERIC_QUERY_TOKENS: frozenset[str] = frozenset({
    "прокат", "аренда", "арендовать", "забронировать", "купить",
    "заказать", "цена", "цены", "стоимость", "туры", "тур",
})


def _term_tokens(value: str) -> set[str]:
    """Russian/latin token splitter for business focus matching."""
    return {
        t for t in re.split(r"[^0-9a-zа-яё]+", str(value or "").lower())
        if len(t) >= 3
    }


def _tokens_from_values(values) -> set[str]:
    out: set[str] = set()
    for value in values or []:
        out.update(_term_tokens(str(value)))
    return out


def _business_tokens(target_config: dict, *, site_id: str | None = None) -> dict[str, object]:
    """Build a strict query filter from business profile + strategic focus.

    Competitor discovery must compare the current SEO focus, not every
    loosely related service ever mentioned on the site. If the owner set
    `strategic_focus`, require both product and region tokens from it
    and exclude explicitly deprioritised directions.
    """
    cfg = target_config or {}
    focus = cfg.get("strategic_focus") or {}
    focus_products = focus.get("products") or []
    focus_regions = focus.get("regions") or []

    product_tokens = _tokens_from_values(focus_products)
    product_tokens.update(_term_tokens(cfg.get("primary_product") or ""))
    if not product_tokens:
        product_tokens.update(_tokens_from_values(cfg.get("services") or []))
        product_tokens.update(_tokens_from_values(cfg.get("secondary_products") or []))

    # Remember whether the original (pre-filter) product token set had any
    # entries. When primary_product is a generic stop-word like "тур" the
    # strip-on-_GENERIC_QUERY_TOKENS below wipes everything out and we used
    # to silently fall through to region-only matching, admitting every
    # Sochi query as competitor signal.
    had_product_input = bool(product_tokens)
    product_tokens = {
        t for t in product_tokens
        if t not in _GENERIC_QUERY_TOKENS
    }
    product_tokens_emptied_by_filter = had_product_input and not product_tokens
    if product_tokens_emptied_by_filter:
        log.warning(
            "competitors.business_tokens.products_emptied_by_generic_filter "
            "site=%s primary_product=%r — discovery will fail closed.",
            site_id, cfg.get("primary_product"),
        )

    region_tokens = _tokens_from_values(focus_regions)
    if not region_tokens:
        region_tokens.update(_tokens_from_values(cfg.get("geo_primary") or []))

    excluded_tokens = _tokens_from_values(focus.get("deprioritised") or [])
    if region_tokens:
        all_geo_tokens = _tokens_from_values(cfg.get("geo_primary") or [])
        all_geo_tokens.update(_tokens_from_values(cfg.get("geo_secondary") or []))
        excluded_tokens.update(all_geo_tokens - region_tokens)

    query_signal_phrases = [
        " ".join(str(q or "").lower().split())
        for q in (focus.get("query_signals") or [])
        if str(q or "").strip()
    ]

    return {
        "product_tokens": product_tokens,
        "region_tokens": region_tokens,
        "excluded_tokens": excluded_tokens,
        "query_signal_phrases": query_signal_phrases,
        "has_strategic_focus": bool(focus_products or focus_regions),
        # Empty token set fails closed: better to skip discovery than to
        # admit every Sochi query as competitor signal.
        "fail_closed": product_tokens_emptied_by_filter,
    }


def _query_is_relevant(query: str, biz_tokens) -> bool:
    """True if query belongs to the current business/SEO focus."""
    q = " ".join(str(query or "").lower().split())
    if not q:
        return False

    # Backward compatibility for tests/imports that may pass the old set.
    # Use token-set intersection (not substring) so primary_product="тур"
    # doesn't substring-match "литература" / "структура" / "турция".
    if isinstance(biz_tokens, set):
        if not biz_tokens:
            return True
        return bool(_term_tokens(q) & biz_tokens)

    filt = biz_tokens or {}

    # Fail-closed: when product tokens were wiped by the generic filter
    # (e.g. primary_product="тур"), refuse every query instead of falling
    # through to region-only matching.
    if filt.get("fail_closed"):
        return False

    q_tokens = _term_tokens(q)
    excluded = set(filt.get("excluded_tokens") or set())
    if q_tokens & excluded:
        return False

    signal_phrases = filt.get("query_signal_phrases") or []
    if any(phrase and phrase in q for phrase in signal_phrases):
        return True

    product_tokens = set(filt.get("product_tokens") or set())
    if product_tokens and not (q_tokens & product_tokens):
        return False

    region_tokens = set(filt.get("region_tokens") or set())
    if region_tokens and not (q_tokens & region_tokens):
        return False

    # Reject pure generic rental/price phrases once a focus exists.
    if filt.get("has_strategic_focus") and q_tokens <= _GENERIC_QUERY_TOKENS:
        return False

    return True


async def _pick_top_queries(
    db, site_id: UUID, limit: int, *, biz_tokens=None,
) -> list[str]:
    """Best-effort source ranking for 'which queries to probe SERP for'.

    Priority:
      1. Observed queries (from Webmaster) that actually brought impressions
         in the last 14 days AND contain at least one business token from
         target_config. The business-token filter drops random queries
         like 'polaris slingshot' that some visitor once typed and
         accidentally landed on the site.
      2. If that's empty or too small, fall back to top TargetClusters by
         business_relevance — these may include queries we target but
         don't yet rank on, useful for early-stage sites with no traffic.
    """
    from datetime import date, timedelta
    since = date.today() - timedelta(days=14)

    # 1) Observed queries with recent impressions.
    # DailyMetric.dimension_id = SearchQuery.id for metric_type='query_performance'
    # (see webmaster collector). Join on UUID, aggregate impressions, sort desc.
    stmt = (
        select(
            SearchQuery.query_text,
            func.coalesce(func.sum(DailyMetric.impressions), 0).label("imp_sum"),
        )
        .join(
            DailyMetric,
            (DailyMetric.site_id == SearchQuery.site_id)
            & (DailyMetric.dimension_id == SearchQuery.id)
            & (DailyMetric.metric_type == "query_performance")
            & (DailyMetric.date >= since),
        )
        .where(SearchQuery.site_id == site_id, SearchQuery.is_branded.is_(False))
        .group_by(SearchQuery.id, SearchQuery.query_text)
        .having(func.coalesce(func.sum(DailyMetric.impressions), 0) > 0)
        .order_by(desc("imp_sum"))
        # pull a wide window then filter by business tokens in Python
        .limit(limit * 5)
    )
    try:
        rows = (await db.execute(stmt)).all()
    except Exception as exc:  # noqa: BLE001 — schema may lack join col
        log.warning("competitors.observed_query_pick_failed err=%s", exc)
        rows = []

    tokens = biz_tokens or set()
    observed = [
        r.query_text for r in rows
        if r.query_text and _query_is_relevant(r.query_text, tokens)
    ][:limit]

    if len(observed) >= max(5, limit // 2):
        return observed

    # 2) Fallback — top clusters by relevance. Use cluster name_ru as the
    # search string (it was already normalised by the expander).
    need = limit - len(observed)
    cl_stmt = (
        select(TargetCluster.name_ru)
        .where(
            TargetCluster.site_id == site_id,
            TargetCluster.quality_tier.in_(("core", "secondary")),
            TargetCluster.is_brand.is_(False),
            TargetCluster.is_competitor_brand.is_(False),
        )
        .order_by(desc(TargetCluster.business_relevance))
        .limit(need * 2)  # extra so we can de-dupe against observed
    )
    extra_rows = await db.execute(cl_stmt)
    extras = [
        r[0] for r in extra_rows
        if r[0] and _query_is_relevant(r[0], tokens)
    ]
    merged: list[str] = list(observed)
    seen = {q.lower() for q in merged}
    for q in extras:
        lq = q.lower()
        if lq in seen:
            continue
        merged.append(q)
        seen.add(lq)
        if len(merged) >= limit:
            break
    return merged[:limit]


def _compute_shadow_picks(
    bt_blob: dict,
    budget: int,
    old_queries: list[str],
) -> tuple[dict, list]:
    """Build v2 picks from a persisted BusinessTruth blob + old queries.

    Returns (diff_for_logging, picks_list). The caller logs the diff
    unconditionally (shadow mode) and decides via the feature flag
    whether to use picks_list as actual discovery queries.
    """
    from app.core_audit.business_truth.dto import (
        BusinessTruth, DirectionEvidence, DirectionKey,
    )
    from app.core_audit.business_truth.query_picker_v2 import (
        pick_queries_from_truth,
    )

    # Rehydrate minimal BusinessTruth from JSONB — we only need
    # DirectionEvidence.key + queries_sample + strengths for the picker.
    directions: list[DirectionEvidence] = []
    for d in bt_blob.get("directions") or []:
        directions.append(DirectionEvidence(
            key=DirectionKey.of(d.get("service", ""), d.get("geo", "")),
            strength_understanding=float(d.get("strength_understanding", 0.0)),
            strength_content=float(d.get("strength_content", 0.0)),
            strength_traffic=float(d.get("strength_traffic", 0.0)),
            pages=tuple(d.get("pages") or ()),
            queries=tuple(d.get("queries_sample") or ()),
        ))
    truth = BusinessTruth(directions=directions)

    # Synthesize fallback templates ({service} {geo} [...]) so low-
    # traffic sites — where observed evidence might only cover 2-3
    # slots — still get a meaningful comparison against legacy.
    result = pick_queries_from_truth(
        truth, budget=budget, synthesize_fallback=True,
    )
    new_queries = [p.query for p in result.queries]

    old_set = set(old_queries)
    new_set = set(new_queries)
    overlap = old_set & new_set
    diff = {
        "old_count": len(old_queries),
        "new_count": len(new_queries),
        "overlap_count": len(overlap),
        "overlap_pct": (
            round(len(overlap) / max(len(new_set), 1) * 100, 1)
            if new_set else 0.0
        ),
        "old_only_sample": sorted(old_set - new_set)[:5],
        "new_only_sample": sorted(new_set - old_set)[:5],
        "direction_budget": {
            f"{k.service}·{k.geo}": v
            for k, v in result.direction_budget.items()
        },
        "deficit": {
            f"{k.service}·{k.geo}": v
            for k, v in result.deficit.items()
        } if result.deficit else None,
    }
    return diff, result.queries


@celery_app.task(name="competitors_discover_site", bind=True, max_retries=0)
def competitors_discover_site_task(
    self,
    site_id: str,
    max_queries: int = DEFAULT_MAX_QUERIES,
    top_k: int = DEFAULT_TOP_K,
    run_id: str | None = None,
) -> dict:
    """Discover competitors for one site via SERP. Returns a summary dict."""

    async def _inner() -> dict:
        try:
            async with task_session() as db:
                lock_key = _advisory_key(UUID(site_id))
                locked = (await db.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key},
                )).scalar_one()
                if not locked:
                    await emit_terminal(
                        db, site_id, "competitor_discovery", "skipped",
                        "Разведка уже идёт — второй запуск пропущен.",
                        run_id=run_id,
                    )
                    await _skip_after_competitor_stop(
                        db,
                        site_id,
                        run_id,
                        reason="concurrent_run",
                    )
                    if not _queue_review_chain(site_id, run_id):
                        await _mark_review_chain_dispatch_failed(db, site_id, run_id)
                    return {
                        "status": "skipped",
                        "reason": "concurrent_run",
                        "site_id": site_id,
                    }
                try:
                    site = await db.get(Site, UUID(site_id))
                    if site is None:
                        await emit_terminal(
                            db, site_id, "competitor_discovery", "failed",
                            "Сайт не найден в базе.",
                            run_id=run_id,
                        )
                        await _skip_after_competitor_stop(
                            db,
                            site_id,
                            run_id,
                            reason="site_not_found",
                        )
                        if not _queue_review_chain(site_id, run_id):
                            await _mark_review_chain_dispatch_failed(db, site_id, run_id)
                        return {"status": "skipped", "reason": "site_not_found"}

                    focus_filter = _business_tokens(site.target_config or {})
                    old_queries = await _pick_top_queries(
                        db, site.id, max_queries,
                        biz_tokens=focus_filter,
                    )

                    # Shadow-mode: if the site has a BusinessTruth blob,
                    # compute the new direction-aware picks alongside,
                    # log the diff, and optionally use them as the
                    # actual discovery queries (gated by feature flag).
                    shadow_diff = None
                    new_picks = None
                    cfg = site.target_config or {}
                    bt_blob = cfg.get("business_truth") if isinstance(cfg, dict) else None
                    if bt_blob and bt_blob.get("directions"):
                        try:
                            shadow_diff, new_picks = _compute_shadow_picks(
                                bt_blob, max_queries, old_queries,
                            )
                        except Exception as exc:  # noqa: BLE001
                            log.warning(
                                "competitors.shadow_pick_failed site=%s err=%s",
                                site_id, exc,
                            )

                    from app.config import settings as _settings
                    use_v2 = _settings.USE_BUSINESS_TRUTH_DISCOVERY and bool(new_picks)
                    queries = (
                        [p.query for p in new_picks] if use_v2 else old_queries
                    )

                    if not queries:
                        await emit_terminal(
                            db, site_id, "competitor_discovery", "skipped",
                            "Нет запросов для разведки — сначала запусти сбор из Вебмастера.",
                            run_id=run_id,
                        )
                        await _skip_after_competitor_stop(
                            db,
                            site_id,
                            run_id,
                            reason="no_queries_available",
                        )
                        if not _queue_review_chain(site_id, run_id):
                            await _mark_review_chain_dispatch_failed(db, site_id, run_id)
                        return {
                            "status": "skipped",
                            "reason": "no_queries_available",
                            "site_id": site_id,
                        }

                    picker_name = (
                        "business_truth_v2"
                        if use_v2
                        else (
                            "legacy_focus_filtered"
                            if focus_filter.get("has_strategic_focus")
                            else "legacy"
                        )
                    )
                    started_extra: dict = {
                        "queries_count": len(queries),
                        "picker": picker_name,
                    }
                    if (
                        not use_v2
                        and focus_filter.get("has_strategic_focus")
                    ):
                        started_extra["focus_filter"] = {
                            "products": sorted(
                                focus_filter.get("product_tokens") or [],
                            ),
                            "regions": sorted(
                                focus_filter.get("region_tokens") or [],
                            ),
                            "excluded": sorted(
                                focus_filter.get("excluded_tokens") or [],
                            )[:10],
                        }
                    if shadow_diff is not None:
                        started_extra["shadow_diff"] = shadow_diff
                    await log_event(
                        db, site_id, "competitor_discovery", "started",
                        f"Ищу конкурентов в Яндекс-выдаче по {len(queries)} запросам…",
                        extra=started_extra,
                        run_id=run_id,
                    )

                    profile = discover_competitors(
                        own_domain=site.domain,
                        queries=queries,
                        max_queries=max_queries,
                        top_k=top_k,
                        site_id=site_id,
                    )

                    # Persist: plain list of domains for the UI + full profile
                    # under target_config for drill-down.
                    # Serialize concurrent writers on target_config + re-read
                    # after acquiring the lock to avoid stomping siblings
                    # (business_truth, growth_opportunities).
                    from app.core_audit.sites.locks import lock_site_target_config
                    await lock_site_target_config(db, site_id)
                    await db.refresh(site)

                    cfg = dict(site.target_config or {})

                    # Respect owner's manual list. Once they've edited
                    # `competitor_domains` from the wizard / admin endpoint,
                    # `competitor_list_manually_edited_at` is set — and the
                    # nightly SERP-driven discovery must NOT silently
                    # overwrite their picks. Same for `competitor_brands`:
                    # weekly run used to wipe wizard-entered brands too.
                    # SERP-derived profile (per-query cache) is still
                    # written because it's auto-collected drill-down
                    # data, not user-curated.
                    manual_marker = cfg.get("competitor_list_manually_edited_at")
                    if not manual_marker:
                        site.competitor_domains = [
                            c.domain for c in profile.competitors
                        ]
                        cfg["competitor_brands"] = []
                    cfg["competitor_profile"] = profile.to_jsonb()
                    site.target_config = cfg

                    await db.commit()

                    await log_event(
                        db, site_id, "competitor_discovery", "done",
                        (
                            f"Разведка готова: найдено {len(profile.competitors)} "
                            f"конкурентов по {profile.queries_probed} запросам."
                        ),
                        extra={
                            "competitors_found": len(profile.competitors),
                            "top3": [c.domain for c in profile.competitors[:3]],
                            "cost_usd": round(profile.cost_usd, 4),
                        },
                        run_id=run_id,
                    )

                    # Chain: discovery done → fire deep-dive automatically
                    # Pass run_id through so the chained task's events
                    # join the same run group on the dashboard.
                    if profile.competitors:
                        try:
                            competitors_deep_dive_site_task.apply_async(
                                args=[site_id],
                                kwargs={"run_id": run_id},
                            )
                        except Exception as exc:  # noqa: BLE001
                            log.warning(
                                "competitors.discovery.chain_dive_failed "
                                "site=%s err=%s", site_id, exc,
                            )
                            await emit_terminal(
                                db, site_id, "competitor_deep_dive", "failed",
                                "Не удалось запустить глубокий анализ "
                                "(брокер задач недоступен).",
                                run_id=run_id,
                            )
                            await _skip_after_competitor_stop(
                                db,
                                site_id,
                                run_id,
                                reason="deep_dive_dispatch_failed",
                                include_deep_dive=False,
                            )
                            if not _queue_review_chain(site_id, run_id):
                                await _mark_review_chain_dispatch_failed(db, site_id, run_id)
                    else:
                        await emit_terminal(
                            db, site_id, "competitor_deep_dive", "skipped",
                            "Конкуренты не найдены — глубокий анализ пропущен.",
                            run_id=run_id,
                        )
                        await _skip_after_competitor_stop(
                            db,
                            site_id,
                            run_id,
                            reason="no_competitors_found",
                            include_deep_dive=False,
                        )
                        if not _queue_review_chain(site_id, run_id):
                            await _mark_review_chain_dispatch_failed(db, site_id, run_id)

                    return {
                        "status": "ok",
                        "site_id": site_id,
                        "queries_probed": profile.queries_probed,
                        "queries_with_results": profile.queries_with_results,
                        "competitors_found": len(profile.competitors),
                        "top3": [c.domain for c in profile.competitors[:3]],
                        "cost_usd": round(profile.cost_usd, 4),
                        "errors": profile.errors,
                        "deep_dive_queued": bool(profile.competitors),
                    }
                finally:
                    await db.execute(
                        text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key},
                    )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "competitors.discover.task_failed site=%s err=%s",
                site_id, exc,
            )
            # Close the stage + cascade dependent stages — otherwise the
            # UI stays in "идёт сейчас…" forever after a transient crash
            # (SERP rate-limit, network glitch). Watchdog can't recover
            # because dependent stages never receive a terminal event.
            try:
                async with task_session() as db2:
                    await emit_terminal(
                        db2, site_id, "competitor_discovery", "failed",
                        f"Разведка остановлена с ошибкой: {str(exc)[:200]}",
                        run_id=run_id,
                    )
                    await _skip_after_competitor_stop(
                        db2,
                        site_id,
                        run_id,
                        reason="discovery_crash",
                    )
                    if not _queue_review_chain(site_id, run_id):
                        await _mark_review_chain_dispatch_failed(db2, site_id, run_id)
            except Exception:  # noqa: BLE001
                pass  # best-effort — already logging the real error above
            return {"status": "error", "site_id": site_id, "err": str(exc)}

    return _run(_inner())


@celery_app.task(name="competitors_deep_dive_site", bind=True, max_retries=0)
def competitors_deep_dive_site_task(self, site_id: str, run_id: str | None = None) -> dict:
    """Crawl top competitor sites and write a structural comparison.

    Uses the persisted competitor_profile.competitors list — for each
    top competitor, visits the homepage + the example_url captured
    during discovery, then aggregates structural signals (price, CTA,
    reviews, schema types). Also analyzes the OWN site with the same
    extractor so the UI can show an apples-to-apples diff.

    Persists to sites.target_config.competitor_deep_dive (list of site
    reports, plus a 'self' entry).
    """
    from app.core_audit.competitors.content_gap import analyze_gaps
    from app.core_audit.competitors.deep_dive import (
        analyze_competitor_site,
    )
    from app.core_audit.competitors.opportunities import build_growth_opportunities

    async def _inner() -> dict:
        try:
            async with task_session() as db:
                site = await db.get(Site, UUID(site_id))
                if site is None:
                    await emit_terminal(
                        db, site_id, "competitor_deep_dive", "failed",
                        "Сайт не найден в базе.",
                        run_id=run_id,
                    )
                    await emit_terminal(
                        db, site_id, "opportunities", "skipped",
                        "Точки роста пропущены — сайт не найден.",
                        run_id=run_id,
                    )
                    if not _queue_review_chain(site_id, run_id):
                        await _mark_review_chain_dispatch_failed(db, site_id, run_id)
                    return {"status": "skipped", "reason": "site_not_found"}

                # Serialize target_config writers for the final persist
                # a few hundred lines down. Lock acquired upfront so the
                # read and write see a consistent snapshot.
                from app.core_audit.sites.locks import lock_site_target_config
                await lock_site_target_config(db, site_id)
                await db.refresh(site)

                cfg = dict(site.target_config or {})
                profile = cfg.get("competitor_profile") or {}
                competitors = profile.get("competitors") or []
                if not competitors:
                    await emit_terminal(
                        db, site_id, "competitor_deep_dive", "skipped",
                        "Нет найденных конкурентов — сначала запусти разведку.",
                        run_id=run_id,
                    )
                    await _skip_after_competitor_stop(
                        db,
                        site_id,
                        run_id,
                        reason="no_competitor_profile",
                        include_deep_dive=False,
                    )
                    if not _queue_review_chain(site_id, run_id):
                        await _mark_review_chain_dispatch_failed(db, site_id, run_id)
                    return {
                        "status": "skipped",
                        "reason": "no_competitor_profile",
                        "site_id": site_id,
                    }

                await log_event(
                    db, site_id, "competitor_deep_dive", "started",
                    f"Глубокий анализ: читаю сайты {min(5, len(competitors))} конкурентов…",
                    run_id=run_id,
                )

                # Pick a few of our own focus pages too. Comparing
                # competitor homepages against only our homepage creates
                # false gaps: the CTA/reviews/schema may live on the
                # Abkhazia landing, not on `/`.
                focus_filter = _business_tokens(site.target_config or {})
                focus_terms = set(focus_filter.get("product_tokens") or set())
                focus_terms.update(focus_filter.get("region_tokens") or set())
                own_page_seed_rows = (await db.execute(
                    select(Page.url, Page.path, Page.title, Page.h1)
                    .where(Page.site_id == site.id)
                    .limit(100),
                )).all()

                def _focus_score(row) -> int:
                    text_blob = " ".join(
                        str(v or "").lower()
                        for v in (row.url, row.path, row.title, row.h1)
                    )
                    return sum(1 for term in focus_terms if term in text_blob)

                own_focus_urls = [
                    r.url for r in sorted(
                        own_page_seed_rows,
                        key=lambda row: (-_focus_score(row), row.path or ""),
                    )
                    if r.url and _focus_score(r) > 0
                ][:3]

                # Top 5 competitors — crawl in parallel so one slow site
                # (findgid.ru observed at 10s while 4 others finished in 3s)
                # doesn't block the pipeline.
                from concurrent.futures import ThreadPoolExecutor, as_completed

                targets = [c for c in competitors[:5] if c.get("domain")]
                reports_by_domain: dict[str, dict] = {}

                def _run_one(c: dict) -> tuple[str, dict]:
                    # Homepage + the ranking example URL. The example URL is
                    # the page Yandex actually rewarded for our query, so it
                    # is the right comparison surface for SEO advice.
                    rep = analyze_competitor_site(
                        domain=c["domain"],
                        urls=[c.get("example_url") or ""],
                        max_pages=2,
                    )
                    return c["domain"], rep.to_dict()

                with ThreadPoolExecutor(max_workers=5) as pool:
                    fut_map = {pool.submit(_run_one, c): c for c in targets}
                    # Also kick off the own-site crawl alongside the
                    # competitors — it's just one more HTTP fetch.
                    own_future = pool.submit(
                        lambda: analyze_competitor_site(
                            domain=site.domain,
                            urls=own_focus_urls,
                            max_pages=4,
                        ).to_dict(),
                    )
                    done_count = 0
                    for fut in as_completed(fut_map):
                        domain, rep_dict = fut.result()
                        reports_by_domain[domain] = rep_dict
                        done_count += 1
                        await log_event(
                            db, site_id, "competitor_deep_dive", "progress",
                            f"Готов {domain} ({done_count}/{len(targets)})…",
                            run_id=run_id,
                        )
                    own_page = own_future.result()

                # Preserve original ranking order when emitting reports.
                reports = [
                    reports_by_domain[c["domain"]]
                    for c in targets
                    if c["domain"] in reports_by_domain
                ]

                cfg["competitor_deep_dive"] = {
                    "competitors": reports,
                    "self": own_page,
                }

                # Close the deep-dive stage BEFORE moving on to build
                # opportunities. Without this, /activity/last returns
                # a stale "progress" as the stage's latest status, and
                # any hasRunning() check across work stages would keep
                # the UI in a spinner forever.
                # Note: this does NOT close the pipeline — pipeline
                # waits for opportunities per _should_close_pipeline().
                successful_pages = sum(
                    1 for r in reports
                    for p in r.get("pages", [])
                    if p.get("status") == "ok"
                )
                await emit_terminal(
                    db, site_id, "competitor_deep_dive", "done",
                    (
                        f"Глубокий анализ: {len(reports)} конкурентов "
                        f"разобрано ({successful_pages} страниц успешно)."
                    ),
                    extra={
                        "competitors_crawled": len(reports),
                        "successful_pages": successful_pages,
                    },
                    run_id=run_id,
                )

                # Build growth opportunities from (cached gaps + fresh deep-dive).
                # Gaps come from the per-query SERP cache captured by discovery.
                query_serps = profile.get("query_serps") or {}
                gap_dicts = []
                if query_serps:
                    gaps = analyze_gaps(
                        own_domain=site.domain,
                        competitor_domains=list(site.competitor_domains or []),
                        query_to_serp=query_serps,
                        top_k_gaps=25,
                    )
                    gap_dicts = [g.to_dict() for g in gaps]

                # Pull our own crawled pages so we can check whether the
                # site already has a page for each gap query. This turns
                # "create new page" into "strengthen existing page" when
                # we already cover the topic.
                # Filter is intentionally loose: in_index comes from Yandex
                # indexation polling and is often False for freshly crawled
                # pages. A title is the minimal signal we need for matching.
                page_stmt = select(
                    Page.url, Page.path, Page.title, Page.h1,
                    Page.meta_description, Page.content_text,
                ).where(Page.site_id == site.id, Page.title.is_not(None))
                page_rows = (await db.execute(page_stmt)).all()
                own_pages_dicts = [
                    {
                        "url": r.url,
                        "path": r.path,
                        "title": r.title,
                        "h1": r.h1,
                        "meta_description": r.meta_description,
                        "content_snippet": (r.content_text or "")[:600],
                    }
                    for r in page_rows
                ]

                opportunities = build_growth_opportunities(
                    content_gaps=gap_dicts,
                    deep_dive_self=own_page,
                    deep_dive_competitors=reports,
                    own_pages=own_pages_dicts,
                    max_items=15,
                )
                cfg["growth_opportunities"] = opportunities
                site.target_config = cfg
                await db.commit()

                # Emit opportunities:done and close the pipeline (if one
                # was opened by the full-analysis button). emit_terminal
                # handles the "is there an open pipeline?" check — no
                # ad-hoc lookups here anymore.
                await emit_terminal(
                    db, site_id, "opportunities", "done",
                    (
                        f"Готово: {len(opportunities)} точек роста, "
                        f"проверено {len(own_pages_dicts)} твоих страниц."
                    ),
                    extra={
                        "opportunities": len(opportunities),
                        "own_pages": len(own_pages_dicts),
                        "competitors_crawled": len(reports),
                    },
                    run_id=run_id,
                )
                if not _queue_review_chain(site_id, run_id):
                    await _mark_review_chain_dispatch_failed(db, site_id, run_id)

                return {
                    "status": "ok",
                    "site_id": site_id,
                    "competitors_crawled": len(reports),
                    "successful_pages": sum(
                        1 for r in reports
                        for p in r.get("pages", [])
                        if p.get("status") == "ok"
                    ),
                    "opportunities_generated": len(opportunities),
                    "own_pages_scanned": len(own_pages_dicts),
                }
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "competitors.deep_dive.task_failed site=%s err=%s",
                site_id, exc,
            )
            try:
                async with task_session() as db2:
                    await emit_terminal(
                        db2, site_id, "competitor_deep_dive", "failed",
                        f"Глубокий анализ остановлен: {str(exc)[:200]}",
                        run_id=run_id,
                    )
                    await _skip_after_competitor_stop(
                        db2,
                        site_id,
                        run_id,
                        reason="deep_dive_failed",
                        include_deep_dive=False,
                    )
                    if not _queue_review_chain(site_id, run_id):
                        await _mark_review_chain_dispatch_failed(db2, site_id, run_id)
            except Exception:  # noqa: BLE001
                pass
            return {"status": "error", "site_id": site_id, "err": str(exc)}

    return _run(_inner())


@celery_app.task(name="competitors_discover_all_weekly", bind=True, max_retries=0)
def competitors_discover_all_weekly_task(self) -> dict:
    """Weekly refresh of competitor lists + deep-dive for every active site.

    Loops over active sites, queues discovery with a 3-minute gap between
    sites so the shared SERP + Haiku quotas don't spike. Discovery
    auto-chains deep-dive, so this one task refreshes both halves of the
    competitor picture for every site.
    """
    async def _inner() -> dict:
        try:
            async with task_session() as db:
                result = await db.execute(
                    select(Site.id, Site.domain).where(Site.is_active.is_(True)),
                )
                rows = result.all()

            queued: list[str] = []
            for i, row in enumerate(rows):
                competitors_discover_site_task.apply_async(
                    args=[str(row.id)],
                    countdown=i * 180,
                )
                queued.append(row.domain)
            return {"status": "ok", "queued": queued}
        except Exception as exc:  # noqa: BLE001
            log.warning("competitors.weekly_all.failed err=%s", exc)
            return {"status": "error", "err": str(exc)}

    return _run(_inner())


__all__ = [
    "competitors_discover_site_task",
    "competitors_deep_dive_site_task",
    "competitors_discover_all_weekly_task",
]
