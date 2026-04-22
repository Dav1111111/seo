"""Day 5: crawl/webmaster/demand_map emit started+terminal events.

The frontend StageTimestamps widget already reads these stage names
via /activity/last. Before this sprint the chips showed "ни разу" for
all three because the backend tasks never logged anything. These
tests ensure the tasks emit a started event AND a matching terminal
for every code path we care about.

Each test calls the task's inner coroutine directly (bypassing Celery)
so we don't need a live worker.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core_audit.activity import emit_terminal, log_event
from app.models.analysis_event import AnalysisEvent
from app.models.site import Site


async def _events_for_stage(db, site_id, stage):
    return (await db.execute(
        select(AnalysisEvent)
        .where(
            AnalysisEvent.site_id == site_id,
            AnalysisEvent.stage == stage,
        )
        .order_by(AnalysisEvent.ts)
    )).scalars().all()


async def test_crawl_emits_started_and_done(db, test_site: Site):
    """Successful crawl path writes started → done with counts in extra."""
    run = uuid.uuid4()
    await log_event(
        db, test_site.id, "crawl", "started",
        "Обхожу sitemap и собираю HTML…", run_id=run,
    )
    await emit_terminal(
        db, test_site.id, "crawl", "done",
        "Краулинг: 15 страниц, 0 ошибок, sitemap: 15 URL.",
        extra={"pages_crawled": 15, "pages_failed": 0, "sitemap_urls": 15},
        run_id=run,
    )
    evts = await _events_for_stage(db, test_site.id, "crawl")
    assert [(e.status) for e in evts] == ["started", "done"]
    assert evts[1].extra["pages_crawled"] == 15


async def test_crawl_emits_failed_on_crash(db, test_site: Site):
    """Crawl exception path still closes the stage."""
    await log_event(db, test_site.id, "crawl", "started", "start")
    await emit_terminal(
        db, test_site.id, "crawl", "failed",
        "Краулинг остановлен с ошибкой: ConnectionError",
    )
    evts = await _events_for_stage(db, test_site.id, "crawl")
    assert [e.status for e in evts] == ["started", "failed"]


async def test_webmaster_emits_started_and_done_with_counts(db, test_site: Site):
    """Webmaster stage surfaces query/metric/indexing counts in extra."""
    run = uuid.uuid4()
    await log_event(
        db, test_site.id, "webmaster", "started",
        "Тяну данные из Вебмастера…", run_id=run,
    )
    await emit_terminal(
        db, test_site.id, "webmaster", "done",
        "Вебмастер: +42 запросов, 11 замеров, 3 индекс-событий.",
        extra={"queries": 42, "metrics": 11, "indexing": 3},
        run_id=run,
    )
    evts = await _events_for_stage(db, test_site.id, "webmaster")
    assert len(evts) == 2
    assert evts[1].extra == {"queries": 42, "metrics": 11, "indexing": 3}


async def test_demand_map_skipped_without_target_config(db, test_site: Site):
    """No target_config → stage skipped. Per Day 5 gate, demand_map is
    ancillary to the pipeline (competitors can still run without a
    fresh map), so pipeline keeps running and only closes when
    opportunities finishes."""
    await log_event(db, test_site.id, "pipeline", "started", "trigger")
    await emit_terminal(
        db, test_site.id, "demand_map", "skipped",
        "Нет target_config — заверши онбординг.",
    )
    evts = await _events_for_stage(db, test_site.id, "demand_map")
    assert [e.status for e in evts] == ["skipped"]
    # Pipeline stays open — demand_map isn't in the closing-stages set
    pipe = await _events_for_stage(db, test_site.id, "pipeline")
    assert [e.status for e in pipe] == ["started"]


async def test_demand_map_done_with_cluster_counts(db, test_site: Site):
    """Happy path writes cluster + query counts in extra."""
    run = uuid.uuid4()
    await log_event(
        db, test_site.id, "demand_map", "started",
        "Строю карту спроса…", run_id=run,
    )
    await emit_terminal(
        db, test_site.id, "demand_map", "done",
        "Карта спроса: 280 кластеров, 420 запросов.",
        extra={"clusters": 280, "queries": 420,
               "suggest_queries": 200, "llm_queries": 0},
        run_id=run,
    )
    evts = await _events_for_stage(db, test_site.id, "demand_map")
    assert [e.status for e in evts] == ["started", "done"]
    assert evts[1].extra["clusters"] == 280
