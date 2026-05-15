"""Tri-state semantics for wordstat.fetch_volume.

Regression coverage for the silent «empty == error» bug found
2026-05-15: empty result from Wordstat (no demand) must be
distinguishable from a genuine API error, so the weekly Celery beat
doesn't re-fetch the same legitimately-empty query forever.

Live probe on prod that triggered this fix:
  - `багги абхазия`     → volume=112  (status=ok)
  - `багги сочи гранд`  → 200 + empty rows (status=empty, NOT error)
  - `https://www...`    → 400, URL-not-phrase (status=invalid_phrase)

Before the fix the second and third both collapsed to None, and the
task wrote nothing to `wordstat_updated_at`, so the next beat tried
them again. After the fix:
  - status=empty → volume=0 written, timestamp stamped
  - status=invalid_phrase → timestamp stamped (data-quality warning)
  - status=error → row untouched, retried on next beat
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

from app.collectors.wordstat import (
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_INVALID_PHRASE,
    STATUS_OK,
    WordstatFetchOutcome,
    fetch_volume,
)


# ── Test doubles ───────────────────────────────────────────────────────


def _ok_response(payload: dict):
    """Fake urllib response usable as a context manager."""
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def getcode(self):
            return 200

        def read(self):
            return json.dumps(payload).encode("utf-8")

    return FakeResp()


@pytest.fixture
def creds():
    """Patch settings so the API key / folder gates don't trip."""
    with patch("app.collectors.wordstat.settings") as s:
        s.YANDEX_SEARCH_API_KEY = "k"
        s.YANDEX_CLOUD_FOLDER_ID = "f"
        yield s


# ── status="ok" — happy path ───────────────────────────────────────────


def test_fetch_volume_ok_returns_status_ok_with_volume(creds) -> None:
    """Successful response with counts → status=ok, volume>0."""
    payload = {
        "results": [
            {"date": "2025-04-01T00:00:00Z", "count": "1000"},
            {"date": "2025-05-01T00:00:00Z", "count": "2000"},
        ],
    }
    with patch(
        "urllib.request.urlopen", return_value=_ok_response(payload),
    ):
        outcome = fetch_volume("багги абхазия")

    assert isinstance(outcome, WordstatFetchOutcome)
    assert outcome.status == STATUS_OK
    assert outcome.volume == 3000
    assert outcome.error is None
    assert outcome.from_date == "2025-05-01T00:00:00Z"
    assert outcome.latest_date == "2025-05-01T00:00:00Z"
    assert outcome.is_actionable is True


# ── status="empty" — API said legitimately zero ────────────────────────


def test_fetch_volume_empty_results_returns_status_empty_zero_volume(creds) -> None:
    """200 with empty results array → status=empty, volume=0 — NOT None.

    This is the case for `багги сочи гранд` on prod: Wordstat is
    answering "no demand for this combination", not failing. We need
    to write volume=0 + stamp wordstat_updated_at so the weekly beat
    doesn't keep re-fetching it forever.
    """
    with patch(
        "urllib.request.urlopen", return_value=_ok_response({"results": []}),
    ):
        outcome = fetch_volume("багги сочи гранд")

    assert outcome.status == STATUS_EMPTY
    assert outcome.volume == 0
    assert outcome.trend == []
    assert outcome.error is None
    # is_actionable=True means the caller should stamp the timestamp.
    assert outcome.is_actionable is True


def test_fetch_volume_only_null_counts_returns_status_empty(creds) -> None:
    """Rows with `date` but no `count` (all months no-data) → empty,
    not error. Same actionable answer as an empty results array."""
    payload = {
        "results": [
            {"date": "2025-08-01T00:00:00Z"},
            {"date": "2025-09-01T00:00:00Z"},
        ],
    }
    with patch(
        "urllib.request.urlopen", return_value=_ok_response(payload),
    ):
        outcome = fetch_volume("очень редкая фраза")

    assert outcome.status == STATUS_EMPTY
    assert outcome.volume == 0


# ── status="error" — transient, do NOT touch the row ───────────────────


