"""Tests for Metrica collector skip-signal handling.

Covers the bugs found in audit 2026-05-15:

  - silent skip when the `bytime` response is missing `data` /
    `time_intervals` (no log, no `stats["errors"]` append, owner
    saw `traffic_days=0` and assumed «нет трафика»),
  - silent skip when the `metrics` array in the response is shorter
    than the 4 we asked for (same failure mode),
  - `counter_code_status` from `fetch_counter_info` is propagated
    into `stats["counter"]` so the surrounding Celery task can
    convert non-CS_OK into a terminal `skipped` instead of `done`.

These tests stay pure-Python: we mock `self.get` to return shaped
responses, and bypass the DB by stubbing `_page_lookup` + `db.execute`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.collectors.metrica import MetricaCollector


def _site_id() -> uuid.UUID:
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


def _collector() -> MetricaCollector:
    return MetricaCollector(oauth_token="fake", counter_id="12345")


def _ok_counter_payload(code_status: str = "CS_OK") -> dict[str, Any]:
    """Shape of /management/v1/counter/<id> we care about."""
    return {
        "counter": {
            "status": "Active",
            "activity_status": "high",
            "code_status": code_status,
            "site": "example.com",
        },
    }


async def _make_get_side_effect(
    bytime_payload: dict[str, Any],
    code_status: str = "CS_OK",
):
    """Return an async side_effect for `self.get` that routes by path."""

    async def _side_effect(path: str, params: Any = None) -> dict[str, Any]:
        if path.startswith("/management/v1/counter/") and path.endswith("/goals"):
            return {"goals": []}
        if path.startswith("/management/v1/counter/"):
            return _ok_counter_payload(code_status=code_status)
        if path == "/stat/v1/data/bytime":
            return bytime_payload
        # landing pages, traffic sources — empty
        if path == "/stat/v1/data":
            return {"data": []}
        return {}

    return _side_effect


@pytest.mark.asyncio
async def test_site_traffic_unexpected_shape_logs_and_appends_error(caplog):
    """Unexpected `bytime` response shape must produce a log + stats.errors entry, not silent zero.

    Reproduces: Metrica returns an empty `data` array or no
    `time_intervals` (transient outage / API change). Previous code
    fell through silently — `stats["traffic_days"]` stayed 0 and no
    error appeared, so the owner saw «нет трафика» where the truth was
    «we didn't get a response».
    """
    coll = _collector()
    bad_payload = {"data": [], "time_intervals": []}
    side = await _make_get_side_effect(bad_payload)

    db = AsyncMock()
    db.execute = AsyncMock()

    with patch.object(coll, "get", side_effect=side), \
         patch.object(coll, "_page_lookup", AsyncMock(return_value=({}, {}))), \
         caplog.at_level(logging.WARNING, logger="app.collectors.metrica"):
        stats = await coll.collect_and_store(
            db,
            _site_id(),
            days_back=3,
            site_domain="example.com",
        )

    site_traffic_errors = [
        e for e in stats["errors"] if e.get("step") == "site_traffic"
    ]
    assert site_traffic_errors, "expected stats.errors to include a site_traffic entry"
    assert "unexpected API shape" in site_traffic_errors[0]["error"]
    assert stats["traffic_days"] == 0
    assert any(
        "unexpected API shape" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    )


@pytest.mark.asyncio
async def test_metrics_list_too_short_logs_and_appends_error(caplog):
    """If Metrica returns fewer than 4 metrics in bytime, must log + append error, not silently zero.

    The bytime call asks for 4 metrics (visits, pageviews, bounce,
    duration). If the API ever returns fewer (partial response,
    quota throttle, schema change), the old code skipped writing
    *anything* without leaving a trace.
    """
    coll = _collector()
    # Only 2 metrics returned instead of 4.
    short_payload = {
        "time_intervals": [["2026-05-10", "2026-05-10"]],
        "data": [
            {
                "dimensions": [],
                "metrics": [[1], [2]],  # only 2, not 4
            }
        ],
    }
    side = await _make_get_side_effect(short_payload)

    db = AsyncMock()
    db.execute = AsyncMock()

    with patch.object(coll, "get", side_effect=side), \
         patch.object(coll, "_page_lookup", AsyncMock(return_value=({}, {}))), \
         caplog.at_level(logging.WARNING, logger="app.collectors.metrica"):
        stats = await coll.collect_and_store(
            db,
            _site_id(),
            days_back=1,
            site_domain="example.com",
        )

    site_traffic_errors = [
        e for e in stats["errors"] if e.get("step") == "site_traffic"
    ]
    assert site_traffic_errors, "expected stats.errors to include a site_traffic entry"
    assert "metrics list" in site_traffic_errors[0]["error"]
    assert stats["traffic_days"] == 0
    assert any(
        "unexpected metrics shape" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    )


@pytest.mark.asyncio
async def test_counter_status_cs_err_unknown_surfaces_in_stats(caplog):
    """CS_ERR_UNKNOWN from fetch_counter_info must be visible in stats so terminal logic can mark `skipped`.

    The Celery task `collect_site_metrica` inspects
    `stats["counter"]["counter_code_status"]` to decide between
    terminal `done` and `skipped`. If that field gets dropped from
    stats, the task will silently report `done` on a broken counter.
    """
    coll = _collector()
    # Valid bytime shape (counter is broken, but the API still
    # returns *some* structure with zeros).
    bytime_payload = {
        "time_intervals": [["2026-05-10", "2026-05-10"]],
        "data": [
            {
                "dimensions": [],
                "metrics": [[0], [0], [0], [0]],
            }
        ],
    }
    side = await _make_get_side_effect(
        bytime_payload, code_status="CS_ERR_UNKNOWN",
    )

    db = AsyncMock()
    db.execute = AsyncMock()

    with patch.object(coll, "get", side_effect=side), \
         patch.object(coll, "_page_lookup", AsyncMock(return_value=({}, {}))):
        stats = await coll.collect_and_store(
            db,
            _site_id(),
            days_back=1,
            site_domain="example.com",
        )

    # `counter` is built from non-None counter_extra entries; the
    # task reads counter_code_status from there to decide terminal.
    assert stats["counter"].get("counter_code_status") == "CS_ERR_UNKNOWN", (
        "CS_ERR_UNKNOWN must propagate into stats['counter'] so the "
        "Celery task can emit a `skipped` terminal instead of `done`."
    )
