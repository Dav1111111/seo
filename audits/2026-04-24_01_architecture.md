# Архитектурный аудит Yandex Growth Tower
**Дата:** 2026-04-24
**Аудитор:** Claude (Opus 4.7, 1M)
**Стадия проекта:** MVP в проде, hardening sprint только что закрыт; на горизонте — multi-tenant спринт.

---

## TL;DR (5 пунктов)

1. **`tenant_id` существует только на `Site`** — все остальные таблицы (`issues`, `daily_metrics`, `analysis_events`, `search_queries`, `page_reviews`, `target_clusters`, `query_intents`, `page_fingerprints`, `outcome_snapshots`, `tasks`, `pages`, `agent_runs`, `alerts`, `report` …) держат только `site_id`. Включить RLS поверх такой схемы — невозможно: придётся писать `WHERE site_id IN (SELECT id FROM sites WHERE tenant_id = current_setting(...))` в каждой политике, что отдаёт «дырявость» и плохо индексируется. **Это блокер для multi-tenant спринта.**

2. **Yandex OAuth токен лежит в `sites.yandex_oauth_token` в открытом виде** (`backend/app/models/site.py:19` — комментарий «encrypted at rest» неправда). `ENCRYPTION_KEY` объявлен в `Settings`, но нигде не используется (`grep encrypt|decrypt|fernet` находит только сам комментарий). При компрометации БД утекают токены чужих кабинетов Яндекса. **CRITICAL** перед мульти-тенантом.

3. **Дрифт между двумя реализациями async-сессии в Celery.** `backend/app/workers/db_session.py` ввёл `task_session()` с обязательным `await engine.dispose()` (предупреждение в docstring: «utterer connection leak»). Но `backend/app/agents/tasks.py:27-32` и `backend/app/collectors/tasks.py:31-36` всё ещё используют локальный `_make_session()` **без dispose** — это утечка connection-pool на каждой задаче. При 50 задачах × `pool_size=2` × `worker_max_tasks_per_child=50` потенциально 100+ висящих коннектов на воркер до перезапуска.

4. **`X-Admin-Key` — единственная защита для всего write-API** (`/admin/*`, full-pipeline, BusinessTruth, onboarding chat, competitor discovery). Ключ хардкоден в `docker-compose.yml:93` (`admin_dev_secret_2026`). Прокси `frontend/app/admin-proxy/[...path]/route.ts` инжектит его без проверки пользователя — в multi-tenant модели один владелец сможет триггерить пайплайн на чужой `site_id`, потому что в API нет owner check'а на уровне `site_id ↔ user`.

5. **Каждая Celery task дублирует одну и ту же мостовую логику sync↔async** (`asyncio.new_event_loop` + локальный `_run`/`_make_session`). 14 файлов копипасты. Архитектурно — нужен один общий `task_runner` хелпер, иначе исправление любого паттерна (logging, error reporting, run_id propagation) надо делать × 14.

---

## Карта системы

```
┌────────────────────────────────────────────────────────────────────┐
│                            Browser                                  │
│   (Next.js 16 + React 19 + SWR @ /priorities, /competitors, ...)    │
└─────────┬────────────────────────────────────┬─────────────────────┘
          │ /api/v1/*  (public read+write)     │ /admin-proxy/*
          │                                    │ (server-side route, инжектит X-Admin-Key)
          ▼                                    ▼
   ┌───────────────────┐            ┌─────────────────────────┐
   │   nginx:80        │──────────► │   Next.js node server    │ (frontend service)
   │                   │            └────────────┬────────────┘
   │                   │                         │ X-Admin-Key
   │                   │                         ▼
   │                   │            ┌─────────────────────────┐
   │                   ├──────────► │  FastAPI backend:8000    │
   └───────────────────┘            │  app.api.v1.router       │
                                    │   ├ sites, dashboard      │
                                    │   ├ collectors, intent    │
                                    │   ├ priority, report      │
                                    │   ├ activity, business_truth │
                                    │   └ /admin/* (header-gated) │
                                    └────┬───────────┬──────────┘
                                         │           │
                            ┌────────────┘           └──────────────┐
                            ▼                                       ▼
                     ┌─────────────┐                       ┌────────────────┐
                     │ PostgreSQL  │ ◄──── async-pg ──────│ Celery worker   │
                     │  (port 5432)│                       │  (-c 2)          │
                     │  + JSONB    │                       │  task_session() │
                     └─────────────┘                       │  ИЛИ _make_session()
                            ▲                              │  (drift)         │
                            │                              └────────┬────────┘
                            │                                       │
                            │       Redis broker:6379               │
                            │ ◄──────────────────────────────────── │
                                                                    │
                                              ┌─────────────────────┴───┐
                                              │  External APIs           │
                                              │  • Yandex Webmaster API  │
                                              │  • Yandex Metrica API    │
                                              │  • Yandex Cloud Search   │
                                              │  • Anthropic (через CF   │
                                              │    Worker proxy)         │
                                              │  • Telegram bot          │
                                              └──────────────────────────┘

                                 ┌────────────┐
                                 │ celery-beat│ → запускает crontab @ UTC
                                 └────────────┘   (см. backend/app/workers/celery_app.py:48-138)
```

**Ключевые контракты event-flow (run_id):**

