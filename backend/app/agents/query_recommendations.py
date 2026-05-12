"""
Query Recommendation Agent — strategic + tactical SEO recommendations.

Two modes:
  - Tactical (daily): per-query actionable tips (position 7-15 → optimize title, etc.)
  - Strategic (weekly): per-cluster growth opportunities (weak clusters, missing content, etc.)

Inherits BaseAgent → produces Issues with types query_opportunity / cluster_opportunity.
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

# Industry CTR-by-position benchmark — used ONLY in Python to compute a
# qualitative "ниже среднего / в норме" flag. NEVER pass these raw numbers
# into the LLM prompt: the owner would see invented percentages as if they
# were measured on their own site.
_EXPECTED_CTR_BY_POS = {
    1: 0.28, 2: 0.15, 3: 0.11, 4: 0.08, 5: 0.07,
    6: 0.05, 7: 0.04, 8: 0.03, 9: 0.03, 10: 0.02,
}


def _ctr_qualitative(position: float, ctr_pct: float) -> str:
    """Compare actual CTR to industry benchmark for the given position.

    Returns a short qualitative phrase (no percentages) for the LLM prompt.
    """
    try:
        pos_int = int(round(position))
    except (TypeError, ValueError):
        return ""
    if pos_int < 1 or pos_int > 10:
        return ""
    expected_frac = _EXPECTED_CTR_BY_POS.get(pos_int)
    if not expected_frac:
        return ""
    actual_frac = (ctr_pct or 0) / 100.0
    if actual_frac < expected_frac * 0.6:
        return "CTR заметно ниже среднего по позиции"
    if actual_frac < expected_frac:
        return "CTR ниже среднего по позиции"
    return "CTR в пределах нормы"


class TacticalQueryAgent(BaseAgent):
    """Daily agent: finds per-query optimization opportunities."""

    agent_name = "query_tactical"
    model_tier = "cheap"

    def get_system_prompt(self, context: AgentContext) -> str:
        return f"""Ты — тактический SEO-помощник для владельца туристического бизнеса.
Даёшь КОНКРЕТНЫЕ рекомендации по каждому запросу. Пишешь ПРОСТЫМ РУССКИМ ЯЗЫКОМ.

Сайт: {context.site_domain}
Дата: {context.analysis_date}

ЧТО ИСКАТЬ:
1. Запросы на позициях 7-15 (близко к первой странице — можно дотянуть!)
   → Рекомендация: "Перепишите заголовок страницы, добавьте ключевое слово в H1"
2. Запросы с высокими показами но 0 кликов (люди видят, но не нажимают)
   → Рекомендация: "Перепишите описание страницы в поиске — сделайте его привлекательнее"
3. Запросы с CTR сильно ниже нормы для их позиции (флаг «CTR ниже среднего» в таблице ниже)
   → Не цитируй конкретных процентов — у соседних сайтов в нише на этой позиции CTR обычно выше, точную цифру по тебе пока не измеряли. Рекомендуй переписать описание в поиске.
4. Новые быстрорастущие запросы (показы растут от недели к неделе)
   → Рекомендация: "Появился новый запрос — создайте или улучшите страницу под него"

ФОРМАТ КАЖДОЙ РЕКОМЕНДАЦИИ:
- Заголовок: конкретный, привязан к запросу ("Запрос 'туры в горы' — на 8й позиции, можно дотянуть до топ-5")
- Описание: что происходит и почему это важно для бизнеса
- Рекомендация: КОНКРЕТНОЕ действие. НЕ "оптимизируйте", а "перепишите заголовок с '...' на '...'"

ТИПЫ ПРОБЛЕМ:
- new_opportunity — для всех рекомендаций по запросам

КОГДА НЕ ДАВАТЬ РЕКОМЕНДАЦИИ:
- Если данных меньше 3 дней — мало информации
- Если показов меньше 3 — слишком мало, не стоит внимания
- Мелкие колебания позиций (±1-2) — это нормально

