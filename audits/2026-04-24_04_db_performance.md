# DB & Performance Audit
**Дата:** 2026-04-24
**Область:** PostgreSQL 16 (SQLAlchemy async + asyncpg) · Celery + Redis · Yandex API коллекторы · Frontend polling
**Репозиторий:** `/Users/davidgevorgan/Desktop/yandex-growth-tower`

---

## TL;DR — Performance Posture

Продукт жизнеспособен на текущем масштабе (единицы сайтов, двузначные тысячи строк) — **схема грамотная**, индексы на большинстве FK и частых WHERE-колонок **есть**, миграции линейны (одна голова `a1b2c3d4e5f6`), `expire_on_commit=False` выставлен корректно для async, каждая Celery-таска аккуратно диспозит свой engine через `task_session()` (`backend/app/workers/db_session.py:29-41`).

Но есть ряд **реальных узких мест**, которые уже сейчас больно едят и порталят по крышке при 10x-масштабе:

1. **Отсутствие `soft_time_limit`/`time_limit` на любых Celery-тасках** — нет ни одного упоминания по проекту (0 совпадений по `grep -rn "soft_time_limit\|time_limit"` кроме комментария в `review/tasks.py`). В связке с `worker_cancel_long_running_tasks_on_connection_loss=False` зависший SERP-пробинг или `competitors_deep_dive` держит воркер часами.
2. **`reconcile_open_pipelines` на каждом /activity-запросе** — frontend опрашивает activity каждые **5 секунд** (`activity-feed.tsx:54`), и каждый раз мы тянем до 50 `pipeline:started` строк + 3-4 подзапроса на каждую → при **двух одновременных владельцах с dashboard открытым** это 200+ запросов к Postgres/мин. На пустых базах сейчас ок, на 100k+ событий — боль.
3. **N+1 в Webmaster upsert** (`backend/app/collectors/webmaster.py:166-194`) — 500 запросов × 2 round-trip (UPSERT + SELECT id) = 1000 round-trip на одну `collect_webmaster_all` задачу для одного сайта. С `RETURNING id` это сжимается до 500 вложенных (или 1 bulk upsert).
4. **Rate-limit семафор модульного уровня** (`backend/app/collectors/base.py:16`) — `asyncio.Semaphore(3)` существует только внутри процесса; при `concurrency=N` воркеров Celery эффективный лимит = `3 * N`, что обходит заявленное "под 5 req/s" от Yandex и приведёт к 429 при росте параллелизма.
5. **JSONB без GIN-индексов** — `sites.target_config` содержит `business_truth`, `competitor_profile`, `growth_opportunities`, читается постоянно; `analysis_events.extra` — тоже JSONB без GIN. Пока нет запросов `WHERE target_config @> …`, но как только появится (а по вашему roadmap появится) — sequential scan.
6. **DateTime без timezone** на `analysis_events.ts`, `page.first_seen_at`/`last_seen_at`, `search_queries.first_seen_at`/`last_seen_at`, `outcome_snapshots.*`. Первая миграция назвалась "initial_schema_with_timestamptz", но половина последующих таблиц снова использует naive `DateTime()`. Дашборд приклеивает "Z" вручную (`backend/app/api/v1/activity.py:36`) — это костыль.

В целом: **никакой катастрофы для MVP нет**, но п.1-3 стоит закрыть в ближайший спринт, остальное — в фоновый backlog.

---

## Карта таблиц

| Таблица | Rows expected (6 мес.) | Ключевые индексы | Замечания |
| --- | --- | --- | --- |
| `tenants` | 10-100 | PK, uniq(slug) | — |
| `sites` | 10-100 | PK, uniq(tenant_id, domain), ix(tenant_id), ix(vertical) | `target_config` JSONB быстро пухнет (business_truth + competitor_profile + growth_opportunities) |
| `pages` | 1k-50k | PK, uniq(site_id, url), ix(site_id) | `content_text` (TEXT до 10k символов) — тянется в `select *`, см. HIGH-3 |
| `search_queries` | 10k-500k | PK, uniq(site_id, query_text), ix(site_id) | нет индекса на `(site_id, is_branded)` — используется в `_pick_top_queries` |
| `daily_metrics` | 100k-5M | PK (BigInt), uniq(site_id, date, metric_type, dimension_id), ix(site_id), ix(date) | Нет композита `(site_id, metric_type, date)` — основной паттерн чтения. Подробнее: HIGH-1 |
| `issues` | 1k-100k | PK, ix(site_id), ix(severity), ix(status) | Нет `(site_id, status)` — частый paginated filter (dashboard.py:213-237) |
| `alerts` | 100-10k | PK, ix(site_id), ix(issue_id) | Нет индекса на `created_at` — `dashboard.py:84` делает `WHERE func.date(created_at) == today` → seqscan |
| `agent_runs` | 10k-1M | PK, ix(site_id) | Нет `(site_id, completed_at DESC)` — `dashboard.py:92-94` + `dashboard.py:299-304` |
| `tasks` | 1k-100k | PK, ix(site_id), ix(issue_id), ix(priority), ix(status) | status + priority дублируют index, но OK |
| `snapshots` | 100-5k | PK, ix(site_id) | — |
| `seasonality_patterns` | 10-1k | PK, ix(site_id) | — |
| `analysis_events` | 1k-500k | PK (BigInt), ix(site_id), ix(ts), ix(site_id, ts), ix(site_id, run_id) | Хорошо покрыт, см. CRITICAL-2 по чтению |
| `outcome_snapshots` | 10-5k | PK, ix(site_id), ix(site_id, applied_at), ix(followup_at), uniq(site_id, recommendation_id) | — |
| `page_fingerprints` | 1k-50k | PK=page_id, ix(site_id), ix(site_id, content_hash), ix(last_fingerprinted_at), partial ix(status WHERE status != 'fingerprinted') | Отлично |
| `query_intents` | 10k-500k | PK=query_id, ix(site_id), ix(intent_code), ix(is_ambiguous) | — |
| `page_intent_scores` | 10k-500k | PK, uniq(page_id, intent_code), ix(site_id, intent_code, score) | — |
| `coverage_decisions` | 1k-50k | PK, ix(site_id), ix(site_id, status) | — |
| `page_reviews` | 1k-100k | PK, ix(page_id), ix(site_id), ix(composite_hash), ix(site_id, status, reviewed_at DESC) | Очень хорошо |
| `page_review_recommendations` | 10k-1M | PK, ix(review_id, category), ix(site_id, user_status), ix(site_id, user_status, priority_score DESC NULLS LAST) | Отлично |
| `weekly_reports` | 100-5k | PK, uniq(site_id, week_end, builder_version), ix(site_id, week_end), ix(health_score) | — |
| `target_clusters` | 1k-100k | PK, uniq(site_id, cluster_key), ix(site_id, quality_tier), ix(site_id, intent_code), partial ix(site_id WHERE growth_intent='grow') | Отлично |
| `target_queries` | 10k-500k | PK, uniq(cluster_id, query_text), ix(cluster_id) | FK на search_queries → `ON DELETE SET NULL` (ок) |

