"""Individual guide — self-employed guide (самозанятый / ИП), not a company.

Differs from tour_operator:
  - No РТО or ОГРН
  - ИНН of individual
  - 'author_byline' becomes critical — authority signal is the person
  - Schema: Person + TouristGuide over Organization
"""

from __future__ import annotations

from app.core_audit.profile_protocol import EEATSignal
from app.core_audit.registry import apply_overlay, register_profile
from app.profiles.tourism import TOURISM_TOUR_OPERATOR


_kept_names = {"inn", "author_byline", "reviews_block", "yandex_maps_reviews"}
_kept = tuple(s for s in TOURISM_TOUR_OPERATOR.eeat_signals if s.name in _kept_names)
_upgraded = tuple(
    EEATSignal(
        name=s.name,
        pattern=s.pattern,
        weight=s.weight if s.name != "author_byline" else 0.35,
        priority="critical" if s.name == "author_byline" else s.priority,
    )
    for s in _kept
)

# Most schema types still apply; add Person for individuals
_schema_adjusted = {**TOURISM_TOUR_OPERATOR.schema_rules}
# Person attaches naturally to the brand page for individuals
from app.core_audit.intent_codes import IntentCode  # noqa: E402

_schema_adjusted[IntentCode.TRANS_BRAND] = ("Person", "BreadcrumbList")


INDIVIDUAL_GUIDE_PROFILE = apply_overlay(
    TOURISM_TOUR_OPERATOR,
    {
        "business_model": "individual_guide",
        "eeat_signals": _upgraded,
        "schema_rules": _schema_adjusted,
    },
)

register_profile("tourism", "individual_guide", INDIVIDUAL_GUIDE_PROFILE)
