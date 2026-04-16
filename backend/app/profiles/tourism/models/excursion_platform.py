"""Excursion marketplace — lists tours from multiple operators.

Differs from tour_operator:
  - No single РТО (platform itself is aggregator, each operator has its own)
  - 'operator_info_per_listing' would be a critical factor (not yet modeled)
  - Uses ItemList schema for category pages (many offers)
"""

from __future__ import annotations

from app.core_audit.intent_codes import IntentCode
from app.core_audit.registry import apply_overlay, register_profile
from app.profiles.tourism import TOURISM_TOUR_OPERATOR


_eeat_without_rto = tuple(
    s for s in TOURISM_TOUR_OPERATOR.eeat_signals if s.name != "rto_number"
)

_schema_with_itemlist = {**TOURISM_TOUR_OPERATOR.schema_rules}
_schema_with_itemlist[IntentCode.COMM_CATEGORY] = (
    "ItemList", "BreadcrumbList", "Organization",
)


EXCURSION_PLATFORM_PROFILE = apply_overlay(
    TOURISM_TOUR_OPERATOR,
    {
        "business_model": "excursion_platform",
        "eeat_signals": _eeat_without_rto,
        "schema_rules": _schema_with_itemlist,
    },
)

register_profile("tourism", "excursion_platform", EXCURSION_PLATFORM_PROFILE)