---

## Находки

### CRITICAL (текущие узкие места)

#### CRIT-1. Celery-таски без `soft_time_limit` / `time_limit`

**Файлы:**
- `backend/app/workers/celery_app.py:11-45` (вся конфигурация)
- Все `@celery_app.task(...)` декораторы (см. примеры: `backend/app/core_audit/competitors/tasks.py:228, 419, 646`; `backend/app/collectors/tasks.py:145, 167, 238, 306`)

**Что:** Поиск по репо `grep -rn "soft_time_limit\|time_limit"` возвращает только упоминание в комментарии (`core_audit/review/tasks.py:5`). Ни одна таска не ограничена по времени. В `celery_app.py:33` явно стоит `worker_cancel_long_running_tasks_on_connection_loss=False`.

**Почему плохо:** `competitors_discover_site` пробивает SERP для N запросов — один внешний таймаут + retry-ожидание тащит воркер 10+ минут. `competitors_deep_dive` крутит `ThreadPoolExecutor(max_workers=5)` с HTTP-фетчами (`core_audit/competitors/tasks.py:492-510`) — один конкурент типа `findgid.ru` (10s+ per fetch, как прокомментировано в коде) может увеличить задачу до минут. Crawl с 50 URL (`collectors/tasks.py:269`) + chained fingerprint — потенциал на десятки минут. При `worker_max_tasks_per_child=50` и отсутствии `visibility_timeout=3600` (он есть, но только для ACK reclaim) **зависшая таска блокирует concurrency slot до физической смерти воркера**.

**Сценарий из прода:** Яндекс-SERP возвращает медленный ответ → одна из 5 параллельных глубоких обходов конкурентов висит 30 мин → на dashboard "пайплайн идёт 15 мин" → frontend крутится "Идёт анализ…", owner думает "платформа сдохла".

**Фикс:** Добавить дефолт на уровне конфига Celery + override на тяжёлых тасках:
```python
# celery_app.py
task_soft_time_limit=300,   # 5 min soft (TaskTimeoutError)
task_time_limit=420,        # 7 min hard (SIGKILL)
```
На `competitors_discover_site` / `competitors_deep_dive_site` / `crawl_site` — поднять до 600/720s.

---

#### CRIT-2. `reconcile_open_pipelines` на каждом poll activity-feed

**Файлы:**
- `backend/app/core_audit/activity.py:297-382` — реализация
- `backend/app/api/v1/activity.py:53, 71, 104` — вызовы в **каждом** из 3 activity endpoint'ов
- `frontend/components/dashboard/activity-feed.tsx:54` — `refreshInterval: 5_000`
- `frontend/components/dashboard/overview.tsx:128` — `activity-last-{siteId}` каждые 10s

**Что:** На каждый GET `/sites/{id}/activity*` вызывается `reconcile_open_pipelines`, который:
1. `SELECT … FROM analysis_events WHERE site_id = $ AND stage='pipeline' AND status='started' AND run_id IS NOT NULL ORDER BY ts DESC LIMIT 50` (1 query)
2. Для каждой из до 50 строк: `_terminal_exists_for_run` (+1 query) + `_latest_stage_events` (+1 query) + возможный `_persist_event` (+1 query)

Итого: **1 + 3N round-trips per poll**, где N = количество незакрытых pipeline:started для сайта. В стационаре N≈0, но при свежем пайплайне или аварии N>0 в течение 10 мин.

**Почему критично сейчас:** Два окна dashboard + один /competitors → 6 poll/min × 3 endpoint = **18 reconciles/min per viewer**. Пока N=0 это 18 index-seeks, копейки. Но N>0 даёт 18×(1+3×5)=288 запросов/мин на одного viewer. При 5 владельцах — 1400/мин.

**Почему будет критично при росте:** `analysis_events` без TTL растёт **линейно по времени** — при ~20 событий на пайплайн и 10 запусков в день это 200 строк/день/сайт = 73k/год/сайт. Индекс `ix_analysis_events_site_ts` спасает от seqscan, но 50 строк × 3 запроса × каждый poll — это пачка мелких round-trip даже при хороших планах.

**Фикс:**
1. Вынести reconcile из GET endpoints в отдельную Celery-таску, крутящуюся раз в 30s (уже есть `queue_health_check` каждые 120s — можно подсадить).
2. Или: кэшировать результат reconcile на 30s в Redis по ключу `reconcile:{site_id}`.
3. Добавить TTL / партиционирование `analysis_events` по месяцу, раз в неделю чистить > 30 дней.

---

#### CRIT-3. N+1 round-trips в `WebmasterCollector.collect_and_store`

**Файл:** `backend/app/collectors/webmaster.py:166-194` (+ тот же паттерн на 210-262)

