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
                "description": "Exactly 5 FAQ items — real tourist questions with concise answers",
            },
        },
        "required": ["items"],
    },
}

SCHEMA_TOOL = {
    "name": "generate_schema",
    "description": "Generate ready-to-paste JSON-LD Schema.org markup for a page.",
    "input_schema": {
        "type": "object",
        "properties": {
            "schema_jsonld": {
                "type": "string",
                "description": "Complete JSON-LD block as a string, wrapped in <script type=\"application/ld+json\">...</script>. Valid JSON. Use only real data — skip fields you can't verify.",
            },
            "schema_type": {
                "type": "string",
                "description": "Primary @type used (TouristTrip, TravelAgency, BlogPosting, FAQPage)",
            },
            "install_notes": {
                "type": "string",
                "description": "1-2 sentences in Russian: куда вставить на странице",
            },
        },
        "required": ["schema_jsonld", "schema_type", "install_notes"],
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

        # Try to map queries to pages (best-effort via title keywords)
        pages_by_title = {(p.title or "").lower(): p for p in data["pages"] if p.title}

        # Opportunity 1: Queries at position 5-15 with many impressions — meta_rewrite
        for q in queries[:20]:
            pos = float(q.get("avg_position") or 0)
            imp = int(q.get("impressions") or 0)
            if 5 <= pos <= 15 and imp >= 3:
                # Find a likely target page by matching query keywords to title
                query_lower = q["query_text"].lower()
                target_page = None
                for title_lower, p in pages_by_title.items():
                    words = set(query_lower.split())
                    if any(w in title_lower for w in words if len(w) > 3):
                        target_page = p
                        break

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
                    "page_url": target_page.url if target_page else None,
                    "current_title": target_page.title if target_page else None,
                    "current_description": target_page.meta_description if target_page else None,
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
                path = page.path or ""
                # Better page type detection
                if "/tours/" in path or "/excursion" in path or "/expeditions/" in path:
                    page_type = "tour"
                elif "/blog/" in path or "/articles/" in path or "/stati/" in path or "/stories/" in path:
                    page_type = "article"
                elif path in ("/", ""):
                    page_type = "landing"
                else:
                    # Skip utility pages (contacts, about, privacy) — generic WebPage schema doesn't help
                    continue

                opportunities.append({
                    "type": "schema_add",
                    "priority": 40,
                    "impact": "medium",
                    "effort": "S",
                    "page_url": page.url,
                    "page_title": page.title,
                    "page_description": page.meta_description,
                    "page_h1": page.h1,
                    "page_type": page_type,
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
        self, opp: dict, site: Site
    ) -> tuple[dict, dict]:
        """Small LLM call for meta_rewrite: query + site → title/description/H1."""
        site_context = self._describe_site(site)
        current_year = date.today().year
        expected_ctr = EXPECTED_CTR.get(int(opp["position"]), 0.02) * 100
        actual_ctr = (opp["clicks"] / max(opp["impressions"], 1) * 100) if opp["impressions"] else 0

        system = f"""Ты SEO-копирайтер для российского туристического рынка с опытом 10+ лет.
Пишешь meta-теги ПОД ЯНДЕКС (не Google — правила отличаются).

О САЙТЕ: {site_context}

ТРЕБОВАНИЯ К TITLE (50-65 символов):
1. Ключевой запрос в первых 30 символах (Яндекс учитывает левое вхождение)
2. Город/регион ОБЯЗАТЕЛЬНО (Сочи / Абхазия / Красная Поляна / Адлер)
3. Год {current_year} для коммерческих запросов (туры, экскурсии, цены)
4. Цена "от X₽" если есть конкретная цифра
5. Один триггер: "с гидом" / "без очередей" / "с трансфером" / "ежедневно"
6. Разделитель — "|" или "—", НЕ дефис
7. ЗАПРЕТ: CAPS, "!", эмодзи, слова "лучший/уникальный/незабываемый" без конкретики

ТРЕБОВАНИЯ К DESCRIPTION (140-160 символов):
1. Первые 100 символов — главный оффер (обрезается на мобильных)
2. Структура: [что] + [где] + [цена/длительность] + [УТП] + [CTA]
3. УТП конкретное: "трансфер от отеля", "группа до 8 чел", "работаем с 2014"
4. CTA в императиве: "Забронируйте", "Выберите дату", "Посмотрите программу"
5. Цифры: "3 водопада", "8 часов", "от 2500₽"

ТРЕБОВАНИЯ К H1:
1. НЕ дублирует title дословно (Яндекс считает переспамом)
2. На 10-20 символов длиннее title, описательнее
3. Один раз основной ключ + LSI-синоним (экскурсия/тур/поездка)
4. Без года и цены

ПРАВИЛО АНТИ-ГАЛЛЮЦИНАЦИИ: пиши только услуги из описания сайта."""

        user_msg = f"""Запрос: "{opp['query']}"
Кластер: {opp.get('cluster') or '—'}
Позиция в Яндексе: {opp['position']:.1f}
Показов за 14 дней: {opp['impressions']} | Кликов: {opp['clicks']} | CTR: {actual_ctr:.1f}% (ожидается на поз.{int(opp['position'])}: {expected_ctr:.0f}%)

Текущий title: "{opp.get('current_title') or '(страница не найдена)'}"
Текущий description: "{opp.get('current_description') or '(пусто)'}"

ЗАДАЧА: Перепиши так, чтобы поднять позицию в топ-5 и CTR минимум до ожидаемого.
Вызови generate_meta."""
        try:
            raw, usage = call_with_tool(
                model_tier="cheap", system=system, user_message=user_msg,
                tool=META_TOOL, max_tokens=800,
            )
            return raw, usage
        except Exception as e:
            logger.warning(f"Meta gen failed for '{opp['query']}': {e}")
            return {}, {"cost_usd": 0, "input_tokens": 0, "output_tokens": 0, "model": "claude-haiku-4-5-20251001"}

    async def _generate_schema_content(self, opp: dict, site: Site) -> tuple[dict, dict]:
        """Small LLM call for schema_add: page info → ready JSON-LD."""
        site_context = self._describe_site(site)
        page_type = opp.get("page_type", "landing")

        # Type-specific guidance
        if page_type == "tour":
            type_hint = (
                "Используй @type: TouristTrip с offers (Offer с price, priceCurrency='RUB'). "
                "Добавь itinerary (ItemList с этапами программы тура), "
                "touristType, provider (ссылка на TravelAgency сайта). "
                "aggregateRating добавь ТОЛЬКО если есть реальные отзывы на странице — иначе ПРОПУСТИ."
            )
        elif page_type == "landing":
            type_hint = (
                "Используй @graph с 2 объектами: TravelAgency (name, description, url, "
                "address с addressLocality='Сочи', areaServed, foundingDate, priceRange) "
                "и WebSite (url, name, publisher). Телефон/sameAs — ТОЛЬКО если есть реальные."
            )
        elif page_type == "article":
            type_hint = (
                "Используй @type: BlogPosting с headline, description, image, "
                "datePublished (из last_crawled_at или сегодня), author (Organization - название сайта), "
                "publisher (Organization с logo), mainEntityOfPage, inLanguage='ru-RU'."
            )
        else:
            type_hint = "Используй @type: WebPage с name, description, url."

        system = f"""Ты эксперт по Schema.org для Яндекса. Пишешь ВАЛИДНЫЙ JSON-LD.

О САЙТЕ: {site_context}

ПРАВИЛА:
- Все URL — абсолютные (https://...)
- Цены в RUB
- Язык 'ru-RU'
- НЕ придумывай данные: если нет номера телефона / реальных отзывов / точного адреса — ПРОПУСТИ эти поля
- НЕ используй плейсхолдеры типа XXX, [name], ...
- Оборачивай в <script type="application/ld+json">...</script>

{type_hint}"""

        user_msg = f"""URL: {opp['page_url']}
Title страницы: {opp.get('page_title') or '—'}
H1: {opp.get('page_h1') or '—'}
Описание: {opp.get('page_description') or '—'}

Сгенерируй JSON-LD для этой страницы. Вызови generate_schema."""

        try:
            raw, usage = call_with_tool(
                model_tier="cheap", system=system, user_message=user_msg,
                tool=SCHEMA_TOOL, max_tokens=2000,
            )
            return raw, usage
        except Exception as e:
            logger.warning(f"Schema gen failed for {opp['page_url']}: {e}")
            return {}, {"cost_usd": 0, "input_tokens": 0, "output_tokens": 0, "model": "claude-haiku-4-5-20251001"}

    async def _generate_faq_content(self, opp: dict, site: Site) -> tuple[dict, dict]:
        """Small LLM call for FAQ: page topic → FAQ items + JSON-LD."""
        site_context = self._describe_site(site)
        system = f"""Ты контент-стратег по туризму. Пишешь FAQ которые попадают в быстрые ответы Яндекса (rich snippets, турбо-выдача).

О САЙТЕ: {site_context}

СТРУКТУРА FAQ:
Ровно 5 вопросов. Вопросы — РЕАЛЬНЫЕ формулировки как пишут туристы в поиске:
- "Сколько стоит {{что-то}}?"
- "Нужен ли загранпаспорт в {{место}}?"
- "Во сколько выезжает группа и откуда забирают?"
- "Что включено в стоимость?"
- "Можно ли с детьми / пожилыми / беременным?"

ОБЯЗАТЕЛЬНЫЕ ТЕМЫ (минимум 4 из 6):
- Цена (что входит/не входит)
- Логистика (откуда забирают, длительность, возврат)
- Документы (паспорт, для Абхазии — внутренний или загран)
- Для кого подходит (дети, пожилые, физподготовка)
- Что взять с собой
- Отмена и возврат денег

ФОРМАТ ОТВЕТОВ (30-60 слов каждый):
1. ПЕРВОЕ предложение — прямой ответ (Да / Нет / Стоимость X₽ / Длится N часов)
2. Конкретные цифры, не "обычно" и "в большинстве случаев"
3. Без воды: не пиши "мы стараемся", "как правило"

ПРАВИЛО: основано на услугах из описания сайта, не выдумывай."""
        user_msg = f"""Страница: {opp['page_url']}
Тема: {opp.get('page_topic', opp.get('page_title', ''))}

Создай 5 вопросов-ответов. Вызови generate_faq."""
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
                    content, usage = await self._generate_meta_content(opp, site)
                    if content:
                        task_data["generated_content"] = content
                    total_cost += usage.get("cost_usd", 0)
                    total_input += usage.get("input_tokens", 0)
                    total_output += usage.get("output_tokens", 0)

                elif opp["type"] == "faq_add":
                    content, usage = await self._generate_faq_content(opp, site)
                    if content and "items" in content:
                        task_data["generated_content"] = {"faq_items": content["items"]}
                    total_cost += usage.get("cost_usd", 0)
                    total_input += usage.get("input_tokens", 0)
                    total_output += usage.get("output_tokens", 0)

                elif opp["type"] == "schema_add":
                    content, usage = await self._generate_schema_content(opp, site)
                    if content and content.get("schema_jsonld"):
                        task_data["generated_content"] = {
                            "schema_type": content.get("schema_type"),
                            "schema_jsonld": content.get("schema_jsonld"),
                            "install_notes": content.get("install_notes"),
                        }
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

    def _describe_site(self, site: Site) -> str:
        """Build a concise site description for LLM context."""
        domain = site.domain
        display = site.display_name or domain

        # Hardcoded descriptions for known sites (manually curated context)
        known = {
            "xn----jtbbjdhsdbbg3ce9iub.xn--p1ai": (
                "Южный Континент — экскурсионное бюро в Сочи с 2014 года. "
                "Проводит экскурсии и туры: Красная Поляна, 33 водопада, Абхазия (Золотое кольцо, Рица, Гагра, Новый Афон), "
                "морские прогулки, джиппинг, Ведьмино ущелье, VIP-туры. Цены 700-5000₽. "
                "Группы от 6 до 50 человек. Бесплатный трансфер из отелей Сочи."
            ),
            "grandtourspirit.ru": (
                "Grand Tour Spirit (GTS) — премиальный клуб активного отдыха в Сочи. "
                "Флагман — багги-экспедиции по Абхазии (маршруты 1-5 дней): урочище Гизла, Кушонский перевал, "
                "Ауадхара (альпийские луга), Сухум-Кодор, Кисловодск-Архыз, Крым. "
                "Также яхты, вертолёты, консьерж-сервис, VIP-трансферы, мероприятия. "
                "Цены экспедиций 24 900 - 359 900₽. Офис в Олимпийском парке."
            ),
        }

        if domain in known:
            return known[domain]

        # Fallback: generic
        return f"{display} ({domain}) — туристический сайт в России."

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
            pt = opp.get("page_type", "landing")
            schema_type = {
                "tour": "TouristTrip",
                "landing": "TravelAgency",
                "article": "BlogPosting",
            }.get(pt, "WebPage")
            base["title"] = f"Добавить Schema.org ({schema_type}) на {opp['page_url'].split('/')[-1] or '/'}"
            base["description"] = (
                f"На странице нет структурированной разметки. Добавьте JSON-LD с типом {schema_type} "
                f"— Яндекс сможет показывать цены/рейтинг в выдаче (rich snippet) = +30% CTR. "
                f"Готовый код прилагается."
            )
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
