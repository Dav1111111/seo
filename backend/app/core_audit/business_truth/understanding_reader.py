"""understanding_reader — onboarding data → weighted direction map.

Reads two JSONB blobs on `sites`:
  • understanding      — LLM-authored narrative + owner-confirmed products
  • target_config      — structured services/geos + optional weights

Produces a list of (DirectionKey, weight) where weights sum to ~1.0.
Callers use this to (a) allocate discovery budget per direction and
(b) populate BusinessTruth.direction.strength_understanding.

Design notes:
  • Pure function — no DB, no I/O.
  • Empty inputs → empty list (discovery falls back to Webmaster-only
    pool with owner-less classification).
  • Unknown weight keys (for services/geos not in the declared list)
    are silently dropped; then remaining weights re-normalize to 1.0.
  • Missing weights for some entries get the "leftover" equally split.
"""

from __future__ import annotations

from typing import Iterable

from app.core_audit.business_truth.dto import DirectionKey


def _normalize_tokens(items: Iterable[str]) -> list[str]:
    """Dedupe + lowercase + strip; preserve first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for it in items or ():
        t = (it or "").strip().lower()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _resolve_weights(
    entries: list[str], raw_weights: dict | None,
) -> dict[str, float]:
    """Return {entry: weight} summing to 1.0.

    Unknown keys dropped. Entries missing from the weights dict take an
    equal share of the leftover. If no weights provided, equal split.
    """
    if not entries:
        return {}
    raw = raw_weights or {}
    # Drop unknown keys
    known = {
        k.strip().lower(): float(v)
        for k, v in raw.items()
        if k and k.strip().lower() in entries and v is not None
    }
    if not known:
        # No weights at all — equal split
        n = len(entries)
        return {e: 1.0 / n for e in entries}

    used_sum = sum(known.values())
    missing = [e for e in entries if e not in known]

    if missing:
        # Leftover share for entries without an explicit weight
        leftover = max(0.0, 1.0 - used_sum)
        per_missing = leftover / len(missing) if missing else 0.0
        for e in missing:
            known[e] = per_missing
        # Recompute in case leftover was 0 (all-assigned weights < 1)
        used_sum = sum(known.values())

    # Final normalize to exactly 1.0 (drops the "weights sum < 1" skew)
    if used_sum <= 0:
        n = len(entries)
        return {e: 1.0 / n for e in entries}
    return {e: v / used_sum for e, v in known.items()}


def read_understanding(
    understanding: dict | None,
    target_config: dict | None,
) -> list[tuple[DirectionKey, float]]:
    """Return [(DirectionKey, weight)] representing the owner's claimed
    direction map. Weights sum to 1.0 (or list is empty).
    """
    cfg = target_config or {}

    # Services list = services + secondary_products (both count)
    services = _normalize_tokens([
        *(cfg.get("services") or []),
        *(cfg.get("secondary_products") or []),
    ])

    # Geos list = primary + secondary
    geos = _normalize_tokens([
        *(cfg.get("geo_primary") or []),
        *(cfg.get("geo_secondary") or []),
    ])

    if not services or not geos:
        return []

    service_w = _resolve_weights(services, cfg.get("service_weights"))
    geo_w = _resolve_weights(geos, cfg.get("geo_weights"))

    out: list[tuple[DirectionKey, float]] = []
    for s in services:
        for g in geos:
            w = service_w.get(s, 0.0) * geo_w.get(g, 0.0)
            if w <= 0:
                continue
            out.append((DirectionKey.of(s, g), w))

    # Normalize cartesian weights to exactly 1.0 (multiplying two
    # probability distributions gives a joint that already sums to 1,
    # but floating-point drift + absent-keys mean we re-normalize).
    total = sum(w for _, w in out)
    if total <= 0:
        return []
    return [(k, w / total) for k, w in out]


__all__ = ["read_understanding"]
