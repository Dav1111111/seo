"""reconciler — merge 3 source maps into one BusinessTruth.

Callers pass 3 pre-computed maps (see readers) + a `sources_used`
diagnostic. This module does the cross-union and produces the final
direction list, sorted by total strength.

It's the thin arithmetic layer between readers and the rest of the
platform. No I/O, no classification — that's all done upstream.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

from app.core_audit.business_truth.dto import (
    BusinessTruth, DirectionEvidence, DirectionKey,
)


def reconcile(
    *,
    understanding_weights: Mapping[DirectionKey, float],
    content_pages: Mapping[DirectionKey, tuple[str, ...]],
    traffic_weights: Mapping[DirectionKey, float],
    traffic_queries: Mapping[DirectionKey, tuple[str, ...]],
    sources_used: dict[str, int],
    top_unclassified_queries: list[tuple[str, int]] | None = None,
    unclassified_share: float = 0.0,
) -> BusinessTruth:
    """Merge 3 per-direction maps into a BusinessTruth.

    `understanding_weights`  — read_understanding output
    `content_pages`          — {key: (url,)} from page_intent over all crawled pages
    `traffic_weights`        — aggregate_traffic.direction_weights
    `traffic_queries`        — {key: (query_text,)} from traffic_reader
    `sources_used`           — {"understanding": N, "content": N, "traffic": N}

    Content strength is derived from the share of pages: if 3 of 10
    pages fall under (багги, сочи), strength_content = 0.3. This is
    comparable with the normalized weights from the other two sources.
    """
    # Union of all keys across sources
    all_keys: set[DirectionKey] = set()
    all_keys.update(understanding_weights.keys())
    all_keys.update(content_pages.keys())
    all_keys.update(traffic_weights.keys())

    # Content share = pages_in_direction / total_pages_with_intent
    total_content_pages = sum(len(v) for v in content_pages.values())

    directions: list[DirectionEvidence] = []
    for key in all_keys:
        u = float(understanding_weights.get(key, 0.0))
        pages = tuple(content_pages.get(key, ()))
        c = (
            len(pages) / total_content_pages
            if total_content_pages > 0
            else 0.0
        )
        t = float(traffic_weights.get(key, 0.0))
        queries = tuple(traffic_queries.get(key, ()))
        directions.append(DirectionEvidence(
            key=key,
            strength_understanding=u,
            strength_content=c,
            strength_traffic=t,
            pages=pages,
            queries=queries,
        ))

    # Sort by sum of strengths descending — the loudest directions first
    directions.sort(
        key=lambda d: -(
            d.strength_understanding + d.strength_content + d.strength_traffic
        ),
    )

    return BusinessTruth(
        directions=directions,
        sources_used=dict(sources_used or {}),
        # Timezone-aware UTC — avoids Python 3.12 deprecation warning
        # and produces ISO strings that downstream clients (Postgres
        # timestamptz, frontend JS) parse unambiguously.
        built_at_iso=datetime.now(timezone.utc).isoformat(),
        top_unclassified_queries=list(top_unclassified_queries or []),
        unclassified_share=float(unclassified_share or 0.0),
    )


__all__ = ["reconcile"]
