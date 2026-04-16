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
    SEASONAL_TOURISM_RE,
    SIGNAL_CERTAINTY,
    SIGNAL_EASE_OVERRIDE,
    SIGNAL_KEYED_EASE_OVERRIDE,
    WEIGHT_CONFIDENCE,
    WEIGHT_EASE,
    WEIGHT_IMPACT,
)
from app.core_audit.priority.dto import ScoreBreakdown

SCORER_VERSION = "1.0.0"


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

    # Seasonality boost — applied post-composition (transparent in notes)
    if _seasonal_match(ctx):
        score = min(100.0, score * (1.0 + SEASONAL_BOOST))
        notes.append("seasonal_boost")

    # Deferred status → half the score
    if ctx.user_status == "deferred":
        score *= DEFERRED_SCORE_MULTIPLIER
        notes.append("deferred_penalty")

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

    impact = 0.45 * imp_norm + 0.25 * gap_norm + 0.20 * prio_weight + 0.10 * cat_weight
    impact = max(min(impact, 1.0), 0.0)
    return impact, {
        "impressions_norm": round(imp_norm, 4),
        "score_gap_norm": round(gap_norm, 4),
        "priority_weight": prio_weight,
        "category_weight": cat_weight,
    }


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
