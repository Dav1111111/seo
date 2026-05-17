"""SERP-intel snapshot collector.

Picks the most valuable queries for a site (selector), probes Yandex
SERP for each one (fetch_serp), and INSERTS a fresh row into
`query_serp_snapshots` per query.

Politeness contract
-------------------
* We never spam Yandex Cloud Search beyond its shared quota:
  - sequential probes only (no parallel fan-out — Cloud rate-limits)
  - explicit `SLEEP_BETWEEN_QUERIES_SEC` sleep between distinct queries
  - the `fetch_serp` call itself runs in a thread because it uses
    blocking urllib (async-aware Celery loop stays free)

Anti-fabrication contract
-------------------------
* Errored probes STILL get a row written, with `error_tag` set and
  `results=[]`. Owner needs to see «API failed» as honestly as «we are
  not in top-10».

Domain-match contract
---------------------
* `our_position` is set ONLY when the SERP doc's domain matches the
  site's host (canonicalised: lowercased, stripped of scheme, www. and
  trailing dot/slash, IDN punycode-decoded so a Cyrillic domain matches
  the `xn--...` form Yandex sometimes returns).
* Subdomains (`m.example.ru`, `blog.example.ru`) DO count as us.
"""

from __future__ import annotations

import logging
import urllib.parse
from uuid import UUID

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.serp_intel.dto import SerpRanking, SerpSnapshotResult
from app.core_audit.serp_intel.selector import pick_queries_to_probe
from app.models.query_serp_snapshot import QuerySerpSnapshot
from app.models.search_query import SearchQuery
from app.models.site import Site


log = logging.getLogger(__name__)


# Politeness gap BETWEEN distinct queries (the `fetch_serp` poll loop
# also sleeps internally). 2s is the measured floor where the Cloud
# Search quota doesn't 429 even with two sites running in parallel.
SLEEP_BETWEEN_QUERIES_SEC = 2.0

# Default cap. Matches the per-task constant in `collectors/tasks.py`
# so manual one-off calls behave identically to the weekly beat.
DEFAULT_MAX_QUERIES = 30

# How many non-our domains we denormalise into
# `top_competitor_domains` for cheap filtering. Three is enough for
# the «who else is on this query» column without bloating JSONB.
TOP_COMPETITOR_DOMAINS_KEEP = 3


def _canonicalise_host(raw: str | None) -> str:
    """Strip scheme, leading www, trailing dot/slash to bare hostname,
    lowercase, and IDN-decode punycode.

    Empty or unparseable input → "" (caller treats as «no host to match
    against» and never sets `our_position`).

    Examples:
        "grandtourspirit.ru"              → "grandtourspirit.ru"
        "WWW.GRANDTOURSPIRIT.RU/"         → "grandtourspirit.ru"
        "https://www.example.ru/"         → "example.ru"
        "xn--80ahzll5b6f.xn--p1ai"        → "южный-континент.рф"
        "южный-континент.рф"              → "южный-континент.рф"
        None / ""                         → ""
    """
    if not raw:
        return ""
    s = raw.strip().lower()
    if not s:
        return ""
    # urlparse needs a scheme to populate hostname — fake one if missing.
    if not s.startswith(("http://", "https://")):
        s = "https://" + s
    host = urllib.parse.urlparse(s).hostname or ""
    host = host.removeprefix("www.").rstrip(".")
    if not host:
        return ""
    # Decode punycode to its Unicode form. Yandex returns the Unicode
    # form in `domain` for some queries and the `xn--` form for others;
    # decoding both sides so they compare equal.
    if "xn--" in host:
        try:
            host = host.encode("ascii").decode("idna")
        except (UnicodeError, UnicodeDecodeError):
            # Malformed punycode — keep the raw lowercased ASCII.
            pass
    return host


def _doc_is_ours(doc_domain: str, our_host: str) -> bool:
    """Domain match with subdomain-aware equality.

    Both inputs are run through `_canonicalise_host` so Cyrillic ↔
    punycode and www-prefix variants compare equal.
    """
    if not our_host:
        return False
    d = _canonicalise_host(doc_domain)
    if not d:
        return False
    return d == our_host or d.endswith("." + our_host)


