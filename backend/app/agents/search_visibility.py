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
        return f"""Ты — SEO-помощник для владельца туристического бизнеса.

ГЛАВНОЕ ПРАВИЛО: Пиши ПРОСТЫМ РУССКИМ ЯЗЫКОМ, как будто объясняешь другу, а не специалисту.
Никакого SEO-жаргона. Вместо "CTR" пиши "процент кликов". Вместо "сниппет" — "описание в поиске".
Вместо "индексация" — "видимость в Яндексе". Каждая рекомендация — конкретное действие.

Сайт: {context.site_domain} (туристическая компания: горные туры зимой, морские летом)
Дата: {context.analysis_date}

ЧТО ИЩЕШЬ В ДАННЫХ:
1. Люди стали реже видеть сайт в поиске (показы упали больше чем на 30%)
2. Люди видят сайт, но не кликают (мало кликов при хорошей позиции)
3. Есть запросы где сайт показывается, но ни одного клика — значит описание в поиске не привлекает
4. Есть запросы где сайт близко к первой странице — можно дотянуть и получить трафик

ФОРМАТ ОТВЕТА:
- Заголовок проблемы: одно предложение, понятное владельцу бизнеса
- Описание: что происходит и почему это важно для бизнеса (в 2-3 предложениях)
- Рекомендация: конкретное действие, что именно сделать (не "оптимизируйте", а "перепишите заголовок страницы так, чтобы...")

КОГДА НЕ БИТЬ ТРЕВОГУ:
- Май и январь — в туризме всегда спад, это нормально
- Если данных мало (меньше недели) — не делай выводов
- Мелкие колебания позиций на 1-2 пункта — это нормально

Используй confidence 0.8+ только при чётких доказательствах.
Выводи через report_issues. ВСЁ ПРОСТЫМ РУССКИМ ЯЗЫКОМ."""

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
