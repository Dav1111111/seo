"""Hotel / гостевой дом — lodging primary, tours/excursions secondary.

Differs from tour_operator:
  - No РТО (not a tour operator)
  - Schema: LodgingBusiness primary for brand pages
  - Commercial factor 'booking_widget_available' becomes critical (not present in
    base profile — kept implicit via 'booking_form')
"""

from __future__ import annotations

from app.core_audit.intent_codes import IntentCode
from app.core_audit.registry import apply_overlay, register_profile
from app.profiles.tourism import TOURISM_TOUR_OPERATOR


_eeat_without_rto = tuple(
    s for s in TOURISM_TOUR_OPERATOR.eeat_signals if s.name != "rto_number"
)

_schema_lodging = {**TOURISM_TOUR_OPERATOR.schema_rules}
_schema_lodging[IntentCode.TRANS_BRAND] = ("LodgingBusiness", "Organization", "BreadcrumbList")
_schema_lodging[IntentCode.COMM_CATEGORY] = ("LodgingBusiness", "BreadcrumbList", "ItemList")


HOTEL_PROFILE = apply_overlay(
    TOURISM_TOUR_OPERATOR,
    {
        "business_model": "hotel",
        "eeat_signals": _eeat_without_rto,
        "schema_rules": _schema_lodging,
    },
)

register_profile("tourism", "hotel", HOTEL_PROFILE)
