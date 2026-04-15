"""
AI Chat — contextual SEO consultant in the dashboard.
User sees a problem → asks AI "why?" → gets simple answer with context from DB.
"""

import json
import logging
import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.issue import Issue
from app.models.daily_metric import DailyMetric
from app.models.search_query import SearchQuery
from app.models.agent_run import AgentRun
from app.models.site import Site
from app.agents.llm_client import get_client
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    issue_id: str | None = None  # optional: context from specific issue


class ChatResponse(BaseModel):
    reply: str
    cost_usd: float


SYSTEM_PROMPT = """Ты — AI-помощник владельца бизнеса по SEO. Отвечаешь ПРОСТЫМ РУССКИМ ЯЗЫКОМ.

ПРАВИЛА:
- Никакого жаргона. Вместо "CTR" говори "процент кликов". Вместо "индексация" — "видимость в Яндексе"
- Каждый ответ — конкретный, с примерами и действиями
- Если не знаешь точно — честно скажи
- Отвечай кратко: 2-4 абзаца максимум
- Если пользователь спрашивает про конкретную проблему — объясни откуда она, почему это важно, и что делать

Ты работаешь с данными из Яндекс.Вебмастера для туристического сайта."""


async def _build_context(
    db: AsyncSession,
    site_id: uuid.UUID,
    issue_id: str | None,
) -> str:
    """Build context from DB for the chat."""
    parts: list[str] = []

    # Site info
    site = await db.execute(select(Site).where(Site.id == site_id))
    site_row = site.scalar_one_or_none()
    if site_row:
        parts.append(f"Сайт: {site_row.domain}, режим: {site_row.operating_mode}")

    # Recent issues summary
    issues = await db.execute(
        select(Issue.title, Issue.severity, Issue.status, Issue.confidence, Issue.recommendation)
        .where(Issue.site_id == site_id, Issue.status.in_(["open", "review"]))
        .order_by(Issue.created_at.desc())
        .limit(5)
    )
    issue_list = issues.fetchall()
    if issue_list:
        parts.append("\nПоследние проблемы:")
        for i in issue_list:
            parts.append(f"- [{i.severity}] {i.title} (уверенность {float(i.confidence)*100:.0f}%)")
            if i.recommendation:
                parts.append(f"  Рекомендация: {i.recommendation[:150]}")

    # Specific issue context
    if issue_id:
        issue = await db.execute(
            select(Issue).where(Issue.id == uuid.UUID(issue_id))
        )
        issue_row = issue.scalar_one_or_none()
        if issue_row:
            parts.append(f"\nПользователь спрашивает про эту проблему:")
            parts.append(f"Заголовок: {issue_row.title}")
            parts.append(f"Описание: {issue_row.description}")
            parts.append(f"Рекомендация: {issue_row.recommendation}")
            parts.append(f"Данные: {json.dumps(issue_row.evidence, ensure_ascii=False)[:500]}")

    # Recent metrics
    metrics = await db.execute(
        select(
            func.sum(DailyMetric.impressions).label("imp"),
            func.sum(DailyMetric.clicks).label("clk"),
            func.avg(DailyMetric.avg_position).label("pos"),
        ).where(
            DailyMetric.site_id == site_id,
            DailyMetric.metric_type == "query_performance",
            DailyMetric.date >= date.today() - timedelta(days=7),
        )
    )
    m = metrics.fetchone()
    if m and m.imp:
        parts.append(f"\nМетрики за 7 дней: {int(m.imp)} показов, {int(m.clk)} кликов, средняя позиция {float(m.pos):.1f}")

    # Top queries
    queries = await db.execute(
        select(SearchQuery.query_text)
        .where(SearchQuery.site_id == site_id)
        .order_by(SearchQuery.last_seen_at.desc())
        .limit(10)
    )
    q_list = [r.query_text for r in queries]
    if q_list:
        parts.append(f"\nТоп запросы: {', '.join(q_list[:10])}")

    return "\n".join(parts)


@router.post("/sites/{site_id}/chat", response_model=ChatResponse)
async def chat(
    site_id: uuid.UUID,
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """AI chat with context from site data."""
    context = await _build_context(db, site_id, body.issue_id)

    # Build messages for Claude
    messages = []
    for msg in body.history[-10:]:  # last 10 messages
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": body.message})

    # Prepend context as first user message if not in history
    if len(messages) == 1:
        context_msg = f"Контекст сайта:\n{context}\n\nМой вопрос: {body.message}"
        messages = [{"role": "user", "content": context_msg}]

    client = get_client()

    system_blocks = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    response = client.messages.create(
        model=settings.AI_DAILY_MODEL,  # Haiku — cheap
        max_tokens=1024,
        system=system_blocks,
        messages=messages,
    )

    reply = ""
    for block in response.content:
        if block.type == "text":
            reply = block.text
            break

    usage = response.usage
    cost = (usage.input_tokens / 1_000_000) * 1.0 + (usage.output_tokens / 1_000_000) * 5.0

    return ChatResponse(reply=reply, cost_usd=round(cost, 6))
