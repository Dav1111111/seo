"""Yandex Suggest client + cluster enrichment.

Phase B of the Target Demand Map — enriches Cartesian clusters with real
user query completions from the public Yandex Suggest endpoint.

Why Suggest:
  - Free (no auth, no quota).
  - Surfaces how real users actually type their queries — catches
    long-tail variants, typos, and popular modifiers the Cartesian
    seeds cannot know about.
  - JSONP-ish response: `[input_query, [sugg1, sugg2, ...]]`.

Guardrails (all enforced in this module):
  - Max 20 HTTP calls per build run (MAX_SUGGEST_CALLS).
  - 200ms polite sleep between calls.
  - 2.0s per-request timeout.
  - Fail-open: network errors return empty tuple; caller gets [].
  - Per-cluster cap (default 3 queries) — keeps DB writes bounded.
  - Top-N cluster selection (default 20) so we spend the budget on
    the most business-relevant clusters.

Nothing in this module raises on HTTP / parse errors — logs and returns
empty so the Celery orchestrator can persist the Cartesian result even
when Suggest is down.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core_audit.demand_map.dto import (
    ClusterSource,
    QualityTier,
    TargetClusterDTO,
    TargetQueryDTO,
    VolumeTier,
)

log = logging.getLogger(__name__)


SUGGEST_URL = "https://suggest.yandex.ru/suggest-ff.cgi"
MAX_SUGGEST_CALLS = 20
SLEEP_BETWEEN_CALLS = 0.2

# Tier ordering for top-N selection (core first, then secondary). Exploratory
# clusters are skipped — no point spending Suggest budget on low-confidence
# seeds. Discarded are always skipped.
_ENRICH_TIER_ORDER: tuple[QualityTier, ...] = (
    QualityTier.core,
    QualityTier.secondary,
)


def fetch_suggestions(query: str, *, timeout: float = 2.0) -> tuple[str, ...]:
    """Return up to 5 Yandex Suggest completions for `query`.

    Fail-open contract: any HTTP error, timeout, or parse failure returns
    an empty tuple and logs a warning. Callers MUST NOT expect exceptions.

    Response format: JSONP-ish array `[input, [sugg1, sugg2, ...]]` — we
    ignore anything beyond the first two elements. Empty query => ().
    """
    q = (query or "").strip()
    if not q:
        return ()

    url = f"{SUGGEST_URL}?{urlencode({'part': q, 'n': 5})}"
    try:
        # stdlib urllib keeps the hot path dependency-free; httpx is only
        # required transitively via the broader project. Callers that
        # want async semantics should call this module from a Celery
        # worker (sync) rather than from FastAPI handlers (async).
        req = Request(url, headers={"User-Agent": "YGT-demand-map/1.0"})
        with urlopen(req, timeout=timeout) as resp:  # nosec B310 — fixed URL
            if getattr(resp, "status", 200) >= 400:
                log.warning(
                    "demand_map.suggest_http_status query=%r status=%s",
                    q, getattr(resp, "status", "?"),
                )
                return ()
            body_bytes = resp.read()
        body = body_bytes.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 — fail-open on anything
        log.warning("demand_map.suggest_http_error query=%r err=%s", q, exc)
        return ()

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        log.warning("demand_map.suggest_parse_error query=%r err=%s", q, exc)
        return ()

    # Expected: [input_query, [suggestion, ...], ...]
    if not isinstance(data, list) or len(data) < 2:
        log.warning("demand_map.suggest_unexpected_shape query=%r", q)
        return ()

    raw = data[1]
    if not isinstance(raw, list):
        return ()

    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
        elif isinstance(item, list) and item and isinstance(item[0], str):
            # Some Suggest responses nest [text, metadata] — take the text.
            s = item[0].strip()
            if s:
                out.append(s)
    return tuple(out[:5])


def _select_clusters_for_enrichment(
    clusters: list[TargetClusterDTO], *, top_n: int
) -> list[TargetClusterDTO]:
    """Pick the top-N enrichment candidates.

    Prefer core > secondary, then by business_relevance descending, then
    by cluster_key for determinism. Competitor / discarded never qualify.
    """
    eligible = [
        c for c in clusters
        if c.quality_tier in _ENRICH_TIER_ORDER
        and not c.is_competitor_brand
    ]
    # Tier rank (0 = core, 1 = secondary). Missing => large number (skip).
    def _rank(c: TargetClusterDTO) -> tuple[int, float, str]:
        try:
            tier_idx = _ENRICH_TIER_ORDER.index(c.quality_tier)
        except ValueError:
            tier_idx = 99
        # Negative relevance so higher scores sort first.
        return (tier_idx, -float(c.business_relevance), c.cluster_key)

    eligible.sort(key=_rank)
    return eligible[:top_n]


def enrich_clusters_with_suggest(
    clusters: list[TargetClusterDTO],
    *,
    top_n: int = 20,
    per_cluster: int = 3,
    sleep_s: float = SLEEP_BETWEEN_CALLS,
    fetcher: Any = None,
) -> list[TargetQueryDTO]:
    """Fetch Suggest completions for the top-N clusters.

    Parameters:
        clusters: full Cartesian output from Phase A expander.
        top_n: number of clusters to enrich (after tier/score ranking).
        per_cluster: max TargetQueryDTO rows emitted per cluster.
        sleep_s: polite inter-call delay — set to 0 in tests.
        fetcher: optional injected callable matching `fetch_suggestions`
            signature — used to mock HTTP in tests.

    Returns a (possibly empty) list of TargetQueryDTO rows marked
    `source=ClusterSource.suggest`. Completions equal to the cluster's
    `name_ru` are skipped (duplicates add no information). The total
    HTTP call count never exceeds MAX_SUGGEST_CALLS per invocation.
    """
    if not clusters:
        return []

    fn = fetcher or fetch_suggestions
    picks = _select_clusters_for_enrichment(clusters, top_n=top_n)
    if not picks:
        return []

    queries: list[TargetQueryDTO] = []
    calls_made = 0
    seen_per_cluster: dict[str, set[str]] = {}

    for cluster in picks:
        if calls_made >= MAX_SUGGEST_CALLS:
            log.info(
                "demand_map.suggest_budget_exhausted calls=%d", calls_made
            )
            break

        # Polite pause between calls (skip before the very first call).
        if calls_made > 0 and sleep_s > 0:
            time.sleep(sleep_s)

        suggestions = fn(cluster.name_ru)
        calls_made += 1

        if not suggestions:
            continue

        accepted = seen_per_cluster.setdefault(cluster.cluster_key, set())
        cluster_name_lc = cluster.name_ru.strip().lower()

        emitted_for_cluster = 0
        for sugg in suggestions:
            if emitted_for_cluster >= per_cluster:
                break
            sugg_clean = sugg.strip()
            if not sugg_clean:
                continue
            sugg_lc = sugg_clean.lower()
            if sugg_lc == cluster_name_lc:
                continue
            if sugg_lc in accepted:
                continue
            accepted.add(sugg_lc)
            queries.append(
                TargetQueryDTO(
                    cluster_key=cluster.cluster_key,
                    query_text=sugg_clean,
                    source=ClusterSource.suggest,
                    estimated_volume_tier=cluster.expected_volume_tier
                    or VolumeTier.s,
                )
            )
            emitted_for_cluster += 1

    log.info(
        "demand_map.suggest_done calls=%d clusters=%d queries=%d",
        calls_made, len(picks), len(queries),
    )
    return queries


__all__ = [
    "SUGGEST_URL",
    "MAX_SUGGEST_CALLS",
    "fetch_suggestions",
    "enrich_clusters_with_suggest",
]
