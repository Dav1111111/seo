"""
Task Generator Agent — the core of the "professional SEO agent".

Takes ALL available data:
- Queries with positions, impressions, clicks, clusters
- Site pages with current titles, H1s, meta descriptions, content
- Competitor top-10 data (optional, if available)

Produces concrete SEO tasks with READY-TO-USE content:
- New title/description for specific pages
- Article drafts for new blog posts
- FAQ blocks
- Schema.org markup
- Content additions

Each task has priority, estimated impact, and actionable content.
"""

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
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


TASK_GENERATION_TOOL = {
    "name": "create_seo_tasks",
    "description": (
        "Generate a list of concrete SEO tasks with ready-to-use content. "
        "Each task must have a specific action, target page/query, and generated content "
        "that the user can copy-paste directly."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "task_type": {
                            "type": "string",
                            "enum": [
                                "meta_rewrite",        # rewrite title+description for existing page
                                "new_page",            # create new landing/hub page
                                "new_article",         # write new blog article
                                "content_expansion",   # add content to thin page
                                "schema_add",          # add Schema.org markup
                                "faq_add",             # add FAQ block
                                "internal_linking",    # fix/add internal links
                                "h1_rewrite",          # improve H1
                            ],
                        },
                        "priority": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "description": "1-100, higher = more important (based on traffic potential)",
                        },
                        "estimated_impact": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "estimated_effort": {
                            "type": "string",
                            "enum": ["XS", "S", "M", "L", "XL"],
                            "description": "XS=15min, S=1h, M=half-day, L=day, XL=week",
                        },
                        "title": {
                            "type": "string",
                            "description": "Task title — concrete, actionable (max 200 chars)",
                        },
                        "description": {
                            "type": "string",
                            "description": "What to do and why. 2-4 sentences, plain Russian.",
                        },
                        "target_query": {
                            "type": "string",
                            "description": "Main search query this task targets (optional)",
                        },
                        "target_cluster": {
                            "type": "string",
                            "description": "Cluster this task targets (optional)",
                        },
                        "target_page_url": {
                            "type": "string",
                            "description": "URL of page to modify, or proposed URL for new page",
                        },
                        "generated_content": {
                            "type": "object",
                            "description": "Ready-to-paste content (varies by task_type)",
                            "properties": {
                                "new_title": {"type": "string"},
                                "new_description": {"type": "string"},
                                "new_h1": {"type": "string"},
                                "article_outline": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "For new_article: H2 section headings",
                                },
                                "article_intro": {
                                    "type": "string",
                                    "description": "For new_article: opening paragraph",
                                },
                                "faq_items": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "question": {"type": "string"},
                                            "answer": {"type": "string"},
                                        },
                                    },
                                },
                                "schema_type": {
                                    "type": "string",
                                    "description": "e.g. 'TouristTrip', 'Product', 'FAQPage', 'Review'",
                                },
                            },
                        },
                    },
                    "required": [
                        "task_type", "priority", "estimated_impact",
                        "estimated_effort", "title", "description",
                    ],
                },
            },
            "summary": {
                "type": "string",
                "description": "Overall summary of generated tasks",
            },
        },
        "required": ["tasks", "summary"],
    },
}


