"""Safety Layer — enforces Rule 2:
Любая рекомендация создания новой страницы проходит проверку на
дубль, doorway-pattern и каннибализацию.

Uses Module 1 fingerprinting for similarity calculations.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fingerprint import api as fp_api
from app.fingerprint.models import PageFingerprint
from app.intent.enums import IntentCode
from app.intent.models import PageIntentScore
from app.models.page import Page

logger = logging.getLogger(__name__)

# Thresholds (from seo-technical methodology, calibrated for Russian)
SIMILARITY_BLOCK_HIGH = 0.85     # near-identical → BLOCK
SIMILARITY_BLOCK_MID = 0.55      # mid + intent overlap → BLOCK
SIMILARITY_SAFE = 0.40           # below this → safe create
INTENT_OVERLAP_THRESHOLD = 4.0    # intent score on existing page ≥ 4 → occupied


class CheckResult:
    """Outcome of a single safety check."""
    def __init__(self, passed: bool, reason: str, evidence: dict | None = None):
        self.passed = passed
        self.reason = reason
        self.evidence = evidence or {}


@dataclass
class SafetyVerdict:
    """Aggregate verdict of all safety checks."""
    safe_to_create: bool
    blocks: list[CheckResult]      # blocking issues
    warnings: list[CheckResult]    # non-blocking concerns
    alternative_action: str | None  # STRENGTHEN / MERGE / LEAVE suggested
    alternative_page_url: str | None


# ── URL anti-patterns that trigger doorway concern ────────────────────

_DOORWAY_URL_PATTERNS = [
    # Geo-swap at top level: /excursii-loo, /excursii-adler
    re.compile(r"^/\w+-(\w+)/?$", re.I),
    # Year spam
    re.compile(r"-\d{4}/?$"),
    # Spam trigger words in URL
    re.compile(r"-(deshevo|nedorogo|luchshie)\b", re.I),
]


async def check_duplicate_risk(
    db: AsyncSession,
    proposed_title: str,
    proposed_content_sample: str,
    site_id: UUID,
) -> CheckResult:
    """Check if proposed page would be near-duplicate of existing pages.

    Phase 2C uses a heuristic: compare proposed title against existing titles.
    Full fingerprint comparison requires generating fingerprint of the proposed
    content — defer that to Phase 2D once we actually generate draft content.
    """
    from app.intent.classifier import detect_brand

    # Fetch existing page titles
    rows = await db.execute(
        select(PageFingerprint.page_id, PageFingerprint.title_normalized, Page.url, Page.title)
        .join(Page, Page.id == PageFingerprint.page_id)
        .where(PageFingerprint.site_id == site_id)
    )

    proposed_norm = (proposed_title or "").lower().strip()
    if not proposed_norm:
        return CheckResult(passed=True, reason="no_title_to_check")

    for page_id, title_norm, url, title in rows:
        if not title_norm:
            continue
        # Title-level check (not full content — that requires proposed fingerprint)
        if proposed_norm == title_norm:
            return CheckResult(
                passed=False,
                reason="title_exact_match",
                evidence={"existing_page_id": str(page_id), "existing_url": url},
            )
        # Partial match: >80% token overlap
        tokens_a = set(proposed_norm.split())
        tokens_b = set(title_norm.split())
        if not tokens_a or not tokens_b:
            continue
        overlap = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
        if overlap > 0.8:
            return CheckResult(
                passed=False,
                reason="title_high_overlap",
                evidence={
                    "existing_page_id": str(page_id),
                    "existing_url": url,
                    "token_overlap": round(overlap, 2),
                },
            )

    return CheckResult(passed=True, reason="no_duplicate_title")


async def check_doorway_pattern(
    db: AsyncSession,
    proposed_url_path: str,
    site_id: UUID,
) -> CheckResult:
    """Detect doorway URL pattern — 3+ existing pages with geo-swap only differ."""
    if not proposed_url_path:
        return CheckResult(passed=True, reason="no_url")

    # Check anti-pattern regex
    for pattern in _DOORWAY_URL_PATTERNS[1:]:  # skip geo-swap check; geo pickup pages are legitimate
        if pattern.search(proposed_url_path):
            return CheckResult(
                passed=False,
                reason="url_spam_pattern",
                evidence={"pattern": pattern.pattern, "url": proposed_url_path},
            )

    # Check if 3+ existing pages match the same template pattern
    # (e.g. /excursii-*, /tours/... where only last segment changes)
    parent_path = "/".join(proposed_url_path.rstrip("/").split("/")[:-1]) or "/"
    rows = await db.execute(
        select(Page.url, Page.path).where(Page.site_id == site_id)
    )
    siblings = [p for _, p in rows if p and p.rsplit("/", 1)[0] == parent_path]

    if len(siblings) >= 10:
        # Lots of pages already in this template — new one is OK if content differs
        # This is a warning, not a block
        return CheckResult(
            passed=True,
            reason="many_siblings_exists",
            evidence={"parent_path": parent_path, "sibling_count": len(siblings)},
        )

    return CheckResult(passed=True, reason="no_doorway_pattern")


async def check_cannibalization(
    db: AsyncSession,
    proposed_intent: IntentCode,
    site_id: UUID,
) -> CheckResult:
    """Check if existing page already strongly serves this intent.

    If best_page_score >= INTENT_OVERLAP_THRESHOLD for this intent → cannibalize.
    """
    rows = await db.execute(
        select(PageIntentScore.page_id, PageIntentScore.score, Page.url)
        .join(Page, Page.id == PageIntentScore.page_id)
        .where(
            PageIntentScore.site_id == site_id,
            PageIntentScore.intent_code == proposed_intent.value,
        )
        .order_by(PageIntentScore.score.desc())
        .limit(5)
    )
    top_pages = [(pid, score, url) for pid, score, url in rows]
    if not top_pages:
        return CheckResult(passed=True, reason="no_existing_pages")

    best_page_id, best_score, best_url = top_pages[0]
    if best_score >= INTENT_OVERLAP_THRESHOLD:
        return CheckResult(
            passed=False,
            reason="existing_page_already_serves_intent",
            evidence={
                "existing_page_id": str(best_page_id),
                "existing_url": best_url,
                "existing_score": round(best_score, 2),
                "intent": proposed_intent.value,
            },
        )

    return CheckResult(
        passed=True,
        reason="no_strong_incumbent",
        evidence={"best_existing_score": round(best_score, 2)},
    )


async def check_thin_content_forecast(
    *,
    proposed_intent: IntentCode,
    query_volume_14d: int,
    queries_in_cluster: int,
) -> CheckResult:
    """Forecast whether the niche can support 500+ words of unique content.

    Phase 2C heuristic (Phase 2D will add SERP check + LLM fact check):
      - If commercial intent but total 14d impressions < 10 AND queries < 3 → thin risk
      - If informational intent and queries_in_cluster < 5 → thin risk
    """
    if proposed_intent in (
        IntentCode.TRANS_BOOK,
        IntentCode.COMM_MODIFIED,
        IntentCode.COMM_CATEGORY,
        IntentCode.LOCAL_GEO,
    ):
        if query_volume_14d < 10 and queries_in_cluster < 3:
            return CheckResult(
                passed=False,
                reason="thin_commercial_niche",
                evidence={"impressions_14d": query_volume_14d, "queries": queries_in_cluster},
            )
    else:  # informational
        if queries_in_cluster < 5:
            return CheckResult(
                passed=False,
                reason="thin_informational_niche",
                evidence={"queries": queries_in_cluster},
            )

    return CheckResult(passed=True, reason="niche_has_potential")


async def run_safety_checks(
    db: AsyncSession,
    *,
    proposed_title: str,
    proposed_url_path: str,
    proposed_intent: IntentCode,
    site_id: UUID,
    query_volume_14d: int = 0,
    queries_in_cluster: int = 0,
) -> SafetyVerdict:
    """Run all 4 safety checks, aggregate into SafetyVerdict."""
    blocks: list[CheckResult] = []
    warnings: list[CheckResult] = []
    alternative_action: str | None = None
    alternative_url: str | None = None

    # Check 1 — Duplicate
    r = await check_duplicate_risk(db, proposed_title, "", site_id)
    if not r.passed:
        blocks.append(r)
        alternative_action = "STRENGTHEN"
        alternative_url = r.evidence.get("existing_url")

    # Check 2 — Doorway
    r = await check_doorway_pattern(db, proposed_url_path, site_id)
    if not r.passed:
        blocks.append(r)
        if not alternative_action:
            alternative_action = "REVIEW_URL_PATTERN"
    elif r.reason == "many_siblings_exists":
        sibling_count = r.evidence.get("sibling_count", 0)
        warnings.append(CheckResult(
            passed=True,
            reason=f"doorway_risk: {sibling_count} similar sibling pages detected",
            evidence=r.evidence,
        ))

    # Check 3 — Cannibalization
    r = await check_cannibalization(db, proposed_intent, site_id)
    if not r.passed:
        blocks.append(r)
        if not alternative_action:
            alternative_action = "STRENGTHEN"
            alternative_url = r.evidence.get("existing_url")
    else:
        existing_score = r.evidence.get("best_existing_score", 0.0) or 0.0
        if existing_score >= 2.0:
            warnings.append(CheckResult(
                passed=True,
                reason=(
                    f"cannibalization_risk: existing page scores {existing_score:.2f} "
                    f"for {proposed_intent.value} (below block threshold "
                    f"{INTENT_OVERLAP_THRESHOLD} but non-trivial overlap)"
                ),
                evidence=r.evidence,
            ))

    # Check 4 — Thin content
    r = await check_thin_content_forecast(
        proposed_intent=proposed_intent,
        query_volume_14d=query_volume_14d,
        queries_in_cluster=queries_in_cluster,
    )
    if not r.passed:
        blocks.append(r)
        if not alternative_action:
            alternative_action = "LEAVE"
    else:
        # Borderline-thin forecast: not a hard block, but worth surfacing
        if proposed_intent in (
            IntentCode.TRANS_BOOK,
            IntentCode.COMM_MODIFIED,
            IntentCode.COMM_CATEGORY,
            IntentCode.LOCAL_GEO,
        ):
            if query_volume_14d < 30 and queries_in_cluster < 5:
                warnings.append(CheckResult(
                    passed=True,
                    reason=(
                        f"thin_content_forecast: only {queries_in_cluster} queries / "
                        f"{query_volume_14d} impressions in 14d for commercial niche"
                    ),
                    evidence={
                        "impressions_14d": query_volume_14d,
                        "queries": queries_in_cluster,
                    },
                ))
        else:
            if queries_in_cluster < 8:
                warnings.append(CheckResult(
                    passed=True,
                    reason=(
                        f"thin_content_forecast: only {queries_in_cluster} queries "
                        f"in informational cluster"
                    ),
                    evidence={"queries": queries_in_cluster},
                ))

    return SafetyVerdict(
        safe_to_create=len(blocks) == 0,
        blocks=blocks,
        warnings=warnings,
        alternative_action=alternative_action,
        alternative_page_url=alternative_url,
    )
