"""Tests for app.collectors.wordstat.

Mocks urllib so tests don't depend on network and run instantly. The
contract these tests pin down is what the Studio /queries module
relies on:

  - empty / blank phrase short-circuits without an API call
    (returns status="invalid_phrase")
  - missing API key / folder short-circuits without an API call
    (returns status="error" — no creds is treated as a failure to
    fetch, not a data-quality bug)
  - HTTP 4xx returns status="error" (not raise)
  - empty `results` array returns status="empty" (NOT an error —
    Wordstat is telling us "no demand", a real answer)
  - results with all-null counts returns status="empty"
  - successful response: volume is the SUM of monthly counts, NOT the
    last month's count alone (this is the easy bug to introduce later)
  - trend is preserved per-month including months with null counts so
    UI can render data gaps
  - the `from_date` we expose is the LATEST month, not the earliest

See also `test_wordstat_empty_vs_error.py` — narrower regression
suite for the tri-state semantics added 2026-05-15.
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from app.collectors.wordstat import (
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_INVALID_PHRASE,
    STATUS_OK,
    TREND_MONTHS,
    WordstatFetchOutcome,
    WordstatTopRequest,
    WordstatVolume,
    fetch_top_requests,
    fetch_volume,
)


def _ok_response(payload: dict):
    """Build a fake urllib response object that the with-statement uses."""
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


# ── Short-circuit guards (no network call) ─────────────────────────────

def test_empty_phrase_short_circuits_without_calling_network() -> None:
    with patch("urllib.request.urlopen") as mock:
        out_empty = fetch_volume("")
        out_blank = fetch_volume("   ")
        assert out_empty.status == STATUS_INVALID_PHRASE
        assert out_blank.status == STATUS_INVALID_PHRASE
    mock.assert_not_called()


def test_missing_api_key_short_circuits_without_calling_network() -> None:
    with patch("urllib.request.urlopen") as mock, \
         patch("app.collectors.wordstat.settings") as fake_settings:
        fake_settings.YANDEX_SEARCH_API_KEY = ""
        fake_settings.YANDEX_CLOUD_FOLDER_ID = "folder123"
        result = fetch_volume("багги абхазия")
        assert result.status == STATUS_ERROR
        assert "YANDEX_SEARCH_API_KEY" in (result.error or "")
    mock.assert_not_called()


def test_missing_folder_id_short_circuits_without_calling_network() -> None:
    with patch("urllib.request.urlopen") as mock, \
         patch("app.collectors.wordstat.settings") as fake_settings:
        fake_settings.YANDEX_SEARCH_API_KEY = "key123"
        fake_settings.YANDEX_CLOUD_FOLDER_ID = ""
        result = fetch_volume("багги абхазия")
        assert result.status == STATUS_ERROR
    mock.assert_not_called()


# ── Error responses ────────────────────────────────────────────────────

def test_http_400_returns_status_error() -> None:
    err = HTTPError(
        "https://x", 400, "Bad Request", {},
        BytesIO(b'{"error": "bad enum value"}'),
    )
    with patch("urllib.request.urlopen", side_effect=err), \
         patch("app.collectors.wordstat.settings") as s:
        s.YANDEX_SEARCH_API_KEY = "k"
        s.YANDEX_CLOUD_FOLDER_ID = "f"
        result = fetch_volume("phrase")
        assert result.status == STATUS_ERROR
        assert result.http_code == 400


def test_network_error_returns_status_error() -> None:
    with patch("urllib.request.urlopen", side_effect=URLError("dns")), \
         patch("app.collectors.wordstat.settings") as s:
        s.YANDEX_SEARCH_API_KEY = "k"
        s.YANDEX_CLOUD_FOLDER_ID = "f"
        result = fetch_volume("phrase")
        assert result.status == STATUS_ERROR
        assert "network" in (result.error or "")


# ── Empty / no-data responses ──────────────────────────────────────────

def test_empty_results_array_returns_status_empty() -> None:
    with patch(
        "urllib.request.urlopen",
        return_value=_ok_response({"results": []}),
    ), patch("app.collectors.wordstat.settings") as s:
        s.YANDEX_SEARCH_API_KEY = "k"
        s.YANDEX_CLOUD_FOLDER_ID = "f"
        result = fetch_volume("very rare phrase")
        assert result.status == STATUS_EMPTY
        assert result.volume == 0


def test_results_with_only_null_counts_returns_status_empty() -> None:
    """Yandex returns rows with `date` but no `count` for months with
    no data. If ALL rows are like that, treat as no-volume."""
    payload = {
        "results": [
            {"date": "2025-08-01T00:00:00Z"},
            {"date": "2025-09-01T00:00:00Z"},
            {"date": "2025-10-01T00:00:00Z"},
        ],
    }
    with patch(
        "urllib.request.urlopen", return_value=_ok_response(payload),
    ), patch("app.collectors.wordstat.settings") as s:
        s.YANDEX_SEARCH_API_KEY = "k"
        s.YANDEX_CLOUD_FOLDER_ID = "f"
        result = fetch_volume("rare phrase")
        assert result.status == STATUS_EMPTY
        assert result.volume == 0


# ── Successful aggregation ─────────────────────────────────────────────

def test_success_aggregates_counts_across_months() -> None:
    """`count` MUST be the sum of all monthly counts, not the latest
    one. Easy bug to slip in later — pin it down."""
    payload = {
        "results": [
            {"date": "2025-04-01T00:00:00Z", "count": "1000"},
            {"date": "2025-05-01T00:00:00Z", "count": "1500"},
            {"date": "2025-06-01T00:00:00Z", "count": "2500"},
        ],
    }
    with patch(
        "urllib.request.urlopen", return_value=_ok_response(payload),
    ), patch("app.collectors.wordstat.settings") as s:
        s.YANDEX_SEARCH_API_KEY = "k"
        s.YANDEX_CLOUD_FOLDER_ID = "f"
        result = fetch_volume("phrase")

    assert isinstance(result, WordstatVolume)
    assert result.count == 5000
    assert result.from_date == "2025-06-01T00:00:00Z"   # latest, not first
    assert result.phrase == "phrase"


def test_trend_preserves_null_count_months_for_ui_gaps() -> None:
    """UI plots the trend as a line. Months with no data should
    render as gaps, not be silently dropped."""
    payload = {
        "results": [
            {"date": "2025-04-01T00:00:00Z", "count": "100"},
            {"date": "2025-05-01T00:00:00Z"},                       # no data
            {"date": "2025-06-01T00:00:00Z", "count": "200"},
        ],
    }
    with patch(
        "urllib.request.urlopen", return_value=_ok_response(payload),
    ), patch("app.collectors.wordstat.settings") as s:
        s.YANDEX_SEARCH_API_KEY = "k"
        s.YANDEX_CLOUD_FOLDER_ID = "f"
        result = fetch_volume("phrase")

    assert result is not None
    assert len(result.trend) == 3
    assert result.trend[1] == {"date": "2025-05-01T00:00:00Z", "count": None}
    assert result.count == 300   # nulls excluded from sum


def test_trend_has_twelve_entries_for_full_year() -> None:
    """When Yandex returns 12 months of full data, the trend column
    contains exactly 12 entries — what the UI sparkline expects."""
    rows = [
        {"date": f"2025-{m:02d}-01T00:00:00Z", "count": str(100 + m)}
        for m in range(1, 13)
    ]
    payload = {"results": rows}
    with patch(
        "urllib.request.urlopen", return_value=_ok_response(payload),
    ), patch("app.collectors.wordstat.settings") as s:
        s.YANDEX_SEARCH_API_KEY = "k"
        s.YANDEX_CLOUD_FOLDER_ID = "f"
        result = fetch_volume("phrase")

    assert result is not None
    assert len(result.trend) == TREND_MONTHS
    # Sum of 101..112
    assert result.count == sum(100 + m for m in range(1, 13))


def test_to_dict_roundtrips_through_json() -> None:
    """The Celery task writes `volume.to_dict()['trend']` straight
    into Postgres JSONB. Anything not JSON-serialisable here would
    blow up the worker mid-batch."""
    payload = {
        "results": [
            {"date": "2025-06-01T00:00:00Z", "count": "42"},
        ],
    }
    with patch(
        "urllib.request.urlopen", return_value=_ok_response(payload),
    ), patch("app.collectors.wordstat.settings") as s:
        s.YANDEX_SEARCH_API_KEY = "k"
        s.YANDEX_CLOUD_FOLDER_ID = "f"
        result = fetch_volume("phrase")

    assert result is not None
    raw = result.to_dict()
    json.dumps(raw)   # must not raise


def test_negative_count_in_response_is_ignored() -> None:
    """Defensive: API shouldn't return negative counts but if it does,
    don't poison the aggregate."""
    payload = {
        "results": [
            {"date": "2025-04-01T00:00:00Z", "count": "100"},
            {"date": "2025-05-01T00:00:00Z", "count": "-50"},
            {"date": "2025-06-01T00:00:00Z", "count": "200"},
        ],
    }
    with patch(
        "urllib.request.urlopen", return_value=_ok_response(payload),
    ), patch("app.collectors.wordstat.settings") as s:
        s.YANDEX_SEARCH_API_KEY = "k"
        s.YANDEX_CLOUD_FOLDER_ID = "f"
        result = fetch_volume("phrase")

    assert result is not None
    assert result.count == 300   # negative dropped