```
POST /api/v1/admin/sites/{id}/pipeline/full
  ├─ создаёт run_id = uuid4
  ├─ log_event('pipeline','started', extra={'queued':[...]})
  ├─ celery_app.send_task('crawl_site',         kwargs={'run_id':run_id})
  ├─ celery_app.send_task('collect_site_webmaster', kwargs={'run_id':run_id})
  └─ celery_app.send_task('demand_map_build_site',  kwargs={'run_id':run_id})

каждая task:
  log_event('<stage>','started')
  ... работа ...
  emit_terminal('<stage>','done'|'failed'|'skipped', run_id=...)
        │
        └─► внутри: если все queued стейджи терминальные →
              log_event('pipeline','done'|'failed'|'skipped')
```

---

## Находки

### CRITICAL

#### A-001 — `tenant_id` есть только у `Site`; multi-tenant RLS невозможен без миграции
- **Где:** `backend/app/models/site.py:13`, `backend/app/models/issue.py:13`, `backend/app/models/analysis_event.py:25-27`, `backend/app/models/daily_metric.py`, `backend/app/intent/models.py:20-25`, `backend/app/core_audit/review/models.py`, `backend/app/core_audit/demand_map/models.py`, `backend/app/core_audit/report/models.py`, `backend/app/fingerprint/models.py`, `backend/app/models/page.py`, `backend/app/models/search_query.py`, `backend/app/models/agent_run.py`, `backend/app/models/alert.py`, `backend/app/models/outcome_snapshot.py`, `backend/app/models/task.py`, `backend/app/models/snapshot.py`.
- **Что:** Из 18+ доменных таблиц `tenant_id` несёт только `sites`. Все остальные ссылаются на `site_id`. Запрос «выдай activity для этого тенанта» невозможен без JOIN; политики RLS придётся писать через `EXISTS(SELECT 1 FROM sites WHERE sites.id = THIS.site_id AND sites.tenant_id = current_setting('app.tenant_id'))` — это (а) бьёт по производительности (subquery на каждый row check), (б) увеличивает поверхность ошибки при добавлении новых таблиц.
- **Почему критично:** Multi-tenant спринт обещан следующим. Без `tenant_id` колонки нельзя ни RLS включить, ни сделать приличный compound index `(tenant_id, site_id, …)`. Дополнительно — текущий API `GET /sites/{site_id}/...` не проверяет, чей это site_id; сейчас один владелец → не страшно, но добавление второго пользователя моментально откроет горизонтальную эскалацию.
- **Как чинить:**
  1. Alembic-миграция: на каждую таблицу с `site_id` добавить `tenant_id UUID NOT NULL` + backfill SQL `UPDATE t SET tenant_id = (SELECT tenant_id FROM sites WHERE sites.id = t.site_id)` + `CREATE INDEX ... (tenant_id, …)` параллельно (CONCURRENTLY).
  2. Поднять `tenant_id` в SQLAlchemy моделях с FK на `tenants.id`.
  3. На API ввести `Depends(current_tenant)` который читает JWT/session и возвращает tenant_id; все queries фильтровать `WHERE tenant_id = current_tenant`.
  4. Добавить middleware, прокидывающий `SET LOCAL app.tenant_id = '...'` в начало каждого request — основа для будущей RLS.

#### A-002 — Yandex OAuth токены лежат в БД в открытом виде
- **Где:** `backend/app/models/site.py:19` (`yandex_oauth_token: Text  # encrypted at rest` — комментарий лжёт). `ENCRYPTION_KEY: str = ""` объявлен (`backend/app/config.py:36`), но `grep -rn "encrypt|decrypt|fernet|cryptography"` по проекту не находит ни одного использования. Токен записывается напрямую в коллекторах (`backend/app/collectors/tasks.py:137,211,341`).
- **Что:** OAuth-токены Яндекса (доступ к Webmaster + Metrica + Cloud Search) хранятся as-is. На проде на Jino при компрометации `pgdata` volume утекают токены всех тенантов.
- **Почему критично:** Multi-tenant = чужие токены. PII/security риск + нарушение Яндекс-соглашений на хранение OAuth-секретов. Для одного владельца на двух доменах — терпимо, для платного SaaS — нет.
- **Как чинить:** EnvelopeEncryption — Fernet с ключом из ENV, прозрачный SQLAlchemy `TypeDecorator` поверх `Text`. Альтернативно — вынести в отдельный secret store (HashiCorp Vault / KMS), хранить в БД только идентификатор. Параллельно зашифровать существующие записи one-shot скриптом.

#### A-003 — `X-Admin-Key` как единственный auth для всех write/admin endpoints
- **Где:** `backend/app/api/v1/admin_ops.py:30-35`, `backend/app/api/v1/admin_demand_map.py:47-53`, `backend/app/api/v1/business_truth.py:22-27` (3 одинаковых `_require_admin`). Прокси: `frontend/app/admin-proxy/[...path]/route.ts:4,21`. Default-ключ в репо: `docker-compose.yml:93` (`admin_dev_secret_2026`).
- **Что:** Любой, кто знает один shared-secret, может вызвать пайплайн, переписать `target_config`, отметить outcome, перезапустить onboarding. Прокси прозрачно инжектит ключ для всех запросов с фронтенда — браузер не знает ключа, но и backend не знает, какой пользователь стоит за запросом.
- **Почему критично:** В мульти-тенант всё это становится horizontal escalation: один владелец вызывает `POST /admin/sites/{чужой_site_id}/pipeline/full` — успешно. Нет ни owner-check, ни IP allowlist'a, ни rate-limit.
- **Как чинить:**
  1. Заменить shared-secret на JWT (или Session) с tenant_id и user_id.
  2. На каждом `/admin/sites/{site_id}/...` добавить `Depends(require_site_owner(site_id))` — проверка что `site.tenant_id == current_user.tenant_id`.
  3. Прокси `/admin-proxy` либо удалить, либо превратить в чистый pass-through cookie/JWT (без shared key).
  4. До миграции: ротировать ADMIN_API_KEY, убрать дефолт из docker-compose, поднять только через секреты.

