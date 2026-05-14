"""DTOs for the Schema.org audit.

EXACT contract — frozen by upstream agreement. Do not change field
names or shape without coordinating with `studio.py` and the frontend
serializer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Severity = Literal["critical", "warning", "info"]
SchemaSource = Literal["json-ld", "microdata", "rdfa", "dom"]


@dataclass(frozen=True)
class SchemaIssue:
    """One audit finding.

    `code` is stable across versions — consumers may switch on it.
    `evidence` is a concrete fact from the data (raw price string,
    block index, etc.) — never invented, always traceable.
    """

    code: str            # stable, e.g. "schema.offer.price_string", ≤120 chars
    severity: Severity
    message_ru: str      # human-readable Russian
    evidence: str | None  # concrete fact from the data, ≤300 chars
    fix_ru: str          # what the owner should do
    source: SchemaSource


@dataclass
class SchemaAuditResult:
    """Audit verdict — JSON-serializable via `to_dict()`."""

    detected_types: list[str] = field(default_factory=list)
    formats: list[str] = field(default_factory=list)
    valid_blocks_count: int = 0
    parse_error_count: int = 0
    issues: list[SchemaIssue] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    summary_ru: str = ""

    def to_dict(self) -> dict:
        """JSON-serializable representation.

        We use `asdict` for the dataclass tree; frozen `SchemaIssue`
        instances are converted to plain dicts. The result is safe to
        pass to `json.dumps` directly.
        """
        return {
            "detected_types": list(self.detected_types),
            "formats": list(self.formats),
            "valid_blocks_count": self.valid_blocks_count,
            "parse_error_count": self.parse_error_count,
            "issues": [asdict(issue) for issue in self.issues],
            "recommendations": list(self.recommendations),
            "summary_ru": self.summary_ru,
        }