async def collect_serp_snapshot_for_site(
    db: AsyncSession,
    site_id: UUID,
    *,
    max_queries: int = DEFAULT_MAX_QUERIES,
) -> SerpSnapshotResult:
    """Pick top-N important queries, fetch SERP for each, INSERT a
    `QuerySerpSnapshot` row per query, return a per-run summary.

    Caller is responsible for emitting Celery `started`/terminal events
    around this — the snapshot collector itself just does the work.

    On `fetch_serp` returning (_, error_tag): we still insert a row with
    `results=[]`, `error_tag=error_tag`, `our_position=None`. The owner
    needs to see «API failed» too.
    """
    # Late-import to avoid pulling the collector chain into selector
    # tests that mock it.
    from app.collectors.yandex_serp import fetch_serp

    site = await db.get(Site, site_id)
    if site is None:
        return SerpSnapshotResult(
            site_id=site_id,
            queries_probed=0,
            queries_skipped=0,
            queries_failed=0,
            snapshots=[],
        )

    our_host = _canonicalise_host(site.domain)

    # Load every classifiable SearchQuery for the site — selector
    # decides which to keep. Cheap on real sites (≤ few thousand rows).
    all_queries = (await db.execute(
        select(SearchQuery).where(SearchQuery.site_id == site_id)
    )).scalars().all()

    picked = pick_queries_to_probe(all_queries, max_n=max_queries)
    if not picked:
        return SerpSnapshotResult(
            site_id=site_id,
            queries_probed=0,
            queries_skipped=0,
            queries_failed=0,
            snapshots=[],
        )

    snapshots_view: list[dict] = []
    probed = 0
    failed = 0

    for idx, q in enumerate(picked):
        # `fetch_serp` is synchronous urllib — push to a thread so we
        # don't block the event loop. It has its own internal poll
        # sleeps; we add a between-query gap below.
        docs, error_tag = await anyio.to_thread.run_sync(
            fetch_serp, q.query_text,
        )

        our_position: int | None = None
        our_url: str | None = None
        results_payload: list[dict] = []
        top_competitors: list[str] = []
        seen_competitor_hosts: set[str] = set()

        if error_tag:
            failed += 1
        else:
            probed += 1
            for d in docs:
                ranking = SerpRanking(
                    position=d.position,
                    url=d.url,
                    domain=(d.domain or "").lower().lstrip("."),
                    title=(d.title or "")[:500],
                    headline=(d.headline or "")[:500],
                )
                results_payload.append(ranking.to_dict())

                if _doc_is_ours(ranking.domain, our_host):
                    if our_position is None:
                        our_position = ranking.position
                        our_url = ranking.url
                else:
                    canon = _canonicalise_host(ranking.domain)
                    if (
                        canon
                        and canon not in seen_competitor_hosts
                        and len(top_competitors) < TOP_COMPETITOR_DOMAINS_KEEP
                    ):
                        seen_competitor_hosts.add(canon)
                        top_competitors.append(canon)

        # Persist — every probe is its own row (time-series, no UPSERT).
        db.add(QuerySerpSnapshot(
            site_id=site_id,
            query_id=q.id,
            results=results_payload,
            our_position=our_position,
            our_url=our_url,
            top_competitor_domains=top_competitors,
            error_tag=(error_tag[:64] if error_tag else None),
        ))

        snapshots_view.append({
            "query_id": str(q.id),
            "query_text": q.query_text,
            "our_position": our_position,
            "error_tag": error_tag,
            "top_competitor_domains": list(top_competitors),
        })

        # Politeness sleep between distinct queries. Skip after the
        # last one — no point sleeping right before the terminal.
        if idx < len(picked) - 1:
            await anyio.sleep(SLEEP_BETWEEN_QUERIES_SEC)

    return SerpSnapshotResult(
        site_id=site_id,
        queries_probed=probed,
        queries_skipped=0,
        queries_failed=failed,
        snapshots=snapshots_view,
    )


__all__ = [
    "collect_serp_snapshot_for_site",
    "SLEEP_BETWEEN_QUERIES_SEC",
    "DEFAULT_MAX_QUERIES",
    "TOP_COMPETITOR_DOMAINS_KEEP",
    "_canonicalise_host",
    "_doc_is_ours",
]
