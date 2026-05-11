# 2026-05-11 — Deep Extract Analyzer: апгрейд + дорожная карта к автономному помощнику

Сессионный лог: что сделано прямо сейчас, что нужно дальше для финальной цели «утром зашёл — помощник уже подумал и что-то починил сам», какие SEO-агенты и скилы участвуют на каждом шаге.

---

## Часть 1 — Что сделано в этой сессии

### Контекст
Пользователь запросил, чтобы AI-анализатор Deep Extract выдавал не generic SEO-чек-лист, а советы уровня senior эксперта с источниками и приоритизацией. Три SEO-агента (`seo-content`, `seo-technical`, `seo-geo`) поставили предыдущей версии анализатора 4-5.5/10.

### Сделанные правки (все в `backend/app/api/v1/studio.py`)

1. **`_DEEP_ANALYZE_SYSTEM` — переписан**
   - Добавлено **правило P0**: «JS-ошибки ≥3 → пункт №1 всего отчёта»
   - Добавлено **правило P1**: «бизнес-контекст важнее косметики; если бизнес работает из 3+ городов — НЕ предлагать убирать гео из H1»
   - Добавлен **запрет на generic-советы** («сделать контрастнее без замера», «LCP без запаса при ≤2200 мс», путаница меню с CTA)
   - Добавлен **INP** (Interaction to Next Paint ≤200 мс) в чек-лист #1
   - Добавлено: «CLS=0 в данных НЕ значит CLS=0 на клиенте при hydration errors»
   - **Новый чек-лист #5 — «Мульти-гео туризм»**: `areaServed`, programmatic `/iz-{city}/`, `sameAs` к VK/Дзен/Я.Бизнес/YouTube, latent-geo H2-блоки, llms.txt
   - **Жёсткое правило приоритизации**: P0 hydration → P1 Schema → P2 trust → P3 content → P4 H1/title → P5 CRO-косметика
   - **Новая секция в формате ответа: «🌐 Проверить вне страницы»** (Я.Бизнес, Я.Карты, llms.txt, sameAs, brand-mention)

2. **`_format_business_context(site)` — новый helper**
   - Достаёт из `Site.target_config`: `primary_product`, `services`, `geo_primary`, `geo_secondary`, `strategic_focus`, `narrative_ru`
   - Если найдено ≥3 региона — добавляет явное предупреждение: «бизнес обслуживает 3+ городов, применяй чек-лист #5»

3. **`_format_extract_for_llm` — два изменения**
   - JS-ошибки **в начале блока**, не в конце; с маркером 🚨 P0 если ≥3
   - Передаются первые 10 **alt-текстов целиком**, не только `with_alt=N`, с просьбой оценить уникальность

4. **`analyze_deep_extract` endpoint — добавлен Site lookup**
   - Подгружает `Site` по `site_id` и инжектит `_format_business_context(site)` перед `_format_extract_for_llm(row)` в user_message

### Деплой
- Образ backend пересобран и пересоздан
- Кеш `ai_summary_md` очищен (UPDATE 18 строк)
- Smoke-тест на свежем снимке `c08f22c0` главной grandtourspirit: 16.3 сек, $0.006, 6647 символов, **ответ без обрезки**

### Ожидаемое качество
- Старая версия: **4.5/10** (по трём SEO-агентам)
- Новая версия: **~8.5/10** ожидается. Подтверждено руками — анализатор:
  - не предложил убрать гео из H1 (вместо этого — areaServed + programmatic)
  - правильно отметил `LCP=632 мс` как «уже хорошо», без алармизма
  - увидел `strategic_focus` из `target_config` и заявил «H1 распыляет фокус — флагман это багги-Абхазия»
  - проверил alt-уникальность по реальным текстам
  - вынес off-page блок (Я.Бизнес, Я.Карты, llms.txt)

---

## Часть 2 — Дорога к «автономному помощнику»

Конечная цель пользователя (его слова): «утром захожу — помощник уже неделю думал, нашёл проблемы, что-то починил сам, остальное предлагает с готовыми текстами». Текущая система покрывает **~60-65%** этого видения. Не хватает 4 блоков:

### Блок A — Lateral Query Expansion (расширение запросов)
**Что:** автоматический поиск косвенно-релевантных запросов («экскурсии Сочи» для бизнеса «багги Абхазия»).
**Где:** новый модуль `backend/app/core_audit/lateral/`.
**Как:** раз в неделю Celery beat → LLM получает (business_truth + competitor SERP + Wordstat related) → возвращает 30 свежих идей запросов с confidence-скором и пометкой «прямой/связанный/инфо/слабый».
**Время:** 3-4 дня
**Стоимость:** ~$0.05/неделю/сайт

### Блок B — Reverse-Rank Diagnoser («почему не в топе»)
**Что:** на каждый blind-spot из `business_truth` — берём топ-5 SERP, сравниваем с нашей страницей через LLM, решаем CREATE/STRENGTHEN с конкретным «что менять».
**Где:** новый модуль `backend/app/core_audit/diagnose/`.
**Как:** использует существующий Yandex Search API collector + сравнительный LLM-промпт.
**Время:** 3-4 дня
**Стоимость:** ~$0.02/blind-spot, ~$0.10-0.20/неделю/сайт

### Блок C — Decision Tree → Готовая правка с текстом
**Что:** `decision_tree.py` сейчас отдаёт enum (STRENGTHEN/CREATE). Нужен мост к `task_generator.py` — чтобы для каждого STRENGTHEN был **готовый текст** правки (новый title, новый H1, готовый абзац).
**Где:** мост между `core_audit/decision_tree.py` и `agents/task_generator.py`.
**Время:** 2 дня
**Стоимость:** включена в B

### Блок D — Утренний брифинг
**Что:** каждый понедельник 9:00 → один экран (UI + email/Telegram) со сводкой: что сделал сам, что ждёт ОК, что срочно починить, что выросло/упало.
**Где:** новый endpoint `/studio/morning-brief`, опциональный Telegram/email отправщик.
**Время:** 2 дня
**Стоимость:** ничего нового, использует существующие данные

**Итого:** ~2 недели работы до полной автономности.

---

## Часть 3 — Какие SEO-агенты и скилы нужны на каждом шаге

В Claude Code доступны: `seo-content`, `seo-technical`, `seo-geo`, `seo-schema`, `seo-sitemap`, `seo-performance`, `seo-visual`.

### Сделано в этой сессии
| Задача | Кого использовали |
|---|---|
| Аудит качества анализатора | `seo-content`, `seo-technical`, `seo-geo` параллельно |
| Сбор checklists для промпта | те же 3 + `seo-schema` |
| Прямые правки кода | без агентов, в основном потоке |

### Что нужно на блок A (Lateral Query Expansion)
| Подзадача | Кого использовать |
|---|---|
| Промпт для LLM «придумай похожие запросы» | `seo-geo` (latent intent, AI search) + `general-purpose` для исследования паттернов |
| Тестирование на 5+ ниш | `seo-content` для оценки релевантности результатов |
| Интеграция с Wordstat и demand_map | `Plan` агент для дизайна архитектуры |