class TaskGeneratorAgent:
    """Generates concrete, actionable SEO tasks from all available data."""

    agent_name = "task_generator"
    model_tier = "cheap"  # Haiku — faster, fits Vercel proxy timeout. Quality is good enough for SEO tasks.

    async def load_context(self, db: AsyncSession, site_id: UUID) -> dict:
        """Load everything the agent needs: queries, pages, clusters."""
        today = date.today()
        end = today - timedelta(days=5)
        start = end - timedelta(days=13)  # 2 weeks

        # Site info
        site_row = await db.execute(select(Site).where(Site.id == site_id))
        site = site_row.scalar_one_or_none()
        if not site:
            return {}

        # Top queries with positions (last 2 weeks)
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
            .limit(25)
        )
        queries = [dict(r._mapping) for r in queries_rows]

        # All pages (crawled)
        pages_rows = await db.execute(
            select(Page).where(Page.site_id == site_id)
        )
        pages = pages_rows.scalars().all()

        # Cluster summary
        clusters_rows = await db.execute(
            select(
                SearchQuery.cluster,
                func.count(SearchQuery.id).label("count"),
                func.sum(DailyMetric.impressions).label("impressions"),
                func.avg(DailyMetric.avg_position).label("avg_position"),
            )
            .join(DailyMetric,
                (DailyMetric.dimension_id == SearchQuery.id)
                & (DailyMetric.metric_type == "query_performance")
                & (DailyMetric.date.between(start, end))
            )
            .where(SearchQuery.site_id == site_id, SearchQuery.cluster.isnot(None))
            .group_by(SearchQuery.cluster)
            .order_by(func.sum(DailyMetric.impressions).desc())
        )
        clusters = [dict(r._mapping) for r in clusters_rows]

        return {
            "site": {
                "domain": site.domain,
                "display_name": site.display_name,
                "operating_mode": site.operating_mode,
            },
            "queries": queries,
            "pages": [
                {
                    "url": p.url,
                    "path": p.path,
                    "title": p.title,
                    "meta_description": p.meta_description,
                    "h1": p.h1,
                    "word_count": p.word_count or 0,
                    "has_schema": p.has_schema,
                    "content_preview": (p.content_text or "")[:500] if p.content_text else None,
                }
                for p in pages
            ],
            "clusters": clusters,
            "period": f"{start} → {end}",
        }

    def build_system_prompt(self, context: dict) -> str:
        site = context.get("site", {})
        return f"""Ты — профессиональный SEO-специалист для туристического бизнеса.
Твоя задача — превратить данные о сайте в КОНКРЕТНЫЕ, ДЕЙСТВЕННЫЕ задачи с ГОТОВЫМ контентом.

Сайт: {site.get('domain', '?')} ({site.get('display_name', '?')})

ЧТО ТЫ АНАЛИЗИРУЕШЬ:
1. Запросы, по которым сайт показывается, с позициями и показами
2. Текущие страницы сайта с их title/meta description/H1/контентом
3. Кластеры запросов (тематические группы)

ПРИОРИТЕТЫ (от важнейшего к наименее важному):
1. **Запросы 5-15 позиция, много показов** — можно быстро подтянуть в топ. Пиши задачу "meta_rewrite" с новым привлекательным title+description.
2. **Запросы в топ-10, но CTR < 3%** — позиция хорошая, но кликают мало. Переписать meta description чтобы был магнит для кликов.
3. **Кластеры без выделенной страницы** — запросы есть, отдельной страницы нет. Задача "new_page" с готовой структурой.
4. **Тонкий контент (word_count < 500)** — добавить объём. Задача "content_expansion" с готовыми H2-секциями.
5. **Страницы без Schema.org** — добавить разметку. Задача "schema_add" с готовым JSON-LD.
6. **Страницы без FAQ** — добавить FAQ-блок (Яндекс любит, даёт rich snippet). Задача "faq_add" с 5-8 готовыми вопросами-ответами.

ПРАВИЛА ГЕНЕРАЦИИ КОНТЕНТА (КРАТКО!):
- Title: 50-70 символов, с ключевым словом + цена/год
- Description: 140-160 символов, продающий, с УТП
- H1: естественный, с основным запросом
- Для статей: outline 4-5 коротких H2
- Вступление статьи: 2-3 предложения (НЕ длинное!)
- FAQ: 3-4 вопроса с КРАТКИМИ ответами (30-50 слов каждый)

СТРОГОЕ ОГРАНИЧЕНИЕ: МАКСИМУМ 6 задач за раз. Лучше 6 качественных и концентрированных
чем 15 размытых. Экономь токены — не пиши "воду".

ТИПЫ ЗАДАЧ И КОГДА ИХ ВЫДАВАТЬ:
- **meta_rewrite** — есть страница, но title/description слабые → улучшить
- **new_page** — есть кластер запросов без посадочной страницы → создать
- **new_article** — есть информационный запрос без статьи → написать статью в блог
- **content_expansion** — страница с малым контентом → расширить
- **schema_add** — страница без Schema.org → добавить разметку
- **faq_add** — страница без FAQ → добавить блок

НЕ ВЫДАВАЙ:
- Общие советы без конкретного контента ("улучшите сайт" — нет)
- Задачи с confidence <0.6
- Более 6 задач за раз (максимум 6, приоритизируй лучшие)

Выводи через create_seo_tasks. Контент — на русском языке."""

    def build_user_message(self, context: dict) -> str:
        lines = [
            f"ПЕРИОД АНАЛИЗА: {context.get('period')}",
            "",
            f"ЗАПРОСЫ ({len(context.get('queries', []))} топ-запросов):",
            "запрос | кластер | показы | клики | CTR% | позиция",
        ]
        for q in context.get("queries", [])[:20]:
            imp = int(q.get("impressions") or 0)
            clk = int(q.get("clicks") or 0)
            ctr = round(clk / imp * 100, 1) if imp > 0 else 0
            pos = round(float(q.get("avg_position") or 0), 1)
            cluster = q.get("cluster") or "—"
            lines.append(f"  {q['query_text']} | {cluster} | {imp} | {clk} | {ctr}% | {pos}")

        lines += ["", f"КЛАСТЕРЫ ({len(context.get('clusters', []))}):"]
        for c in context.get("clusters", [])[:10]:
            lines.append(
                f"  {c['cluster']}: {c['count']} запросов, "
                f"{int(c.get('impressions') or 0)} показов, "
                f"поз. {round(float(c.get('avg_position') or 0), 1)}"
            )

        lines += ["", f"СТРАНИЦЫ САЙТА ({len(context.get('pages', []))} проиндексированных):"]
        for p in context.get("pages", [])[:15]:
            wc = p.get("word_count") or 0
            schema = "✓" if p.get("has_schema") else "✗"
            title = (p.get("title") or "НЕТ")[:60]
            desc = (p.get("meta_description") or "НЕТ")[:60]
            lines.append(f"  {p['path']} | title: {title} | desc: {desc} | слов: {wc} schema:{schema}")

        lines += [
            "",
            "СГЕНЕРИРУЙ SEO-ЗАДАЧИ.",
            "Каждая задача должна иметь ГОТОВЫЙ контент который можно скопировать и вставить.",
            "Приоритизируй по потенциалу трафика.",
            "Вызови create_seo_tasks.",
        ]
        return "\n".join(lines)

    async def run(self, db: AsyncSession, site_id: UUID, trigger: str = "manual") -> dict:
        """Generate SEO tasks for a site."""
        t0 = time.monotonic()

        context = await self.load_context(db, site_id)
        if not context:
            return {"status": "no_site", "tasks_created": 0}

        if not context.get("queries"):
            return {
                "status": "no_data",
                "reason": "No query data. Run Webmaster collection first.",
                "tasks_created": 0,
            }

        # Record run start
        run_record = AgentRun(
            site_id=site_id,
            agent_name=self.agent_name,
            model_used="pending",
            trigger=trigger,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        db.add(run_record)
        await db.flush()

        system = self.build_system_prompt(context)
        user_msg = self.build_user_message(context)

        try:
            raw_output, usage = call_with_tool(
                model_tier=self.model_tier,
                system=system,
                user_message=user_msg,
                tool=TASK_GENERATION_TOOL,
                max_tokens=4096,
            )
        except Exception as exc:
            logger.error(f"TaskGenerator LLM call failed: {exc}")
            run_record.status = "failed"
            run_record.error_message = str(exc)[:2000]
            run_record.completed_at = datetime.now(timezone.utc)
            await db.flush()
            return {"status": "error", "error": str(exc), "tasks_created": 0}

        tasks_data = raw_output.get("tasks", [])
        summary = raw_output.get("summary", "")
        logger.info(
            f"TaskGenerator raw_output: {len(tasks_data)} tasks, keys: {list(raw_output.keys())}"
        )
        if not tasks_data:
            logger.warning(f"TaskGenerator: no tasks in output. Full output: {str(raw_output)[:500]}")

        # Save tasks to DB
        created = 0
        for t in tasks_data:
            try:
                new_task = Task(
                    site_id=site_id,
                    title=t.get("title", "")[:500],
                    description=t.get("description", ""),
                    task_type=t.get("task_type", "meta_rewrite"),
                    priority=int(t.get("priority", 50)),
                    estimated_impact=t.get("estimated_impact"),
                    estimated_effort=t.get("estimated_effort"),
                    target_query=t.get("target_query"),
                    target_cluster=t.get("target_cluster"),
                    target_page_url=t.get("target_page_url"),
                    generated_content=t.get("generated_content"),
                    status="backlog",
                )
                db.add(new_task)
                created += 1
            except Exception as exc:
                logger.warning(f"Failed to save task: {exc}")

        await db.commit()

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        run_record.status = "completed"
        run_record.completed_at = datetime.now(timezone.utc)
        run_record.duration_ms = elapsed_ms
        run_record.model_used = usage["model"]
        run_record.input_tokens = usage["input_tokens"]
        run_record.output_tokens = usage["output_tokens"]
        run_record.cost_usd = usage["cost_usd"]
        run_record.output_summary = {
            "tasks_created": created,
            "summary": summary[:500],
        }
        await db.flush()
        await db.commit()

        logger.info(
            f"TaskGenerator done: {created} tasks, ${usage['cost_usd']:.4f}, {elapsed_ms}ms"
        )

        return {
            "status": "completed",
            "tasks_created": created,
            "summary": summary,
            "cost_usd": usage["cost_usd"],
        }