# ── fetch_top_requests (semantic expansion / "что ищут со словом X") ──

def test_top_requests_empty_seed_returns_none() -> None:
    with patch("urllib.request.urlopen") as mock:
        assert fetch_top_requests("") is None
        assert fetch_top_requests("   ") is None
    mock.assert_not_called()


def test_top_requests_missing_creds_returns_none() -> None:
    with patch("urllib.request.urlopen") as mock, \
         patch("app.collectors.wordstat.settings") as s:
        s.YANDEX_SEARCH_API_KEY = ""
        s.YANDEX_CLOUD_FOLDER_ID = "f"
        assert fetch_top_requests("багги абхазия") is None
    mock.assert_not_called()


def test_top_requests_http_error_returns_none() -> None:
    err = HTTPError(
        "https://x", 400, "Bad Request", {},
        BytesIO(b'{"error":"bad"}'),
    )
    with patch("urllib.request.urlopen", side_effect=err), \
         patch("app.collectors.wordstat.settings") as s:
        s.YANDEX_SEARCH_API_KEY = "k"
        s.YANDEX_CLOUD_FOLDER_ID = "f"
        assert fetch_top_requests("phrase") is None


def test_top_requests_empty_results_returns_empty_list() -> None:
    """Empty results = no related phrases (valid for niche seeds).
    Distinct from None which means API failure."""
    with patch(
        "urllib.request.urlopen", return_value=_ok_response({"results": []}),
    ), patch("app.collectors.wordstat.settings") as s:
        s.YANDEX_SEARCH_API_KEY = "k"
        s.YANDEX_CLOUD_FOLDER_ID = "f"
        result = fetch_top_requests("very rare seed")

    assert result == []