**Что:**
```python
for q in queries:                        # до 500 запросов
    stmt = pg_insert(SearchQuery)…       # round-trip 1: UPSERT
    result = await db.execute(stmt)
    sq_row = await db.execute(            # round-trip 2: SELECT id
        select(SearchQuery.id).where(...)
    )
```

**Почему плохо:** 500 × 2 = **1000 round-trip** к Postgres на одну `collect_site_webmaster`. Умножить на `collect_webmaster_all` для всех активных сайтов в 07:00 MSK → минуты реального времени в шедуле. У async SQLAlchemy нет batching, каждый execute это отдельный RTT к asyncpg.

Дополнительно: после основного upsert'а в каждой итерации ещё цикл по `shows_map.keys()` (до 30 дат) с upsert в `daily_metrics` (строка 217-235) — т.е. ещё ×30 round-trip на query.

**Фикс:**
```python
stmt = pg_insert(SearchQuery).values([...]).on_conflict_do_update(
    index_elements=["site_id", "query_text"],
    set_={...},
).returning(SearchQuery.id, SearchQuery.query_text)
result = await db.execute(stmt)
id_map = {row.query_text: row.id for row in result}
```
И для `daily_metrics` — накопить все строки и один bulk `pg_insert(...).values([...])`.

---

#### CRIT-4. Семафор rate-limit не работает между Celery-процессами

**Файл:** `backend/app/collectors/base.py:16`

**Что:**
```python
_semaphore = asyncio.Semaphore(3)          # module-level, per-process
```

**Почему плохо:** В Celery prefork-модели каждый воркер — отдельный процесс с собственной копией модуля → собственным семафором. При `concurrency=4` реальный лимит `4 × 3 = 12 req/s`, при `concurrency=8` — 24. Yandex Webmaster рекомендует ~5 req/s, и при 429 мы backoff'имся (это реализовано, `base.py:81-85`) — **но только ретраим проблемный запрос, не снижаем будущие**.

**Что ломается:** При запуске `crawl_all_sites_monthly` (первый день месяца, 02:00 UTC) все сайты в очереди обрабатываются параллельно воркерами. Если это 20 сайтов × `concurrency=4`, то Yandex API ловит всплески до 12+ req/s. Далее — часы 429 ответов, retries, stretched wall time до 30+ минут вместо 5.

**Фикс:** Распределённый rate-limit через Redis (`aiolimiter` с Redis backend или самописный `redis.incr` + EXPIRE). Комментарий `# Yandex API allows ~5 req/sec; we stay under 3 for safety` в файле должен стать правдой.

---

### HIGH (упадёт на 10x масштабе)

#### HIGH-1. Отсутствует композитный индекс `daily_metrics(site_id, metric_type, date)`

**Файлы чтения:**
- `backend/app/api/v1/dashboard.py:38-42, 51-55, 69-77, 152-157, 184-188`
- `backend/app/api/v1/queries.py:47-80`
- `backend/app/api/v1/admin_ops.py:141-146` (_baseline_metrics)
- `backend/app/core_audit/competitors/tasks.py:106-119` (join к DailyMetric)

**Что:** Таблица будет самой большой (100k-5M строк). Текущие индексы: `ix_daily_metrics_site_id`, `ix_daily_metrics_date`, plus UNIQUE `(site_id, date, metric_type, dimension_id)`. UNIQUE служит индексом, **но только при фильтре по полному префиксу**. Типичный запрос — `WHERE site_id = $ AND metric_type = 'query_performance' AND date BETWEEN ... ` — использует `ix_daily_metrics_site_id`, затем фильтрует в памяти по metric_type/date.

**Почему плохо:** При 500k строк на один активный сайт планировщик выберет `ix_daily_metrics_site_id` → подтащит все строки сайта (всех типов) → отфильтрует в памяти. 7-дневное окно dashboard.py:38-42 вернёт <1000 строк, но прочитает ~500k. На холодном кэше это 200-500ms.

**Фикс:**
```sql
CREATE INDEX ix_daily_metrics_site_type_date
  ON daily_metrics (site_id, metric_type, date DESC);
```

Unique `(site_id, date, metric_type, dimension_id)` можно оставить (нужен для upsert), либо переупорядочить на `(site_id, metric_type, date, dimension_id)` и использовать его для обеих задач.

---

#### HIGH-2. `dashboard.py::alerts_today` делает `func.date(created_at) == today`

**Файл:** `backend/app/api/v1/dashboard.py:82-86`

**Что:**
```python
alerts_today = await db.execute(
    select(func.count()).where(
        Alert.site_id == site_id,
        func.date(Alert.created_at) == today,
    )
)
```

**Почему плохо:** `func.date(created_at)` — функция от столбца — **не использует индекс** на `created_at` (и на `created_at` индекса и нет). `alerts.created_at` — TIMESTAMPTZ; надо сравнивать с диапазоном `>= today_midnight AND < tomorrow_midnight`, тогда btree сработает.

**Фикс:**
```python
from datetime import datetime, time, timezone
start_of_day = datetime.combine(today, time.min, tzinfo=timezone.utc)
end_of_day = datetime.combine(today, time.max, tzinfo=timezone.utc)
... .where(Alert.site_id == site_id,
           Alert.created_at >= start_of_day,
           Alert.created_at <= end_of_day)
```

Добавить индекс `CREATE INDEX ix_alerts_site_created ON alerts (site_id, created_at DESC)`.

---

#### HIGH-3. SELECT широких TEXT-столбцов без нужды

