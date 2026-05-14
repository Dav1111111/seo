"""Deterministic Yandex robots.txt audit module.

Pure-function validator: takes raw robots.txt text + fetch metadata +
the URLs to test, returns a typed `YandexRobotsAuditResult`. No DB,
no network, no LLM.

Public surface:
  - `audit_yandex_robots(...)` — entry point.
  - `YandexRobotsAuditResult`, `YandexRobotsIssue`, `YandexRobotsUrlCheck`
    dataclasses for typing and JSON serialization.

Design contract:
  - Stable issue `code` strings consumers may switch on.
  - Yandex-specific precedence in the matcher: most-specific UA wins,
    longest rule wins, Allow beats Disallow at equal length.
  - Determinism: same input → same output. No clocks, no randomness.
"""

from app.core_audit.yandex_robots.audit import audit_yandex_robots
from app.core_audit.yandex_robots.dto import (
    Risk,
    Severity,
    YandexRobotsAuditResult,
    YandexRobotsIssue,
    YandexRobotsUrlCheck,
)

__all__ = [
    "audit_yandex_robots",
    "YandexRobotsAuditResult",
    "YandexRobotsIssue",
    "YandexRobotsUrlCheck",
    "Risk",
    "Severity",
]
