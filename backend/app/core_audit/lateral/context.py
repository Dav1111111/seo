"""Assemble the LLM input snapshot for one site.

Reads only data that's already in our DB. No external API calls — the
weekly task is purely DB-bound (apart from the single LLM call later).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.lateral.dto import LateralContext, normalize_query
from app.models.lateral_query import LateralQuery
from app.models.search_query import SearchQuery
from app.models.site import Site

# How many observed queries (top by wordstat_volume) to surface to the
# LLM. More than ~25 starts to dilute the signal and waste tokens; the
# LLM only needs a representative slice of "what the site already
# ranks for" to propose *adjacent* ideas.
OBSERVED_QUERIES_TOP_N = 25


async def build_context(db: AsyncSession, site: Site) -> LateralContext:
    site_id = site.id

    tc = site.target_config or {}
    understanding = site.understanding or {}

    services = _flatten_named(tc.get("services") or [])
    geo = _flatten_named(tc.get("geo_primary") or []) + _flatten_named(
        tc.get("geo_secondary") or [],
    )

    business_summary = _compose_business_summary(site, tc, understanding)
    strategic_focus = _strategic_focus_str(tc)

    competitor_brands = _competitor_brands_from(site, understanding)

    top_observed = await _load_top_observed(db, site_id)
    existing_norms = await _load_existing_norms(db, site_id)

    return LateralContext(
        site_id=str(site_id),
        domain=site.domain,
        business_summary=business_summary,
        services=services[:10],
        geo=geo[:10],
        competitor_brands=competitor_brands[:5],
        top_observed_queries=top_observed,
        existing_lateral_norms=existing_norms,
        strategic_focus=strategic_focus,
    )


def _flatten_named(items: list) -> list[str]:
    out: list[str] = []
    for it in items:
        if isinstance(it, str):
            if it.strip():
                out.append(it.strip())
        elif isinstance(it, dict):
            name = it.get("name") or it.get("title") or it.get("value")
            if name and isinstance(name, str) and name.strip():
                out.append(name.strip())
    return out


def _strategic_focus_str(tc: dict) -> str | None:
    sf = tc.get("strategic_focus")
    if not sf:
        return None
    if isinstance(sf, str):
        return sf.strip() or None
    if isinstance(sf, dict):
        products = _flatten_named(sf.get("products") or [])
        regions = _flatten_named(sf.get("regions") or [])
        bits: list[str] = []
        if products:
            bits.append("продукты: " + ", ".join(products[:5]))
        if regions:
            bits.append("регионы: " + ", ".join(regions[:5]))
        return "; ".join(bits) or None
    return None


def _compose_business_summary(site: Site, tc: dict, understanding: dict) -> str:
    """Two-sentence summary the LLM uses as primary anchor.

    Prefers `target_config.narrative_ru` (owner-curated), falls back to
    `understanding.detected_niche` and primary_product, then to the
    bare domain.
    """
    narrative = tc.get("narrative_ru") or understanding.get("narrative_ru")
    if isinstance(narrative, str) and narrative.strip():
        return narrative.strip()[:600]

    primary = tc.get("primary_product") or understanding.get("detected_niche")
    if primary:
        return str(primary)[:600]

    return f"Сайт {site.domain}; целевая ниша не зафиксирована."


def _competitor_brands_from(site: Site, understanding: dict) -> list[str]:
    domains = site.competitor_domains or []
    out: list[str] = []
    for d in domains:
        if isinstance(d, str) and d.strip():
            out.append(d.strip())
        elif isinstance(d, dict):
            name = (
                d.get("brand_name")
                or d.get("name")
                or d.get("domain")
            )
            if name and isinstance(name, str):
                out.append(name.strip())
    if not out:
        # Some sites store competitors only under understanding.
        u_comp = understanding.get("competitors") or []
        for c in u_comp:
            if isinstance(c, dict):
                nm = c.get("brand_name") or c.get("name") or c.get("domain")
                if isinstance(nm, str) and nm.strip():
                    out.append(nm.strip())
    return out


async def _load_top_observed(
    db: AsyncSession, site_id: UUID,
) -> list[dict]:
    """Top observed queries (non-branded) by Wordstat volume.

    Brand-aware: we drop is_branded queries — they're noise for *lateral*
    ideas (the LLM should not be repeating the brand back at us). We
    also drop relevance='spam' / 'disputed' to keep the seed clean.
    """
    stmt = (
        select(SearchQuery.query_text, SearchQuery.wordstat_volume)
        .where(
            SearchQuery.site_id == site_id,
            SearchQuery.is_branded.is_(False),
            SearchQuery.relevance.in_(("own", "adjacent", "unclassified")),
        )
        .order_by(SearchQuery.wordstat_volume.desc().nullslast())
        .limit(OBSERVED_QUERIES_TOP_N)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {"query": q, "volume": int(v) if v is not None else None}
        for q, v in rows
    ]


async def _load_existing_norms(
    db: AsyncSession, site_id: UUID,
) -> set[str]:
    """Already-known lateral norms — passed to the LLM as 'don't repeat'.

    Cheap UNIQUE-aware short-circuit: even if the LLM ignores our hint,
    the DB's `uq_lateral_queries_site_norm` plus the persistence helper
    will still de-duplicate. This is purely to save Haiku tokens.
    """
    stmt = select(LateralQuery.query_norm).where(
        LateralQuery.site_id == site_id,
    )
    rows = (await db.execute(stmt)).all()
    return {normalize_query(r[0]) for r in rows if r[0]}