---

### HIGH

#### A-004 — Connection-pool leak в Celery: 14 файлов с `asyncio.new_event_loop` + не диспозят engine
- **Где:** `backend/app/agents/tasks.py:17-32`, `backend/app/collectors/tasks.py:21-36`. Контр-пример (правильный): `backend/app/workers/db_session.py:28-41` (`task_session()` с `await eng.dispose()`).
- **Что:** Половина task-модулей (новый `core_audit/*`, `fingerprint`, `intent`) уже мигрировала на `task_session`/`task_session_factory`. Старые два (агенты + коллекторы) — нет; они каждый раз создают `create_async_engine(...)` и не вызывают `dispose()`. Engine с unused connections остаётся в памяти Python до следующего GC + до `worker_max_tasks_per_child=50`.
- **Почему high (не critical):** Сейчас дефолтный postgres `max_connections=100` достаточно широкий, и проблема маскируется автоматическим перезапуском процесса каждые 50 задач. Но при росте до 50+ сайтов и nightly-pipeline эта утечка станет первой причиной «сегодня воркер просто перестал отвечать».
- **Как чинить:** Удалить `_make_session` в `agents/tasks.py` и `collectors/tasks.py`, прогнать всё через `task_session()`. Это 1 PR с linter-проверкой `grep "_make_session"` = 0 на CI.

#### A-005 — Дублирование `_run` (asyncio bridge) в каждом task-модуле
- **Где:** Идентичная функция `_run(coro)` объявлена в 14 файлах: `backend/app/agents/tasks.py:17-24`, `backend/app/collectors/tasks.py:21-28`, `backend/app/intent/tasks.py:20-26`, `backend/app/fingerprint/tasks.py:24+`, `backend/app/core_audit/competitors/tasks.py:43-49`, `backend/app/core_audit/business_truth/tasks.py:21-27`, `backend/app/core_audit/draft_profile/tasks.py:26+`, `backend/app/core_audit/onboarding/tasks.py:44+`, `backend/app/core_audit/demand_map/tasks.py:45+`, `backend/app/core_audit/priority/tasks.py:18+`, `backend/app/core_audit/review/tasks.py:25+`, `backend/app/core_audit/report/tasks.py:21+`, `backend/app/core_audit/outcomes/tasks.py:114+`, `backend/app/core_audit/health/tasks.py:68+`.
- **Что:** Каждый раз — одинаковые 8 строк, без отличий. Хочется разово добавить, скажем, `try/except` с Sentry или structured logging — нужно править 14 файлов.
- **Почему high:** Чисто DX, но сильно бьёт по скорости любых инфра-изменений. Hardening sprint исправил терминальные состояния, но не унифицировал task scaffolding.
- **Как чинить:** Создать `app/workers/runtime.py` с `def run_async(coro): ...` и единым декоратором `@async_celery_task(name=...)` — обёртка вокруг `@celery_app.task`, которая (а) делает `_run`, (б) логирует start/end, (в) пробрасывает `run_id` в лог. Migration — постепенная.

#### A-006 — Бизнес-логика в API-слое; нет service layer для большинства фич
- **Где:** `backend/app/api/v1/dashboard.py:24-133` (агрегации + KPI расчёт + pct_change хелпер прямо в роуте), `backend/app/api/v1/admin_ops.py:125-153` (`_baseline_metrics` SQL функция приватная в роуте), `backend/app/api/v1/admin_demand_map.py:295-410` (валидация + merge + save в одном теле, сравните с чистым `app/core_audit/priority/service.py`), `backend/app/api/v1/admin_demand_map.py:660-857` (целая chat-state-machine — `_chat_state`, `_save_chat_state`, `_seed_initial_state_from_understanding` — в HTTP-слое).
- **Что:** Ручки длиной 100+ строк смешивают: pydantic validation, SQL запросы, доменные расчёты, persist. Сравнить с `priority` (`app/core_audit/priority/service.py` — отдельный сервис, ручка тонкая). В `dashboard.py` — наоборот: всё в одной функции; `pct_change`, `safe_int` — лямбды inline.
- **Почему high:** Тестировать невозможно без HTTP-клиента. Когда придёт мульти-тенант, проверка owner-check'a в каждой такой ручке станет copy-paste; сервис-слой инкапсулировал бы это раз.
- **Как чинить:** Вынести `DashboardService`, `OutcomeService`, `OnboardingChatService` в `app/services/`. Ручки оставить тонкими: validate → call service → return DTO. Постепенно (один сервис = одна PR).

#### A-007 — Хардкод `SITE_ID` в frontend и `tenant_id="default"` в backend
- **Где:** `frontend/lib/api.ts:5` (`SITE_ID = process.env.NEXT_PUBLIC_SITE_ID || "1e11339f-..."`); `docker-compose.yml:91` (тот же UUID); `backend/app/api/v1/sites.py:13-23` (`DEFAULT_TENANT_SLUG = "default"` + `_ensure_default_tenant`). `SiteProvider` (`frontend/lib/site-context.tsx:28-56`) уже умеет переключать сайт через `localStorage`, но fallback до сих пор тянет хардкод.
- **Что:** Single-tenant assumptions глубоко зашиты в дефолты. Любой site без `?siteId=` параметра попадает на захардкоженный UUID Дэвида.
- **Почему high:** Это одновременно (а) source of truth bug — если PROD UUID изменится, фронт сломается без понятного error message, (б) блок для перехода на JWT-auth, где сайт должен браться из текущего юзера.
- **Как чинить:** Удалить `NEXT_PUBLIC_SITE_ID` после auth-MVP. До этого — заставить `apiFetch` валить запросы при пустом `siteId` (сейчас валит, но через странную проверку `path.includes("//")` в `lib/api.ts:11-15`). Backend: убрать `_ensure_default_tenant`, требовать tenant_id из middleware.

