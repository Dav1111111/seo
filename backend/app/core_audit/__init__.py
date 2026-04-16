"""Universal SEO audit core.

Industry-agnostic engines: classifier, page scoring, decision tree, safety layer,
standalone value test. Rules are NOT here — they live in app.profiles.<vertical>.

Wiring: Decisioner loads a SiteProfile via `registry.get_profile(vertical, model)`
and threads it into every engine call. Engines iterate profile data; profiles
contain no logic (except URL/title proposers tied to site state).
"""

from app.core_audit.profile_protocol import (
    CommercialFactor,
    EEATSignal,
    IntentRule,
    PageRequirements,
    SiteProfile,
)
from app.core_audit.registry import (
    apply_overlay,
    get_profile,
    register_profile,
)

__all__ = [
    "CommercialFactor",
    "EEATSignal",
    "IntentRule",
    "PageRequirements",
    "SiteProfile",
    "apply_overlay",
    "get_profile",
    "register_profile",
]