def test_fetch_volume_http_500_returns_status_error_with_code(creds) -> None:
    """5xx → status=error with http_code set, NOT silent None."""
    err = HTTPError(
        "https://x", 500, "Internal Server Error", {},
        BytesIO(b"server boom"),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        outcome = fetch_volume("phrase")

    assert outcome.status == STATUS_ERROR
    assert outcome.http_code == 500
    assert outcome.error is not None
    assert outcome.is_actionable is False  # caller must NOT write


def test_fetch_volume_http_400_returns_status_error(creds) -> None:
    """4xx → status=error too. Distinct from invalid_phrase: 400 means
    the server rejected something we couldn't predict; invalid_phrase
    is a boundary check we DID predict."""
    err = HTTPError(
        "https://x", 400, "Bad Request", {},
        BytesIO(b'{"error":"bad enum"}'),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        outcome = fetch_volume("phrase")

    assert outcome.status == STATUS_ERROR
    assert outcome.http_code == 400


def test_fetch_volume_network_error_returns_status_error(creds) -> None:
    """URLError (DNS, socket, timeout) → status=error, http_code=None."""
    with patch("urllib.request.urlopen", side_effect=URLError("name resolution")):
        outcome = fetch_volume("phrase")

    assert outcome.status == STATUS_ERROR
    assert outcome.http_code is None  # no HTTP code on network error
    assert "network" in (outcome.error or "")


# ── status="invalid_phrase" — boundary check, no API call ──────────────


def test_fetch_volume_url_phrase_rejected_without_api_call() -> None:
    """URL-shaped phrases must be rejected at the boundary — don't
    even call the API. This is the `https://www.grandtourspirit.ru`
    case from the prod probe."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        outcome = fetch_volume("https://www.grandtourspirit.ru")

    assert outcome.status == STATUS_INVALID_PHRASE
    assert outcome.error is not None
    mock_urlopen.assert_not_called()


def test_fetch_volume_http_url_rejected_without_api_call() -> None:
    """http:// variant — same boundary check."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        outcome = fetch_volume("http://example.com/page")

    assert outcome.status == STATUS_INVALID_PHRASE
    mock_urlopen.assert_not_called()


def test_fetch_volume_www_url_rejected_without_api_call() -> None:
    """Bare `www.` prefix also rejected — Wordstat will 400 these too."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        outcome = fetch_volume("www.example.com")

    assert outcome.status == STATUS_INVALID_PHRASE
    mock_urlopen.assert_not_called()


def test_fetch_volume_scheme_anywhere_rejected() -> None:
    """`://` anywhere in the string → URL-like, reject."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        outcome = fetch_volume("ftp://files.example.com")

    assert outcome.status == STATUS_INVALID_PHRASE
    mock_urlopen.assert_not_called()


def test_fetch_volume_empty_phrase_returns_invalid() -> None:
    """Empty/whitespace phrase → invalid_phrase, no API call."""
    with patch("urllib.request.urlopen") as mock_urlopen:
        out_empty = fetch_volume("")
        out_blank = fetch_volume("   \t  ")

    assert out_empty.status == STATUS_INVALID_PHRASE
    assert out_blank.status == STATUS_INVALID_PHRASE
    mock_urlopen.assert_not_called()


# ── is_actionable property — what the Celery task gates on ─────────────


def test_outcome_is_actionable_for_ok_and_empty(creds) -> None:
    """ok + empty + invalid_phrase → is_actionable=True so the task
    stamps `wordstat_updated_at`. error → False so the task leaves the
    row for retry next beat."""
    ok_payload = {
        "results": [{"date": "2025-06-01T00:00:00Z", "count": "10"}],
    }

    with patch("urllib.request.urlopen", return_value=_ok_response(ok_payload)):
        ok = fetch_volume("seed")
    assert ok.status == STATUS_OK
    assert ok.is_actionable is True

    with patch(
        "urllib.request.urlopen", return_value=_ok_response({"results": []}),
    ):
        empty = fetch_volume("seed")
    assert empty.status == STATUS_EMPTY
    assert empty.is_actionable is True

    invalid = fetch_volume("https://example.com")
    assert invalid.status == STATUS_INVALID_PHRASE
    assert invalid.is_actionable is True

    err = HTTPError("https://x", 503, "down", {}, BytesIO(b""))
    with patch("urllib.request.urlopen", side_effect=err):
        error = fetch_volume("seed")
    assert error.status == STATUS_ERROR
    assert error.is_actionable is False


# ── Always has a timestamp (caller stamps from it) ─────────────────────


def test_every_outcome_carries_fetched_at(creds) -> None:
    """`fetched_at` is the source the Celery task uses for
    `wordstat_updated_at`. It must be populated on every outcome —
    even error/invalid — so the task always has a value to stamp."""
    with patch("urllib.request.urlopen", return_value=_ok_response({"results": []})):
        empty = fetch_volume("seed")
    assert empty.fetched_at is not None

    with patch("urllib.request.urlopen", side_effect=URLError("boom")):
        err = fetch_volume("seed")
    assert err.fetched_at is not None

    invalid = fetch_volume("https://example.com")
    assert invalid.fetched_at is not None


# ── Backwards-compat alias still works ─────────────────────────────────


def test_count_property_aliases_volume(creds) -> None:
    """Older callers may still read `.count` — preserved as a property
    so they keep working through the dataclass rename."""
    payload = {
        "results": [
            {"date": "2025-04-01T00:00:00Z", "count": "7"},
            {"date": "2025-05-01T00:00:00Z", "count": "3"},
        ],
    }
    with patch("urllib.request.urlopen", return_value=_ok_response(payload)):
        outcome = fetch_volume("seed")

    assert outcome.count == outcome.volume == 10
