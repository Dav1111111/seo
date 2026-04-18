"""Tourism vertical — first profile. Base = tour_operator business model.

Business-model overlays (travel_agency, individual_guide, hotel,
excursion_platform) live in `./models/` and register themselves.
"""

from app.core_audit.profile_protocol import ProfileData
from app.core_audit.registry import register_profile
from app.profiles.tourism.brand_tokens import TOURISM_BRAND_TOKENS
from app.profiles.tourism.commercial_factors import TOURISM_COMMERCIAL_FACTORS
from app.profiles.tourism.eeat_rules import TOURISM_EEAT_SIGNALS
from app.profiles.tourism.entity_patterns import (
    TOURISM_GENERIC_MODIFIER_PATTERNS,
    TOURISM_UNIQUE_ENTITY_PATTERNS,
)
from app.profiles.tourism.intent_rules import (
    TOURISM_DOORWAY_SPAM_URL_PATTERNS,
    TOURISM_FALLBACK_COMMERCIAL_PATTERN,
    TOURISM_INTENT_RULES,
)
from app.profiles.tourism.page_requirements import TOURISM_PAGE_REQUIREMENTS
from app.profiles.tourism.page_signals import (
    TOURISM_BOOKING_CTA_PATTERNS,
    TOURISM_CONTENT_SIGNALS,
    TOURISM_INFO_CTA_PATTERNS,
    TOURISM_URL_PATTERNS,
)
from app.profiles.tourism.schema_rules import TOURISM_SCHEMA_RULES
from app.profiles.tourism.seed_templates import TOURISM_SEED_TEMPLATES
from app.profiles.tourism.url_heuristics import propose_title, propose_url


def _build_tour_operator_profile() -> ProfileData:
    return ProfileData(
        vertical="tourism",
        business_model="tour_operator",
        intent_rules=TOURISM_INTENT_RULES,
        brand_tokens=TOURISM_BRAND_TOKENS,
        unique_entity_patterns=TOURISM_UNIQUE_ENTITY_PATTERNS,
        generic_modifier_patterns=TOURISM_GENERIC_MODIFIER_PATTERNS,
        url_patterns=TOURISM_URL_PATTERNS,
        content_signals=TOURISM_CONTENT_SIGNALS,
        cta_patterns_booking=TOURISM_BOOKING_CTA_PATTERNS,
        cta_patterns_info=TOURISM_INFO_CTA_PATTERNS,
        fallback_commercial_pattern=TOURISM_FALLBACK_COMMERCIAL_PATTERN,
        doorway_spam_url_patterns=TOURISM_DOORWAY_SPAM_URL_PATTERNS,
        page_requirements=TOURISM_PAGE_REQUIREMENTS,
        schema_rules=TOURISM_SCHEMA_RULES,
        eeat_signals=TOURISM_EEAT_SIGNALS,
        commercial_factors=TOURISM_COMMERCIAL_FACTORS,
        seed_cluster_templates=TOURISM_SEED_TEMPLATES,
    )


TOURISM_TOUR_OPERATOR = _build_tour_operator_profile()

# Attach proposer functions so the object satisfies the SiteProfile Protocol.
TOURISM_TOUR_OPERATOR.__class__.propose_url = staticmethod(propose_url)  # type: ignore[attr-defined]
TOURISM_TOUR_OPERATOR.__class__.propose_title = staticmethod(propose_title)  # type: ignore[attr-defined]

register_profile("tourism", "tour_operator", TOURISM_TOUR_OPERATOR)

# Register business-model overlays (imported at end so TOURISM_TOUR_OPERATOR
# exists when overlay modules compute their diffs).
from app.profiles.tourism import models as _models  # noqa: E402,F401
