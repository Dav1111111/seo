"""Profile-driven Safety Layer — Rule 2 enforcement.

Four checks gate every CREATE recommendation:
  1. Duplicate risk (title overlap)
  2. Doorway pattern (URL spam, sibling template flood)
  3. Cannibalization (existing page already serves intent)
  4. Thin content forecast (volume vs intent type)

Thresholds are universal; doorway URL patterns come from the profile.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core_audit.intent_codes import IntentCode
from app.core_audit.profile_protocol import SiteProfile
from app.fingerprint.models import PageFingerprint
from app.intent.models import PageIntentScore
from app.models.page import Page

logger = logging.getLogger(__name__)

SIMILARITY_BLOCK_HIGH = 0.85
SIMILARITY_BLOCK_MID = 0.55
SIMILARITY_SAFE = 0.40
INTENT_OVERLAP_THRESHOLD = 4.0


class CheckResult:
    def __init__(self, passed: bool, reason: str, evidence: dict | None = None):
        self.passed = passed
        self.reason = reason
        self.evidence = evidence or {}


@dataclass
class SafetyVerdict:
    safe_to_create: bool
    blocks: list[CheckResult]
    warnings: list[CheckResult]
    alternative_action: str | None
    alternative_page_url: str | None


async def check_duplicate_risk(
    db: AsyncSession,
    proposed_title: str,
    proposed_content_sample: str,
    site_id: UUID,
) -> CheckResult:
    rows = await db.execute(
        select(PageFingerprint.page_id, PageFingerprint.title_normalized, Page.url, Page.title)
        .join(Page, Page.id == PageFingerprint.page_id)
        .where(PageFingerprint.site_id == site_id)
    )

    proposed_norm = (proposed_title or "").lower().strip()
    if not proposed_norm:
        return CheckResult(passed=True, reason="no_title_to_check")

    for page_id, title_norm, url, _title in rows:
        if not title_norm:
            continue
        if proposed_norm == title_norm:
            return CheckResult(
                passed=False,
                reason="title_exact_match",
                evidence={"existing_page_id": str(page_id), "existing_url": url},
            )
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
    profile: SiteProfile,
) -> CheckResult:
    if not proposed_url_path:
        return CheckResult(passed=True, reason="no_url")

    for pattern in profile.doorway_spam_url_patterns:
        if pattern.search(proposed_url_path):
            return CheckResult(
                passed=False,
                reason="url_spam_pattern",
                evidence={"pattern": pattern.pattern, "url": proposed_url_path},
            )

    parent_path = "/".join(proposed_url_path.rstrip("/").split("/")[:-1]) or "/"
    rows = await db.execute(
        select(Page.url, Page.path).where(Page.site_id == site_id)
    )
    siblings = [p for _, p in rows if p and p.rsplit("/", 1)[0] == parent_path]

    if len(siblings) >= 10:
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
    else:
        if queries_in_cluster < 5:
            return CheckResult(
                passed=False,
                reason="thin_informational_niche",
                evidence={"queries": queries_in_cluster},
            )

    return CheckResult(passed=True, reason="niche_has_potential")


async def run_safety_checks(
    db: AsyncSession,
    profile: SiteProfile,
    *,
    proposed_title: str,
    proposed_url_path: str,
    proposed_intent: IntentCode,
    site_id: UUID,
    query_volume_14d: int = 0,
    queries_in_cluster: int = 0,
) -> SafetyVerdict:
    blocks: list[CheckResult] = []
    warnings: list[CheckResult] = []
    alternative_action: str | None = None
    alternative_url: str | None = None

    r = await check_duplicate_risk(db, proposed_title, "", site_id)
    if not r.passed:
        blocks.append(r)
        alternative_action = "STRENGTHEN"
        alternative_url = r.evidence.get("existing_url")

    r = await check_doorway_pattern(db, proposed_url_path, site_id, profile)
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
