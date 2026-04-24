"""Unit tests for the IndexNow client.

Focus on the policy boundaries where Yandex would otherwise reject us:
key format, host normalisation, URL dedup, empty-list short-circuit.
The HTTP layer is mocked — real IndexNow posting is covered by manual
prod-side smoke tests after each deploy.
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from app.collectors.indexnow import (
    MAX_URLS_PER_PING,
    PingResult,
    generate_key,
    is_valid_key,
    ping_urls,
    verify_key_file,
)


# ── Key format ────────────────────────────────────────────────────────────

def test_generated_key_is_valid() -> None:
    for _ in range(20):
        assert is_valid_key(generate_key())


def test_rejects_keys_that_would_fail_yandex_validation() -> None:
    # Too short, too long, wrong chars — all the ways Yandex would 422.
    assert not is_valid_key("")
    assert not is_valid_key("short")                   # <8
    assert not is_valid_key("x" * 129)                 # >128
    assert not is_valid_key("bad_chars_!@#")           # punctuation
    assert not is_valid_key("has spaces inside")       # whitespace
    assert is_valid_key("AaBbCc11")                    # 8 chars minimal
    assert is_valid_key("a-b-c-d-1-2-3-4")             # dashes OK


# ── ping_urls input handling ──────────────────────────────────────────────

def test_ping_refuses_without_host_or_key() -> None:
    r = ping_urls("", "k" * 32, ["https://x.ru/a"])
    assert r.accepted is False and r.error == "missing_host_or_key"
    r = ping_urls("example.ru", "", ["https://x.ru/a"])
    assert r.accepted is False and r.error == "missing_host_or_key"


def test_ping_refuses_malformed_key() -> None:
    r = ping_urls("example.ru", "bad key!", ["https://example.ru/a"])
    assert r.accepted is False
    assert r.error == "invalid_key_format"


def test_ping_is_noop_on_empty_url_list_and_does_not_call_network() -> None:
    with patch("urllib.request.urlopen") as mock:
        r = ping_urls("example.ru", "a" * 32, [])
    assert r.accepted is True
    assert r.url_count == 0
    assert r.error == "no_urls"
    mock.assert_not_called()


def test_ping_deduplicates_urls_and_skips_non_http_schemes() -> None:
    urls = [
        "https://example.ru/a",
        "https://example.ru/a",   # dup
        "https://example.ru/b",
        "javascript:void(0)",     # not http(s)
        "",                       # blank
        "ftp://example.ru/c",     # wrong scheme
        "https://example.ru/c",
    ]
    captured_body = {}

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        captured_body["bytes"] = req.data
        resp = MagicMock()
        resp.getcode.return_value = 200
        resp.__enter__ = lambda self: self
        resp.__exit__ = lambda self, *a: None
        return resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        r = ping_urls("example.ru", "k" * 32, urls)
    body = json.loads(captured_body["bytes"].decode())
    assert r.accepted is True
    assert r.url_count == 3
    assert body["urlList"] == [
        "https://example.ru/a",
        "https://example.ru/b",
        "https://example.ru/c",
    ]


def test_ping_caps_url_count_at_max_per_ping() -> None:
    urls = [f"https://example.ru/p{i}" for i in range(MAX_URLS_PER_PING + 50)]

    with patch("urllib.request.urlopen") as mock:
        resp = MagicMock()
        resp.getcode.return_value = 200
        resp.__enter__ = lambda self: self
        resp.__exit__ = lambda self, *a: None
        mock.return_value = resp
        r = ping_urls("example.ru", "k" * 32, urls)

    assert r.accepted is True
    assert r.url_count == MAX_URLS_PER_PING


def test_ping_host_normalises_scheme_and_trailing_slash() -> None:
    captured: dict = {}

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        captured["body"] = json.loads(req.data.decode())
        resp = MagicMock()
        resp.getcode.return_value = 200
        resp.__enter__ = lambda self: self
        resp.__exit__ = lambda self, *a: None
        return resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        ping_urls("HTTPS://Example.RU/", "k" * 32, ["https://example.ru/a"])
    assert captured["body"]["host"] == "example.ru"
    assert captured["body"]["keyLocation"] == f"https://example.ru/{'k' * 32}.txt"


# ── HTTP error surface ────────────────────────────────────────────────────

def test_ping_surfaces_http_403_without_raising() -> None:
    err = HTTPError("url", 403, "Forbidden", {}, BytesIO(b"key mismatch"))
    with patch("urllib.request.urlopen", side_effect=err):
        r = ping_urls("example.ru", "k" * 32, ["https://example.ru/a"])
    assert r.accepted is False
    assert r.status_code == 403
    assert r.error == "http_403"


def test_ping_surfaces_network_error_distinctly() -> None:
    from urllib.error import URLError

    with patch("urllib.request.urlopen", side_effect=URLError("timed out")):
        r = ping_urls("example.ru", "k" * 32, ["https://example.ru/a"])
    assert r.accepted is False
    assert r.status_code is None
    assert r.error == "network"


# ── Key file verification ─────────────────────────────────────────────────

def test_verify_fails_cleanly_when_owner_has_not_uploaded_file() -> None:
    err = HTTPError("url", 404, "Not Found", {}, BytesIO(b""))
    with patch("urllib.request.urlopen", side_effect=err):
        ok, reason = verify_key_file("example.ru", "k" * 32)
    assert ok is False
    assert reason == "http_404"


def test_verify_fails_on_key_mismatch() -> None:
    resp = MagicMock()
    resp.read.return_value = b"some-other-text"
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *a: None

    with patch("urllib.request.urlopen", return_value=resp):
        ok, reason = verify_key_file("example.ru", "a" * 32)
    assert ok is False
    assert reason == "key_mismatch"


def test_verify_succeeds_when_body_equals_key_with_surrounding_whitespace() -> None:
    """Owner may accidentally add a trailing newline — we must tolerate."""
    key = "a" * 32
    resp = MagicMock()
    resp.read.return_value = (key + "\n").encode()
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *a: None

    with patch("urllib.request.urlopen", return_value=resp):
        ok, reason = verify_key_file("example.ru", key)
    assert ok is True
    assert reason is None


def test_ping_result_to_dict_round_trips_through_json() -> None:
    r = PingResult(accepted=True, status_code=200, url_count=5, error=None)
    payload = r.to_dict()
    assert json.loads(json.dumps(payload)) == payload
