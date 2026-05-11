"""Long-term memory for the studio assistant.

Each `chat_conversations` row already keeps the full back-and-forth.
But when the owner opens a NEW conversation, the assistant has zero
recall of "we already talked about X yesterday". This module pulls
the most recent user turns from past conversations of the same site
and surfaces them as a small "things the owner mentioned recently"
block in the system context.

Cheap: pure SQL, no LLM. Deterministic.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatConversation, ChatMessage


# Hard cap so the prompt doesn't bloat. 12 turns × ~120 chars ≈ 1500
# chars, well under the system-prompt size budget.
DEFAULT_MEMORY_TURNS = 12

# Skip very short messages — they're "ага" / "да" / "ок" and add
# nothing for the next conversation's context.
MIN_TURN_CHARS = 20


async def load_recent_owner_turns(
    db: AsyncSession,
    site_id: UUID,
    *,
    exclude_conversation_id: UUID | None = None,
    limit: int = DEFAULT_MEMORY_TURNS,
) -> list[str]:
    """Return up to `limit` most-recent user-role messages from past
    conversations of the site, oldest-first.

    Excludes the current conversation (we don't want the assistant to
    re-quote what's already in this turn's history). Branded boilerplate
    ("спасибо", "ок") is filtered by length.
    """
    stmt = (
        select(ChatMessage.content)
        .join(
            ChatConversation,
            ChatConversation.id == ChatMessage.conversation_id,
        )
        .where(ChatConversation.site_id == site_id)
        .where(ChatMessage.role == "user")
    )
    if exclude_conversation_id is not None:
        stmt = stmt.where(ChatMessage.conversation_id != exclude_conversation_id)

    stmt = stmt.order_by(desc(ChatMessage.created_at)).limit(limit * 3)

    rows = (await db.execute(stmt)).scalars().all()

    out: list[str] = []
    for content in rows:
        text = (content or "").strip()
        if len(text) < MIN_TURN_CHARS:
            continue
        # Hard char cap per turn — even one long turn shouldn't blow
        # the memory budget.
        if len(text) > 240:
            text = text[:237].rstrip() + "…"
        out.append(text)
        if len(out) >= limit:
            break

    # Reverse to oldest-first so the assistant reads them in order.
    out.reverse()
    return out


def format_memory_block(turns: list[str]) -> str:
    """Render the memory list as a labelled block for the prompt.

    Returns empty string when there's nothing to show — caller should
    skip injecting the block to keep the prompt clean.
    """
    if not turns:
        return ""
    lines = ["ИЗ ПРОШЛЫХ БЕСЕД С ЭТИМ ВЛАДЕЛЬЦЕМ (что он сам говорил, новейшее снизу):"]
    for t in turns:
        lines.append(f"  • {t}")
    lines.append(
        "Используй это как контекст: ссылайся на конкретные слова владельца "
        "если уместно («ты упоминал X — давай вернёмся к этому»). "
        "Не повторяй прошлые ответы дословно — отвечай на текущий вопрос."
    )
    return "\n".join(lines)


__all__ = [
    "DEFAULT_MEMORY_TURNS",
    "format_memory_block",
    "load_recent_owner_turns",
]
