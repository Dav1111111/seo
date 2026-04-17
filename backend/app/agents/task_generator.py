"""
Task Generator Agent — profesional SEO agent that creates concrete tasks.

NEW APPROACH: Instead of one big LLM call that times out on Vercel proxy,
we use a heuristic layer (pure Python) to identify opportunities,
then small per-task LLM calls (3-5s each) to generate the ready content.

This is more reliable and produces better-quality content per task.
"""

import logging
import time
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm_client import call_with_tool
from app.models.agent_run import AgentRun
from app.models.daily_metric import DailyMetric
from app.models.page import Page
from app.models.search_query import SearchQuery
from app.models.site import Site
from app.models.task import Task

logger = logging.getLogger(__name__)

# Expected CTR by position (industry benchmark)
EXPECTED_CTR = {
    1: 0.28, 2: 0.15, 3: 0.11, 4: 0.08, 5: 0.07,
    6: 0.05, 7: 0.04, 8: 0.03, 9: 0.03, 10: 0.02,
}

# Small per-task content generation tool (fast, focused)
META_TOOL = {
    "name": "generate_meta",
    "description": "Generate optimized title and meta description for a page.",
    "input_schema": {
        "type": "object",
        "properties": {
            "new_title": {
                "type": "string",
                "description": "SEO-optimized title, 50-70 chars, with keyword + price/year",
            },
            "new_description": {
                "type": "string",
                "description": "Meta description, 140-160 chars, selling, with UTP + CTA",
            },
            "new_h1": {
                "type": "string",
                "description": "H1 heading, natural with main query",
            },
            "reasoning": {
                "type": "string",
                "description": "Brief explanation (1-2 sentences) why this improves SEO",
            },
        },
        "required": ["new_title", "new_description", "new_h1"],
    },
}

FAQ_TOOL = {
    "name": "generate_faq",
    "description": "Generate FAQ block for a tourism page.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "answer": {"type": "string", "description": "30-60 words"},
                    },
                    "required": ["question", "answer"],
                },
                "description": "3-5 FAQ items — real tourist questions with concise answers",
            },
        },
        "required": ["items"],
    },
}


