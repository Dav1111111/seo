"""Query relevance — Studio v2 etap 4 / 5 cores.

Classifies each `search_queries` row as:

    own         — direct match: query mentions our primary product
                  AND a region we operate in. Owner clearly cares.
    adjacent    — different surface but the same customer would search
                  for it (e.g. «экскурсии Сочи» for a buggy-expedition
                  company in Sochi). LLM call, not rules.
    disputed    — could go either way; surface to owner for an opinion.
    spam        — homonym / unrelated topic («джинсы багги», «багги
                  своими руками» for a tour operator).
    unclassified— never been processed yet (default for new rows).

Three writers, in priority order:

    user        — owner clicks «нет, это мой» / «нет, это мусор»;
                  this verdict is FINAL and never overwritten.
    llm         — Haiku batch classifies anything `unclassified`.
    rules       — cheap regex pass: catches the obvious own cases
                  before paying the LLM.

This file is the rules-only part. The LLM piece lives in
`relevance_llm.py` and is invoked by the `classify_queries_site_task`
Celery task — they share the writer-priority contract and the
RELEVANCE_VALUES / SET_BY_VALUES allowlists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


# ── Allowed values (must match the migration's CHECK constraint) ───

RELEVANCE_VALUES = ("own", "adjacent", "disputed", "spam", "unclassified")
SET_BY_VALUES = ("rules", "llm", "user")


@dataclass(frozen=True)
class RelevanceVerdict:
    relevance: str
    set_by: str
    reason_ru: str


# ── Profile typing (loose — we only need a few keys) ───────────────

@dataclass
class ProfileSlice:
    """The minimal target_config slice the classifier needs."""
    primary_product: str
    services: list[str]
    secondary_products: list[str]
    geo_primary: list[str]
    geo_secondary: list[str]

    @classmethod
    def from_target_config(cls, cfg: dict | None) -> "ProfileSlice":
        cfg = cfg or {}

        def _strs(key: str) -> list[str]:
            raw = cfg.get(key) or []
            return [
                str(x).strip().lower()
                for x in raw
                if x and str(x).strip()
            ]

        return cls(
            primary_product=str(cfg.get("primary_product") or "").strip().lower(),
            services=_strs("services"),
            secondary_products=_strs("secondary_products"),
            geo_primary=_strs("geo_primary"),
            geo_secondary=_strs("geo_secondary"),
        )


# ── Rules classifier ───────────────────────────────────────────────

# Word-boundary substring test against the query. We use `\b` for
# Latin and `(?<![а-яёА-ЯЁ])`/`(?![а-яёА-ЯЁ])` for Cyrillic so
# «прокат» doesn't false-positive on «прокатился».
_CYR_WORD_LEFT = r"(?<![а-яёА-ЯЁa-zA-Z0-9])"
_CYR_WORD_RIGHT = r"(?![а-яёА-ЯЁa-zA-Z0-9])"


def _contains_token(text: str, token: str) -> bool:
    """Whole-word match for a single Russian/Latin token in text.

    Handles inflections like «багги», «багги-туры», «на багги» but
    rejects partial matches like «штаны багги» containing «багги»
    as a clothing modifier — that's why we DON'T do this on its own;
    the spam detection is left to the LLM. Rules only fire for the
    `own` verdict where the signal is unambiguous.
    """
    if not token:
        return False
    pattern = _CYR_WORD_LEFT + re.escape(token) + _CYR_WORD_RIGHT
    return re.search(pattern, text, re.IGNORECASE) is not None


def _contains_any(text: str, tokens: Iterable[str]) -> bool:
    return any(_contains_token(text, t) for t in tokens if t)


def classify_by_rules(
    query_text: str, profile: ProfileSlice,
) -> RelevanceVerdict | None:
    """Cheap first pass. Returns a verdict only when the signal is
    unambiguous; otherwise None → defer to LLM.

    What rules confidently catch:

      own:   query contains primary_product AND any region we operate
             in. The classic positive.

    What rules INTENTIONALLY don't catch:

      spam:  rules can't tell «багги» (vehicle) from «багги» (jeans).
             Needs LLM with business narrative for context.
      adjacent: requires understanding «экскурсии Сочи» fits a
             premium-buggy-expedition company. Pure semantics — LLM.
      disputed: judgment call by definition.

    So a None return means «I'm not sure, let LLM decide». That's the
    intended interaction — rules are optional cost-saver, not the
    arbiter.
    """
    if not query_text:
        return None
    if not profile.primary_product:
        # Profile incomplete → don't fabricate a verdict.
        return None

    text = query_text.strip().lower()

    has_primary = _contains_token(text, profile.primary_product)
    if not has_primary:
        return None

    # Primary product mentioned. Now check geo.
    all_geos = list(profile.geo_primary) + list(profile.geo_secondary)
    matched_geo = next((g for g in all_geos if _contains_token(text, g)), None)

    if matched_geo:
        return RelevanceVerdict(
            relevance="own",
            set_by="rules",
            reason_ru=(
                f"Содержит наш основной продукт «{profile.primary_product}» "
                f"и регион «{matched_geo}»"
            ),
        )

    # Has primary but no region — could still be ours, but ambiguous
    # ("багги тур" without geo could go anywhere). Defer to LLM.
    return None


__all__ = [
    "RELEVANCE_VALUES",
    "SET_BY_VALUES",
    "RelevanceVerdict",
    "ProfileSlice",
    "classify_by_rules",
]
