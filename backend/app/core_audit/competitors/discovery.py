"""CompetitorDiscoveryAgent — SERP-based competitor finder.

Rationale
---------
Prior iteration asked an LLM to "propose competitor brands" from observed
queries. That route hallucinated (e.g. "Polaris Slingshot" — a product,
not a competitor brand). The fix is boring: **ask Yandex who is actually
ranking for your money queries**, and count the domains that show up
most often. Zero LLM in the decision path.

Algorithm
---------
1. Pick top-N queries for a site. Source order of preference:
   a. observed queries (SearchQuery) with impressions_14d > 0 — these
      are what real users search AND bring clicks, the most reliable
      money-query signal we have.
   b. if that's empty or too small, top clusters from TargetCluster
      filtered by business_relevance >= 0.4.
2. For each query → call YandexSerpCollector.fetch_serp → list of docs.
3. Aggregate domains across all SERPs. For each domain count:
   - serp_hits: how many distinct queries the domain ranked on
   - best_position: min position across all its appearances
   - avg_position: mean position
4. Exclude the site's own domain + any "nothing-domains" (yandex.ru,
   avito.ru, wildberries.ru, major marketplaces that aren't vertical
   competitors).
5. Keep top-K domains by (serp_hits desc, avg_position asc). These are
   the real competitors — domains that consistently fight for your
   queries.

Output
------
`CompetitorProfile` — plain Python dataclass, JSON-serializable, ready
for persistence in sites.competitor_domains (list of dicts).

The agent never writes to DB directly — caller (Celery task) owns
persistence. Agent is a pure function for testability.
"""

from __future__ import annotations

import dataclasses
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Sequence

from app.collectors.yandex_serp import SerpDoc, fetch_serp


log = logging.getLogger(__name__)

# How many queries to probe per site. Each SERP call costs ~$0.002
# (Yandex Cloud pricing) + ~5 s wall time with async + poll. 30 queries
# ≈ 2.5 min and ≈ $0.06. Good tradeoff for a weekly rebuild.
DEFAULT_MAX_QUERIES = 30

# Top K competitors to keep after aggregation.
DEFAULT_TOP_K = 10

# Domain suffixes to always drop from competitor results. Marketplaces,
# big media/video portals and Yandex's own services saturate the SERP
# but don't compete with a tourism operator. Matching is suffix-based so
# that subdomains (m.avito.ru, vk.com, uslugi.yandex.ru) are caught too.
EXCLUDED_DOMAIN_SUFFIXES: tuple[str, ...] = (
    # Yandex own services
    "yandex.ru", "yandex.com", "ya.ru", "dzen.ru",
    # marketplaces
    "wildberries.ru", "ozon.ru", "aliexpress.ru", "aliexpress.com",
    "avito.ru", "youla.ru", "market.yandex.ru",
    # socials / video
    "youtube.com", "rutube.ru",
    "vk.com", "vk.ru", "ok.ru",
    "facebook.com", "instagram.com", "tiktok.com", "telegram.org",
    # review aggregators / general media
    "tripadvisor.com", "tripadvisor.ru",
    "2gis.ru", "2gis.com",
    "kp.ru", "lenta.ru", "ria.ru", "rbc.ru", "gazeta.ru",
    # manufacturer / product sites (not service operators)
    "polaris.com",
)


def _is_excluded(domain: str) -> bool:
    """True if `domain` equals or is a subdomain of any excluded root."""
    d = domain.lower().removeprefix("www.")
    for sfx in EXCLUDED_DOMAIN_SUFFIXES:
        if d == sfx or d.endswith("." + sfx):
            return True
    return False

# Sleep between SERP requests to be polite to the API and avoid rate
# spikes. Yandex Cloud doesn't publish hard limits for Search API but
# small spacing keeps us well clear of anything hostile.
SLEEP_BETWEEN_CALLS_SEC = 0.1

# Max queries in flight simultaneously. 8 threads hit HTTP 429 "Too
# Many Requests" from Yandex Search API — retries pushed wall time
# UP, not down. 5 is the measured sweet spot: enough parallelism to
# beat serial, below the rate ceiling so no 429 retries.
CONCURRENT_FETCHES = 5


@dataclasses.dataclass(frozen=True)
class CompetitorRow:
    """One aggregated competitor."""

    domain: str
    serp_hits: int            # queries where domain appears in top-N
    best_position: int        # 1 = best
    avg_position: float       # mean position across its SERP appearances
    example_url: str          # one representative URL (highest-ranking hit)
    example_title: str        # title of that URL
    example_query: str        # query where the example was collected

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["avg_position"] = round(d["avg_position"], 2)
        return d


@dataclasses.dataclass
class CompetitorProfile:
    """Result of one discovery run."""

    site_id: str
    own_domain: str
    queries_probed: int
    queries_with_results: int
    competitors: list[CompetitorRow]
    unique_domains_seen: int
    cost_usd: float                  # estimated — Search API calls
    errors: dict[str, int]           # error tag -> count

    # Per-query SERP cache — enables content_gap analyzer to reuse the
    # same SERP data without re-hitting the API. Kept compact: only
    # domain/url/title/position per doc.
    query_serps: dict[str, list[dict]] = dataclasses.field(default_factory=dict)

    def to_jsonb(self) -> dict:
        return {
            "site_id": self.site_id,
            "own_domain": self.own_domain,
            "queries_probed": self.queries_probed,
            "queries_with_results": self.queries_with_results,
            "competitors": [c.to_dict() for c in self.competitors],
            "unique_domains_seen": self.unique_domains_seen,
            "cost_usd": round(self.cost_usd, 4),
            "errors": dict(self.errors),
            "query_serps": self.query_serps,
        }