**Файлы:**
- `backend/app/core_audit/competitors/tasks.py:569-573` — `select(Page.url, Page.path, Page.title, Page.h1, Page.meta_description, Page.content_text)` для всех страниц сайта при каждом `competitors_deep_dive`. `content_text` — до 10k символов.
- `backend/app/api/v1/dashboard.py:227-237` — `select(Issue)` — тянет все столбцы, включая `description`, `recommendation` (TEXT, потенциально большие).
- `backend/app/api/v1/report.py:60-63` — `ReportService().list_for_site` возвращает `list_reports` без `payload`, но `get_latest/get_report` (api/v1/report.py:32-45) возвращают **целиком `payload`** (один weekly_report весит 50-500kb JSONB). Frontend `reports/page.tsx` использует `reports/sites/{id}/latest` — это огромный ответ.

**Почему плохо при 10x:** 1000 страниц × 10kb `content_text` = 10MB, которые тянем в `competitors_deep_dive` ради того, чтобы в следующей строке усечь до 600 символов (`tasks.py:581`). Read-amplification × 17 и лишний network-traffic к asyncpg.

**Фикс:**
```python
page_stmt = select(
    Page.url, Page.path, Page.title, Page.h1,
    Page.meta_description,
    func.substr(Page.content_text, 1, 600).label("content_snippet"),
).where(...)
```

Для issues — страничная ручка должна возвращать только нужные поля; full description только при GET /issues/{id}.

---

#### HIGH-4. JSONB без GIN-индексов + хранение большого состояния в `sites.target_config`

**Файлы:**
- `backend/app/models/site.py:23-64` — `settings`, `target_config`, `target_config_draft`, `understanding`, `competitor_domains`, `kpi_targets` — все JSONB.
- `backend/app/core_audit/competitors/tasks.py:331-338` — пишет `competitor_profile`, `competitor_brands`, `competitor_deep_dive`, `growth_opportunities` в `target_config`.
- `backend/app/core_audit/business_truth/rebuild.py` — пишет `business_truth` туда же.

**Что:** Строка `sites` превращается в "god object" — 50-500KB JSONB на строку при заполненном пайплайне. Каждый `site.target_config = new_value` это **полная перезапись JSONB** (TOAST page rewrite). Каждый `db.get(Site, id)` вычитывает всё.

Нет GIN-индексов ни на одном JSONB (проверено по миграциям, grep `CREATE INDEX … USING GIN` — 0 совпадений). Это ок пока никто не делает `WHERE target_config @> '{...}'`, но есть потенциал: `business_truth.directions`, `growth_opportunities` — естественные кандидаты для поиска.

**Почему плохо при 10x:** 100 сайтов × 500KB = 50MB в hot set, но каждая запись конкурирует на `sites` — активная таблица c мелкими UPDATE (onboarding_step и т.п.). Vacuum будет страдать.

**Фикс (долгий):** Вынести крупные блобы в отдельные таблицы `site_business_truth(site_id FK PK, payload JSONB, built_at)`, `competitor_profiles(site_id FK PK, ...)` — это сразу и ссылочная целостность, и меньший bloat на `sites`.

**Фикс (быстрый):** Хотя бы не читать `target_config` целиком там, где нужен только один ключ:
```python
stmt = select(Site.target_config["business_truth"].astext).where(Site.id == site_id)
```

---

#### HIGH-5. Отсутствие `selectinload` в API-маршрутах с relationship

**Файл:** `backend/app/models/tenant.py:17-18`
```python
sites = relationship("Site", back_populates="tenant", lazy="select")
```
Плюс `back_populates="tenant"` на `Site.tenant` (`site.py:66`).

**Что:** `grep -rn "selectinload\|joinedload"` по `backend/app` возвращает **0 совпадений**. Комментарий на tenant.py:17 говорит "use selectinload() explicitly in queries", но нигде это не сделано. Сейчас это не проблема только потому, что эти relationship **не используются** в API (во всех endpoints прямой `select(Site).where(tenant_id == ...)` без обращения к `tenant.sites`).

**Почему потенциально плохо:** Если кто-то напишет `sites = await db.execute(select(Tenant)); for t in sites.scalars(): t.sites` — lazy load в async-context выстрелит `MissingGreenlet`. Ещё не выстрелило, но это pitfall.

**Фикс:** Либо `lazy="raise"` на relationship (жёстко ломает ленивую подгрузку → заставляет писать явно), либо `lazy="selectin"` если всегда хотим eager.

---

#### HIGH-6. `reconcile_open_pipelines` в N+1 стиле

**Файл:** `backend/app/core_audit/activity.py:322-381`

**Что:** Цикл `for started in started_rows:` (до 50 итераций), каждая итерация делает 2-3 запроса: `_terminal_exists_for_run`, `_latest_stage_events`, возможный `_persist_event`. Это классический N+1.

**Фикс:** Всё можно сделать одним запросом — `LEFT JOIN LATERAL` или `GROUP BY run_id` с агрегатом `bool_and(status IN ('done','failed','skipped'))` + `MAX(ts)`. Тогда один roundtrip вместо 50+.

---

### MEDIUM

#### MED-1. `DateTime` без timezone на новых таблицах

**Файлы:**
- `backend/app/models/analysis_event.py:36` — `ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)` — naive!
- `backend/app/models/outcome_snapshot.py:33, 36, 41` — `applied_at`, `followup_at`, `created_at` — все naive DateTime.
- `backend/app/models/page.py:19-20, 34` — `first_seen_at`, `last_seen_at`, `last_crawled_at` — naive.
- `backend/app/models/search_query.py:17-18, 23` — naive.

**Почему плохо:** Проект назвал initial migration `6f67765adf8f_initial_schema_with_timestamptz`, потом `bc745533ed8a_agent_run_timestamptz` — чинил именно эти грабли на `agent_runs`. Новые таблицы наступили на них снова. API вручную лепит "Z" (`activity.py:36`) → фронт считает UTC правильно, но внутренние операции вроде `ts >= cutoff` в `activity.py:127` сравнивают naive UTC с naive UTC — работает, пока все пишут через `datetime.utcnow()`. **Один вызов через `datetime.now()` (local time)** и дашборд покажет события "в будущем".