class TaskGeneratorAgent:
    """Finds SEO opportunities heuristically, then generates ready content via small LLM calls."""

    agent_name = "task_generator"
    model_tier = "cheap"

    async def load_data(self, db: AsyncSession, site_id: UUID) -> dict:
        today = date.today()
        end = today - timedelta(days=5)
        start = end - timedelta(days=13)

        site_row = await db.execute(select(Site).where(Site.id == site_id))
        site = site_row.scalar_one_or_none()
        if not site:
            return {}

        # Top queries with metrics
        queries_rows = await db.execute(
            select(
                SearchQuery.id,
                SearchQuery.query_text,
                SearchQuery.cluster,
                func.sum(DailyMetric.impressions).label("impressions"),
                func.sum(DailyMetric.clicks).label("clicks"),
                func.avg(DailyMetric.avg_position).label("avg_position"),
            )
            .join(SearchQuery, DailyMetric.dimension_id == SearchQuery.id)
            .where(
                DailyMetric.site_id == site_id,
                DailyMetric.metric_type == "query_performance",
                DailyMetric.date.between(start, end),
            )
            .group_by(SearchQuery.id, SearchQuery.query_text, SearchQuery.cluster)
            .order_by(func.sum(DailyMetric.impressions).desc())
            .limit(30)
        )
        queries = [dict(r._mapping) for r in queries_rows]

        # All pages
        pages_rows = await db.execute(select(Page).where(Page.site_id == site_id))
        pages = pages_rows.scalars().all()

        return {
            "site": site,
            "queries": queries,
            "pages": pages,
        }

    def _find_opportunities(self, data: dict) -> list[dict]:
        """Heuristic layer — finds SEO opportunities WITHOUT LLM.

        Returns list of opportunities, each with enough context for the LLM
        to generate specific content in a tiny follow-up call.
        """
        opportunities: list[dict] = []
        queries = data["queries"]
        pages = {p.path: p for p in data["pages"]}
        pages_by_url = {p.url: p for p in data["pages"]}

        # Opportunity 1: Queries at position 5-15 with many impressions — meta_rewrite
        for q in queries[:20]:
            pos = float(q.get("avg_position") or 0)
            imp = int(q.get("impressions") or 0)
            if 5 <= pos <= 15 and imp >= 3:
                opportunities.append({
                    "type": "meta_rewrite",
                    "priority": min(100, 50 + int(imp * 2) + int(15 - pos) * 2),
                    "impact": "high" if pos <= 10 else "medium",
                    "effort": "S",
                    "query": q["query_text"],
                    "cluster": q.get("cluster"),
                    "position": pos,
                    "impressions": imp,
                    "clicks": int(q.get("clicks") or 0),
                })

        # Opportunity 2: Pages with thin content (< 200 words) — content_expansion
        for page in data["pages"]:
            wc = page.word_count or 0
            if 0 < wc < 200 and page.http_status == 200 and not page.path.startswith("/admin"):
                opportunities.append({
                    "type": "content_expansion",
                    "priority": 60,
                    "impact": "medium",
                    "effort": "M",
                    "page_url": page.url,
                    "current_title": page.title,
                    "current_word_count": wc,
                })

        # Opportunity 3: Pages without Schema.org — schema_add
        for page in data["pages"]:
            if not page.has_schema and page.http_status == 200 and not page.path.startswith("/admin"):
                opportunities.append({
                    "type": "schema_add",
                    "priority": 40,
                    "impact": "medium",
                    "effort": "S",
                    "page_url": page.url,
                    "page_title": page.title,
                    "page_type": "tour" if "/tours/" in page.path else "landing",
                })

        # Opportunity 4: Tour/product pages without FAQ — faq_add
        for page in data["pages"]:
            path = page.path or ""
            if ("/tours/" in path or "/excursion" in path) and page.http_status == 200:
                content = (page.content_text or "").lower()
                if "вопрос" not in content and "faq" not in content:
                    opportunities.append({
                        "type": "faq_add",
                        "priority": 55,
                        "impact": "medium",
                        "effort": "M",
                        "page_url": page.url,
                        "page_title": page.title,
                        "page_topic": page.h1 or page.title,
                    })

        # Sort by priority desc, dedupe by type+url
        seen = set()
        unique = []
        for o in sorted(opportunities, key=lambda x: x["priority"], reverse=True):
            key = (o["type"], o.get("page_url") or o.get("query"))
            if key in seen:
                continue
            seen.add(key)
            unique.append(o)

        return unique[:8]  # Cap at 8 tasks per run

    async def _generate_meta_content(
        self, opp: dict, site_domain: str
    ) -> tuple[dict, dict]:
        """Small LLM call for meta_rewrite: query + site → title/description/H1."""
        system = """Ты SEO-копирайтер для туристического сайта. Пиши ПРОДАЮЩИЕ meta-теги на русском.
Title 50-70 символов с ключевым словом + цена/год. Description 140-160 символов с УТП и призывом."""
        user_msg = f"""Сайт: {site_domain}
Запрос: "{opp['query']}"
Позиция: {opp['position']} | Показов: {opp['impressions']} | Кликов: {opp['clicks']}

Напиши новый title, description и H1 чтобы поднять позицию и CTR. Вызови generate_meta."""
        try:
            raw, usage = call_with_tool(
                model_tier="cheap", system=system, user_message=user_msg,
                tool=META_TOOL, max_tokens=800,
            )
            return raw, usage
        except Exception as e:
            logger.warning(f"Meta gen failed for '{opp['query']}': {e}")
            return {}, {"cost_usd": 0, "input_tokens": 0, "output_tokens": 0, "model": "claude-haiku-4-5-20251001"}

    async def _generate_faq_content(self, opp: dict, site_domain: str) -> tuple[dict, dict]:
        """Small LLM call for FAQ: page topic → FAQ items."""
        system = """Ты помощник для туристического сайта. Пишешь FAQ-блоки на русском.
3-5 реальных вопросов туристов с краткими ответами (30-60 слов каждый)."""
        user_msg = f"""Сайт: {site_domain}
Страница: {opp['page_url']}
Тема: {opp.get('page_topic', opp.get('page_title', ''))}

Создай FAQ-блок. Вызови generate_faq."""
        try:
            raw, usage = call_with_tool(
                model_tier="cheap", system=system, user_message=user_msg,
                tool=FAQ_TOOL, max_tokens=1500,
            )
            return raw, usage
        except Exception as e:
            logger.warning(f"FAQ gen failed for {opp['page_url']}: {e}")
            return {}, {"cost_usd": 0, "input_tokens": 0, "output_tokens": 0, "model": "claude-haiku-4-5-20251001"}

    async def run(self, db: AsyncSession, site_id: UUID, trigger: str = "manual") -> dict:
        t0 = time.monotonic()
        data = await self.load_data(db, site_id)
        if not data:
            return {"status": "no_site", "tasks_created": 0}
        if not data.get("queries") and not data.get("pages"):
            return {"status": "no_data", "tasks_created": 0}

        site = data["site"]
        opportunities = self._find_opportunities(data)
        logger.info(f"TaskGenerator: found {len(opportunities)} opportunities for {site.domain}")

        run_record = AgentRun(
            site_id=site_id,
            agent_name=self.agent_name,
            model_used="claude-haiku-4-5-20251001",
            trigger=trigger,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        db.add(run_record)
        await db.flush()

        total_cost = 0.0
        total_input = 0
        total_output = 0
        created = 0

        for opp in opportunities:
            try:
                task_data = self._build_task_base(opp)

                # For meta_rewrite + faq_add, generate content via small LLM call
                if opp["type"] == "meta_rewrite":
                    content, usage = await self._generate_meta_content(opp, site.domain)
                    if content:
                        task_data["generated_content"] = content
                    total_cost += usage.get("cost_usd", 0)
                    total_input += usage.get("input_tokens", 0)
                    total_output += usage.get("output_tokens", 0)

                elif opp["type"] == "faq_add":
                    content, usage = await self._generate_faq_content(opp, site.domain)
                    if content and "items" in content:
                        task_data["generated_content"] = {"faq_items": content["items"]}
                    total_cost += usage.get("cost_usd", 0)
                    total_input += usage.get("input_tokens", 0)
                    total_output += usage.get("output_tokens", 0)

                # For content_expansion and schema_add, no LLM call —
                # the task itself carries the instruction, user sees what to do

                new_task = Task(
                    site_id=site_id,
                    **task_data,
                    status="backlog",
                )
                db.add(new_task)
                created += 1
            except Exception as exc:
                logger.warning(f"Failed to create task for opp {opp}: {exc}")

        await db.commit()

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        run_record.status = "completed"
        run_record.completed_at = datetime.now(timezone.utc)
        run_record.duration_ms = elapsed_ms
        run_record.input_tokens = total_input
        run_record.output_tokens = total_output
        run_record.cost_usd = total_cost
        run_record.output_summary = {
            "opportunities": len(opportunities),
            "tasks_created": created,
        }
        await db.flush()
        await db.commit()

        logger.info(
            f"TaskGenerator done: {created}/{len(opportunities)} tasks, "
            f"${total_cost:.4f}, {elapsed_ms}ms"
        )

        return {
            "status": "completed",
            "opportunities": len(opportunities),
            "tasks_created": created,
            "cost_usd": total_cost,
        }

    def _build_task_base(self, opp: dict) -> dict:
        """Build Task fields from opportunity data."""
        t = opp["type"]
        base = {
            "task_type": t,
            "priority": opp.get("priority", 50),
            "estimated_impact": opp.get("impact", "medium"),
            "estimated_effort": opp.get("effort", "M"),
            "target_query": opp.get("query"),
            "target_cluster": opp.get("cluster"),
            "target_page_url": opp.get("page_url"),
        }

        if t == "meta_rewrite":
            base["title"] = f"Переписать meta-теги для запроса «{opp['query']}»"
            base["description"] = (
                f"Запрос на позиции {opp['position']}, {opp['impressions']} показов, "
                f"{opp['clicks']} кликов. Перепишите title и description на странице — "
                f"цель подтянуть позицию в топ-5 и увеличить CTR."
            )
        elif t == "content_expansion":
            wc = opp["current_word_count"]
            base["title"] = f"Расширить контент страницы {opp['page_url'].split('/')[-1] or '/'}"
            base["description"] = (
                f"На странице всего {wc} слов — это тонкий контент, Яндекс его понижает. "
                f"Добавьте основные разделы: описание, программа, включено/не включено, отзывы. "
                f"Цель — 800+ слов полезного текста."
            )
        elif t == "schema_add":
            schema_type = "TouristTrip" if opp["page_type"] == "tour" else "TravelAgency"
            base["title"] = f"Добавить Schema.org ({schema_type}) на {opp['page_url'].split('/')[-1] or '/'}"
            base["description"] = (
                f"На странице нет структурированной разметки. Добавьте JSON-LD с типом {schema_type} "
                f"— Яндекс сможет показывать цены/рейтинг в выдаче (rich snippet) = +30% CTR."
            )
            base["generated_content"] = {"schema_type": schema_type}
        elif t == "faq_add":
            base["title"] = f"Добавить FAQ-блок на {opp['page_url'].split('/')[-1] or '/'}"
            base["description"] = (
                "Яндекс любит FAQ — показывает их rich snippets в выдаче. "
                "Добавьте блок с 3-5 частыми вопросами туристов и ответами. "
                "Готовые вопросы и ответы прилагаются — просто скопируйте."
            )
        else:
            base["title"] = f"SEO-задача: {t}"
            base["description"] = "Автогенерация задачи"

        return base
