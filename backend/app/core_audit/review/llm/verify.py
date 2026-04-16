"""Post-LLM validation — reject hallucinations and off-whitelist items.

Hard rules enforced:
  - `finding_id` in rewrites must be present in the set we actually sent.
  - `target_url` in link_proposals must be in ri.link_candidates URL set.
  - Phone/price/ИНН/РТО/ОГРН regex hits in after_text must exist in
    ri.content_text (otherwise the LLM made them up — drop the rewrite).
  - City tokens (Лоо/Адлер/Хоста/Дагомыс/Красная Поляна) in after_text
    must appear in ri.content_text or top_queries.
  - Cargo-cult enum values must be from the known set.

On rejection: drop the offending entry, KEEP the rest. Log warning.
"""

from __future__ import annotations

import logging
import re

from app.core_audit.review.dto import ReviewInput
from app.core_audit.review.llm.base import (
    LLMEnrichment,
    LLMH2Draft,
    LLMLinkProposal,
    LLMRewrite,
)

logger = logging.getLogger(__name__)

_PHONE_RE = re.compile(r"(?:\+?7|\b8)[\s\-\(\)]*\d{3}[\s\-\(\)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")
_PRICE_RE = re.compile(r"\b\d{3,}\s*(?:руб|₽|р\.|rub)\b", re.I)
_INN_RE = re.compile(r"\bИНН\s*:?\s*(?:\d{10}|\d{12})\b", re.I)
_RTO_RE = re.compile(r"\b(?:РТО|ТТ)\s*[-:]?\s*\d{4,}\b", re.I)
_OGRN_RE = re.compile(r"\bОГРН(?:ИП)?\s*:?\s*\d{13}(?!\d)", re.I)   # 13 digits for юрлицо

_CITY_TOKENS = frozenset({
    "лоо", "адлер", "хоста", "кудепста", "лазаревское", "дагомыс",
    "эсто-садок", "красная поляна", "мацеста",
})

CARGO_CULT_SCHEMA_TYPES = frozenset({
    "TouristTrip",
    "TouristAttraction",
    "TouristDestination",
    "Event",
    "TravelAction",
    "Trip",
})


def _fact_leaked(after: str, source: str, regex: re.Pattern) -> bool:
    """True if a fact matching regex is in `after` but NOT in `source`."""
    after_hits = set(regex.findall(after or ""))
    source_hits = set(regex.findall(source or ""))
    return bool(after_hits - source_hits)


def _city_leaked(after: str, source_text: str) -> bool:
    after_l = (after or "").lower()
    source_l = (source_text or "").lower()
    for city in _CITY_TOKENS:
        if city in after_l and city not in source_l:
            return True
    return False


def verify(
    enrichment: LLMEnrichment,
    ri: ReviewInput,
    sent_finding_ids: set[str],
) -> LLMEnrichment:
    """Filter an LLMEnrichment. Returns a new enrichment with invalid items dropped."""
    source_text = " ".join(filter(None, [
        ri.content_text or "",
        " ".join(ri.top_queries),
        ri.title or "",
        ri.h1 or "",
    ]))

    # Rewrites
    kept_rewrites: list[LLMRewrite] = []
    for rw in enrichment.rewrites:
        if rw.finding_id not in sent_finding_ids:
            logger.warning("llm: rewrite for unknown finding_id=%r dropped", rw.finding_id)
            continue
        if _fact_leaked(rw.after_text, source_text, _PHONE_RE):
            logger.warning("llm: phone hallucination in rewrite %s dropped", rw.finding_id)
            continue
        if _fact_leaked(rw.after_text, source_text, _PRICE_RE):
            logger.warning("llm: price hallucination in rewrite %s dropped", rw.finding_id)
            continue
        if _fact_leaked(rw.after_text, source_text, _INN_RE):
            logger.warning("llm: ИНН hallucination in rewrite %s dropped", rw.finding_id)
            continue
        if _fact_leaked(rw.after_text, source_text, _RTO_RE):
            logger.warning("llm: РТО hallucination in rewrite %s dropped", rw.finding_id)
            continue
        if _fact_leaked(rw.after_text, source_text, _OGRN_RE):
            logger.warning("llm: ОГРН hallucination in rewrite %s dropped", rw.finding_id)
            continue
        if _city_leaked(rw.after_text, source_text):
            logger.warning("llm: city hallucination in rewrite %s dropped", rw.finding_id)
            continue
        # Title rewrites specifically must stay ≤65 chars
        if rw.finding_id.startswith("title_") and len(rw.after_text) > 65:
            logger.warning("llm: title rewrite over 65 chars (%d) dropped", len(rw.after_text))
            continue
        kept_rewrites.append(rw)

    # H2 drafts
    kept_h2: list[LLMH2Draft] = []
    for d in enrichment.h2_drafts:
        if _fact_leaked(d.draft_ru, source_text, _PHONE_RE) or \
           _fact_leaked(d.draft_ru, source_text, _PRICE_RE) or \
           _fact_leaked(d.draft_ru, source_text, _INN_RE) or \
           _fact_leaked(d.draft_ru, source_text, _RTO_RE) or \
           _fact_leaked(d.draft_ru, source_text, _OGRN_RE):
            logger.warning("llm: fact hallucination in H2 draft %r dropped", d.block_title)
            continue
        if _city_leaked(d.draft_ru, source_text):
            logger.warning("llm: city hallucination in H2 draft %r dropped", d.block_title)
            continue
        kept_h2.append(d)

    # Link proposals — target_url whitelist
    allowed_urls = {lc.url for lc in ri.link_candidates}
    kept_links: list[LLMLinkProposal] = []
    for lp in enrichment.link_proposals:
        if lp.target_url not in allowed_urls:
            logger.warning("llm: link target_url=%r not in candidates — dropped", lp.target_url)
            continue
        kept_links.append(lp)

    # Cargo-cult list: keep only recognised types
    kept_cargo = tuple(
        s for s in enrichment.detected_cargo_cult_schemas
        if s in CARGO_CULT_SCHEMA_TYPES
    )

    return LLMEnrichment(
        rewrites=tuple(kept_rewrites),
        h2_drafts=tuple(kept_h2),
        link_proposals=tuple(kept_links),
        detected_cargo_cult_schemas=kept_cargo,
    )
