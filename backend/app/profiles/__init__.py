"""Vertical profiles for the SEO audit engine.

Importing this package triggers registration of every shipped profile so
that `core_audit.registry.get_profile(vertical, model)` can resolve them.

Add new verticals under profiles/<vertical>/ with __init__.py that calls
register_profile() at import time, then add the import below.
"""

from app.profiles import tourism  # noqa: F401 — triggers registration

# Stub verticals — not implemented yet; imported for discoverability.
# Unknown vertical/model falls back to tourism/tour_operator via registry.
from app.profiles import ecommerce, local_business, media, saas  # noqa: F401

__all__ = ["ecommerce", "local_business", "media", "saas", "tourism"]