**Фикс:** Миграция `ALTER COLUMN ts TYPE timestamptz USING ts AT TIME ZONE 'UTC'` на всех пострадавших столбцах + заменить `datetime.utcnow()` на `datetime.now(timezone.utc)`.

---

#### MED-2. `Site.yandex_oauth_token` как Text без шифрования — отмечено комментарием, не реализовано

**Файл:** `backend/app/models/site.py:19`
```python
yandex_oauth_token: Mapped[str | None] = mapped_column(Text)  # encrypted at rest
```

Комментарий утверждает "encrypted at rest", но в коде нигде не видно ни SQLAlchemy TypeDecorator, ни Postgres pgcrypto. `collectors/tasks.py:137, 210, 336` читает его как plain string.

**Фикс:** Либо удалить комментарий (если расчёт на disk-level шифрование Postgres), либо завести `EncryptedType` (sqlalchemy-utils) с ключом из settings.

---

#### MED-3. Дублирующиеся рейт-лимит семафоры

**Файлы:**
- `backend/app/collectors/base.py:16` — `asyncio.Semaphore(3)` — shared для всех коллекторов в процессе
- `backend/app/collectors/site_crawler.py:189` — `asyncio.Semaphore(4)` — локальный в методе `crawl_and_store`

Первый семафор задуман для Yandex API; site_crawler это отдельная вещь (HTTP к сайту клиента). Хорошо, что они разные, но `base.py` semaphore используется даже для Metrica API и Wordstat (если активируется) — а там квоты другие.

**Фикс:** Вынести лимиты в `settings` как `YANDEX_WEBMASTER_RPS=3`, `YANDEX_METRICA_RPS=5` и разнести семафоры по коллекторам.

---

#### MED-4. `review_stats` делает 6 независимых запросов вместо одного

**Файл:** `backend/app/api/v1/review.py:149-211`

**Что:** Шесть последовательных `await db.execute(...)` для каждого агрегата. В aiohttp/asyncpg это sequential round-trips — 6 × ~2ms локально = 12ms минимум, в проде по сети — 60ms+.

**Фикс:** Собрать всё в один CTE:
```sql
WITH r AS (SELECT * FROM page_reviews WHERE site_id=$1),
     c AS (SELECT * FROM page_review_recommendations WHERE site_id=$1)
SELECT ... FROM r UNION ALL SELECT ... FROM c
```

Или `asyncio.gather(*six_queries)` — проще и даёт 3-5× speedup.

---

#### MED-5. Pool размеры на разные engines: главный `pool_size=5, max_overflow=10`, task-engine `pool_size=2, max_overflow=0`

**Файлы:**
- `backend/app/database.py:10-12` — главный (FastAPI)
- `backend/app/workers/db_session.py:35, 52` — per-task
- `backend/app/collectors/tasks.py:35` — per-task (дубликат логики)
- `backend/app/agents/tasks.py:31` — ещё один per-task

**Арифметика:** Постгрес по умолчанию `max_connections=100`. FastAPI-процесс держит до 15 соединений. Celery: каждый prefork-воркер может порождать 1 engine на инвокацию, каждый engine держит до 2 соединений — с `concurrency=4` и `worker_max_tasks_per_child=50` пиковое потребление ≤ 4×2=8 одновременных. Итого в нормальной работе: FastAPI (15) + Celery (8) + Celery beat (≤2) + внешние psql = **~25-30 соединений** — в пределах.

При `concurrency=8` и нескольких FastAPI workers (gunicorn `-w 4`): 4×15 + 8×2 = 76 → близко к 100. При 10x сайтов и росте FastAPI reply time (из-за CRIT-2 и HIGH-1) — соединения начнут висеть дольше, очередь upstream растёт → таймауты.

**Фикс:** Задокументировать текущий бюджет `max_connections`; когда будем масштабировать FastAPI — через PgBouncer (transaction mode).

---

#### MED-6. `crawl_all_sites_monthly` космический wall-time

**Файл:** `backend/app/collectors/tasks.py:306-320`

**Что:**
```python
for i, site in enumerate(sites):
    crawl_site.apply_async(args=[...], countdown=i * 60)
```
Каждый следующий сайт запускается на минуту позже → для 20 сайтов последний стартует через 20 минут. При crawl_site 2-5 минут это: 20-минутное окно стартов + 2-5 мин выполнения = 22-25 минут полного цикла. С ThreadPool max_workers=5 в deep_dive + fingerprint chain — потенциал ~час на 20 сайтов.

**Почему medium:** На 2-3 сайта сейчас неактуально, но когда будет 50+ — один-прокидный `countdown=i*60` станет упираться в `visibility_timeout=3600` и ретраиться.

**Фикс:** Celery `chord` / `group` с ограничением одновременных через `rate_limit="5/m"` на `crawl_site`.

---

#### MED-7. Отсутствие каскадов на FK в большинстве моделей

**Файлы:** проверь все `ForeignKey("sites.id")` в `backend/app/models/` — **ни одно не имеет `ondelete=`**.

Исключения (только в миграциях, не в ORM-моделях):
- `page_reviews`: `ondelete='CASCADE'` на `site_id`, `page_id` (миграция `f5c9a2d1e837`)
- `weekly_reports`: `ondelete='CASCADE'` на `site_id` (`b7d8e9f0c1a2`)
- `page_fingerprints`: `ondelete='CASCADE'` (`c8a1e4f9d702`)
- `query_intents`: `ondelete='CASCADE'` на `query_id` (`d9f2b4c8e501`)
- `target_clusters/queries`: `ondelete='CASCADE'` (`c4d5e6f7a8b9`)

Плюс рассинхрон: ORM на `Page.site_id` (page.py:14) `ForeignKey("sites.id")` без `ondelete`, но миграция initial (6f67765adf8f:136) тоже без onDelete. Для `pages`/`search_queries`/`issues`/`daily_metrics`/`alerts`/`tasks`/`agent_runs`/`snapshots`/`seasonality_patterns` удаление сайта = foreign_key_violation.

