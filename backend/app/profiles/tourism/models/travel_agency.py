"""Travel agency — resells tours, does not operate them.

Differs from tour_operator:
  - РТО (реестр туроператоров) NOT required — agency sells another operator's tours
  - ИНН/ОГРН still required
  - Commercial factor 'rto_in_footer' downgraded from critical → medium
"""

from __future__ import annotations

from app.core_audit.profile_protocol import CommercialFactor
from app.core_audit.registry import apply_overlay, register_profile
from app.profiles.tourism import TOURISM_TOUR_OPERATOR


_eeat_without_rto = tuple(
    s for s in TOURISM_TOUR_OPERATOR.eeat_signals if s.name != "rto_number"
)

_commercial_softened = tuple(
    CommercialFactor(
        name=cf.name,
        detection_pattern=cf.detection_pattern,
        priority="medium" if cf.name == "rto_in_footer" else cf.priority,
        description_ru=cf.description_ru,
    )
    for cf in TOURISM_TOUR_OPERATOR.commercial_factors
)

TRAVEL_AGENCY_PROFILE = apply_overlay(
    TOURISM_TOUR_OPERATOR,
    {
        "business_model": "travel_agency",
        "eeat_signals": _eeat_without_rto,
        "commercial_factors": _commercial_softened,
    },
)

register_profile("tourism", "travel_agency", TRAVEL_AGENCY_PROFILE)
