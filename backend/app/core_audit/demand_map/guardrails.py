"""Size / recursion guardrails for the Target Demand Map expander.

The expander MUST use these constants and helpers — they are the only place
where caps can be tuned. Caps are intentionally conservative for Phase A so
the pipeline cannot blow up cost/latency before downstream consumers land.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Dict, List

from app.core_audit.demand_map.dto import QualityTier, TargetClusterDTO

log = logging.getLogger(__name__)

# Hard caps — exceeding raises or truncates.
MAX_CLUSTERS_PER_SITE = 500
MAX_CARTESIAN_DEPTH = 3         # e.g. service x geo x modifier, no deeper
MAX_GEO_PERMUTATIONS = 50       # primary + secondary combined, deduped
MAX_PER_TEMPLATE = 30

# Soft caps per tier — overflow triggers retiering, not drops.
SOFT_CAPS_PER_TIER: Dict[QualityTier, int] = {
    QualityTier.core: 50,
    QualityTier.secondary: 150,
    QualityTier.exploratory: 250,
}


class GuardrailError(RuntimeError):
    """Raised when a hard cap is exceeded and there is no safe fallback."""


def cap_geo_permutations(
    geo_primary: Sequence[str],
    geo_secondary: Sequence[str],
    cap: int = MAX_GEO_PERMUTATIONS,
) -> tuple[list[str], list[str]]:
    """Deduplicate primary+secondary geo and cap at `cap` items.

    Primary wins on conflict: if cap forces a trim, secondary is trimmed
    first, then primary from the tail. A value that appears in both lists
    is kept in primary only.
    """
    seen: set[str] = set()
    primary_dedup: list[str] = []
    for g in geo_primary:
        if g and g not in seen:
            seen.add(g)
            primary_dedup.append(g)

    secondary_dedup: list[str] = []
    for g in geo_secondary:
        if g and g not in seen:
            seen.add(g)
            secondary_dedup.append(g)

    total = len(primary_dedup) + len(secondary_dedup)
    if total <= cap:
        return primary_dedup, secondary_dedup

    # Trim secondary first.
    remaining = cap - len(primary_dedup)
    if remaining <= 0:
        # Primary alone exceeds the cap.
        return primary_dedup[:cap], []
    return primary_dedup, secondary_dedup[:remaining]


def enforce_global_cap(
    clusters: Iterable[TargetClusterDTO],
    max_n: int = MAX_CLUSTERS_PER_SITE,
) -> List[TargetClusterDTO]:
    """Enforce the hard global cap — raises if exceeded.

    We materialize into a list so callers get a deterministic snapshot
    back. If the total cluster count (including discarded) exceeds
    `max_n`, `GuardrailError` is raised — this is the intended final
    safety net for catastrophically oversized template/config pairs.
    """
    result = list(clusters)
    if len(result) > max_n:
        raise GuardrailError(
            f"target demand map exceeded hard cap: {len(result)} > {max_n}"
        )
    return result


# Retiering cascade: core -> secondary -> exploratory (stops here). Tier
# `discarded` is NEVER assigned by retiering — it is reserved for hard-rule
# hits (competitor brand, excluded geo/service, sub-threshold relevance).
# Overflow past exploratory's soft cap is admitted as-is; the global hard
# cap in `enforce_global_cap` is the final safety net for catastrophic
# overflow (raises `GuardrailError`).
_DOWNGRADE_ORDER: tuple[QualityTier, ...] = (
    QualityTier.core,
    QualityTier.secondary,
    QualityTier.exploratory,
)


def _next_softer(tier: QualityTier) -> QualityTier | None:
    """Return the next-softer tier below `tier`, or None at the floor."""
    try:
        idx = _DOWNGRADE_ORDER.index(tier)
    except ValueError:
        return None
    if idx >= len(_DOWNGRADE_ORDER) - 1:
        return None
    return _DOWNGRADE_ORDER[idx + 1]


def enforce_tier_caps(
    clusters: Sequence[TargetClusterDTO],
    soft_caps: Dict[QualityTier, int] | None = None,
) -> List[TargetClusterDTO]:
    """Return a new list where per-tier soft-cap overflows are retiered.

    Processing order: clusters keep their submission order (stable), and
    within a tier the earliest N (N = cap) stay in that tier; the rest are
    downgraded to the next softer tier. If downgrading pushes the next tier
    over its own cap, cascading downgrades apply. Discarded clusters are
    never retiered.
    """
    caps = dict(SOFT_CAPS_PER_TIER if soft_caps is None else soft_caps)
    # Tier counts as we emit.
    counts: Dict[QualityTier, int] = {
        QualityTier.core: 0,
        QualityTier.secondary: 0,
        QualityTier.exploratory: 0,
        QualityTier.discarded: 0,
    }
    out: List[TargetClusterDTO] = []

    for c in clusters:
        tier = c.quality_tier
        if tier == QualityTier.discarded:
            out.append(c)
            counts[tier] += 1
            continue

        cap = caps.get(tier)
        while cap is not None and counts[tier] >= cap:
            downgraded = _next_softer(tier)
            if downgraded is None:
                # At exploratory floor -- admit the overflow as-is, the
                # global hard cap is the ultimate bound.
                break
            log.info(
                "demand_map.retiering",
                extra={
                    "from": tier.value,
                    "to": downgraded.value,
                    "cluster_key": c.cluster_key,
                },
            )
            tier = downgraded
            cap = caps.get(tier)

        if tier != c.quality_tier:
            # Rebuild the DTO with the new tier (frozen dataclass).
            c = TargetClusterDTO(
                site_id=c.site_id,
                cluster_key=c.cluster_key,
                name_ru=c.name_ru,
                intent_code=c.intent_code,
                cluster_type=c.cluster_type,
                quality_tier=tier,
                keywords=c.keywords,
                seed_slots=c.seed_slots,
                is_brand=c.is_brand,
                is_competitor_brand=c.is_competitor_brand,
                expected_volume_tier=c.expected_volume_tier,
                business_relevance=c.business_relevance,
                source=c.source,
            )
        out.append(c)
        counts[tier] += 1

    return out
