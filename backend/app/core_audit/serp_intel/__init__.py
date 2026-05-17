"""SERP intelligence — per-query Yandex top-N snapshots.

Point 2 of the owner roadmap: probe the most-valuable queries weekly,
store the top-N rankings, and surface gaps + repeat competitor
pressure in the unified advice feed.

Architecture
------------
* `selector` — pure function that picks which queries are worth a
  SERP-API call (volume × layer-weight, capped at N).
* `snapshot` — the collector: pulls picked queries, calls
  `collectors/yandex_serp.fetch_serp` per query, stores rows.
* `dto` — frozen `SerpRanking` / `SerpSnapshotResult` data classes
  whose field names match the JSONB row layout exactly so the frontend
  parses both sources with one mapper.

The Celery task wrapper lives in `app.collectors.tasks`
(`serp_intel_probe_for_site` + `serp_intel_probe_all`); the brain
aggregation rule lives in `app.core_audit.brain.rules`
(`_rule_serp_competitor_pressure`); the advisor card formatter lives
in `app.core_audit.advisor.formatters` (`format_serp_gap`).
"""

from app.core_audit.serp_intel.dto import SerpRanking, SerpSnapshotResult
from app.core_audit.serp_intel.selector import (
    LAYER_WEIGHTS,
    MIN_VOLUME_TO_PROBE,
    SKIP_RELEVANCES,
    pick_queries_to_probe,
)
from app.core_audit.serp_intel.snapshot import (
    DEFAULT_MAX_QUERIES,
    SLEEP_BETWEEN_QUERIES_SEC,
    TOP_COMPETITOR_DOMAINS_KEEP,
    collect_serp_snapshot_for_site,
)

__all__ = [
    "SerpRanking",
    "SerpSnapshotResult",
    "pick_queries_to_probe",
    "LAYER_WEIGHTS",
    "MIN_VOLUME_TO_PROBE",
    "SKIP_RELEVANCES",
    "collect_serp_snapshot_for_site",
    "DEFAULT_MAX_QUERIES",
    "SLEEP_BETWEEN_QUERIES_SEC",
    "TOP_COMPETITOR_DOMAINS_KEEP",
]
