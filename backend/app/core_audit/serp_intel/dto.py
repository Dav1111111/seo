"""SERP intelligence — frozen data contracts.

Field names here are LOAD-BEARING — they're consumed by:
  * the snapshot collector when it stores rows into `query_serp_snapshots.results`
  * the studio API endpoint that serves the latest snapshot to the frontend
  * the frontend agent that renders «who ranks on this query»

Keep the field set identical between `SerpRanking` (in-memory) and the
JSONB row layout: {position, url, domain, title, headline}.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class SerpRanking:
    """One row from a stored top-N SERP snapshot.

    Mirrors the JSONB shape persisted in
    ``QuerySerpSnapshot.results``: every field of this dataclass is
    written as-is into the JSON object so a one-line `to_dict()` keeps
    the contract tight.
    """
    position: int
    url: str
    domain: str
    title: str
    headline: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "url": self.url,
            "domain": self.domain,
            "title": self.title,
            "headline": self.headline,
        }


@dataclass(frozen=True)
class SerpSnapshotResult:
    """Outcome of one site-wide probe run.

    Returned by ``collect_serp_snapshot_for_site`` so the Celery task
    can emit a useful terminal `extra={…}` and so manual callers (the
    admin endpoint) can render a small summary.

    `snapshots` is a list of small {query_id, query_text, our_position,
    error_tag} dicts — useful for the activity feed and for debugging
    a single run, but NOT the canonical store (the persisted DB rows
    are). Keep this view-only.
    """
    site_id: UUID
    queries_probed: int
    queries_skipped: int
    queries_failed: int
    snapshots: list[dict[str, Any]] = field(default_factory=list)


__all__ = ["SerpRanking", "SerpSnapshotResult"]
