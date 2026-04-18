"""Deterministic Cartesian expander.

Walks a vertical profile's `seed_cluster_templates` and fills them with
values from a site's `target_config`, producing `TargetClusterDTO`s.

Phase A — NO NETWORK. No LLM, no Yandex Suggest, no HTTP.

Pipeline per call:
  1. Normalize + cap target_config (geo permutations -> cap at 50).
  2. For each template, enumerate valid slot combinations (<= MAX_PER_TEMPLATE
     per template).
  3. Detect competitor-brand hits, excluded-service hits, excluded-geo hits.
  4. Compute business relevance + quality tier per candidate.
  5. Emit DTOs. Dedupe by cluster_key (same slots -> same cluster).
  6. Enforce tier soft caps via retiering.
  7. Enforce the hard global cap (>500 -> GuardrailError).

Determinism: the same `(profile, target_config)` pair always yields the
same cluster_keys, in the same order. We achieve this by iterating the
template tuple in its declared order and by sorting slot value lists
stably inside the expander (we preserve declared order for each axis).
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import re
import uuid
from typing import Any, Iterable, Mapping, Sequence

from app.core_audit.demand_map.dto import (
    ClusterSource,
    ClusterType,
    QualityTier,
    SeedTemplate,
    TargetClusterDTO,
    VolumeTier,
)
from app.core_audit.demand_map.guardrails import (
    MAX_CARTESIAN_DEPTH,
    MAX_CLUSTERS_PER_SITE,
    MAX_GEO_PERMUTATIONS,
    MAX_PER_TEMPLATE,
    SOFT_CAPS_PER_TIER,
    cap_geo_permutations,
    enforce_global_cap,
    enforce_tier_caps,
)
from app.core_audit.demand_map.quality import classify_quality_tier
from app.core_audit.demand_map.relevance import compute_relevance

log = logging.getLogger(__name__)

_SLOT_RE = re.compile(r"\{([a-z_]+)\}")
_TOKEN_RE = re.compile(r"[\wёЁ]+", re.UNICODE)


# ---------- slot resolution -------------------------------------------------

# Which target_config keys feed which slot names. Order in each tuple
# defines precedence: the first non-empty source wins.
_SLOT_SOURCES: dict[str, tuple[str, ...]] = {
    "service": ("services",),
    "activity": ("services",),
    "city": ("geo_primary", "geo_secondary"),
    "destination": ("geo_primary", "geo_secondary"),
    "region": ("geo_primary", "geo_secondary"),
    "pickup_city": ("geo_primary", "geo_secondary"),
    "month": ("months",),
    "n": ("day_counts",),
}

# Modest defaults keep expansions bounded when target_config omits these
# slot types. Small vocabularies on purpose: month/day expansions multiply
# against the Cartesian product and can balloon past guardrails.
_DEFAULT_DAY_COUNTS: tuple[str, ...] = ("2",)
_DEFAULT_MONTHS_RU: tuple[str, ...] = ("июле", "августе", "январе")


def _slot_values(
    slot: str,
    target_config: Mapping[str, Any],
    geo_primary: Sequence[str],
    geo_secondary: Sequence[str],
) -> list[str]:
    """Return ordered unique candidate values for `slot`.

    Geo slots share the already-capped primary+secondary list. Numeric /
    seasonal slots fall back to defaults when target_config does not provide
    them, so templates can expand out-of-the-box for typical tourism sites.
    """
    if slot in ("city", "destination", "region", "pickup_city"):
        vals: list[str] = []
        seen: set[str] = set()
        for v in list(geo_primary) + list(geo_secondary):
            if v and v not in seen:
                vals.append(v)
                seen.add(v)
        return vals

    if slot in ("service", "activity"):
        services = target_config.get("services", []) or []
        out: list[str] = []
        seen = set()
        for v in services:
            if v and v not in seen:
                out.append(v)
                seen.add(v)
        return out

    if slot == "month":
        months = target_config.get("months")
        if months:
            return [str(m) for m in months if m]
        return list(_DEFAULT_MONTHS_RU)

    if slot == "n":
        day_counts = target_config.get("day_counts")
        if day_counts:
            return [str(d) for d in day_counts if d]
        return list(_DEFAULT_DAY_COUNTS)

    return list(target_config.get(slot, []) or [])


def _template_slot_names(template: SeedTemplate) -> tuple[str, ...]:
    """Extract `{slot}` names from the pattern, in declared left-to-right order."""
    seen: list[str] = []
    for m in _SLOT_RE.finditer(template.pattern):
        name = m.group(1)
        if name not in seen:
            seen.append(name)
    return tuple(seen)


def _literal_tokens(pattern: str) -> frozenset[str]:
    """Tokens in the pattern that are NOT slot names — guards against
    redundancy like `{activity} туры {region}` where activity=туры would
    produce "туры туры region".
    """
    # Remove {slot} placeholders first
    stripped = _SLOT_RE.sub(" ", pattern)
    return frozenset(m.group(0).lower() for m in _TOKEN_RE.finditer(stripped))


# ---------- key + keywords --------------------------------------------------


def _slot_hash(cluster_type: ClusterType, slots: Mapping[str, str]) -> str:
    """Short deterministic digest over cluster_type + sorted slot items.

    8 hex chars (32 bits) is enough for per-site dedup — site_id already
    scopes the namespace. SHA1 is used for stability across Python versions.
    """
    parts = [cluster_type.value]
    for k in sorted(slots.keys()):
        parts.append(f"{k}={slots[k]}")
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:10]


def _build_cluster_key(cluster_type: ClusterType, slots: Mapping[str, str]) -> str:
    return f"{cluster_type.value}:{_slot_hash(cluster_type, slots)}"


def _tokenize_ru(text: str) -> tuple[str, ...]:
    """Lowercase Cyrillic/latin tokens. Lightweight — Phase A has no morph."""
    return tuple(m.group(0).lower() for m in _TOKEN_RE.finditer(text))


def _fill_pattern(pattern: str, slots: Mapping[str, str]) -> str:
    def repl(m: re.Match) -> str:
        return slots.get(m.group(1), m.group(0))

    return _SLOT_RE.sub(repl, pattern)


# ---------- competitor + exclusion detection --------------------------------


def _is_competitor_brand_hit(
    slots: Mapping[str, str], competitor_brands: Sequence[str]
) -> bool:
    """Return True if any slot value matches a competitor brand (case-insensitive)."""
    if not competitor_brands:
        return False
    lowered = {b.lower() for b in competitor_brands if b}
    for v in slots.values():
        if v and v.lower() in lowered:
            return True
    return False


def _slots_touch_excluded(
    slots: Mapping[str, str],
    excluded: Sequence[str],
    slot_names: Iterable[str],
) -> bool:
    """Return True if any of the named slots holds an excluded value."""
    if not excluded:
        return False
    excl = {e.lower() for e in excluded if e}
    for name in slot_names:
        v = slots.get(name)
        if v and v.lower() in excl:
            return True
    return False


# ---------- main entry point ------------------------------------------------


def expand_for_site(
    profile: Any,
    target_config: Mapping[str, Any],
    *,
    site_id: uuid.UUID | None = None,
    max_clusters: int = MAX_CLUSTERS_PER_SITE,
    max_per_template: int = MAX_PER_TEMPLATE,
    max_depth: int = MAX_CARTESIAN_DEPTH,
    max_geo: int = MAX_GEO_PERMUTATIONS,
) -> list[TargetClusterDTO]:
    """Deterministic Cartesian expansion.

    `profile` may be any object with a `seed_cluster_templates` tuple
    attribute — duck-typed on purpose so tests can pass a lightweight
    namespace without importing the full vertical profile module.

    Returns a list of `TargetClusterDTO`s with per-tier soft caps enforced
    via retiering and the global hard cap enforced as the final step.
    An empty `target_config` or a profile with no templates returns [].
    """
    templates: tuple[SeedTemplate, ...] = tuple(
        getattr(profile, "seed_cluster_templates", ()) or ()
    )
    if not templates:
        return []

    # Normalize config.
    target_config = dict(target_config or {})
    if not target_config:
        return []

    services = list(target_config.get("services", []) or [])
    excluded_services = list(target_config.get("excluded_services", []) or [])
    excluded_geo = list(target_config.get("excluded_geo", []) or [])
    competitor_brands = list(target_config.get("competitor_brands", []) or [])

    geo_primary_raw = list(target_config.get("geo_primary", []) or [])
    geo_secondary_raw = list(target_config.get("geo_secondary", []) or [])
    geo_primary, geo_secondary = cap_geo_permutations(
        geo_primary_raw, geo_secondary_raw, cap=max_geo
    )
    primary_set = set(geo_primary)

    site_id_final = site_id or uuid.uuid5(
        uuid.NAMESPACE_DNS, f"demand_map:{target_config.get('domain', 'unknown')}"
    )

    emitted: dict[str, TargetClusterDTO] = {}

    for template in templates:
        slot_names = _template_slot_names(template)
        if len(slot_names) > max_depth:
            log.info(
                "demand_map.template_skipped_depth",
                extra={
                    "pattern": template.pattern,
                    "depth": len(slot_names),
                    "max_depth": max_depth,
                },
            )
            continue

        # Gather value lists per slot in declared pattern order.
        slot_value_axes: list[list[str]] = []
        valid_template = True
        for slot in slot_names:
            vals = _slot_values(slot, target_config, geo_primary, geo_secondary)
            if not vals:
                if slot in template.required_slots or (
                    not template.required_slots and slot in _SLOT_SOURCES
                ):
                    valid_template = False
                    break
                vals = [""]
            slot_value_axes.append(vals)

        if not valid_template:
            continue
        if not slot_value_axes:
            continue

        literal_tokens = _literal_tokens(template.pattern)

        per_template = 0
        for combo in itertools.product(*slot_value_axes):
            if per_template >= max_per_template:
                break

            filled: dict[str, str] = {
                name: val for name, val in zip(slot_names, combo) if val
            }
            # Missing any required slot -> skip.
            if any(r not in filled for r in template.required_slots):
                continue

            # Token-collision guard: skip if any filled value duplicates a
            # literal token already in the template pattern. Prevents things
            # like "экскурсии туры сочи" where {activity}=экскурсии fills a
            # pattern that already contains the literal "туры" — such a
            # combo is semantically redundant.
            if literal_tokens and any(
                val.lower() in literal_tokens for val in filled.values() if val
            ):
                continue
            if not filled:
                # Template with no slots — emit once.
                pass

            # Exclusion / competitor detection.
            is_competitor = _is_competitor_brand_hit(filled, competitor_brands)
            in_excl_geo = _slots_touch_excluded(
                filled,
                excluded_geo,
                ("city", "destination", "region", "pickup_city"),
            )
            in_excl_service = _slots_touch_excluded(
                filled, excluded_services, ("service", "activity")
            )

            effective_type = (
                ClusterType.competitor_brand if is_competitor else template.cluster_type
            )

            relevance = compute_relevance(
                cluster_type=effective_type,
                filled_slots=filled,
                target_config=target_config,
            )
            tier = classify_quality_tier(
                cluster_type=effective_type,
                business_relevance=relevance,
                is_competitor_brand=is_competitor,
                in_excluded_geo=in_excl_geo,
                in_excluded_service=in_excl_service,
            )

            cluster_key = _build_cluster_key(effective_type, filled)
            if cluster_key in emitted:
                # Deterministic dedup — same slots always collapse.
                continue

            name_ru = _fill_pattern(template.pattern, filled)
            keywords = _tokenize_ru(name_ru)

            # Geo tier confirmation: if a geo value is in primary, a modest
            # volume bump is reasonable — still bounded by template default.
            expected_volume = template.default_volume_tier
            geo_val = (
                filled.get("city")
                or filled.get("destination")
                or filled.get("region")
                or filled.get("pickup_city")
            )
            if geo_val and geo_val in primary_set and (
                expected_volume == VolumeTier.xs
            ):
                expected_volume = VolumeTier.s

            dto = TargetClusterDTO(
                site_id=site_id_final,
                cluster_key=cluster_key,
                name_ru=name_ru,
                intent_code=template.intent_code,
                cluster_type=effective_type,
                quality_tier=tier,
                keywords=keywords,
                seed_slots=dict(filled),
                is_brand=False,
                is_competitor_brand=is_competitor,
                expected_volume_tier=expected_volume,
                business_relevance=relevance,
                source=ClusterSource.cartesian,
            )
            emitted[cluster_key] = dto
            per_template += 1

    # Stable order preserved by dict insertion order (Py>=3.7).
    ordered = list(emitted.values())

    # Soft caps via retiering.
    retiered = enforce_tier_caps(ordered, SOFT_CAPS_PER_TIER)

    # Hard cap at the very end — raises GuardrailError if exceeded.
    return enforce_global_cap(retiered, max_n=max_clusters)
