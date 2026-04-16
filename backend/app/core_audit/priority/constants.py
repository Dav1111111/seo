"""Tunable weight maps for the priority scorer.

Kept module-level so tests can assert values and ops can adjust without
touching the scoring function itself.
"""

from __future__ import annotations

import re


# ── Component weights in final score ──────────────────────────────────
WEIGHT_IMPACT = 0.55
WEIGHT_CONFIDENCE = 0.20
WEIGHT_EASE = 0.25


# ── Impact subcomponents ──────────────────────────────────────────────
# log2 — better spread for tourism's 100–10000 impressions range
IMP_CAP = 10_000

# RecPriority → normalized weight
PRIORITY_WEIGHTS: dict[str, float] = {
    "critical": 1.00,
    "high": 0.60,
    "medium": 0.32,
    "low": 0.12,
}

# Category → how much this category moves Yandex ranking (Impact multiplier)
CATEGORY_IMPACT_WEIGHT: dict[str, float] = {
    "title": 1.00,
    "h1_structure": 0.95,
    "meta_description": 0.60,          # CTR signal, not ranking
    "schema": 0.70,
    "eeat": 0.80,
    "commercial": 0.75,
    "over_optimization": 0.85,
    "internal_linking": 0.65,
}
DEFAULT_CATEGORY_IMPACT = 0.70


# ── Confidence subcomponents ──────────────────────────────────────────
# Reviewer model → how much to trust the rewrite
MODEL_BOOST: dict[str, float] = {
    "python-only": 0.80,
    "claude-haiku-4-5": 0.90,
    "python+claude-haiku-4-5": 1.00,
    "python+claude-haiku-4-5-20251001": 1.00,
}
DEFAULT_MODEL_BOOST = 0.75

# Signal-type → detection certainty class
SIGNAL_CERTAINTY: dict[str, float] = {
    # High — regex-on-present-text, deterministic
    "title_length": 1.00,
    "title_missing": 1.00,
    "h1_missing": 1.00,
    "h1_equals_title": 1.00,
    "density_title": 0.95,
    "density_h1": 0.95,
    "density_body": 0.95,
    # Medium — regex on content, can false-negative on different formats
    "title_keyword_repetition": 0.85,
    "eeat_signal_missing": 0.80,
    "commercial_factor_missing": 0.80,
    "missing_critical_h2": 0.80,
    "missing_recommended_h2": 0.80,
    "schema_missing": 0.70,            # boolean-only in v1
    # Lower — LLM-detected / DOM-position
    "schema_cargo_cult_present": 0.65,
    "commercial_factor_deferred_to_llm": 0.55,
    "over_optimization_stuffing": 0.85,
}
DEFAULT_SIGNAL_CERTAINTY = 0.70

# Confidence floor for schema recs — below this we drop the rec entirely
# (SEO rationale: uncertain schema recommendations erode trust).
SCHEMA_CONFIDENCE_FLOOR = 0.70


# ── Ease subcomponents (minutes to implement) ─────────────────────────
# Lower = easier = higher Ease score
CATEGORY_EASE_MINUTES: dict[str, int] = {
    "title": 5,
    "meta_description": 10,
    "h1_structure": 15,
    "over_optimization": 20,
    "internal_linking": 30,
    "commercial": 20,
    "schema": 90,
    "eeat": 30,
}
DEFAULT_EASE_MINUTES = 60

# Per-signal override — both eeat_signal_missing and commercial_factor_missing
# hide wildly different fixes under a single category. Key by
# (signal_type, extra_discriminator) via a two-layer lookup: (signal_type, name)
# where `name` is evidence.signal_name or evidence.factor_name.
SIGNAL_EASE_OVERRIDE: dict[str, int] = {
    "h1_equals_title": 3,
    "h1_missing": 10,
    "title_length": 5,
    "title_missing": 15,
    "title_keyword_repetition": 5,
    "density_title": 5,
    "density_h1": 5,
    "density_body": 20,
    "missing_critical_h2": 18,
    "missing_recommended_h2": 12,
    "schema_missing": 45,              # generic JSON-LD install
    "schema_cargo_cult_present": 30,
    "over_optimization_stuffing": 15,
    "commercial_factor_deferred_to_llm": 30,
}

# Two-layer: signal_type + discriminator (e.g. "eeat_signal_missing:rto_number")
SIGNAL_KEYED_EASE_OVERRIDE: dict[str, int] = {
    "eeat_signal_missing:rto_number": 5,        # registry lookup + display
    "eeat_signal_missing:inn": 22,
    "eeat_signal_missing:ogrn": 22,
    "eeat_signal_missing:license_section": 10,
    "eeat_signal_missing:author_byline": 20,
    "eeat_signal_missing:reviews_block": 15,
    "eeat_signal_missing:yandex_maps_reviews": 18,

    "commercial_factor_missing:phone_in_header": 10,
    "commercial_factor_missing:callback_form": 10,
    "commercial_factor_missing:price_above_fold": 20,
    "commercial_factor_missing:schedule_block": 20,
    "commercial_factor_missing:payment_icons": 20,
    "commercial_factor_missing:rto_in_footer": 22,
    "commercial_factor_missing:reviews_with_schema": 8,
    "commercial_factor_missing:yandex_maps_address": 10,
    "commercial_factor_missing:contract_offer": 20,
}

EASE_CAP_MINUTES = 480                           # 8 hours → Ease floor
DRAFTED_EASE_BONUS = 0.15                        # LLM already produced after_text


# ── Seasonality (Russian tourism) ─────────────────────────────────────
# Current Russian season months (MSK). April-August = summer tourism peak.
SEASONAL_MONTHS = frozenset({4, 5, 6, 7, 8})
SEASONAL_BOOST = 0.05                            # added to impact subcomponent

SEASONAL_TOURISM_RE = re.compile(
    r"\bлет[оа]\w*|\bпляж\w*|\bкупать\w*|\bсезон\w*|\bавгуст\w*|\bиюл\w*|\bмор[ея]\b",
    re.IGNORECASE | re.UNICODE,
)


# ── Deferred/applied status behavior ──────────────────────────────────
DEFERRED_SCORE_MULTIPLIER = 0.5                  # deferred recs halved, not excluded


# ── Defaults for missing context fields ───────────────────────────────
DEFAULT_CURRENT_SCORE = 3.0                      # mid-range when score unknown
DEFAULT_DETECTOR_CONFIDENCE = 0.60
DEFAULT_IMPRESSIONS_FLOOR = 0.15                 # long-tail pages still rankable
