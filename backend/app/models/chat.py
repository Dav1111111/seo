"""Chat persistence — Studio v2 etap 7 Phase D.

Two tables back the Помощник module:

  ChatConversation  — one thread (free-chat or per-action). Stores
                      site_id so we can list per-site, kind so we
                      can filter free vs action chats, plus rolling
                      stats (message_count, total_cost_usd) for the
                      sidebar / list view.

  ChatMessage       — one user/assistant turn. Always paired (user
                      then assistant) but stored as separate rows so
                      future tools (citations, edits, regenerate)
                      can attach metadata per-turn.

Cascade DELETE on conversation drops its messages — owner-initiated
clear is a single DELETE call.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin


class ChatConversation(Base, TimestampMixin):
    __tablename__ = "chat_conversations"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('free', 'action')",
            name="ck_chat_conv_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="free",
    )
    # For action-scoped chats (Phase B). null for free chats.
    action_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # First user message snippet, set on persist of the first turn.
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False, default=0,
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_chat_msg_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(8, 6), nullable=False, default=0,
    )
    input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


__all__ = ["ChatConversation", "ChatMessage"]
