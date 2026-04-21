"""Vertical profiles for the SEO audit engine.

For now only the tourism vertical is implemented. When a second vertical
becomes a paying customer, re-add its profile package here and register
it via core_audit.registry.
"""

from app.profiles import tourism  # noqa: F401 — triggers registration

__all__ = ["tourism"]
