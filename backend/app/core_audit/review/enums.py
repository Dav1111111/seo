"""Enum constants for Module 3 Page Review."""

from __future__ import annotations

from enum import Enum


class RecCategory(str, Enum):
    """Recommendation category. Order reflects review-call grouping."""
    title = "title"
    meta_description = "meta_description"
    h1_structure = "h1_structure"
    schema = "schema"
    eeat = "eeat"
    commercial = "commercial"
    over_optimization = "over_optimization"
    internal_linking = "internal_linking"


class RecPriority(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class ReviewStatus(str, Enum):
    """Lifecycle of a single page_reviews row."""
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class SkipReason(str, Enum):
    """Explicit skip reasons — every non-processed review must log one."""
    unchanged_hash = "unchanged_hash"
    not_strengthen = "not_strengthen"
    missing_content = "missing_content"
    no_profile_rules = "no_profile_rules"
    page_deleted = "page_deleted"
    no_fingerprint = "no_fingerprint"
    over_budget_cap = "over_budget_cap"


class UserStatus(str, Enum):
    """User's action on a recommendation — drives UI workflow."""
    pending = "pending"
    applied = "applied"
    dismissed = "dismissed"
    deferred = "deferred"