def test_top_requests_parses_phrases_with_counts() -> None:
    """Real shape we get from the Yandex /topRequests endpoint."""
    payload = {
        "totalCount": "3",
        "results": [
            {"phrase": "купить квартиру", "count": "8714567"},
            {"phrase": "купить квартиру вторичка", "count": "880127"},
            {"phrase": "купить 1 комнатную квартиру", "count": "449042"},
        ],
    }
    with patch(
        "urllib.request.urlopen", return_value=_ok_response(payload),
    ), patch("app.collectors.wordstat.settings") as s:
        s.YANDEX_SEARCH_API_KEY = "k"
        s.YANDEX_CLOUD_FOLDER_ID = "f"
        result = fetch_top_requests("купить квартиру")

    assert result is not None
    assert len(result) == 3
    assert all(isinstance(r, WordstatTopRequest) for r in result)
    assert result[0].phrase == "купить квартиру"
    assert result[0].count == 8714567


def test_top_requests_drops_malformed_rows() -> None:
    """Rows without phrase or count must be skipped silently — never
    poison the batch."""
    payload = {
        "results": [
            {"phrase": "valid one", "count": "100"},
            {"phrase": "", "count": "50"},               # empty phrase
            {"count": "75"},                              # missing phrase
            {"phrase": "no count"},                       # missing count
            {"phrase": "negative", "count": "-10"},       # negative
            {"phrase": "non-numeric", "count": "abc"},    # garbage count
            "not-a-dict",                                 # wrong type
            {"phrase": "another valid", "count": "200"},
        ],
    }
    with patch(
        "urllib.request.urlopen", return_value=_ok_response(payload),
    ), patch("app.collectors.wordstat.settings") as s:
        s.YANDEX_SEARCH_API_KEY = "k"
        s.YANDEX_CLOUD_FOLDER_ID = "f"
        result = fetch_top_requests("seed")

    assert result is not None
    phrases = [r.phrase for r in result]
    assert phrases == ["valid one", "another valid"]


def test_top_requests_only_total_count_returns_empty_list() -> None:
    """Real prod observation: `{"totalCount": "4"}` with no `results`
    array — niche seed has nothing to return. Treat as empty list, not
    failure."""
    payload = {"totalCount": "4"}
    with patch(
        "urllib.request.urlopen", return_value=_ok_response(payload),
    ), patch("app.collectors.wordstat.settings") as s:
        s.YANDEX_SEARCH_API_KEY = "k"
        s.YANDEX_CLOUD_FOLDER_ID = "f"
        result = fetch_top_requests("багги абхазия")

    assert result == []
