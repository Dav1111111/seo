"""Business-model overlays within the tourism vertical.

Each module below imports the base tour_operator profile and registers its
variant via `apply_overlay` + `register_profile`.
"""

from app.profiles.tourism.models import (  # noqa: F401 — trigger registration
    excursion_platform,
    hotel,
    individual_guide,
    travel_agency,
)