**Почему medium:** Сейчас удаления сайтов не делают (через API нет delete endpoint). Но тестовые сброса / cleanup скриптами — боль. А если появится soft-delete → hard-delete, пойдут ошибки.

**Фикс:** Пройтись миграцией: `ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE` на всех основных таблицах. Синхронизировать ORM.

---

#### MED-8. `String(255)` и `String(2048)` без обоснования

**Файлы:**
- `site.py:14-18` — `domain`, `display_name`, `yandex_webmaster_host_id`, `yandex_metrica_counter_id` — все `String(255)` (стандарт MySQL legacy, в Postgres разницы нет)
- `page.py:15-16` — `url`, `path` — `String(2048)` (URL max практически 2083, ок)
- `task.py:17, 32, 34` — `title: String(500)`, `target_query: String(1000)`, `target_page_url: String(2048)`

**Почему:** В Postgres `VARCHAR(N)` и `TEXT` имеют **одинаковую производительность** (внутренне это TOAST-enabled varlena), но `VARCHAR(N)` навязывает проверку на вставку. При изменении лимита требуется ALTER. Для URL это нормальное ограничение, для названий — overkill.

**Фикс:** Всё, что семантически "короткая метка без обоснованной границы" → `Text`. Оставить `VARCHAR(N)` там, где N продиктован протоколом (URL=2048).

---

#### MED-9. Нет pagination на ряде endpoints

**Файлы:**
- `backend/app/api/v1/sites.py:26-30` — `list_sites` без limit/offset
- `backend/app/api/v1/review.py:58-77` — есть `limit=50`, но нет offset
- `backend/app/api/v1/admin_ops.py:213-243` — `list_outcomes` с фиксированным `limit(100)`, без offset

Для текущей нагрузки (≤10 сайтов, ≤100 outcomes) — ок. На 1000+ — надо.

---

### LOW

#### LOW-1. Celery result backend = Redis → раздувание ключей

**Файл:** `backend/app/workers/celery_app.py:8`
```python
backend=settings.REDIS_URL,
```

**Что:** Каждая таска пишет результат в Redis с ключом `celery-task-meta-<task_id>`. Дефолтное expiry — 1 день (`result_expires=86400`). В коде это не переопределено — берётся Celery default. Для 1000 задач/день это 1000 ключей × ~500 байт = 500KB. Ничего страшного, но не используется (никто не делает `result.get()` по task_id).

**Фикс:** `result_backend=None` или `task_ignore_result=True` в `conf.update` — экономит Redis RAM и одну запись на таску.

---

#### LOW-2. `broker_transport_options.visibility_timeout=3600` — таски крупнее часа умрут

**Файл:** `backend/app/workers/celery_app.py:39`

**Что:** Если таска реально идёт >1 часа (а при отсутствии time_limit это возможно), Celery поверит что воркер её потерял, ретаскует другому воркеру → дубль.

**Фикс:** См. CRIT-1 — принудительный time_limit < visibility_timeout.

---

#### LOW-3. Frontend: слишком частые poll

**Файлы:**
- `frontend/components/dashboard/activity-feed.tsx:54` — 5s
- `frontend/components/dashboard/last-run-summary.tsx:50` — 4s
- `frontend/components/dashboard/overview.tsx:128` — 10s
- `frontend/app/priorities/page.tsx:50` — 5s
- `frontend/app/competitors/page.tsx:30` — 5s, затем 8s
- `frontend/app/onboarding/[siteId]/page.tsx:47` — 3s

**Арифметика одного открытого dashboard:**
- activity-feed (5s) → 12/min GET `/activity/current-run`
- last-run-summary (4s) → 15/min
- overview.activity-last (10s) → 6/min GET `/activity/last`
- overview.dashboard (60s) → 1/min

≈ **34 запросов/мин только от dashboard**. При 5 владельцах с открытыми вкладками — 170/мин. Каждый activity запрос тянет `reconcile_open_pipelines` (см. CRIT-2).

**Фикс:**
- Использовать `refreshInterval: prioritiesRunning ? 5_000 : 0` везде (уже частично сделано в priorities.tsx:58).
- Переключиться на WebSocket / SSE для activity-потока — один persistent connection вместо 12 GET/мин.

---

#### LOW-4. Onboarding polling 3s

**Файл:** `frontend/app/onboarding/[siteId]/page.tsx:47`

Неактивный onboarding экран опрашивает `/onboarding/state` каждые 3 секунды. При длинной паузе (владелец задумался) это 20 запросов/мин безрезультатных. Конечно, после `active` стадии polling прекращается, но до того — трафик.

---

#### LOW-5. Миграции: check constraints через string interpolation

**Файл:** `backend/alembic/versions/e1a2b3c4d5f6_onboarding_schema.py:96-100`
```python
"onboarding_step IN "
+ str(ONBOARDING_STEPS).replace("[", "(").replace("]", ")"),
```

**Почему:** `str(tuple)` даёт `('pending_analyze', 'confirm_business', ...)` — внезапно с одинарными кавычками (что PostgreSQL хочет). Трюк работает **случайно** — Python по дефолту печатает строки в одинарных кавычках. Если кто-то добавит значение с апострофом внутри — СQL injection / breakage.

**Фикс:** Прямая конкатенация с sql-escape или generated at build time.

---

#### LOW-6. `onboarding/restart` ставит `site.understanding = None` в колонке с `nullable=False`

**Файл:** `backend/app/api/v1/admin_ops.py:314`
```python
site.understanding = None
```

Но в модели (`site.py:56-58`): `understanding: Mapped[dict] = mapped_column(JSONB, nullable=False, default=lambda: {}, server_default="{}")`.

**Почему:** SQLAlchemy отправит `UPDATE sites SET understanding = NULL` — Postgres уронит с `NotNullViolation`. То есть это **баг**, который прячется под "onboarding restart никто не тестил после миграции `e1a2b3c4d5f6`".

