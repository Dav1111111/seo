"""
Agent 2 — TechnicalIndexingAgent

Analyses:
- Pages dropped from / appeared in Yandex search
- HTTP error spikes (4xx / 5xx)
- Index coverage trends (indexed pages over time)
- Crawl anomalies
"""

import logging
from datetime import date, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.agents.schemas import AgentContext
from app.models.daily_metric import DailyMetric

logger = logging.getLogger(__name__)


class TechnicalIndexingAgent(BaseAgent):
    agent_name = "technical_indexing"
    model_tier = "cheap"

    def get_system_prompt(self, context: AgentContext) -> str:
        return f"""Ты — помощник владельца туристического бизнеса. Проверяешь, видит ли Яндекс сайт.

ГЛАВНОЕ ПРАВИЛО: Пиши ПРОСТЫМ РУССКИМ ЯЗЫКОМ, понятным человеку без технических знаний.
Никакого жаргона. "Индексация" = "видимость в Яндексе". "4xx ошибки" = "страницы которые не открываются".
"5xx" = "сайт падал". Каждая рекомендация — конкретное действие.

Сайт: {context.site_domain}
Дата: {context.analysis_date}

ЧТО ИЩЕШЬ:
1. Яндекс стал видеть меньше страниц сайта (раньше видел 100, теперь 80 — плохо)
2. На сайте появились битые страницы (ошибки — пользователь заходит и видит "страница не найдена")
3. Страницы пропадают из поиска (были в Яндексе, а теперь не находятся)
4. Сайт падал (сервер не отвечал)

ФОРМАТ:
- Заголовок: одно простое предложение ("Яндекс перестал видеть 5 страниц сайта")
- Описание: почему это плохо для бизнеса, простыми словами
- Рекомендация: что конкретно сделать

КОГДА НЕ БИТЬ ТРЕВОГУ:
- Маленькие колебания (плюс-минус 5%) — это нормально
- Если данных нет за какой-то день — Яндекс просто не обновил, не ошибка
- Проблема на 1 день — возможно случайность

Если всё хорошо — так и напиши: "Всё в порядке, Яндекс видит сайт нормально".
Выводи через report_issues. ПРОСТЫМ РУССКИМ ЯЗЫКОМ."""

    async def load_data(self, db: AsyncSession, context: AgentContext) -> dict:
        today = context.analysis_date
        start = today - timedelta(days=30)

        # Indexing metrics over last 30 days
        idx_rows = await db.execute(
            select(
                DailyMetric.date,
                DailyMetric.pages_indexed,
                DailyMetric.extra,
            )
            .where(
                DailyMetric.site_id == context.site_id,
                DailyMetric.metric_type == "indexing",
                DailyMetric.date >= start,
            )
            .order_by(DailyMetric.date)
        )
        indexing = [dict(r._mapping) for r in idx_rows]

        # Search events (appeared/removed)
        events_rows = await db.execute(
            select(
                DailyMetric.date,
                DailyMetric.pages_in_search,
                DailyMetric.extra,
            )
            .where(
                DailyMetric.site_id == context.site_id,
                DailyMetric.metric_type == "search_events",
                DailyMetric.date >= start,
            )
            .order_by(DailyMetric.date)
        )
        events = [dict(r._mapping) for r in events_rows]

        return {
            "analysis_period": {"start": start.isoformat(), "end": today.isoformat()},
            "indexing": indexing,
            "search_events": events,
        }

    def format_user_message(self, context: AgentContext, data: dict) -> str:
        if not data.get("indexing") and not data.get("search_events"):
            return "No indexing data available. Webmaster collection may not have run yet."

        lines = [
            f"Analysis period: {data['analysis_period']['start']} to {data['analysis_period']['end']}",
            "",
        ]

        # Indexing history
        idx = data.get("indexing", [])
        if idx:
            lines.append("INDEXING HISTORY (date | indexed_pages | http_4xx | http_5xx):")
            for row in idx:
                extra = row.get("extra") or {}
                lines.append(
                    f"{row['date']} | {row['pages_indexed'] or 0} | "
                    f"{extra.get('http_4xx', 0)} | {extra.get('http_5xx', 0)}"
                )
        else:
            lines.append("No indexing history data available.")

        lines.append("")

        # Search events
        events = data.get("search_events", [])
        if events:
            lines.append("SEARCH EVENTS (date | appeared_in_search | removed_from_search):")
            for row in events:
                extra = row.get("extra") or {}
                lines.append(
                    f"{row['date']} | {row['pages_in_search'] or 0} | "
                    f"{extra.get('removed_from_search', 0)}"
                )
        else:
            lines.append("No search event data available.")

        lines += [
            "",
            "Analyse the above for technical indexing issues and call report_issues with findings.",
            "If data is too sparse to draw conclusions, note this in the summary.",
        ]

        return "\n".join(lines)