### Что нужно на блок B (Reverse-Rank Diagnoser)
| Подзадача | Кого использовать |
|---|---|
| Промпт «сравни топ-5 с моей страницей» | `seo-content` + `seo-technical` |
| Логика STRENGTHEN vs CREATE | `seo-content` (content gap analysis) |
| Учёт мульти-гео и programmatic | `seo-geo` (важно — иначе диагноз будет советовать «создать новую страницу» когда нужно patch'нуть существующую) |

### Что нужно на блок C (Decision Tree → готовый текст)
| Подзадача | Кого использовать |
|---|---|
| Генератор title/H1/meta | `seo-content` |
| Генератор Schema.org JSON-LD | `seo-schema` (специально для этого создан) |
| Генератор готовых FAQ-блоков | `seo-content` + `seo-geo` (для AI-citability) |

### Что нужно на блок D (Утренний брифинг)
| Подзадача | Кого использовать |
|---|---|
| Шаблон отчёта (UI/email/Telegram) | без SEO-агентов, фронт+бэк |
| Приоритизация что показать | `seo-content` (для оценки «важно/не важно») |
| Тест читабельности для не-технического владельца | `seo-content` |

---

## Часть 4 — Приоритеты и план запуска

### Очередность блоков (по ROI)
1. **A — Lateral Query Expansion** — самый высокий ROI. Без него ассистент не «думает», а только реагирует.
2. **B — Reverse-Rank Diagnoser** — закрывает «почему мы не в топе» как явный продукт.
3. **C — Готовые тексты правок** — превращает рекомендации в копипаст для владельца.
4. **D — Утренний брифинг** — связывает всё в один UX и делает использование привычкой.

### Открытые вопросы для следующей сессии
1. Какая CMS у `grandtourspirit.ru` и `xn----jtbbjdhsdbbg3ce9iub.xn--p1ai`? (от этого зависит формат «готовых правок» в блоке C)
2. Хочет ли пользователь автоприменение «безопасных» правок (title/meta) или всё через его ОК?
3. Куда слать утренний брифинг — UI / email / Telegram / все три?
4. Какой день/час хочет получать сводку?

### Подготовка к следующей сессии
- Прогнать blok A для grandtourspirit на тестовых данных — посмотреть, какие 30 запросов выпадают
- Заранее уточнить у пользователя ответы на 4 вопроса выше
- Подготовить SQL для миграции (новая таблица `lateral_queries` для блока A)

---

## Известные ограничения текущей системы

- Vercel proxy free-tier: hard-cap 60s на одну функцию → используем `gpt-5.4-mini` + `max_tokens=3000`. Если когда-то понадобится `smart` (gpt-5.4) с длинным ответом — нужен платный Vercel plan или другой прокси (Cloudflare Workers).
- Anthropic balance exhausted → fallback на OpenAI работает прозрачно через `llm_client.call_plain`.
- Deep Extract скриншоты иногда падают на сайтах с React-ошибками (CDP fallback решает).
- Анализатор работает только на одной странице — нет ещё кросс-страничной аналитики (это придёт с блоком B).

---

## Файлы, изменённые в этой сессии

- `backend/app/api/v1/studio.py` — главные изменения (промпт + business_context + endpoint)
- `backend/app/collectors/deep_crawler.py` — CDP fallback для скриншотов
- `frontend/app/studio/pages/[page_id]/page.tsx` — редирект на /pages при 404 после смены сайта
- (без изменений) `backend/app/models/site.py`, `backend/app/collectors/deep_extract_tasks.py`, `backend/app/workers/celery_app.py`

---

**Подпись:** Claude Opus 4.7 (1M context), сессия 2026-05-11

---

## Дополнение 2026-05-11 — Блок A собран в коде

Реализован в той же сессии. Файлы:

- `backend/alembic/versions/a9f0c3b1d2e4_add_lateral_queries.py` — миграция
- `backend/app/models/lateral_query.py` — ORM
- `backend/app/core_audit/lateral/{__init__,dto,context,llm_expansion,persistence,tasks}.py`
- `backend/app/api/v1/lateral.py` — 3 endpoints (GET list, PATCH status, POST manual expand)
- `backend/app/workers/celery_app.py` — beat `lateral-expand-weekly` (пн 03:45 UTC) + autodiscover
- `backend/tests/core_audit/test_lateral_persistence.py` — 5 тест-кейсов, главный — invariant защиты owner-статуса

Параметры по согласованию с пользователем:
- 15-20 идей за прогон (просим 18, держим до 20)
- UPSERT с защитой owner-статуса: rows в accepted/rejected/promoted **никогда** не перезаписываются LLM
- Все активные сайты (через `onboarded_site_ids`)
- Контекст: business_truth + competitor brands + observed queries (top-25 non-branded, не spam/disputed)

Не сделано в этой сессии:
- Прогон в Docker (Docker daemon был выключен на dev-машине)
- UI на /studio/ — намеренно отложен до оценки качества LLM-выхлопа
- Мост «accepted lateral → demand_map» — это блок C, отдельно

