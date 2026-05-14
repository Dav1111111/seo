"""Fetch robots.txt over HTTP with SSRF protection.

The Yandex robots-audit module (`core_audit.yandex_robots`) is pure /
network-free — it analyses a string. This module supplies that string
by fetching `/robots.txt` from a site over the network, behind the
project's SSRF guard (`app.security.network.safe_urlopen`).

Contract is deliberately tiny and forgiving — the audit core handles
"no robots.txt" / "blocked" / "wrong status" cases via the dict shape
we return below. We never raise; we encode all failure modes via
``status=None`` or a non-2xx status with ``body=None``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TypedDict
from urllib.error import HTTPError, URLError

from app.security.network import SSRFBlocked, safe_urlopen

logger = logging.getLogger(__name__)

# Generous cap for malicious / runaway robots.txt files. Anything legit
# is in the low-KB range; 1 MB is well past any sensible threshold so
# we can keep diagnostics readable when we hit the limit.
MAX_BODY_BYTES = 1_000_000

# Read in chunks so we can abort early when oversized. 64 KB is a
# pragmatic compromise — small enough that the cap fires within a few
# iterations, large enough to amortise read overhead.
_CHUNK_BYTES = 64 * 1024

# 10s total per attempt; we try https first, then http once.
_TIMEOUT_SECONDS = 10.0

USER_AGENT = "YandexGrowthTower/1.0 (+robots-audit)"


class RobotsFetchResult(TypedDict):
    url: str
    status: int | None
    body: str | None
    size_bytes: int


def _fetch_sync(url: str) -> RobotsFetchResult:
    """Blocking fetch — wrapped from the async entry point.

    Always returns a result dict; encodes network/SSRF failures as
    ``status=None``. HTTP errors (4xx/5xx) come back with the real
    status code and ``body=None``.
    """
    out: RobotsFetchResult = {
        "url": url,
        "status": None,
        "body": None,
        "size_bytes": 0,
    }
    try:
        resp = safe_urlopen(
            url,
            timeout=_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )
    except HTTPError as exc:
        # Server replied with 4xx/5xx — we still want the status code
        # so the auditor can distinguish 404 (no robots.txt = effectively
        # allow-all) from 5xx (transient — UA-specific Disallow).
        out["status"] = int(getattr(exc, "code", 0) or 0) or None
        return out
    except (URLError, SSRFBlocked, TimeoutError, OSError, ValueError) as exc:
        logger.info("robots_fetcher: network failure for %s: %s", url, exc)
        return out

    # Read in chunks with a hard size cap. We intentionally do NOT
    # raise on overflow — store what we got and let the auditor
    # decide; "too large" robots.txt is a finding, not a crash.
    chunks: list[bytes] = []
    total = 0
    try:
        with resp:
            status = int(resp.status or 0) or None
            out["status"] = status
            while True:
                chunk = resp.read(_CHUNK_BYTES)
                if not chunk:
                    break
                remaining = MAX_BODY_BYTES - total
                if remaining <= 0:
                    break
                if len(chunk) > remaining:
                    chunks.append(chunk[:remaining])
                    total += remaining
                    break
                chunks.append(chunk)
                total += len(chunk)
    except (URLError, TimeoutError, OSError) as exc:
        logger.info("robots_fetcher: read failure for %s: %s", url, exc)
        # Mid-read failure: keep whatever we have but the status we
        # already captured is still meaningful.
        # If we got nothing, fall through with zeroed body.

    body_bytes = b"".join(chunks)
    out["size_bytes"] = len(body_bytes)

    if out["status"] is not None and 200 <= out["status"] < 300 and body_bytes:
        try:
            out["body"] = body_bytes.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — decode("replace") shouldn't raise
            out["body"] = None

    return out


async def fetch_robots_txt(domain: str) -> RobotsFetchResult:
    """Fetch ``https://{domain}/robots.txt`` (one http fallback retry).

    Returns a dict::

        {
            "url": str,           # URL we last attempted
            "status": int | None, # HTTP status; None on network failure
            "body": str | None,   # text body if status 2xx, else None
            "size_bytes": int,    # bytes read (after cap)
        }

    Does not raise — every error path is encoded into the dict.
    Body is capped at :data:`MAX_BODY_BYTES`.
    """
    https_url = f"https://{domain}/robots.txt"
    https_result = await asyncio.to_thread(_fetch_sync, https_url)

    # Treat any non-None HTTP response (even 4xx/5xx) as a real answer —
    # the auditor needs that signal. Only retry over http when the
    # https attempt failed at the network level (status is None).
    if https_result["status"] is not None:
        return https_result

    http_url = f"http://{domain}/robots.txt"
    http_result = await asyncio.to_thread(_fetch_sync, http_url)
    return http_result