#### A-008 — Pipeline orchestration хрупкий: одни stage обозначены `_should_close_pipeline`, другие — через `queued` контракт
- **Где:** `backend/app/core_audit/activity.py:56-77`, `230-294` (`emit_terminal`).
- **Что:** Логика «когда закрывать pipeline:done» имеет два режима:
  - **новый**: `pipeline:started` пишет `extra.queued=['crawl','webmaster','demand_map']`; pipeline закрывается, когда **все** перечисленные стэйджы достигли терминала с тем же `run_id`.
  - **legacy**: ловит первое появление `opportunities:done` или `competitor_*:failed` — никаких `queued`. Это путь для старых событий без run_id.
  Оба режима живут параллельно (см. `if pipeline_status is None` в `emit_terminal` строки 280-283). Плюс есть `reconcile_open_pipelines` (`backend/app/core_audit/activity.py:297-382`), который backfill'ит терминалы для исторических run'ов — вызывается из API при каждом запросе activity (`backend/app/api/v1/activity.py:53,71,104`).
- **Почему high:** Три разных кодовых тропы для «закрыть пайплайн». Любой новый task (например, `business_truth_rebuild_site_task` который не входит в `queued`) отрабатывает корректно только потому что не пытается закрыть pipeline. При добавлении 5-го stage надо помнить про оба режима. Reconcile в SELECT-запросах активности — это **запись в БД при каждом GET**, что нарушает HTTP-семантику и обнуляет идемпотентность read'ов.
- **Как чинить:**
  1. Удалить legacy режим `_should_close_pipeline` после backfill'a; в проде уже не должно быть pipeline без `queued`.
  2. Перенести `reconcile_open_pipelines` в Celery beat job (раз в минуту) — не дёргать на каждом GET. Активити-feed должен быть pure-read.
  3. Переименовать «pipeline» → «run» в схеме событий (более универсально для будущих типов запусков).

#### A-009 — Нет enforcement бюджета AI; `AI_MONTHLY_BUDGET_USD = 10` чисто декларативно
- **Где:** `backend/app/config.py:28`. По всему репо — единственное упоминание этой переменной. Стоимость считается per-call (`backend/app/agents/llm_client.py:61-71`) и пишется в `agent_runs.cost_usd` (`backend/app/agents/base.py:243`), но никакой circuit-breaker нет.
- **Что:** Sonnet 4.6 стоит ~$3 input/$15 output на 1M токенов. Один баговый агент в цикле или одна большая страница в `query_clustering` могут пробить $10/мес за час. После этого — продолжаем тратить.
- **Почему high:** Платит David из своего кармана; для будущего SaaS — это первичный механизм cost control per-tenant.
- **Как чинить:** Перед каждым `call_with_tool` / `call_plain` — read `SUM(cost_usd) FROM agent_runs WHERE tenant_id = ? AND created_at >= date_trunc('month', now())`. Если больше budget × 0.9 — log warning + email Telegram. Если больше budget — RaiseBudgetExceeded(exit). Кэшировать в Redis (TTL 60s), чтобы не убивать БД.

#### A-010 — Naive datetime + timestamp without timezone в части моделей; UI ставит `Z` в JS вручную
- **Где:** `backend/app/models/analysis_event.py:36` (`DateTime, default=datetime.utcnow` — naive), `backend/app/models/outcome_snapshot.py:33,41` (то же). Сравнить с `backend/app/models/agent_run.py:26-27`, `backend/app/intent/models.py:39,77,122`, `backend/app/core_audit/review/models.py:79,111,119` где `DateTime(timezone=True)`. UI workaround: `frontend/components/dashboard/activity-feed.tsx:27-38` (`utcIso = ... ? iso : iso + "Z"`) и `backend/app/api/v1/activity.py:33-37` (тоже добавляет `Z` в serializer).
- **Что:** Половина таблиц tz-aware, половина — naive. Активити приходит из БД naive, в JSON наклеивается `Z`, на фронте парсится как UTC. Это работает, но это **двойной костыль** на границе. В Python `datetime.now(timezone.utc).replace(tzinfo=None)` (`backend/app/core_audit/activity.py:97`) — наглядное свидетельство, что код знает про проблему и пытается её обойти ad-hoc.
- **Почему high:** При мульти-тенант часовые пояса станут per-tenant фичей. С naive datetime это путь к багам «отчёт ушёл в 04:00 чьего-то локального».
- **Как чинить:** Миграция: все timestamp колонки → `TIMESTAMP WITH TIME ZONE`. Default → `func.now()` на серверной стороне. В коде убрать ручные `+ "Z"` в serializers.

---

### MEDIUM

