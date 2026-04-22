"""Yandex Cloud Search API client — async SERP fetcher.

Replaces the legacy xml.yandex.ru flow (that route is being phased out in
favour of the Yandex Cloud / AI Studio Search API). The new API uses a
static Api-Key (same key that also works for YandexGPT / Foundation
Models) and returns results in the historical XML envelope so parsing
stays close to the old format.

Flow
----
    POST /v2/web/searchAsync  → operation_id
    GET  /operations/{id}     → rawData (base64 of XML)   (3–5 s typical)
    parse XML → list[SerpDoc]

Design notes
------------
- Keep this module tiny and dependency-free (stdlib urllib, no requests).
  Runs inside Celery workers and in pytest without extra installs.
- Fail-open: any network/parse error returns an empty list plus the raw
  error string — callers log and continue.
- Politeness: sleep between polls; cap total wait time per query so a
  single stuck operation can't freeze the worker for minutes.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Sequence
from xml.etree import ElementTree as ET

from app.config import settings


log = logging.getLogger(__name__)


SEARCH_ENDPOINT = "https://searchapi.api.cloud.yandex.net/v2/web/searchAsync"
OPERATION_ENDPOINT = "https://operation.api.cloud.yandex.net/operations"

# Poll schedule — results usually ready in 3–5 s, but allow up to ~20 s.
# Yandex async operations typically complete within ~1s. Tried 0.4s
# and got hammered with HTTP 429 (Too Many Requests) because the poll
# endpoint rate-limits aggressive re-polling. 0.7s is the measured
# sweet spot: first poll usually hits a ready op, and we stay below
# the rate ceiling even with 4-5 concurrent discovery threads.
POLL_INTERVAL_SEC = 0.7
# Previously 30 attempts × 0.7s = 21s per-query ceiling. One stuck
# query then dominated the whole discovery wall time. Cut to 15 so a
# single rotten query contributes at most ~10.5s. Real ops complete
# in 1-3 polls, so this loses nothing in the common case.
POLL_MAX_ATTEMPTS = 15

# Request defaults
DEFAULT_REGION = "225"                   # 225 = Russia country-wide
DEFAULT_GROUPS_ON_PAGE = 10              # top-10 SERP
DEFAULT_SEARCH_TYPE = "SEARCH_TYPE_RU"   # Russian locale
REQUEST_TIMEOUT_SEC = 15.0


@dataclasses.dataclass(frozen=True)
class SerpDoc:
    """One result row from a SERP response."""

    position: int           # 1..10 (within the current page)
    url: str
    domain: str
    title: str              # HTML-stripped plain text
    headline: str           # snippet

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _strip_tags(text: str) -> str:
    """Tiny HTML tag stripper — <hlword> markers etc. from Yandex XML."""
    if not text:
        return ""
    out = []
    in_tag = False
    for ch in text:
        if ch == "<":
            in_tag = True
            continue
        if ch == ">":
            in_tag = False
            continue
        if not in_tag:
            out.append(ch)
    return "".join(out).strip()


def _extract_domain(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).netloc or ""
    except Exception:  # noqa: BLE001
        return ""
    return host.lower().removeprefix("www.")


def _build_async_request_body(query: str, region: str, groups: int) -> dict:
    return {
        "query": {
            "searchType": DEFAULT_SEARCH_TYPE,
            "queryText": query,
        },
        "groupSpec": {
            "groupMode": "GROUP_MODE_FLAT",
            "groupsOnPage": groups,
            "docsInGroup": 1,
        },
        "maxPassages": 1,
        "region": region,
        "l10n": "LOCALIZATION_RU",
        "folderId": settings.YANDEX_CLOUD_FOLDER_ID or None,
    }


def _post(url: str, body: dict, api_key: str) -> dict:
    data = json.dumps({k: v for k, v in body.items() if v is not None}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Api-Key {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(url: str, api_key: str) -> dict:
    req = urllib.request.Request(
        url, method="GET",
        headers={"Authorization": f"Api-Key {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_xml(xml_text: str) -> list[SerpDoc]:
    """Parse the classic Yandex XML SERP envelope into SerpDoc rows.

    Empty / malformed XML returns []. Tolerates missing <headline> on a
    per-doc basis but not missing <url> (those are skipped).
    """
    out: list[SerpDoc] = []
    if not xml_text:
        return out
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.warning("yandex_serp.xml_parse_failed err=%s", exc)
        return out

    # Results live under yandexsearch/response/results/grouping/group/doc
    # The <title> and <headline> may contain <hlword> highlight children;
    # ET.tostring handles that, then we strip tags manually.
    position = 0
    for group in root.findall("./response/results/grouping/group"):
        for doc in group.findall("./doc"):
            url_el = doc.find("./url")
            if url_el is None or not url_el.text:
                continue
            position += 1
            title_el = doc.find("./title")
            headline_el = doc.find("./headline")
            domain_el = doc.find("./domain")
            title_raw = ET.tostring(title_el, encoding="unicode") if title_el is not None else ""
            headline_raw = ET.tostring(headline_el, encoding="unicode") if headline_el is not None else ""
            out.append(SerpDoc(
                position=position,
                url=url_el.text.strip(),
                domain=(domain_el.text.strip() if domain_el is not None and domain_el.text
                        else _extract_domain(url_el.text)),
                title=_strip_tags(title_raw),
                headline=_strip_tags(headline_raw),
            ))
    return out


def fetch_serp(
    query: str,
    *,
    region: str = DEFAULT_REGION,
    groups: int = DEFAULT_GROUPS_ON_PAGE,
    api_key: str | None = None,
) -> tuple[list[SerpDoc], str | None]:
    """Fetch top-N SERP for `query`.

    Returns (docs, error). On success `error` is None; on any failure
    `docs` is [] and `error` is a short human-readable tag.
    """
    key = api_key or settings.YANDEX_SEARCH_API_KEY
    if not key:
        return [], "missing_api_key"

    body = _build_async_request_body(query, region, groups)

    try:
        op = _post(SEARCH_ENDPOINT, body, key)
    except urllib.error.HTTPError as exc:
        return [], f"http_{exc.code}_on_submit"
    except Exception as exc:  # noqa: BLE001
        log.warning("yandex_serp.submit_failed query=%r err=%s", query, exc)
        return [], "submit_exception"

    op_id = op.get("id")
    if not op_id:
        return [], "no_operation_id"

    op_url = f"{OPERATION_ENDPOINT}/{op_id}"
    raw_b64 = None
    for _ in range(POLL_MAX_ATTEMPTS):
        try:
            poll = _get(op_url, key)
        except Exception as exc:  # noqa: BLE001
            log.warning("yandex_serp.poll_failed op=%s err=%s", op_id, exc)
            time.sleep(POLL_INTERVAL_SEC)
            continue
        if poll.get("done"):
            raw_b64 = (poll.get("response") or {}).get("rawData") or ""
            break
        time.sleep(POLL_INTERVAL_SEC)

    if raw_b64 is None:
        return [], "timeout_waiting_for_operation"
    if not raw_b64:
        return [], "empty_response_raw_data"

    try:
        xml_text = base64.b64decode(raw_b64).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        log.warning("yandex_serp.b64_decode_failed err=%s", exc)
        return [], "b64_decode_failed"

    docs = _parse_xml(xml_text)
    return docs, None


__all__ = ["SerpDoc", "fetch_serp"]