**Фикс:** `site.understanding = {}`.

---

#### LOW-7. `agent_runs` без TTL / архивирования

Потенциально может расти по тысячам в день (агент-события). При стационарной нагрузке 100k/мес. Индекс `ix_agent_runs_site_id` есть; типичный запрос `WHERE site_id=$ ORDER BY created_at DESC LIMIT 20` будет тянуть всё → сортировать в памяти. Нужен `(site_id, created_at DESC)`.

---

## Индексы

| Таблица | Колонка(и) | Есть? | Нужен? | Запрос-источник |
| --- | --- | --- | --- | --- |
| `daily_metrics` | `(site_id, metric_type, date)` | Нет | **ДА** | `dashboard.py:38-42, 51-55`, `queries.py:56-60`, `admin_ops.py:141-146` |
| `alerts` | `(site_id, created_at)` | Нет | **ДА** | `dashboard.py:82-86` — +нужен переход к range-фильтру |
| `agent_runs` | `(site_id, completed_at DESC)` | Нет | ДА | `dashboard.py:92-94` |
| `agent_runs` | `(site_id, created_at DESC)` | Нет | ДА | `dashboard.py:299-304` |
| `issues` | `(site_id, status, created_at DESC)` | Нет | ДА | `dashboard.py:227-237` (paginated) |
| `search_queries` | `(site_id, is_branded)` | Нет | опционально | `competitors/tasks.py:113-115` |
| `analysis_events` | `(site_id, ts DESC)` | ДА (ix_analysis_events_site_ts) | — | activity endpoints |
| `analysis_events` | `(site_id, run_id)` | ДА | — | `_latest_stage_events` |
| `sites.target_config` | GIN | Нет | backlog | ожидаемые `@>` запросы |
| `analysis_events.extra` | GIN | Нет | backlog | — |
| `tasks` | `(site_id, status, priority DESC)` | Нет | опционально | никто пока не читает, но видно назначение |
| `outcome_snapshots` | `(site_id, applied_at)` | ДА | — | — |
| FK на `Page.site_id` | PARTIAL `WHERE in_index=true` | Нет | возможно | `competitors/tasks.py:569-572` |

---

## Миграции — риски

**Цепочка линейна** (проверено `grep -E "^revision\|^down_revision"`):
```
6f67765adf8f (initial) →
bc745533ed8a (agent_run_timestamptz) →
a7b3f2e1d5c6 (extend_task_and_page) →
c8a1e4f9d702 (page_fingerprints) →
d9f2b4c8e501 (intent_tables) →
f3b2a0e4c819 (site_profile_columns) →
f5c9a2d1e837 (page_reviews) →
a12b3c4d5e6f (priority_scores) →
b7d8e9f0c1a2 (weekly_reports) →
c4d5e6f7a8b9 (target_demand_map) →
d9e0f1a2b3c4 (target_config_draft) →
e1a2b3c4d5f6 (onboarding_schema) →
f7e8a9b0c1d2 (activity_and_outcomes) →
a1b2c3d4e5f6 (run_id) ← HEAD
```
**Одна голова** (`grep down_revision = None` → только `6f67765adf8f`). ✅

**Риски по отдельным миграциям:**

1. **`e1a2b3c4d5f6_onboarding_schema.py:60-95`** — добавляет 4 NOT NULL JSONB столбца в `sites` с `server_default`. В PG11+ это **fast-path** (метаданные меняются, данные не переписываются). Безопасно. ✅
2. **`c4d5e6f7a8b9_add_target_demand_map.py:28-36`** — NOT NULL `target_config` с `server_default='{}'` — аналогично, fast-path. ✅
3. **`f3b2a0e4c819_add_site_profile_columns.py:26-33`** — NOT NULL `vertical`, `business_model` с default. Safe. ✅
4. **`d9e0f1a2b3c4_add_target_config_draft.py:31-39`** — аналогично. ✅
5. **`a12b3c4d5e6f_add_priority_scores.py:37-41`** — `create_index` **без `CONCURRENTLY`** на `page_review_recommendations`. Таблица мелкая сейчас, но если взять 1M строк — лок экслюзивный на время построения. Во всех миграциях `create_index` идёт без `postgresql_concurrently=True` — см. `f5c9a2d1e837:56-63`, `c4d5e6f7a8b9:60-69`, etc.

**Фикс для будущих миграций на больших таблицах:** использовать `op.create_index(..., postgresql_concurrently=True)` + `op.execute("COMMIT")` чтобы выйти из транзакции.

6. **`bc745533ed8a_agent_run_timestamptz.py`** — тип-ALTER `DateTime → TIMESTAMPTZ`. На пустой БД ок, на 1M строк — полный перезапись. Уже применено, так что ретроспективно ок.

7. **Downgrade'ы есть** у всех миграций — это хорошо для rollback, но несколько из них не восстанавливают partial indexes (например `e1a2b3c4d5f6:163` восстанавливает `ix_target_clusters_site_growing` только в upgrade, без partial клаузы — хотя в downgrade он просто drop'ается). Проверять вручную при rollback не стоит, но формально down не возвращает точную схему.

8. **Нет мигрӑций для установки `ondelete`**. Связанный риск: см. MED-7.

---

## Quick wins (1-4 часа)

Оценка по impact/effort, отсортировано по ROI:

