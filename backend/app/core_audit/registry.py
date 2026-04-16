"""Profile registry + overlay merge.

Profiles register themselves at import time:

    from app.core_audit import register_profile
    register_profile("tourism", "tour_operator", TOURISM_TOUR_OPERATOR_PROFILE)

Engines load a profile once per Decisioner run:

    profile = get_profile(site.vertical, site.business_model)
"""

from __future__ import annotations

import logging
from dataclasses import fields, is_dataclass, replace
from typing import Any

logger = logging.getLogger(__name__)

_REGISTRY: dict[tuple[str, str], Any] = {}


def register_profile(vertical: str, business_model: str, profile: Any) -> None:
    """Register a profile under (vertical, business_model). Last write wins."""
    key = (vertical.lower(), business_model.lower())
    if key in _REGISTRY:
        logger.warning("profile re-registered: %s/%s", vertical, business_model)
    _REGISTRY[key] = profile


def get_profile(vertical: str, business_model: str) -> Any:
    """Return registered profile. Falls back to tourism/tour_operator on miss."""
    key = (vertical.lower(), business_model.lower())
    if key in _REGISTRY:
        return _REGISTRY[key]
    fallback = ("tourism", "tour_operator")
    if fallback in _REGISTRY:
        logger.warning("unknown profile %s/%s — falling back to %s", vertical, business_model, fallback)
        return _REGISTRY[fallback]
    raise LookupError(f"no profile registered for {vertical!r}/{business_model!r} and no fallback")


def apply_overlay(base: Any, overlay: dict[str, Any]) -> Any:
    """Return a new profile with overlay fields replacing base fields.

    For model overlays: tour_operator is base tourism profile; travel_agency
    passes {"eeat_signals": (...)} removing РТО requirement. Flat merge only —
    nested dicts like `url_patterns` are replaced wholesale per-key.
    """
    if not is_dataclass(base):
        raise TypeError(f"apply_overlay base must be a dataclass, got {type(base).__name__}")

    valid_fields = {f.name for f in fields(base)}
    unknown = set(overlay) - valid_fields
    if unknown:
        raise ValueError(f"overlay has unknown fields: {unknown}")

    return replace(base, **overlay)


def list_registered() -> list[tuple[str, str]]:
    """Inspection helper for tests and admin tools."""
    return sorted(_REGISTRY.keys())


def clear_registry() -> None:
    """Test-only — wipe all registered profiles."""
    _REGISTRY.clear()
