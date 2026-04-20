"""Pydantic DTOs for the Draft Profile Builder (Phase F).

`DraftProfile` is the top-level object produced by `builder.build_draft_profile`.
It serializes to JSON for storage in `sites.target_config_draft` and is
also the response payload of the admin `GET /draft-profile` endpoint.

Design notes
------------
- `draft_config` mirrors the `target_config` JSONB shape exactly so the
  admin commit endpoint is a straight copy (with optional overrides).
- `confidences` gives per-field explainability for the Phase G wizard UI.
- `signals` captures generator telemetry (cost, timings, input sizes).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


GENERATOR_VERSION = "1.0.0"


class ExtractedService(BaseModel):
    """A single service candidate extracted from page content."""

    name: str
    occurrence_count: int = 0
    pages_with: int = 0
    confidence: float = 0.0


class ExtractedGeo(BaseModel):
    """Geo extraction result — primary, secondary, frequency map."""

    primary: list[str] = Field(default_factory=list)
    secondary: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    frequency_map: dict[str, int] = Field(default_factory=dict)


class CompetitorBrand(BaseModel):
    """A competitor-brand proposal from the LLM."""

    name: str
    confidence_ru: float = 0.0


class FieldConfidence(BaseModel):
    """Per-field confidence + evidence summary for the wizard."""

    field: str
    confidence: float
    evidence_count: int
    reasoning_ru: str


class DraftReasoning(BaseModel):
    """Human-readable explanations keyed by field name.

    Not currently persisted separately — the reasoning is folded into
    the per-field `FieldConfidence.reasoning_ru` text. Kept as a named
    type so Phase G UI can evolve the schema without another pivot.
    """

    by_field: dict[str, str] = Field(default_factory=dict)


class DraftProfile(BaseModel):
    """Top-level draft profile for a site.

    Serialized to JSONB for `sites.target_config_draft`. The
    `draft_config` sub-object has the exact same shape as
    `sites.target_config`.
    """

    site_id: UUID
    draft_config: dict[str, Any]
    confidences: list[FieldConfidence] = Field(default_factory=list)
    overall_confidence: float = 0.0
    generated_at: datetime
    generator_version: str = GENERATOR_VERSION
    signals: dict[str, Any] = Field(default_factory=dict)

    def to_jsonb(self) -> dict[str, Any]:
        """Return a plain-JSON-serializable dict for JSONB storage."""
        return {
            "site_id": str(self.site_id),
            "draft_config": self.draft_config,
            "confidences": [c.model_dump() for c in self.confidences],
            "overall_confidence": float(self.overall_confidence),
            "generated_at": self.generated_at.isoformat(),
            "generator_version": self.generator_version,
            "signals": self.signals,
        }


__all__ = [
    "GENERATOR_VERSION",
    "ExtractedService",
    "ExtractedGeo",
    "CompetitorBrand",
    "FieldConfidence",
    "DraftReasoning",
    "DraftProfile",
]
