"""
Agent 1 — SearchVisibilityAgent

Analyses GSC-style data from Yandex Webmaster:
- Week-over-week impression/click drops per query
- Position drops (>3 positions)
- CTR anomalies
- High-impressions / zero-click opportunities
- Query cannibalization signals
"""

import logging
from datetime import date, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.agents.schemas import AgentContext
from app.models.daily_metric import DailyMetric
from app.models.search_query import SearchQuery

logger = logging.getLogger(__name__)

# Expected CTR by average position (industry benchmark)
EXPECTED_CTR = {
    1: 0.28, 2: 0.15, 3: 0.11, 4: 0.08, 5: 0.07,
    6: 0.05, 7: 0.04, 8: 0.03, 9: 0.03, 10: 0.02,
}


class SearchVisibilityAgent(BaseAgent):
    agent_name = "search_visibility"
    model_tier = "cheap"  # Haiku 4.5

    def get_system_prompt(self, context: AgentContext) -> str:
        return f"""Ты — старший SEO-аналитик, специализирующийся на видимости в Яндекс Поиске для туристических сайтов.

ВАЖНО: Все ответы, заголовки проблем, описания и рекомендации — ТОЛЬКО на русском языке.

Сайт: {context.site_domain}
Дата анализа: {context.analysis_date}

КОНТЕКСТ ОТРАСЛИ:
- Это российская туристическая компания: горные туры (зима), морские туры (лето)
- Яндекс — основная поисковая система (>50% рынка в России)
- Брендовые запросы (название сайта) должны иметь высокий CTR
- Небрендовые запросы в позиции 1-3 обычно получают 11-28% CTR

ТВОЯ ЗАДАЧА:
Проанализировать данные по поисковым запросам за два периода и найти:
1. ПРОСАДКИ ПОКАЗОВ — запросы с падением >30% показов неделя к неделе
2. ПРОСАДКИ КЛИКОВ — запросы с падением >40% кликов неделя к неделе
3. АНОМАЛИИ CTR — запросы в позиции 1-10 с CTR значительно ниже бенчмарка
4. QUICK WINS — запросы в позиции 4-15 с >20 показами, но CTR <3% (оптимизировать сниппет)
5. ВОЗМОЖНОСТИ — запросы с показами, но 0 кликов (позиция >15, можно расширить контент)

ПРАВИЛА КАЛИБРОВКИ:
- Сезонные падения в мае (конец каникул) и январе (после праздников) — НОРМАЛЬНО для туризма. Снижай confidence
- Колебания позиции ±2 — это ШУМ, не сигнал (confidence 0.3 или ниже)
- Отмечай проблему только при наличии ≥7 дней данных
- Брендовые запросы (с названием сайта) — отмечай, но не бей тревогу

Используй confidence 0.8+ только когда данные однозначно подтверждают проблему.
Выводи результаты через инструмент report_issues. ВСЁ НА РУССКОМ ЯЗЫКЕ."""

    async def load_data(self, db: AsyncSession, context: AgentContext) -> dict:
        today = context.analysis_date
        curr_end = today - timedelta(days=5)
        curr_start = curr_end - timedelta(days=6)
        prev_end = curr_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=6)

        # Current period: aggregated metrics per query
        curr_rows = await db.execute(
            select(
                SearchQuery.query_text,
                func.sum(DailyMetric.impressions).label("impressions"),
                func.sum(DailyMetric.clicks).label("clicks"),
                func.avg(DailyMetric.avg_position).label("avg_position"),
                func.count(DailyMetric.date).label("days_count"),
            )
            .join(SearchQuery, DailyMetric.dimension_id == SearchQuery.id)
            .where(
                DailyMetric.site_id == context.site_id,
                DailyMetric.metric_type == "query_performance",
                DailyMetric.date.between(curr_start, curr_end),
            )
            .group_by(SearchQuery.query_text)
            .order_by(func.sum(DailyMetric.impressions).desc())
            .limit(200)
        )
        curr_data = {r.query_text: dict(r._mapping) for r in curr_rows}

        # Previous period
        prev_rows = await db.execute(
            select(
                SearchQuery.query_text,
                func.sum(DailyMetric.impressions).label("impressions"),
                func.sum(DailyMetric.clicks).label("clicks"),
                func.avg(DailyMetric.avg_position).label("avg_position"),
            )
            .join(SearchQuery, DailyMetric.dimension_id == SearchQuery.id)
            .where(
                DailyMetric.site_id == context.site_id,
                DailyMetric.metric_type == "query_performance",
                DailyMetric.date.between(prev_start, prev_end),
            )
            .group_by(SearchQuery.query_text)
        )
        prev_data = {r.query_text: dict(r._mapping) for r in prev_rows}

        return {
            "curr_period": {"start": curr_start.isoformat(), "end": curr_end.isoformat()},
            "prev_period": {"start": prev_start.isoformat(), "end": prev_end.isoformat()},
            "current": curr_data,
            "previous": prev_data,
        }

    def format_user_message(self, context: AgentContext, data: dict) -> str:
        if not data.get("current"):
            return "No search query data available for this site."

        curr = data["current"]
        prev = data["previous"]

        lines = [
            f"Current period: {data['curr_period']['start']} to {data['curr_period']['end']}",
            f"Previous period: {data['prev_period']['start']} to {data['prev_period']['end']}",
            f"Total queries in current period: {len(curr)}",
            "",
            "QUERY PERFORMANCE (sorted by impressions desc):",
            "query | curr_impressions | curr_clicks | curr_ctr% | curr_position | prev_impressions | prev_clicks | prev_position | impression_change% | click_change%",
        ]

        rows = []
        for query, c in curr.items():
            p = prev.get(query, {})
            curr_imp = int(c.get("impressions") or 0)
            curr_clk = int(c.get("clicks") or 0)
            curr_pos = round(float(c.get("avg_position") or 0), 1)
            curr_ctr = round(curr_clk / curr_imp * 100, 1) if curr_imp > 0 else 0.0

            prev_imp = int(p.get("impressions") or 0)
            prev_clk = int(p.get("clicks") or 0)
            prev_pos = round(float(p.get("avg_position") or 0), 1)

            imp_change = round((curr_imp - prev_imp) / max(prev_imp, 1) * 100, 1) if prev_imp else None
            clk_change = round((curr_clk - prev_clk) / max(prev_clk, 1) * 100, 1) if prev_clk else None

            rows.append({
                "query": query,
                "curr_imp": curr_imp,
                "curr_clk": curr_clk,
                "curr_ctr": curr_ctr,
                "curr_pos": curr_pos,
                "prev_imp": prev_imp,
                "prev_clk": prev_clk,
                "prev_pos": prev_pos,
                "imp_change": imp_change,
                "clk_change": clk_change,
                "days": int(c.get("days_count") or 0),
            })

        # Sort by impression for context, but put biggest drops first
        rows.sort(key=lambda r: r["curr_imp"], reverse=True)

        for r in rows[:100]:  # cap at 100 rows for token budget
            lines.append(
                f"{r['query']} | {r['curr_imp']} | {r['curr_clk']} | "
                f"{r['curr_ctr']}% | {r['curr_pos']} | "
                f"{r['prev_imp']} | {r['prev_clk']} | {r['prev_pos']} | "
                f"{r['imp_change']}% | {r['clk_change']}% | days={r['days']}"
            )

        lines += [
            "",
            "EXPECTED CTR BY POSITION (benchmark):",
            ", ".join(f"pos{k}={v*100:.0f}%" for k, v in EXPECTED_CTR.items()),
            "",
            "Analyse the above and call report_issues with all findings.",
        ]

        return "\n".join(lines)
