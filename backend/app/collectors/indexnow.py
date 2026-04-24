"""IndexNow client — ask Yandex (and any IndexNow-compliant engine) to
re-crawl a specific list of URLs.

Why this exists
---------------
Webmaster's "Переобход страниц" button is the canonical way to force a
re-crawl, but it requires the host to be in the indexed state. For
sites stuck at HOST_NOT_LOADED, that path is closed. IndexNow is the
protocol replacement: POST a JSON body with the URLs you want crawled,
Yandex picks them up within ~24 hours, no Webmaster dependency.

Protocol summary (per https://www.indexnow.org/documentation)
--------------------------------------------------------------
1. Owner generates a key (8–128 chars, `a–z A–Z 0–9 -`).
2. Owner uploads a plaintext file at `https://<host>/<key>.txt` whose
   body contains exactly the key — that proves ownership.
3. We POST to `https://yandex.com/indexnow` with:
       { host, key, keyLocation, urlList }
   Yandex returns 200 on accept, 202 on accepted-with-delay, 400/403/422
   on policy errors. None of these confirm indexing — only intent-to-
   crawl. Actual indexing is measured separately via `check_indexation`.

Design
------
- stdlib urllib (same discipline as yandex_serp.py) — no aiohttp/httpx.
- Synchronous (called from Celery task threads), fail-open on error.
- Per-site key lives on `Site.target_config['indexnow']` so each
  tenant has an independent key; if one leaks, others keep working.
- Owner has to upload the key file themselves; we verify it by HTTP
  GET before we'll start pinging, so we never lie about being set up.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import secrets
import urllib.error
import urllib.request
from typing import Sequence


log = logging.getLogger(__name__)


INDEXNOW_ENDPOINT = "https://yandex.com/indexnow"

# Per protocol: 8–128 chars, `a-zA-Z0-9-`. 32 hex chars = 128 bits of
# entropy, well inside the allowed set.
_KEY_ALPHABET = re.compile(r"^[A-Za-z0-9\-]{8,128}$")

REQUEST_TIMEOUT_SEC = 10.0

# Yandex accepts up to 10 000 URLs per submission, but we stay well
# under. Sites we work with have <500 pages; batching larger than the
# typical site size wastes no budget but costs memory if the
# `urlList` pulls in the entire sitemap.
MAX_URLS_PER_PING = 1000


@dataclasses.dataclass(frozen=True)
class PingResult:
    """Outcome of a single POST to the IndexNow endpoint.

    `accepted` is True for HTTP 200/202 (Yandex will crawl these);
    False for policy errors (400/403/422) or network failures. The
    raw `status_code` + `error` let the caller surface a specific
    reason to the owner so they can fix the setup.
    """

    accepted: bool
    status_code: int | None
    url_count: int
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "status_code": self.status_code,
            "url_count": self.url_count,
            "error": self.error,
        }


def generate_key() -> str:
    """Create a fresh IndexNow key — 32 hex chars (128-bit entropy).

    Stored in `Site.target_config['indexnow']['key']`. The owner
    uploads a file named `<key>.txt` at the site root containing
    exactly this string.
    """
    return secrets.token_hex(16)


def is_valid_key(key: str) -> bool:
    """Does `key` conform to the IndexNow spec (8–128 chars, `a-zA-Z0-9-`)?"""
    return bool(_KEY_ALPHABET.match(key or ""))


def verify_key_file(host: str, key: str, *, timeout: float = REQUEST_TIMEOUT_SEC) -> tuple[bool, str | None]:
    """Fetch `https://<host>/<key>.txt` and confirm its body is the key.

    Returns `(True, None)` on success, `(False, reason)` otherwise.
    Reasons distinguish "owner hasn't uploaded yet" (404) from "wrong
    content" (key mismatch) so the UI can tell them what to fix.
    """
    if not host or not key or not is_valid_key(key):
        return False, "invalid_host_or_key"

    host_clean = host.strip().lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").rstrip("/")
    url = f"https://{host_clean}/{key}.txt"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace").strip()
    except urllib.error.HTTPError as exc:
        return False, f"http_{exc.code}"
    except urllib.error.URLError as exc:
        return False, f"url_error_{type(exc.reason).__name__}"
    except Exception as exc:  # noqa: BLE001
        log.warning("indexnow.verify_failed host=%s err=%s", host_clean, exc)
        return False, "verify_exception"

    if body == key:
        return True, None
    return False, "key_mismatch"


def ping_urls(
    host: str,
    key: str,
    urls: Sequence[str],
    *,
    endpoint: str = INDEXNOW_ENDPOINT,
    timeout: float = REQUEST_TIMEOUT_SEC,
) -> PingResult:
    """Submit a batch of URLs to IndexNow.

    Fails fast if `host` / `key` look wrong rather than letting Yandex
    reject with a vague 400. Empty URL list is a no-op (returns accept
    with url_count=0) — callers can opt-in to treating that as "nothing
    to do".
    """
    if not host or not key:
        return PingResult(accepted=False, status_code=None, url_count=0, error="missing_host_or_key")
    if not is_valid_key(key):
        return PingResult(accepted=False, status_code=None, url_count=0, error="invalid_key_format")

    host_clean = host.strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
    if not host_clean:
        return PingResult(accepted=False, status_code=None, url_count=0, error="invalid_host")

    deduped = []
    seen: set[str] = set()
    for u in urls:
        u2 = (u or "").strip()
        if u2 and u2 not in seen and u2.startswith(("http://", "https://")):
            seen.add(u2)
            deduped.append(u2)
        if len(deduped) >= MAX_URLS_PER_PING:
            break

    if not deduped:
        return PingResult(accepted=True, status_code=None, url_count=0, error="no_urls")

    body = {
        "host": host_clean,
        "key": key,
        "keyLocation": f"https://{host_clean}/{key}.txt",
        "urlList": deduped,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Host": "yandex.com",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            return PingResult(accepted=True, status_code=code, url_count=len(deduped), error=None)
    except urllib.error.HTTPError as exc:
        # 400 = bad request, 403 = key mismatch, 422 = unprocessable.
        # All of these are "Yandex said no" — surface the code to UI.
        msg = None
        try:
            msg = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        log.warning("indexnow.http_error host=%s code=%s body=%r", host_clean, exc.code, msg)
        return PingResult(
            accepted=False,
            status_code=exc.code,
            url_count=len(deduped),
            error=f"http_{exc.code}",
        )
    except urllib.error.URLError as exc:
        log.warning("indexnow.url_error host=%s err=%s", host_clean, exc)
        return PingResult(accepted=False, status_code=None, url_count=len(deduped), error="network")
    except Exception as exc:  # noqa: BLE001
        log.warning("indexnow.exception host=%s err=%s", host_clean, exc)
        return PingResult(accepted=False, status_code=None, url_count=len(deduped), error="submit_exception")


__all__ = [
    "PingResult",
    "generate_key",
    "is_valid_key",
    "verify_key_file",
    "ping_urls",
    "INDEXNOW_ENDPOINT",
    "MAX_URLS_PER_PING",
]
