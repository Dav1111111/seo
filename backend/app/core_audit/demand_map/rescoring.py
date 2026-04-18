"""Observed-overlap rescoring for the Target Demand Map.

Phase B — after the deterministic Cartesian expansion + Suggest/LLM
enrichment, we check each cluster against observed Yandex Webmaster
queries. Clusters whose tokens overlap with real impressions get a
small business_relevance boost (+0.05, clamped to 1.0).

Why not bigger: observed queries often contain noise (typos,
accidental matches via shared stopwords). A modest bump re-orders
edge cases without reshaping the tier distribution.

The overlap is computed on LEMMAS (via `app.fingerprint.lemmatize`)
so "экскурсия" and "экскурсии" collide. Stopwords are dropped. At
least ONE non-stopword lemma must match for a boost.
"""

from __future__ import annotations

import logging
from typing import Iterable

from app.core_audit.demand_map.dto import TargetClusterDTO
from app.fingerprint.lemmatize import lemmatize_tokens, tokenize

log = logging.getLogger(__name__)


OVERLAP_BOOST = 0.05


def _lemma_set(text: str) -> frozenset[str]:
    """Lowercase -> tokenize -> lemmatize -> drop stopwords -> frozenset."""
    if not text:
        return frozenset()
    return frozenset(lemmatize_tokens(tokenize(text), drop_stopwords=True))


def _cluster_lemma_set(cluster: TargetClusterDTO) -> frozenset[str]:
    """Union of the cluster name_ru and its keywords."""
    parts: list[str] = [cluster.name_ru or ""]
    parts.extend(cluster.keywords or ())
    return _lemma_set(" ".join(parts))


def _build_observed_lemma_set(
    observed: Iterable[tuple[str, int]],
) -> frozenset[str]:
    """Union all observed query lemmas (impressions-weighted is NOT needed —
    we only check existence)."""
    bucket: set[str] = set()
    for row in observed or ():
        if not row:
            continue
        if isinstance(row, (list, tuple)) and len(row) >= 1:
            text = str(row[0] or "")
        else:
            # Permit a bare string as a defensive convenience.
            text = str(row)
        bucket.update(_lemma_set(text))
    return frozenset(bucket)


def rescore_with_observed_overlap(
    clusters: list[TargetClusterDTO],
    observed_queries: list[tuple[str, int]],
) -> list[TargetClusterDTO]:
    """Return new DTOs with +0.05 relevance boost when observed lemmas overlap.

    Parameters:
        clusters: clusters from `expand_for_site` (Phase A output).
        observed_queries: list of `(query_text, impressions)` tuples
            sourced from the site's recent Yandex Webmaster data. The
            second element is accepted for forward compatibility — the
            current rule weights all queries equally.

    Returns a new list (frozen dataclasses cannot be mutated in-place).
    Score is clamped to 1.0. Clusters with no observed overlap are
    returned unchanged (same identity). No tier re-classification
    happens here — tier caps were already applied upstream.
    """
    if not clusters:
        return []

    observed_set = _build_observed_lemma_set(observed_queries or [])
    if not observed_set:
        return list(clusters)

    out: list[TargetClusterDTO] = []
    boosted = 0

    for c in clusters:
        cluster_set = _cluster_lemma_set(c)
        # Drop the intersect test if either side is empty — no overlap possible.
        if not cluster_set or not (cluster_set & observed_set):
            out.append(c)
            continue

        new_score = round(min(1.0, float(c.business_relevance) + OVERLAP_BOOST), 3)
        if new_score == c.business_relevance:
            out.append(c)
            continue

        out.append(
            TargetClusterDTO(
                site_id=c.site_id,
                cluster_key=c.cluster_key,
                name_ru=c.name_ru,
                intent_code=c.intent_code,
                cluster_type=c.cluster_type,
                quality_tier=c.quality_tier,
                keywords=c.keywords,
                seed_slots=c.seed_slots,
                is_brand=c.is_brand,
                is_competitor_brand=c.is_competitor_brand,
                expected_volume_tier=c.expected_volume_tier,
                business_relevance=new_score,
                source=c.source,
            )
        )
        boosted += 1

    if boosted:
        log.info(
            "demand_map.rescore_applied total=%d boosted=%d",
            len(clusters), boosted,
        )
    return out


__all__ = ["OVERLAP_BOOST", "rescore_with_observed_overlap"]