| # | Задача | Файлы | Время | Impact |
| --- | --- | --- | --- | --- |
| 1 | Добавить `task_soft_time_limit=300, task_time_limit=420` в Celery config | `backend/app/workers/celery_app.py:11-45` | 15 мин | Устраняет "зависания воркеров навсегда" (CRIT-1) |
| 2 | `CREATE INDEX CONCURRENTLY ix_daily_metrics_site_type_date ON daily_metrics (site_id, metric_type, date)` | новая миграция | 30 мин | 10-50× ускорение `dashboard`, `queries`, `admin_ops._baseline_metrics` (HIGH-1) |
| 3 | Починить `admin_ops.py:82-86` на range-фильтр + индекс `ix_alerts_site_created` | `backend/app/api/v1/dashboard.py:80-86` + миграция | 20 мин | Устраняет seqscan на `alerts` (HIGH-2) |
| 4 | Убрать N+1 в Webmaster upsert через `.returning()` | `backend/app/collectors/webmaster.py:166-194, 210-262` | 1-2 часа | 10× ускорение `collect_site_webmaster` (CRIT-3) |
| 5 | Truncate `content_text` на уровне SQL в competitors deep_dive | `backend/app/core_audit/competitors/tasks.py:569-572` | 15 мин | 5-10× уменьшение трафика (HIGH-3) |
| 6 | Заменить `site.understanding = None` на `= {}` | `backend/app/api/v1/admin_ops.py:314` | 2 мин | Чинит баг (LOW-6) |
| 7 | `task_ignore_result=True` (не используется result backend) | `backend/app/workers/celery_app.py:18` | 5 мин | Экономит Redis RAM (LOW-1) |
| 8 | Добавить индекс `(site_id, created_at DESC)` на `agent_runs` | миграция | 20 мин | `dashboard.py:92-94, 299-304` — убирает sort в памяти |
| 9 | `refreshInterval` на activity-feed повысить до 10s когда нет running | `frontend/components/dashboard/activity-feed.tsx:54` | 15 мин | -50% polling traffic (LOW-3) |
| 10 | `asyncio.gather(*six_queries)` в review.stats | `backend/app/api/v1/review.py:149-211` | 20 мин | 3-5× ускорение endpoint (MED-4) |

**Итого: ~4-5 часов закроет CRIT-1, CRIT-3, HIGH-1, HIGH-2, HIGH-3 и пару LOW-ов.**

---

## Бенчмарки — что рекомендовано замерить

Эти измерения дадут фактические цифры под принятие решений по оставшимся пунктам:

1. **EXPLAIN ANALYZE основных dashboard запросов при N=100k `daily_metrics` и N=500k `analysis_events`.**
   - `dashboard.py:33-43` — current-week traffic sum
   - `activity.py:105-128` — current-run fetch
   - `activity.py:309-319` — reconcile started_rows scan
   
   Инструмент: `docker exec postgres psql` + `pg_stat_statements`. Критерий: любой запрос >50ms на холодном кэше — кандидат на доп. индекс.

2. **Profiler Celery-таски `crawl_site` на реальном сайте (grandtour/grandtourspirit).**
   Записать wall time по фазам: sitemap fetch, N pages fetch, N pages upsert, fingerprint chain. Цель — найти где 14-17 секунд пайплайна расходуются.

3. **`pg_stat_activity` snapshot** во время `crawl_all_sites_monthly` или полного пайплайна. Сколько **реально** соединений занимают Celery-процессы vs FastAPI — проверка арифметики из MED-5.

4. **Redis memory footprint** через `INFO memory` перед и после запуска 100 задач. Оценка раздутия result backend без `task_ignore_result`.

5. **Load test** на `/sites/{id}/activity/current-run` с 20 RPS через `wrk` / `k6`. Проверка гипотезы что `reconcile_open_pipelines` убивает производительность (CRIT-2).

6. **`VACUUM VERBOSE sites`** раз в неделю — проверка bloat на таблице с активным UPDATE по JSONB (HIGH-4). Если dead tuples > 20% от live — argument для декомпозиции.

7. **pganalyze / pgbadger** на слоу-лог — но только после применения quick wins, иначе результат будет про те же узкие места.

---

## Дополнительные наблюдения

- **`database.py:7-13`** — `pool_recycle=300` (5 мин) довольно агрессивно; стандартные значения — 1800 (30 мин). При 5 сек idle на async TCP keepalive не срабатывает, соединение recycle'ится → ненужный overhead. Рекомендую `pool_recycle=1800` + `pool_pre_ping=True`.

- **`database.py:36-39`** — event listener `before_update` хардкодит `updated_at = now(utc)` для всего наследования Base, что дублирует логику `onupdate=` на TimestampMixin. Работает, но двойная запись на каждый flush.

- **`analysis_events.ts` получает default через `datetime.utcnow` из Python-слоя** (`analysis_event.py:36`), а миграция `f7e8a9b0c1d2:40-43` делает `server_default=text("CURRENT_TIMESTAMP")`. Расхождение: ORM `add()` всегда шлёт время с Python side, server_default срабатывает только на сыром INSERT. В репо консистентно Python — ок.

- **`site_crawler.py:189, 199`** — `asyncio.Semaphore(4)` + `asyncio.gather(...)` означает что все 50 URL из sitemap стартуют одновременно, просто ждут в семафоре. Это ок в async, но пиковая память по HTML-телам может прыгнуть. Пара `asyncio.Queue` + 4 consumer — чище.

- **В коде много `_run_async(coro)` (`collectors/tasks.py:21-28`, `core_audit/competitors/tasks.py:43-49`)** — в каждой Celery таске новый event loop + `loop.close()`. Корректно, но дублируется по 6+ файлам. Вынести в один хелпер (`workers/runner.py`).

- **`collectors/tasks.py:31-36`** — `_make_session` создаёт engine но **не диспозит его**. Это отличается от `workers/db_session.py:task_session` — именно здесь и может быть утечка соединений, описанная в комментарии `db_session.py:5-7`. Файл `collectors/tasks.py` должен использовать `task_session` как все остальные.

- **Frontend `traffic-chart.tsx:16`** — `refreshInterval: 300_000` (5 мин) — разумно для графика.

- **Весь `frontend` — `useSWR` везде**, это и плюс (deduping), и минус (дубли запросов на разных страницах). `settings/page.tsx:42` опрашивает `/health` каждые 30s — это ок.
