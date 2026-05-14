"""DTOs for the Yandex robots.txt audit.

EXACT contract — frozen by upstream agreement. Field names and shape
are stable for consumers (studio.py, brain, frontend).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Severity = Literal["critical", "warning", "info"]
Risk = Literal["ok", "warning", "blocked"]


@dataclass(frozen=True)
class YandexRobotsIssue:
    """One robots.txt audit finding.

    `code` is stable across versions — consumers may switch on it.
    `evidence` is a literal quote or fact from the file — never invented.
    """

    code: str
    severity: Severity
    message_ru: str
    evidence: str
    fix_ru: str


@dataclass(frozen=True)
class YandexRobotsUrlCheck:
    """Result of testing one URL against robots.txt for Yandex."""

    url: str
    path: str
    user_agent: str
    allowed: bool
    matched_user_agent: str
    matched_rule: str
    risk: Risk
    explanation_ru: str


@dataclass
class YandexRobotsAuditResult:
    """Audit verdict — JSON-serializable via `to_dict()`."""

    robots_url: str = ""
    http_status: int | None = None
    is_accessible: bool = False
    size_bytes: int = 0
    valid_for_yandex: bool = False
    matched_groups: list[str] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)
    clean_params: list[str] = field(default_factory=list)
    issues: list[YandexRobotsIssue] = field(default_factory=list)
    url_checks: list[YandexRobotsUrlCheck] = field(default_factory=list)
    summary_ru: str = ""
    recommendations_ru: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "robots_url": self.robots_url,
            "http_status": self.http_status,
            "is_accessible": self.is_accessible,
            "size_bytes": self.size_bytes,
            "valid_for_yandex": self.valid_for_yandex,
            "matched_groups": list(self.matched_groups),
            "sitemaps": list(self.sitemaps),
            "clean_params": list(self.clean_params),
            "issues": [asdict(issue) for issue in self.issues],
            "url_checks": [asdict(check) for check in self.url_checks],
            "summary_ru": self.summary_ru,
            "recommendations_ru": list(self.recommendations_ru),
        }