#### A-011 — Модели разбросаны по проекту: 13 в `app/models/` + 5 «островных» `models.py`
- **Где:** Центральные: `backend/app/models/*` (13 файлов). «Островные»: `backend/app/intent/models.py`, `backend/app/fingerprint/models.py`, `backend/app/core_audit/review/models.py`, `backend/app/core_audit/report/models.py`, `backend/app/core_audit/demand_map/models.py`.
- **Что:** Часть моделей лежит близко к домену (intent, review, report) — это нормальный DDD. Но `Site`, `Issue`, `DailyMetric`, `AnalysisEvent` — в общем `models/`. Граница «что общее, что доменное» нечёткая.
- **Как чинить:** Не трогать; просто задокументировать правило: модель ≥1 домена → `models/`, моноцелевая → внутри домена. Без urgency.

#### A-012 — `BaseAgent.run()` — fat pattern: detection + LLM call + persist + audit run в одной функции (200+ строк)
- **Где:** `backend/app/agents/base.py:39-247`.
- **Что:** Логика загрузки сайта, создания `agent_run`, вызова Claude, парсинга, сохранения issues, обновления audit row, обработки ошибок — всё в одном методе. `_save_issues` имеет inline-логику классификации `affected_entity_type` по эвристике `'/' not in title[0]` (`base.py:209`) — хрупко.
- **Почему medium:** Сейчас агентов мало (4), стиль повторяется. Когда станет 10+ — каждое изменение паттерна (например, добавить tenant_id в issue) ломает всех.
- **Как чинить:** Разбить на этапы (`_create_run_record`, `_call_llm`, `_persist_results`, `_finalize_run`) — каждый покрывается отдельным тестом. `_save_issues` вынести в `IssueRepository`.

#### A-013 — `IssuePipeline` пишет issues дважды — сначала агенты сохраняют, потом валидатор ходит читать и обновляет confidence
- **Где:** `backend/app/services/issue_pipeline.py:74-168`.
- **Что:** Цитата из самого файла (строка 88-90):
  > Re-read from DB since BaseAgent.run already saves them.
  > Actually, let's restructure: don't save in BaseAgent, save here after validation
  > For now, issues are already saved — validator will adjust confidence in place

  Архитектурный TODO в коде. Pipeline сохраняет → читает «сегодняшние» issues (костыль `created_at >= midnight`) → проверяет → обновляет статусы. Это ломается при двух запусках в один день: второй прогон валидирует issues первого и может откатить им confidence.
- **Почему medium:** Боль ощущается раз в N дней. Но при manual triggers (через `/pipeline/full`) — почти каждый раз.
- **Как чинить:** Завести `pipeline_run_id` (или использовать `agent_run.id`) на issue → валидатор фильтрует `WHERE pipeline_run_id = current`. Альтернатива (правильнее): не сохранять в `BaseAgent`, передавать список IssueDetection через структуру `AgentOutput` в pipeline, валидатор уже фильтрует, потом одна транзакция INSERT'ит финал.

#### A-014 — Activity reconcile делает write в обработчике GET-запроса
- **Где:** `backend/app/api/v1/activity.py:53,71,104` — каждый из трёх GET-эндпоинтов начинается с `await reconcile_open_pipelines(db, site_id)`.
- **Что:** Реконсайл backfill-ит «забытые» терминалы и пишет новые row'ы в `analysis_events`. Это (а) нарушает HTTP-семантику (GET не должен быть write), (б) добавляет 50+ row scan + потенциальные insert'ы на каждый poll фронта (фронт опрашивает раз в 5 сек).
- **Почему medium:** Реально ломает только при большом каталоге событий или большом числе одновременных пользователей. Сейчас — N=1.
- **Как чинить:** Перенести в `queue-health-2min`-стиль beat job. GET оставить чистым read.

#### A-015 — SWR poll на 5 секунд для activity feed на каждом сайте
- **Где:** `frontend/components/dashboard/activity-feed.tsx:54` (5s), `frontend/app/competitors/page.tsx:30,51-66` (5s/8s), `frontend/app/priorities/page.tsx:50,58,72` (5s), `frontend/app/onboarding/[siteId]/page.tsx:47` (3s), `frontend/components/dashboard/last-run-summary.tsx:50` (4s), `frontend/components/dashboard/overview.tsx:128` (10s).
- **Что:** При активном dashboard tab нагенерится ~1 запрос/сек на пользователя через комбинацию опросов. Сейчас на 1-2 владельцев — копейки. На 50 — 50 RPS на один nginx instance + БД hit за каждым.
- **Почему medium:** Не блокер, но архитектурно — это polling-only flow без debounce. Когда idle (никаких pipeline не запущено), polling не должен крутиться.
- **Как чинить:** (1) Условный `refreshInterval: hasRunning ? 5000 : 30000` — частично уже сделано (`competitors/page.tsx:46`), распространить на activity-feed. (2) Долгосрочно — Server-Sent Events / WebSocket из FastAPI; FastAPI это умеет нативно.

#### A-016 — Onboarding chat state хранится в `target_config_draft.onboarding_chat` (JSONB)
- **Где:** `backend/app/api/v1/admin_demand_map.py:623-639` (helper `_chat_state` / `_save_chat_state`).
- **Что:** История сообщений, текущий draft, round number — всё внутри одного JSONB на `Site`. При активном чате каждый user message → SELECT целиком + UPDATE целиком. Нет нормализации, нет append-only, нет ограничения на размер истории.
- **Почему medium:** Сейчас onboarding одноразовый, история <20 сообщений; терпимо. Если планируется returning chat / много раундов — нужна отдельная таблица `onboarding_messages`.
- **Как чинить:** При следующем рефакторе onboarding'a — вынести в `onboarding_messages(site_id, role, content, round, ts)`. До тех пор: документировать «не более 20 сообщений».

