"""Per-card-category dispatcher for advice-card verification.

Routes a card's `(category, id, link, source_module)` tuple to the
appropriate deterministic verifier in `verifiers.py`. The dispatcher
itself never does I/O or LLM calls — it's a thin router.

Anti-fabrication rule (CLAUDE.md rule 5/6):
    Whenever we can't auto-check a category, the fallback is
    ``user_attested`` — explicit «we trust the owner» — NOT a silent
    ``verified`` based on hope.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


log = logging.getLogger(__name__)


# Allowed terminal statuses. Kept aligned with the enum documented on
# AdviceCardState.verification_status.
VERIFICATION_STATUSES: tuple[str, ...] = (
    "verified",
    "not_yet_visible",
    "user_attested",
    "failed",
)


@dataclass(frozen=True)
class VerificationResult:
    """Result of one auto-verification attempt.

    `status` — one of `verified` / `not_yet_visible` / `user_attested`
        / `failed`. NEVER `pending` (that's the in-flight state managed
        by the Celery task before this returns).
    `evidence` — JSON-safe diff used by the UI tooltip and by the
        beat-job retry decision (before/after counts, URLs, etc.).
    `message_ru` — one human sentence; surfaces in the activity feed
        verbatim.
    """
    status: str
    evidence: dict[str, Any]
    message_ru: str

    def __post_init__(self) -> None:
        if self.status not in VERIFICATION_STATUSES:
            raise ValueError(
                f"VerificationResult.status={self.status!r} is not one of "
                f"{VERIFICATION_STATUSES}",
            )


def _user_attested(reason: str) -> VerificationResult:
    """The category isn't auto-checkable — trust the owner."""
    return VerificationResult(
        status="user_attested",
        evidence={"reason": reason},
        message_ru=(
            "Автоматически проверить эту категорию нельзя — принимаем "
            "на слово, что владелец действительно применил совет."
        ),
    )


async def verify_card(
    db: AsyncSession,
    site_id: UUID,
    card_id: str,
    *,
    card_category: str,
    card_link: str | None,
    card_source_module: str,
) -> VerificationResult:
    """Dispatch by category/id-prefix to the right deterministic verifier.

    Contract for the frontend (see ``__init__.py``): this function
    never raises — all error paths are funneled into a
    ``status="failed"`` ``VerificationResult`` with the exception
    message in ``evidence["error"]``.
    """
    # Local imports keep this module light and avoid pulling the whole
    # verifiers chain (which imports collectors/schema_audit/etc.)
    # when callers only need the DTO.
    from app.core_audit.advisor.verification import verifiers

    try:
        # 1. Schema cards (e.g. "schema:missing_type:faqpage").
        if card_id.startswith("schema:missing_type:"):
            return await verifiers.verify_schema(
                db, site_id, card_id, card_link=card_link,
            )

        # 2. Robots audit ("robots:critical" or any robots:* brain rule).
        if card_id == "robots:critical" or card_id.startswith("brain:robots:"):
            return await verifiers.verify_robots(db, site_id)

        # 3. Keyword cards (aggregate "keywords:gaps" or per-page).
        if card_id == "keywords:gaps" or card_id.startswith("keyword_placement."):
            return await verifiers.verify_keywords(
                db, site_id, card_id, card_link=card_link,
            )

        # 4. Technical health ("health:stage_failed:<stage>").
        if card_id.startswith("health:stage_failed:"):
            return await verifiers.verify_technical(db, site_id, card_id)

        # 5. Metrica counter ("health:metrica_counter").
        if card_id == "health:metrica_counter":
            return await verifiers.verify_health_metrica(db, site_id)

        # 6. Funnel top — either raw safety-net or brain rule.
        if (
            card_id == "funnel:top_gap_raw"
            or card_id.startswith("brain:funnel:")
        ):
            return await verifiers.verify_funnel_top(db, site_id, card_id)

        # 7. Brain-emitted SEO-content / queries cards.
        if (
            card_id.startswith("brain:queries:")
            or card_id.startswith("brain:review:")
            or card_id.startswith("brain:outcomes:")
            or card_id.startswith("brain:wordstat:")
            or card_id.startswith("brain:ctr:")
            or card_id.startswith("brain:behavioral:")
            or card_id.startswith("brain:indexation:")
            or card_category == "seo_content"
        ):
            return await verifiers.verify_seo_content(
                db, site_id, card_id, card_category=card_category,
            )

        # Fallback for anything else — explicit «we don't know how».
        return _user_attested(
            f"no verifier for card_id={card_id!r} category={card_category!r}",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "advice.verify_dispatch_failed card_id=%s err=%s",
            card_id, exc,
        )
        return VerificationResult(
            status="failed",
            evidence={"error": str(exc)[:500], "card_id": card_id},
            message_ru=(
                "Автоматическая проверка сломалась — техническая "
                "ошибка, не зависящая от правки. Попробуй ещё раз "
                "вручную через «Проверить снова»."
            ),
        )


__all__ = ["VerificationResult", "verify_card", "VERIFICATION_STATUSES"]
