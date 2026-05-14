"""Deterministic Schema.org audit module.

Pure-function validator over `schema_blocks` extracted by the crawler.
No DB writes, no LLM calls. Other modules (studio.py, brain) consume
`audit_schema()` result on-the-fly per request.

Design contract:
  - JSON-LD blocks get full content rules.
  - Microdata / RDFa get info-level "marker" findings only (we see only
    `@type`, never full content).
  - FAQ DOM mismatch is *warning only* if NONE of the FAQ questions
    appear in `full_text` (tolerant of accordions / lazy renderers).
  - Wording is honest: never "rich snippet impossible", always
    "менее вероятно" / "может мешать".

Public surface:
  - `audit_schema(schema_blocks, full_text, url, title, h1)` → SchemaAuditResult
  - `SchemaAuditResult`, `SchemaIssue` dataclasses for typing/consumers.
"""

from app.core_audit.schema_audit.dto import (
    SchemaAuditResult,
    SchemaIssue,
    SchemaSource,
    Severity,
)
from app.core_audit.schema_audit.validator import audit_schema

__all__ = [
    "audit_schema",
    "SchemaAuditResult",
    "SchemaIssue",
    "SchemaSource",
    "Severity",
]