#### A-017 — Глобальный singleton Anthropic клиента в `llm_client.py`
- **Где:** `backend/app/agents/llm_client.py:26,46-58` (`_client: anthropic.Anthropic | None = None`).
- **Что:** Один клиент инициализируется один раз и переиспользуется. Это нормально для FastAPI worker'а, но для Celery worker с `worker_max_tasks_per_child=50` — клиент теряет внутренние коннекшны при перезапуске процесса (это OK). Главная проблема — он `Anthropic` (sync), а вызывается из async-контекста через blocking IO в `with client.messages.stream(...)`. Это блокирует event loop асинхронной FastAPI ручки `/chat` (`backend/app/api/v1/chat.py`), заставляя другие requests ждать.
- **Почему medium:** Сейчас вызовы Claude в API-слое — только в `/chat`, низкочастотный endpoint. Но любая попытка стриминга для UI повиснет.
- **Как чинить:** В FastAPI-контексте использовать `anthropic.AsyncAnthropic` через отдельный async-метод. В Celery worker остаётся sync-клиент.

#### A-018 — Нет fallback для AI при недоступности Claude
- **Где:** `backend/app/agents/llm_client.py:108-115,174-179` — `with client.messages.stream(...)`, обёрнуто только в `try/except` на уровне task'a. При ошибке Anthropic API — issue/agent просто валится, в `agent_runs.status='failed'` пишется ошибка, и день без рекомендаций.
- **Что:** `MODEL_MAP` имеет два ключа `cheap`/`smart`, оба указывают на Sonnet 4.6. Нет даунгрейда на Haiku при failure. Нет local fallback / cached previous result.
- **Почему medium:** Anthropic — single point of failure. У вас уже есть Cloudflare Worker proxy для обхода геоблока (`ANTHROPIC_BASE_URL`), но если упал прокси — упало всё.
- **Как чинить:** Wrapper `call_with_fallback` который пробует proxy → direct → cached_last_response → ошибка. Cached last — `agent_runs.output_summary` уже хранит предыдущий результат, можно отдавать с пометкой `stale=true`.

#### A-019 — Дрифт между `_make_session` и `task_session` (см. также A-004): новые модули обновлены, старые — нет
- **Где:** `_make_session` живёт в `backend/app/agents/tasks.py:27-32` (5 вызовов в файле), `backend/app/collectors/tasks.py:31-36` (6 вызовов). `task_session` — все 12 файлов в `core_audit/`, `fingerprint/`, `intent/`.
- **Почему medium (отдельно от A-004):** Это типичный признак «hardening sprint исправил то, что вижу, забыл что не вижу». Линт-правило защитит от регрессии.
- **Как чинить:** Завести pre-commit hook `grep '_make_session' app/` = error. После миграции — удалить упоминания совсем.

#### A-020 — Frontend ↔ Backend контракт: типы дублируются и расходятся
- **Где:** `frontend/lib/api.ts` — большие литеральные типы прямо в return type. На бэке: pydantic models в каждой ручке.
- **Что:** Изменение `BusinessTruth.directions[].strength_*` потребует ручного обновления и в `app/core_audit/business_truth/dto.py`, и в `app/api/v1/business_truth.py`, и в `frontend/lib/api.ts`. Нет автогенерации.
- **Почему medium:** Сейчас FE/BE один разработчик; ошибки ловятся быстро. При росте команды — типичный source of UI-ломок.
- **Как чинить:** FastAPI экспортит OpenAPI schema → `openapi-typescript` / `orval` генерит TS типы в build-step. Можно добавить как `npm run codegen` без полной перестройки.

---

### LOW

#### A-021 — Логические alias'ы стейджей в нескольких местах
- **Где:** `backend/app/core_audit/activity.py:26-31` (`LEGACY_STAGE_ALIASES = {"crawl_site": "crawl", ...}`), `frontend/components/dashboard/activity-feed.tsx:13-25` (`STAGE_LABEL = {crawl: "краулинг сайта", ...}`).
- **Что:** Логические имена stage расходятся в трёх местах: имя celery task'и, `stage` в БД, label в UI. Любое переименование — три PR.
- **Как чинить:** Один `app/core_audit/stages.py` с enum'ом + ru-label + alias'ами. Frontend получает label через response (а не локальную карту).

#### A-022 — `app.profiles.tourism` — единственная вертикаль; чтение `vertical/business_model` уже есть в `Site`, но используется для одного profile
- **Где:** `backend/app/intent/tasks.py:9` (`import app.profiles`), `backend/app/intent/tasks.py:38-44`. `Site.vertical: default="tourism"`, `business_model: default="tour_operator"` — `backend/app/models/site.py:25-26`.
- **Что:** Multi-vertical архитектура заложена, но не используется. Единственный профиль — `tourism/tour_operator` (`backend/app/profiles/tourism/`).
- **Почему low:** Это «правильный» дизайн с расчётом на будущее; не вред.
- **Как чинить:** Не нужно. Просто отметить как место для второго профиля при выходе на другую нишу.

#### A-023 — `lifespan` в FastAPI вызывает `engine.dispose()` только на shutdown
- **Где:** `backend/app/main.py:8-11`.
- **Что:** Хелсчеки и одиночные ручки используют общий `engine` (`backend/app/database.py:7-13`) с `pool_size=5, max_overflow=10`. Это нормально, но `pool_recycle=300` слишком короткий — каждые 5 минут все коннекшны пересоздаются. Postgres воспринимает это как фоновую нагрузку (PG не любит частое create connection).
- **Как чинить:** `pool_recycle=1800` (30 минут) — достаточно для борьбы со стейл коннекшнами от idle timeouts NAT.