def _norm_domain(d: str) -> str:
    """lowercase + drop www. — canonical form for aggregation."""
    return (d or "").strip().lower().removeprefix("www.")


def discover_competitors(
    own_domain: str,
    queries: Sequence[str],
    *,
    max_queries: int = DEFAULT_MAX_QUERIES,
    top_k: int = DEFAULT_TOP_K,
    fetcher=fetch_serp,
    sleep_between_calls: float = SLEEP_BETWEEN_CALLS_SEC,
    site_id: str = "",
) -> CompetitorProfile:
    """Run SERP discovery for a site.

    `own_domain` is filtered from the result set.
    `queries` is the pre-selected list of money queries (Celery task
    picks them from SearchQuery / TargetCluster).
    `fetcher` is injectable for tests — defaults to fetch_serp().
    """
    own = _norm_domain(own_domain)
    qs = [q for q in queries if q and q.strip()][:max_queries]

    # per-domain accumulators
    hits: dict[str, int] = defaultdict(int)
    positions: dict[str, list[int]] = defaultdict(list)
    best_url: dict[str, tuple[int, str, str, str]] = {}  # domain -> (pos, url, title, query)
    unique_domains: set[str] = set()
    errors: dict[str, int] = defaultdict(int)
    queries_with_results = 0
    cost_usd = 0.0
    query_serps: dict[str, list[dict]] = {}

    # Fetch SERPs in parallel. Each fetch is IO-bound (submit + poll),
    # so a small thread pool cuts wall time ~4x without GIL pain.
    # Preserve original query order in results dict so callers that
    # iterate by insertion order still see a stable sequence.
    results: dict[str, tuple[list[SerpDoc], str | None]] = {}
    with ThreadPoolExecutor(max_workers=CONCURRENT_FETCHES) as pool:
        future_to_q = {pool.submit(fetcher, q): q for q in qs}
        for fut in as_completed(future_to_q):
            q = future_to_q[fut]
            try:
                results[q] = fut.result()
            except Exception as exc:  # noqa: BLE001
                log.warning("competitors.serp_exception query=%r err=%s", q, exc)
                results[q] = ([], "fetch_exception")

    for q in qs:  # walk in original order for deterministic aggregation
        docs, err = results.get(q, ([], "no_result"))
        # cost estimation — 1 cent per call is the safe upper bound on
        # Yandex Cloud Search API at the time of writing. Real figure
        # in Q2 2026 is closer to $0.002, so this is a ceiling.
        cost_usd += 0.002
        if err:
            errors[err] += 1
            log.info("competitors.serp_err query=%r err=%s", q, err)
            continue
        if not docs:
            errors["empty_docs"] += 1
            continue
        queries_with_results += 1

        # Cache a compact SERP snapshot for downstream content-gap analysis.
        query_serps[q] = [
            {"position": d.position, "domain": d.domain,
             "url": d.url, "title": d.title}
            for d in docs
        ]

        # First occurrence per domain within a single SERP only — we
        # don't double-count a site that lists many subpages on one
        # query.
        seen_in_this_serp: set[str] = set()
        for d in docs:
            dom = _norm_domain(d.domain) or _norm_domain(_extract_from_url(d.url))
            if not dom:
                continue
            unique_domains.add(dom)
            if dom == own:
                continue
            if _is_excluded(dom):
                continue
            if dom in seen_in_this_serp:
                continue
            seen_in_this_serp.add(dom)

            hits[dom] += 1
            positions[dom].append(d.position)
            existing = best_url.get(dom)
            if existing is None or d.position < existing[0]:
                best_url[dom] = (d.position, d.url, d.title, q)

    # rank domains: more SERP hits first, break ties by avg position asc
    ranked = sorted(
        hits.keys(),
        key=lambda d: (-hits[d], sum(positions[d]) / max(len(positions[d]), 1)),
    )

    out_rows: list[CompetitorRow] = []
    for dom in ranked[:top_k]:
        plist = positions[dom]
        pos_best, url, title, example_q = best_url.get(dom, (0, "", "", ""))
        out_rows.append(CompetitorRow(
            domain=dom,
            serp_hits=hits[dom],
            best_position=pos_best,
            avg_position=sum(plist) / max(len(plist), 1),
            example_url=url,
            example_title=title,
            example_query=example_q,
        ))

    return CompetitorProfile(
        site_id=str(site_id),
        own_domain=own,
        queries_probed=len(qs),
        queries_with_results=queries_with_results,
        competitors=out_rows,
        unique_domains_seen=len(unique_domains),
        cost_usd=cost_usd,
        errors=dict(errors),
        query_serps=query_serps,
    )


def _extract_from_url(url: str) -> str:
    """Fallback domain extractor if the XML had no <domain> element."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc or ""
    except Exception:  # noqa: BLE001
        return ""
    return host.lower().removeprefix("www.")


__all__ = [
    "CompetitorRow",
    "CompetitorProfile",
    "discover_competitors",
    "DEFAULT_MAX_QUERIES",
    "DEFAULT_TOP_K",
    "EXCLUDED_DOMAIN_SUFFIXES",
]
