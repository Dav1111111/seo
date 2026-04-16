"""
Query Clustering Agent — groups search queries into semantic clusters.

Uses Claude Haiku via tool_use to assign cluster names to queries.
Processes in batches of 80 queries. Cost: ~$0.01 per 500 queries.
Runs weekly via Celery Beat (Monday 09:00 MSK).
"""

import logging
import time
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm_client import call_with_tool
from app.models.search_query import SearchQuery
from app.models.daily_metric import DailyMetric
from app.models.agent_run import AgentRun

logger = logging.getLogger(__name__)

CLUSTERING_TOOL = {
    "name": "assign_clusters",
    "description": "Assign a semantic cluster name to each search query.",
    "input_schema": {
        "type": "object",
        "properties": {
            "assignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "0-based index of the query in the input list",
                        },
                        "cluster": {
                            "type": "string",
                            "description": "Cluster name in Russian, 1-3 words, lowercase, underscores instead of spaces",
                        },
                    },
                    "required": ["index", "cluster"],
                },
            },
            "summary": {
                "type": "string",
                "description": "Brief summary of clusters found",
            },
        },
        "required": ["assignments", "summary"],
    },
}

BATCH_SIZE = 80


class QueryClusteringAgent:
    """Groups search queries into semantic clusters using Claude Haiku."""

    model_tier = "cheap"  # Haiku

    def _build_system_prompt(self, domain: str) -> str:
        return f"""Ты — SEO-аналитик. Группируешь поисковые запросы в тематические кластеры.

Сайт: {domain} (туристический бизнес: экскурсии, экспедиции, активный отдых, Сочи, Абхазия)

ПРАВИЛА:
- Каждый кластер — одна тема или интент пользователя
- Название кластера: 1-3 слова на русском, через подчёркивание (например: горные_туры, абхазия_экскурсии, морские_прогулки, трансферы, цены)
- Если запрос содержит название компании или сайта — кластер "бренд"
- Запросы про конкретную локацию — включай локацию в название (красная_поляна, адлер, абхазия)
- Если запрос не подходит ни в один существующий кластер — создай новый
- Старайся не создавать кластер из 1 запроса — объединяй похожие

Примеры кластеров для туризма:
- горные_туры, морские_прогулки, джиппинг, абхазия_экскурсии
- красная_поляна, адлер, олимпийский_парк
- цены, отзывы, бренд, как_добраться
- семейный_отдых, активный_отдых, vip_туры

Верни решение через assign_clusters."""

    def _format_queries(self, queries: list[dict]) -> str:
        lines = ["ЗАПРОСЫ ДЛЯ КЛАСТЕРИЗАЦИИ:", ""]
        for i, q in enumerate(queries):
            imp = q.get("impressions", 0)
            lines.append(f"[{i}] {q['query_text']} (показов: {imp})")
        lines.append("")
        lines.append("Сгруппируй все запросы в кластеры через assign_clusters.")
        return "\n".join(lines)

    async def run(
        self,
        db: AsyncSession,
        site_id: UUID,
        force_recluster: bool = False,
    ) -> dict:
        """Cluster all queries for a site. Returns stats."""
        t0 = time.monotonic()
        today = date.today()

        # Get site domain
        from app.models.site import Site
        site_row = await db.execute(select(Site.domain).where(Site.id == site_id))
        domain = site_row.scalar_one_or_none() or "unknown"

        # Load queries — either unclustered only, or all (force recluster)
        query_filter = [SearchQuery.site_id == site_id]
        if not force_recluster:
            query_filter.append(SearchQuery.cluster.is_(None))

        # Join with metrics to get impression counts for context
        curr_start = today - timedelta(days=12)
        curr_end = today - timedelta(days=5)

        rows = await db.execute(
            select(
                SearchQuery.id,
                SearchQuery.query_text,
                SearchQuery.cluster,
                func.coalesce(func.sum(DailyMetric.impressions), 0).label("impressions"),
            )
            .outerjoin(
                DailyMetric,
                (DailyMetric.dimension_id == SearchQuery.id)
                & (DailyMetric.metric_type == "query_performance")
                & (DailyMetric.date.between(curr_start, curr_end)),
            )
            .where(*query_filter)
            .group_by(SearchQuery.id, SearchQuery.query_text, SearchQuery.cluster)
            .order_by(func.coalesce(func.sum(DailyMetric.impressions), 0).desc())
        )

        all_queries = [
            {"id": r.id, "query_text": r.query_text, "impressions": int(r.impressions)}
            for r in rows
        ]

        if not all_queries:
            logger.info("No queries to cluster for site %s", site_id)
            return {"status": "no_queries", "queries_clustered": 0}

        # Create agent run record
        run_record = AgentRun(
            site_id=site_id,
            agent_name="query_clustering",
            model_used="pending",
            trigger="manual" if force_recluster else "scheduled",
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        db.add(run_record)
        await db.flush()

        total_cost = 0.0
        total_input = 0
        total_output = 0
        total_clustered = 0
        cluster_counts: dict[str, int] = {}

        # Process in batches
        system = self._build_system_prompt(domain)

        for batch_start in range(0, len(all_queries), BATCH_SIZE):
            batch = all_queries[batch_start:batch_start + BATCH_SIZE]
            user_msg = self._format_queries(batch)

            try:
                raw_output, usage = call_with_tool(
                    model_tier=self.model_tier,
                    system=system,
                    user_message=user_msg,
                    tool=CLUSTERING_TOOL,
                    max_tokens=4096,
                )

                total_cost += usage["cost_usd"]
                total_input += usage["input_tokens"]
                total_output += usage["output_tokens"]

                assignments = raw_output.get("assignments", [])
                for a in assignments:
                    idx = a.get("index", -1)
                    cluster_name = a.get("cluster", "").strip().lower().replace(" ", "_")
                    if 0 <= idx < len(batch) and cluster_name:
                        query_id = batch[idx]["id"]
                        await db.execute(
                            update(SearchQuery)
                            .where(SearchQuery.id == query_id)
                            .values(cluster=cluster_name)
                        )
                        total_clustered += 1
                        cluster_counts[cluster_name] = cluster_counts.get(cluster_name, 0) + 1

                logger.info(
                    "Clustering batch %d-%d: %d assigned, cost=$%.4f",
                    batch_start, batch_start + len(batch),
                    len(assignments), usage["cost_usd"],
                )

            except Exception as exc:
                logger.error("Clustering batch %d failed: %s", batch_start, exc)

        await db.commit()

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # Update run record
        run_record.status = "completed"
        run_record.completed_at = datetime.now(timezone.utc)
        run_record.duration_ms = elapsed_ms
        run_record.model_used = "claude-haiku-4-5-20251001"
        run_record.input_tokens = total_input
        run_record.output_tokens = total_output
        run_record.cost_usd = total_cost
        run_record.output_summary = {
            "queries_clustered": total_clustered,
            "clusters_found": len(cluster_counts),
            "cluster_sizes": cluster_counts,
        }
        await db.flush()

        result = {
            "status": "completed",
            "queries_total": len(all_queries),
            "queries_clustered": total_clustered,
            "clusters_found": len(cluster_counts),
            "cluster_sizes": cluster_counts,
            "cost_usd": total_cost,
            "elapsed_ms": elapsed_ms,
        }

        logger.info(
            "Clustering done: %d queries → %d clusters, $%.4f, %dms",
            total_clustered, len(cluster_counts), total_cost, elapsed_ms,
        )
        return result