#### A-024 — `tests/` покрывает доменную логику (business_truth, fingerprint, draft_profile), но не API integration
- **Где:** `backend/tests/business_truth/` (8 файлов, ~57 тестов), `backend/tests/fingerprint/`, `backend/tests/draft_profile/`. Нет `tests/api/` или `tests/integration/`.
- **Что:** Pure-logic покрыта хорошо. Не покрыты: `admin_ops.trigger_full_pipeline`, реальный run_id flow с моками celery, `OnboardingChatService`. Моков `task_session` нет.
- **Почему low:** Hardening sprint уже добавил `test_stage_events`, `test_activity_log`. Это правильное направление.
- **Как чинить:** Добавить `tests/api/test_pipeline_full.py` с FastAPI TestClient и моком celery `send_task`. Не срочно.

#### A-025 — `Tenant.sites` relationship lazy="select" + async — потенциальная DetachedInstanceError
- **Где:** `backend/app/models/tenant.py:18-19`.
- **Что:** Комментарий говорит «use selectinload() explicitly». Сейчас в API `Tenant.sites` нигде не подгружается, но при добавлении админ-эндпоинта «список сайтов тенанта» легко словить runtime-ошибку.
- **Как чинить:** Либо `lazy="raise"` (защита от ленивого доступа), либо явно использовать `selectinload`.

---

## Что в архитектуре правильно (важно отметить)

1. **Чистое разделение `core_audit/<domain>/` модулей.** `business_truth`, `priority`, `report`, `competitors`, `review` живут как отдельные пакеты с `dto.py`, `service.py`, `tasks.py`, `models.py`. Это правильный domain-package паттерн, легко расширять. **Не ломать.**

2. **`task_session`/`task_session_factory` (`backend/app/workers/db_session.py`) — отличный примитив.** Документация прямо в docstring'е объясняет, почему `eng.dispose()` обязателен. Hardening sprint двигается в правильную сторону, надо доделать миграцию `_make_session` → `task_session` для агентов и коллекторов (A-004/A-019).

3. **`run_id` для группировки событий** (`backend/app/api/v1/admin_ops.py:82-111`, `core_audit/activity.py`). Это сильное архитектурное решение — UI может отделить «вот этот клик» от «прошлый клик». Реализовано грамотно: один UUID, прокидывается через `kwargs={'run_id': ...}` во все task'ы пайплайна.

4. **`emit_terminal` invariant** (`backend/app/core_audit/activity.py:230-294`): «каждый pipeline:started получает pipeline:<terminal> в течение 10 минут». Заложен в код + тесты. Это правильный contract-first подход к event-driven.

5. **Operating mode guard** (`backend/app/services/operating_mode.py`) — простая, но честная архитектура readonly/recommend/propose/autoexecute. Уровни как `IntEnum` — extensible, intuitive.

6. **Cloudflare Worker прокси для Anthropic** + streaming в LLM-клиенте (`backend/app/agents/llm_client.py:108`). Правильное решение проблемы геоблокировки + Vercel timeout. Комментарии объясняют **почему**, не **что**.

7. **Pydantic model_config с extra=allow для `TargetConfigBody`** (`backend/app/api/v1/admin_demand_map.py:80`) — future-proof JSONB API: добавление нового ключа не ломает старых клиентов.

8. **Onboarding gate** (`backend/app/core_audit/onboarding/gate.py`): nightly job берёт только сайты с `onboarding_step="active"`. Прозрачно: скаляр-функция, легко тестируется. Manual triggers намеренно bypass'ят гейт — это правильно.

9. **Idempotency через advisory lock** в competitors discovery (`backend/app/core_audit/competitors/tasks.py:38-40,242-255`). `pg_try_advisory_lock(int_from_uuid)` — хорошая практика для long-running tasks без необходимости рулить очередью вручную.

10. **Healthcheck Celery worker'а через `inspect ping`** (`docker-compose.yml:62`). Это решает реальную проблему «процесс жив, но Redis отвалился» — обычные процесс-уровневые проверки её бы пропустили. Видно, что проблема была пройдена и закрыта (см. `MEMORY.md` про Celery Redis UNBLOCKED).

---

## Готовность к multi-tenant спринту

### Must-fix перед стартом (CRITICAL)

- [ ] **A-002 — token encryption.** Без этого мульти-тенант = немедленный security incident. Один владелец видит токены другого через `pgdata` или backups.
- [ ] **A-003 — заменить `X-Admin-Key` на JWT/Session.** Минимум: добавить `require_site_owner(site_id)` dependency на каждом `/admin/sites/{id}/...`. Без этого horizontal escalation тривиален.
- [ ] **A-001 — `tenant_id` колонка на каждой таблице с `site_id`.** Это самая большая миграция (15+ таблиц, backfill + индексы). Делать в режиме «сначала добавить nullable + backfill + сделать NOT NULL во второй миграции» чтобы не обрушить прод.

### Можно мигрировать постепенно

