"""Tests for the connector registry — structural guarantees only.

We don't mock individual check functions here: the point of the registry
is each check is self-contained and live-tested via the API. What we
DO guarantee structurally:

  - Every connector has a unique id — a UI keyed by id breaks otherwise
  - Every connector belongs to one of the known categories — UI groups by
  - `describe_connector` detects missing-config and sets `configured` flag
  - `_timed` wraps exceptions into CheckResult(ok=False) without raising
"""

from __future__ import annotations

from unittest.mock import patch

from app.health.connectors import (
    CONNECTORS,
    CONNECTORS_BY_ID,
    CheckResult,
    Connector,
    _timed,
    describe_connector,
)


KNOWN_CATEGORIES = {"infra", "llm", "yandex_cloud", "yandex_oauth", "protocol"}


def test_every_connector_has_unique_id() -> None:
    ids = [c.id for c in CONNECTORS]
    assert len(ids) == len(set(ids)), f"duplicate connector ids: {ids}"


def test_every_connector_id_follows_dot_namespace() -> None:
    """IDs like 'yandex_cloud.wordstat.dynamics' — UI splits on the
    first dot for category badge, so this convention is load-bearing."""
    for c in CONNECTORS:
        assert "." in c.id, f"{c.id} must have a namespace prefix"


def test_registry_by_id_matches_list() -> None:
    assert len(CONNECTORS_BY_ID) == len(CONNECTORS)
    for c in CONNECTORS:
        assert CONNECTORS_BY_ID[c.id] is c


def test_every_category_is_known() -> None:
    for c in CONNECTORS:
        assert c.category in KNOWN_CATEGORIES, (
            f"{c.id} has unknown category {c.category!r}"
        )


def test_every_connector_has_ru_description() -> None:
    """UI shows description_ru under the connector name.
    Empty string is worse than missing — fail loud."""
    for c in CONNECTORS:
        assert c.description_ru.strip(), f"{c.id} has empty description"
        assert len(c.description_ru) > 20, (
            f"{c.id} description too short — say what this integration is for"
        )


def test_describe_connector_flags_missing_settings() -> None:
    """When `requires` points at an empty setting, `configured` is False
    and UI can show «не настроено» instead of running a doomed check."""
    fake = Connector(
        id="test.missing",
        category="infra",
        name="X",
        description_ru="A test connector meant to expose missing-setting behaviour",
        check=lambda: CheckResult(ok=True, latency_ms=0),
        requires=("__NONEXISTENT_SETTING__",),
    )
    desc = describe_connector(fake)
    assert desc["configured"] is False
    assert desc["missing_setting"] == "__NONEXISTENT_SETTING__"


def test_describe_connector_reports_configured_when_no_requires() -> None:
    fake = Connector(
        id="test.noreq",
        category="infra",
        name="X",
        description_ru="Connector with no required settings — always reported configured",
        check=lambda: CheckResult(ok=True, latency_ms=0),
    )
    desc = describe_connector(fake)
    assert desc["configured"] is True
    assert desc["missing_setting"] is None


def test_timed_wraps_exceptions_without_raising() -> None:
    """If a check body throws, we must get a CheckResult(ok=False) back
    — never a 500 at the API level. This is the contract."""

    def boom():
        raise RuntimeError("network exploded")

    r = _timed(boom)
    assert r.ok is False
    assert r.error is not None
    assert "RuntimeError" in r.error
    assert r.latency_ms >= 0


def test_timed_measures_latency() -> None:
    import time

    def slow_ok():
        time.sleep(0.02)
        return True, {"marker": 1}, None

    r = _timed(slow_ok)
    assert r.ok is True
    assert r.latency_ms >= 15   # some variance tolerated


def test_checkresult_serialises_to_json() -> None:
    import json

    r = CheckResult(
        ok=True,
        latency_ms=42,
        sample_data={"answer": 42},
        error=None,
    )
    payload = r.to_dict()
    round_tripped = json.loads(json.dumps(payload))
    assert round_tripped["ok"] is True
    assert round_tripped["latency_ms"] == 42
    assert round_tripped["sample_data"] == {"answer": 42}
    # checked_at must be iso string
    assert "T" in round_tripped["checked_at"]


def test_wordstat_endpoints_all_have_folder_id_dependency() -> None:
    """Wordstat API refuses requests without folderId — ensure every
    Wordstat check declares Search API key requirement (which implies
    the folder too). Catches a future regression where someone adds a
    new wordstat endpoint but forgets `requires`."""
    wordstat = [c for c in CONNECTORS if c.id.startswith("yandex_cloud.wordstat.")]
    assert len(wordstat) >= 3, "expected ≥3 wordstat endpoints in registry"
    for c in wordstat:
        assert "YANDEX_SEARCH_API_KEY" in c.requires
