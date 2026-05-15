"""Pure priority scorer — Impact × Confidence × Ease.

Stateless transformation: takes a Recommendation view + context (finding
hint, review metadata, coverage signals) and returns a ScoreBreakdown.
No DB, no I/O — all inputs are explicit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from app.core_audit.priority.constants import (
    CATEGORY_EASE_MINUTES,
    CATEGORY_IMPACT_WEIGHT,
    DEFAULT_CATEGORY_IMPACT,
    DEFAULT_CURRENT_SCORE,
    DEFAULT_DETECTOR_CONFIDENCE,
    DEFAULT_EASE_MINUTES,
    DEFAULT_IMPRESSIONS_FLOOR,
    DEFAULT_MODEL_BOOST,
    DEFAULT_SIGNAL_CERTAINTY,
    DEFERRED_SCORE_MULTIPLIER,
    DRAFTED_EASE_BONUS,
    EASE_CAP_MINUTES,
    IMP_CAP,
    MODEL_BOOST,
    PRIORITY_WEIGHTS,
    SCHEMA_CONFIDENCE_FLOOR,
    SEASONAL_BOOST,
    SEASONAL_MONTHS,
    SEASONAL_PRE_SEASON_BOOST,
    SEASONAL_TOURISM_RE,
    SIGNAL_CERTAINTY,
    SIGNAL_EASE_OVERRIDE,
    SIGNAL_KEYED_EASE_OVERRIDE,
    SUMMER_PEAK_MONTHS,
    SUMMER_PRE_SEASON_MONTHS,
    SUMMER_TOURISM_RE,
    WEIGHT_CONFIDENCE,
    WEIGHT_EASE,
    WEIGHT_IMPACT,
    WINTER_PEAK_MONTHS,
    WINTER_PRE_SEASON_MONTHS,
    WINTER_TOURISM_RE,
)
from app.core_audit.priority.dto import ScoreBreakdown

SCORER_VERSION = "1.0.0"


# ── Funnel-layer impact weights (added 2026-05-16) ────────────────────
#
# Multiplier applied to `impact` based on the funnel layer of the top
# query feeding this recommendation. Bottom-funnel («direct_product»)
# gets full weight; «funnel_top» gets half because the conversion gap
# is much wider; «out_of_market» / «spam» / «disputed» drop to zero
# so we never prioritise work on queries that aren't our market.
#
# Legacy aliases (own / adjacent) preserved so existing recs scored
# before Agent 1's backfill don't suddenly de-rank.
FUNNEL_WEIGHTS: dict[str, float] = {
    "direct_product": 1.0,
    "own": 1.0,             # legacy alias for direct_product
    "funnel_warm": 0.7,
    "adjacent": 0.7,        # legacy alias for funnel_warm
    "funnel_top": 0.5,
    "out_of_market": 0.0,
    "disputed": 0.0,
    "spam": 0.0,
    "unclassified": 0.3,    # neutral-low default — better than zeroing
}

# Used when `top_query_relevance` is None (legacy / unknown). Keeping
# this at 1.0 (NOT 0.3) so the scorer stays byte-identical to the
# pre-funnel version on every existing call site.
_FUNNEL_WEIGHT_DEFAULT = 1.0


@dataclass(frozen=True)
class ScorerContext:
    """All inputs required to score a recommendation.

    Keeping this separate from ORM row so the scorer is pure + testable.
    """
    # From PageReviewRecommendation
    category: str
    priority: str                             # critical|high|medium|low
    user_status: str                          # pending|applied|dismissed|deferred
    has_after_text: bool

    # From source CheckFinding (routed via Recommendation.source_finding_id)
    signal_type: str | None
    signal_name: str | None                   # evidence.signal_name / factor_name
    detector_confidence: float | None         # CheckFinding.confidence

    # From PageReview
    reviewer_model: str

    # From CoverageDecision
    total_impressions_14d: int

    # From PageIntentScore for target intent
    current_score: float                      # 0-5

    # Top query text (for seasonality check)
    top_query: str | None

    # "Today" — injectable for deterministic tests
    today: date | None = None

    # ── Phase D — Target Demand Map integration (all optional) ────────
    # When the USE_TARGET_DEMAND_MAP flag is on, PriorityService looks up
    # the best-matching TargetCluster for the recommendation's intent
    # and attaches these fields. When None, the scorer falls back to
    # the legacy observed-only impact formula.
    target_cluster_relevance: float | None = None
    is_brand_cluster: bool = False
    site_non_brand_coverage_ratio: float | None = None
    # Cluster-level coverage_score (0..1) for the matched target_cluster —
    # used by the Phase D impact formula. None in legacy mode.
    current_coverage_score: float | None = None

    # Funnel layer of the top query driving this recommendation. One of
    # `direct_product` / `funnel_warm` / `funnel_top` / `out_of_market` /
    # `spam` / `disputed` / `unclassified` / legacy `own` / `adjacent`,
    # or None when no query info is available (the scorer falls back
    # to a neutral 1.0 multiplier in that case — keeping every existing
    # caller byte-identical).
    top_query_relevance: str | None = None


def score_recommendation(ctx: ScorerContext) -> ScoreBreakdown | None:
    """Score a recommendation. Returns None if the rec should be dropped
    from priorities (e.g. schema rec below confidence floor)."""
    impact, impact_parts = _impact(ctx)
    confidence, confidence_parts = _confidence(ctx)

    # Schema recs below confidence floor → drop (no score at all)
    if ctx.category == "schema" and confidence < SCHEMA_CONFIDENCE_FLOOR:
        return None

    ease, ease_parts = _ease(ctx)
    score = 100.0 * (
        WEIGHT_IMPACT * impact
        + WEIGHT_CONFIDENCE * confidence
        + WEIGHT_EASE * ease
    )

    notes: list[str] = []

    # Seasonality boost — applied post-composition (transparent in notes).
    # Pre-season window gets a higher boost than peak season because
    # preparation work compounds (Yandex re-ranks gradually over weeks).
    # When the new summer/winter classifier matches we record only the
    # specific kind (`season:{kind}`) — the legacy `seasonal_boost`
    # note is reserved for the regex-only fallback below so the two
    # paths stay distinguishable in logs and tests.
    season_kind, season_boost = _seasonal_classification(ctx)
    if season_kind:
        score = min(100.0, score * (1.0 + season_boost))
        notes.append(f"season:{season_kind}")
    elif _seasonal_match(ctx):
        # Legacy path for any non-summer/winter pattern hit by the
        # original SEASONAL_TOURISM_RE regex (kept so old behavior
        # never regresses).
        score = min(100.0, score * (1.0 + SEASONAL_BOOST))
        notes.append("seasonal_boost")

    # Deferred status → half the score
    if ctx.user_status == "deferred":
        score *= DEFERRED_SCORE_MULTIPLIER
        notes.append("deferred_penalty")

    # Phase D — brand dampening: if the recommendation belongs to a brand
    # cluster AND the site still has weak non-brand coverage foundation,
    # halve the score so non-brand work surfaces first.
    if (
        ctx.is_brand_cluster
        and ctx.site_non_brand_coverage_ratio is not None
        and ctx.site_non_brand_coverage_ratio < 0.3
    ):
        score *= 0.5
        notes.append("brand_deprioritized_until_nonbrand_foundation")

    return ScoreBreakdown(
        impact=round(impact, 4),
        confidence=round(confidence, 4),
        ease=round(ease, 4),
        priority_score=round(score, 2),
        impact_parts=impact_parts,
        confidence_parts=confidence_parts,
        ease_parts=ease_parts,
        notes=tuple(notes),
    )


# ── Components ────────────────────────────────────────────────────────

def _impact(ctx: ScorerContext) -> tuple[float, dict]:
    # log2-scaled impressions, floored
    imp = max(int(ctx.total_impressions_14d or 0), 0)
    imp_norm = math.log2(imp + 1) / math.log2(IMP_CAP + 1)
    imp_norm = max(min(imp_norm, 1.0), DEFAULT_IMPRESSIONS_FLOOR)

    # Score gap vs ideal 5.0
    score = ctx.current_score if ctx.current_score is not None else DEFAULT_CURRENT_SCORE
    gap_norm = max(min((5.0 - score) / 5.0, 1.0), 0.0)

    # Priority mapped to weight
    prio_weight = PRIORITY_WEIGHTS.get(ctx.priority, 0.32)

    # Category weight
    cat_weight = CATEGORY_IMPACT_WEIGHT.get(ctx.category, DEFAULT_CATEGORY_IMPACT)

    # Phase D — if we have a matched target_cluster, blend observed
    # impressions with a cluster-gap-weighted component driven by
    # business_relevance. Legacy path (target_cluster_relevance is None)
    # uses the original formula byte-identically.
    if ctx.target_cluster_relevance is not None:
        cluster_cov = (
            ctx.current_coverage_score
            if ctx.current_coverage_score is not None
            else 0.0
        )
        cluster_cov = max(0.0, min(1.0, float(cluster_cov)))
        rel = max(0.0, min(1.0, float(ctx.target_cluster_relevance)))
        cluster_gap_weighted = (1.0 - cluster_cov) * rel
        imp_component = 0.6 * cluster_gap_weighted + 0.4 * imp_norm
        impact = (
            0.45 * imp_component
            + 0.25 * gap_norm
            + 0.20 * prio_weight
            + 0.10 * cat_weight
        )
        impact = max(min(impact, 1.0), 0.0)
        # Funnel-layer multiplier — same shape as the legacy path below.
        # Stays neutral (1.0) when no funnel info passed, so every Phase-D
        # caller that doesn't yet populate top_query_relevance gets the
        # exact same number as before.
        funnel_weight = _funnel_weight_for(ctx.top_query_relevance)
        if funnel_weight != 1.0:
            impact = max(min(impact * funnel_weight, 1.0), 0.0)
        return impact, {
            "impressions_norm": round(imp_norm, 4),
            "score_gap_norm": round(gap_norm, 4),
            "priority_weight": prio_weight,
            "category_weight": cat_weight,
            "cluster_gap_weighted": round(cluster_gap_weighted, 4),
            "target_cluster_relevance": round(rel, 4),
            "cluster_coverage_score": round(cluster_cov, 4),
            "imp_component": round(imp_component, 4),
            "funnel_weight": round(funnel_weight, 4),
        }

    impact = 0.45 * imp_norm + 0.25 * gap_norm + 0.20 * prio_weight + 0.10 * cat_weight
    impact = max(min(impact, 1.0), 0.0)

    # Funnel-layer multiplier (added 2026-05-16). When the caller passes
    # `top_query_relevance`, we scale impact by the corresponding weight
    # — out_of_market / spam / disputed go to zero so we never prioritise
    # work on queries that aren't our market. When None (legacy), the
    # default 1.0 keeps every existing test byte-identical.
    funnel_weight = _funnel_weight_for(ctx.top_query_relevance)
    if funnel_weight != 1.0:
        impact = max(min(impact * funnel_weight, 1.0), 0.0)
    return impact, {
        "impressions_norm": round(imp_norm, 4),
        "score_gap_norm": round(gap_norm, 4),
        "priority_weight": prio_weight,
        "category_weight": cat_weight,
        "funnel_weight": round(funnel_weight, 4),
    }


def _funnel_weight_for(relevance: str | None) -> float:
    """Resolve a funnel-aware multiplier from a SearchQuery.relevance.

    None / unknown values fall back to a neutral 1.0 so the scorer
    stays backward-compatible with every existing call site that
    doesn't pass funnel info yet.
    """
    if relevance is None:
        return _FUNNEL_WEIGHT_DEFAULT
    return FUNNEL_WEIGHTS.get(relevance, _FUNNEL_WEIGHT_DEFAULT)


def _confidence(ctx: ScorerContext) -> tuple[float, dict]:
    det = ctx.detector_confidence
    if det is None:
        det = DEFAULT_DETECTOR_CONFIDENCE
    det = max(min(float(det), 1.0), 0.0)

    cert = (
        SIGNAL_CERTAINTY.get(ctx.signal_type, DEFAULT_SIGNAL_CERTAINTY)
        if ctx.signal_type else DEFAULT_SIGNAL_CERTAINTY
    )
    boost = MODEL_BOOST.get(ctx.reviewer_model, DEFAULT_MODEL_BOOST)

    confidence = 0.5 * det + 0.3 * cert + 0.2 * boost
    confidence = max(min(confidence, 1.0), 0.0)
    return confidence, {
        "detector_confidence": round(det, 4),
        "signal_certainty": round(cert, 4),
        "model_boost": round(boost, 4),
    }


def _ease(ctx: ScorerContext) -> tuple[float, dict]:
    minutes = _lookup_ease_minutes(ctx)
    bonus = DRAFTED_EASE_BONUS if ctx.has_after_text else 0.0
    raw = 1.0 - (math.log1p(minutes) / math.log1p(EASE_CAP_MINUTES))
    ease = max(min(raw + bonus, 1.0), 0.05)
    return ease, {
        "minutes": minutes,
        "drafted_bonus": bonus,
    }


def _lookup_ease_minutes(ctx: ScorerContext) -> int:
    """Most specific override wins: keyed → signal → category."""
    if ctx.signal_type and ctx.signal_name:
        keyed = SIGNAL_KEYED_EASE_OVERRIDE.get(f"{ctx.signal_type}:{ctx.signal_name}")
        if keyed is not None:
            return keyed
    if ctx.signal_type:
        sig = SIGNAL_EASE_OVERRIDE.get(ctx.signal_type)
        if sig is not None:
            return sig
    return CATEGORY_EASE_MINUTES.get(ctx.category, DEFAULT_EASE_MINUTES)


def _seasonal_match(ctx: ScorerContext) -> bool:
    today = ctx.today or date.today()
    if today.month not in SEASONAL_MONTHS:
        return False
    q = ctx.top_query or ""
    return bool(SEASONAL_TOURISM_RE.search(q))


def _seasonal_classification(ctx: ScorerContext) -> tuple[str | None, float]:
    """Classify a recommendation by season + return the boost amount.

    Returns one of:
      ("summer_pre_season", SEASONAL_PRE_SEASON_BOOST)
      ("summer_peak",       SEASONAL_BOOST)
      ("winter_pre_season", SEASONAL_PRE_SEASON_BOOST)
      ("winter_peak",       SEASONAL_BOOST)
      (None, 0.0)
    """
    today = ctx.today or date.today()
    month = today.month
    q = ctx.top_query or ""
    if not q:
        return None, 0.0

    if SUMMER_TOURISM_RE.search(q):
        if month in SUMMER_PRE_SEASON_MONTHS:
            return "summer_pre_season", SEASONAL_PRE_SEASON_BOOST
        if month in SUMMER_PEAK_MONTHS:
            return "summer_peak", SEASONAL_BOOST

    if WINTER_TOURISM_RE.search(q):
        if month in WINTER_PRE_SEASON_MONTHS:
            return "winter_pre_season", SEASONAL_PRE_SEASON_BOOST
        if month in WINTER_PEAK_MONTHS:
            return "winter_peak", SEASONAL_BOOST

    return None, 0.0