- **A-004/A-019** — `_make_session` → `task_session`. Один PR, не блокирует ничего, но устранит будущие connection-leak инциденты под нагрузкой.
- **A-007** — снести `NEXT_PUBLIC_SITE_ID` дефолт. Уже есть `SiteProvider` с переключателем, нужно лишь перестать опираться на дефолт. Релизный риск низкий.
- **A-009** — AI budget enforcement. Стоит сразу запилить per-tenant, не успеешь — первая же баг-итерация выжрет $50.
- **A-010** — все datetime → `timezone=True`. Не блокер, но если делать миграцию A-001 — добавить и tz-aware конвертацию в одной alembic-волне.
- **A-013** — pipeline_run_id на issue. Поможет валидатору не мешать данные между прогонами.
- **A-014** — вынести `reconcile_open_pipelines` из GET в beat. Защита от шторма polling'a при росте числа пользователей.
- **A-020** — codegen TS-типов. Когда появится второй разработчик, или когда тенантов станет десятки.

### Что НЕ ломать

- Domain-package структуру `core_audit/<X>/`.
- `run_id` контракт + `emit_terminal` инвариант.
- `task_session` хелпер.
- Cloudflare Worker proxy для Anthropic.
- `OperatingModeGuard`.
- Onboarding gate (`onboarded_site_ids`).

---

## Метрики

### Размер кодовой базы

- **Backend Python файлов:** ~230 (`backend/app/**/*.py`).
- **API endpoints:** 16 router-модулей в `backend/app/api/v1/` (`admin_demand_map`, `admin_ops`, `business_truth`, `activity`, `agent_status`, `chat`, `collectors`, `dashboard`, `fingerprint`, `health`, `intent`, `priority`, `queries`, `report`, `review`, `sites`, `tasks`). Из них в `router.py` подмонтировано 12 — 4 модуля «висят» (`chat`, `queries`, `tasks`, `agent_status`, `fingerprint`). Намеренно: см. комментарий в `router.py:18-22`. **Технический долг — удалить неиспользуемые файлы** (не сделано).
- **Доменные модели:** 13 в `backend/app/models/` + 5 «островных» `models.py` в `intent/`, `fingerprint/`, `core_audit/{review,report,demand_map}/` = ~18 таблиц.
- **Celery tasks:** ~45-50 (через `grep @celery_app.task`). Распределение: `agents/tasks.py` (10), `collectors/tasks.py` (6), `fingerprint/tasks.py` (5), `core_audit/competitors/tasks.py` (3), `core_audit/review/tasks.py` (3), `intent/tasks.py` (4), остальные по 1-2.
- **Alembic migrations:** 15.
- **Backend тестов:** 63 файла.
- **Frontend:** 5 страниц (`/`, `/competitors`, `/priorities`, `/reports`, `/settings`, + onboarding wizard под `/onboarding/[siteId]`). 5+5+2 dashboard/layout/priorities компонент.

### Coupling-горячие точки (модули, импортируемые > 10 раз внутри `app/`)

- `app.database` — 37 импортов (`Base`, `TimestampMixin`, `get_db`).
- `app.models.site` — 29 импортов (Site используется почти везде).
- `app.workers.celery_app` — 17 импортов (`celery_app` для `.task`/`send_task`).
- `app.core_audit.activity` — широко импортируется во всех task-модулях для `log_event`/`emit_terminal`.
- `app.workers.db_session` — 12 task-модулей (новых).

Это нормальный coupling для FastAPI+SQLAlchemy: всё опирается на `Base` и `Site` — это ядро. Тревожно, что **`app.config.settings` импортируется в 30+ местах**, в том числе глобально (`AI_DAILY_MODEL` цепляется при импорте `llm_client` — `backend/app/agents/llm_client.py:29-43`). Это значит, что мокать settings в тестах сложно, и ChangeLog flagов AI требует перезапуска сервиса.

### Размер ключевых файлов (LoC, без пустых)

- `backend/app/api/v1/admin_demand_map.py` — **861 строка**. Самый большой router (онбординг + chat + competitors + draft profile в одном файле). **Кандидат на split**.
- `backend/app/api/v1/admin_ops.py` — 322 строки. Терпимо, но `_baseline_metrics` стоит вытащить.
- `backend/app/core_audit/competitors/tasks.py` — 700+ строк. Discovery + deep-dive + opportunities + chain — пора разбить.
- `backend/app/core_audit/activity.py` — 391 строка. Достаточно сложна (3 режима закрытия пайплайна) — нуждается в упрощении (см. A-008).

### Dev/prod parity

- `docker-compose.yml` поднимает то же самое окружение, что и в prod на Jino. **Хорошо.**
- НО: `ADMIN_API_KEY: "admin_dev_secret_2026"` хардкоден в compose (`docker-compose.yml:93`). На проде это должно быть из `.env`/secret store.
- `NEXT_PUBLIC_SITE_ID` хардкоден в compose с реальным prod UUID. Это означает, что dev и prod указывают на одну и ту же запись — **dev frontend ходит в prod-сайт**. Очень опасно, легко случайно через manual trigger дёрнуть прод.

---

## Замечание по контексту фазы

Hardening sprint закрыт, и это видно: появились `task_session`, `run_id`, `emit_terminal`, реконсайл. Но он закрыт **не до конца**: `agents/tasks.py` и `collectors/tasks.py` остались на старом `_make_session`, два режима закрытия pipeline сосуществуют, реконсайл сидит в GET. Перед multi-tenant спринтом стоит выделить ~3-4 дня на финализацию hardening (A-004, A-008, A-014) — иначе мульти-тенант ляжет на полу-устранённый фундамент.

Самая большая инвестиция, которая окупится в десятки раз во всём остальном — это **A-001 (`tenant_id` на каждой таблице) + A-003 (заменить shared-secret на JWT)**. Без них любой следующий feature на multi-tenant темах будет вечно копировать костыли в каждой ручке.
