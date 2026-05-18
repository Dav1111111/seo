"""Per-site workflow state for unified Studio advice cards.

Advice cards are computed on every request from live signals, so their
content is not stored. The owner's workflow state is stored separately
by stable `card_id`: pending / applied / dismissed / snoozed.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AdviceCardState(Base):
    __tablename__ = "advice_card_states"
    __table_args__ = (
        UniqueConstraint("site_id", "card_id", name="uq_advice_card_state_site_card"),
        Index("ix_advice_card_states_site_status", "site_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id"), index=True,
    )
    card_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    source_module: Mapped[str | None] = mapped_column(String(80), nullable=True)
    page_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    note_ru: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    dismissed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ── Auto-verification of «Применил» (2026-05-18) ─────────────────
    # Written by the dispatcher in `core_audit/advisor/verification/`.
    # Field names are part of the frozen contract that
    # `advisor.verification.verify_card` and the frontend agent both
    # consume — see the migration `d4e5f6a7b8c9_add_advice_verification`
    # for the column types.
    #
    # `verification_status` values:
    #   None              — never tried (default for not-yet-applied)
    #   "pending"         — verification queued/running
    #   "verified"        — deterministic check confirms fact changed ✅
    #   "not_yet_visible" — ran, fact didn't change yet (cache, deploy)
    #   "user_attested"   — manual category, we trust owner 🤝
    #   "failed"          — verifier itself crashed (separate from
    #                       not_yet_visible — the technical re-check
    #                       didn't even produce a clean answer)
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    verification_status: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
    )
    verification_evidence: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True,
    )