Используй confidence 0.7+ только при чётких возможностях.
Выводи через report_issues. ВСЁ НА РУССКОМ ЯЗЫКЕ."""

    async def load_data(self, db: AsyncSession, context: AgentContext) -> dict:
        today = context.analysis_date
        curr_end = today - timedelta(days=5)
        curr_start = curr_end - timedelta(days=6)
        prev_end = curr_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=6)

        # Current period per query
        curr_rows = await db.execute(
            select(
                SearchQuery.query_text,
                SearchQuery.cluster,
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
            .group_by(SearchQuery.query_text, SearchQuery.cluster)
            .order_by(func.sum(DailyMetric.impressions).desc())
            .limit(100)
        )
        curr_data = [dict(r._mapping) for r in curr_rows]

        # Previous period for comparison
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

        if not curr_data:
            return {}

        return {
            "period": f"{curr_start} → {curr_end}",
            "current": curr_data,
            "previous": prev_data,
        }

    def format_user_message(self, context: AgentContext, data: dict) -> str:
        curr = data["current"]
        prev = data["previous"]

        lines = [
            f"Период: {data['period']}",
            f"Всего запросов: {len(curr)}",
            "",
            "ЗАПРОСЫ (отсортированы по показам):",
            "запрос | кластер | показы | клики | CTR% | позиция | дней_данных | пред_показы | пред_позиция | флаг_ctr",
        ]

        for q in curr:
            imp = int(q.get("impressions") or 0)
            clk = int(q.get("clicks") or 0)
            pos = round(float(q.get("avg_position") or 0), 1)
            ctr = round(clk / imp * 100, 1) if imp > 0 else 0
            days = int(q.get("days_count") or 0)
            cluster = q.get("cluster") or "—"

            p = prev.get(q["query_text"], {})
            p_imp = int(p.get("impressions") or 0)
            p_pos = round(float(p.get("avg_position") or 0), 1) if p.get("avg_position") else "—"

            ctr_flag = _ctr_qualitative(pos, ctr) or "—"

            lines.append(
                f"{q['query_text']} | {cluster} | {imp} | {clk} | {ctr}% | {pos} | {days} | {p_imp} | {p_pos} | {ctr_flag}"
            )

        lines += [
            "",
            "Колонка «флаг_ctr» уже посчитана в Python: ориентируйся на неё, не цитируй конкретных процентов.",
            "Найди возможности для роста и выдай рекомендации через report_issues.",
            "Тип issue_type = 'new_opportunity' для всех.",
        ]

        return "\n".join(lines)


class StrategicQueryAgent(BaseAgent):
    """Weekly agent: finds cluster-level growth strategies."""

    agent_name = "query_strategic"
    model_tier = "cheap"

    def get_system_prompt(self, context: AgentContext) -> str:
        return f"""Ты — SEO-стратег для туристического бизнеса. Анализируешь КЛАСТЕРЫ запросов.
Пишешь ПРОСТЫМ РУССКИМ ЯЗЫКОМ, понятным владельцу бизнеса.

Сайт: {context.site_domain}
Дата: {context.analysis_date}

ЧТО ИСКАТЬ:
1. Слабые кластеры с потенциалом (много запросов, но плохие позиции)
   → "Направление 'морские экскурсии' — 20 запросов, средняя позиция 25. Нужна отдельная страница"
2. Сильные кластеры для усиления (хорошие позиции, можно доминировать)
   → "Направление 'абхазия' — 10 запросов в топ-10. Добавьте ещё 3 статьи чтобы закрепиться"
3. Кластеры с низким CTR (хорошие позиции, мало кликов)
   → "Запросы по 'горные туры' в топ-5, но кликают редко (CTR ниже среднего по позиции). Перепишите описания"
4. Растущие кластеры (показы растут)
   → "Направление 'активный отдых' растёт по числу показов в течение нескольких недель. Усильте контент"

ФОРМАТ:
- Заголовок: стратегический, про направление бизнеса
- Описание: цифры и факты, понятные владельцу
- Рекомендация: конкретные шаги (создать страницу, написать статьи, улучшить описания)

issue_type = 'new_opportunity' для всех.
severity = 'medium' для возможностей, 'high' для сильных возможностей (позиция 5-15, много показов).

Выводи через report_issues. ВСЁ НА РУССКОМ ЯЗЫКЕ."""

    async def load_data(self, db: AsyncSession, context: AgentContext) -> dict:
        today = context.analysis_date
        curr_end = today - timedelta(days=5)
        curr_start = curr_end - timedelta(days=13)  # 2 weeks for strategic view
        prev_end = curr_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=13)

        # Cluster-level aggregates
        curr_rows = await db.execute(
            select(
                func.coalesce(SearchQuery.cluster, "без_кластера").label("cluster"),
                func.count(SearchQuery.id.distinct()).label("query_count"),
                func.sum(DailyMetric.impressions).label("impressions"),
                func.sum(DailyMetric.clicks).label("clicks"),
                func.avg(DailyMetric.avg_position).label("avg_position"),
            )
            .join(SearchQuery, DailyMetric.dimension_id == SearchQuery.id)
            .where(
                DailyMetric.site_id == context.site_id,
                DailyMetric.metric_type == "query_performance",
                DailyMetric.date.between(curr_start, curr_end),
            )
            .group_by(func.coalesce(SearchQuery.cluster, "без_кластера"))
            .order_by(func.sum(DailyMetric.impressions).desc())
        )
        curr_clusters = [dict(r._mapping) for r in curr_rows]

        # Previous period clusters
        prev_rows = await db.execute(
            select(
                func.coalesce(SearchQuery.cluster, "без_кластера").label("cluster"),
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
            .group_by(func.coalesce(SearchQuery.cluster, "без_кластера"))
        )
        prev_clusters = {r.cluster: dict(r._mapping) for r in prev_rows}

        # Top queries per cluster (for context)
        top_queries = await db.execute(
            select(
                SearchQuery.cluster,
                SearchQuery.query_text,
                func.sum(DailyMetric.impressions).label("impressions"),
                func.avg(DailyMetric.avg_position).label("avg_position"),
            )
            .join(SearchQuery, DailyMetric.dimension_id == SearchQuery.id)
            .where(
                DailyMetric.site_id == context.site_id,
                DailyMetric.metric_type == "query_performance",
                DailyMetric.date.between(curr_start, curr_end),
            )
            .group_by(SearchQuery.cluster, SearchQuery.query_text)
            .order_by(func.sum(DailyMetric.impressions).desc())
            .limit(50)
        )
        queries_by_cluster: dict[str, list] = {}
        for r in top_queries:
            cl = r.cluster or "без_кластера"
            queries_by_cluster.setdefault(cl, []).append({
                "query": r.query_text,
                "impressions": int(r.impressions or 0),
                "position": round(float(r.avg_position), 1) if r.avg_position else None,
            })

        if not curr_clusters:
            return {}

        return {
            "period": f"{curr_start} → {curr_end}",
            "clusters": curr_clusters,
            "prev_clusters": prev_clusters,
            "top_queries": queries_by_cluster,
        }

    def format_user_message(self, context: AgentContext, data: dict) -> str:
        clusters = data["clusters"]
        prev = data["prev_clusters"]
        top_q = data["top_queries"]

        lines = [
            f"Период: {data['period']}",
            f"Всего кластеров: {len(clusters)}",
            "",
            "КЛАСТЕРЫ (отсортированы по показам):",
            "кластер | запросов | показы | клики | CTR% | ср.позиция | пред_показы | изменение%",
        ]

        for cl in clusters:
            name = cl["cluster"]
            imp = int(cl.get("impressions") or 0)
            clk = int(cl.get("clicks") or 0)
            qc = int(cl.get("query_count") or 0)
            pos = round(float(cl.get("avg_position") or 0), 1)
            ctr = round(clk / imp * 100, 1) if imp > 0 else 0

            p = prev.get(name, {})
            p_imp = int(p.get("impressions") or 0)
            change = round((imp - p_imp) / max(p_imp, 1) * 100, 1) if p_imp else "новый"

            lines.append(f"{name} | {qc} | {imp} | {clk} | {ctr}% | {pos} | {p_imp} | {change}%")

            # Show top queries for this cluster
            qs = top_q.get(name, [])[:5]
            for q in qs:
                lines.append(f"  ↳ {q['query']} (показов: {q['impressions']}, поз: {q['position']})")

        lines += [
            "",
            "Дай СТРАТЕГИЧЕСКИЕ рекомендации по каждому кластеру.",
            "Тип issue_type = 'new_opportunity' для всех.",
            "Выведи через report_issues.",
        ]

        return "\n".join(lines)
