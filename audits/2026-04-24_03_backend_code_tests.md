# Backend Code & Tests — Audit
**Дата:** 2026-04-24
**Скоуп:** `/backend/app/` (230 файлов, ~30 740 строк) + `/backend/tests/` (63 файла, ~8 445 строк).

---

## TL;DR

- **Code health: B-.** Архитектура зрелая (async FastAPI + Celery + task_session), pipeline correctness после hardening sprint закрыт тестами. Но есть три системных запаха: (1) sync I/O внутри async через urllib/anthropic, (2) повсеместное `except Exception: noqa BLE001` (100 случаев) без retry-policy, (3) массовое дублирование бойлерплейта `_run(coro)` / `_make_session()` в 11-14 файлах.
- **Test coverage по критичным модулям:** ~450 тестов, но концентрация смещена к новым модулям (business_truth 107, demand_map 89, review 47, intent 44). Ноль тестов на: `collectors/*`, `services/issue_pipeline.py`, все `api/v1/*` (HTTP-endpoints), `agents/base.py` и конкретные агенты (search_visibility, technical_indexing, task_generator, validator), `workers/db_session.py` (хотя критичен для производства).
- **Топ-5 риск-зон:**
  1. **`_make_session()` в `collectors/tasks.py` + `agents/tasks.py` не вызывает `eng.dispose()`** — утечка connection pool-ов при worker_max_tasks_per_child=50 (см. CRITICAL #1).
  2. **`api/v1/chat.py:177` вызывает sync `client.messages.create()` внутри async handler** — блокирует event-loop FastAPI на время LLM-запроса (до 60 с).
  3. **Webmaster collector делает N+1 upsert+select в цикле по 500 запросов** (`webmaster.py:173-195`) — ~1000 round-trip на одну синхронизацию.
  4. **Ни у одного Celery task нет `task_time_limit` / `soft_time_limit`** — зависшая SERP/crawl задача потребляет worker навсегда.
  5. **Нулевое тестовое покрытие коллекторов и API-handlers** — регрессии в парсинге Яндекс.Вебмастера и в dashboard SQL полетят незамеченными.

---

## Метрики

| Метрика | Значение |
|---|---|
| Строк кода backend (`app/`) | 30 740 |
| Строк тестов (`tests/`) | 8 445 |
| Соотношение тестов к коду | 0,27 (умеренно) |
| Файлов в `app/` | 230 |
| Файлов в `tests/` | 63 |
| Тестовых функций (`def test_`) | ~450 |
| Celery-задач (`@celery_app.task`) | 32 |
| `except Exception` (все) | 100 |
| из них с `# noqa: BLE001` | 46 |
| TODO / FIXME / XXX / HACK | 4 (XXX в prompts — не считается) |
| `_run(coro)` дублей | 11 файлов |
| `_make_session()` / `task_session` дубли | 14 файлов используют паттерн, 2 не зовут `dispose()` |
| `datetime.utcnow()` (deprecated в 3.12) | 16 |
| f-string в `logger.*()` вызовах | 9 |
| `selectinload` / `joinedload` во всём app | **0** |
| `response_model=` на роутах | 29 из 90 (32 %) |
| Sync urllib внутри Celery worker | 3 места (yandex_serp, suggest, deep_dive) |
| `client.messages.create` sync внутри async handler | 1 (`api/v1/chat.py:177`) |
| `assert True` / пустых стабов в тестах | 0 (хорошо) |

---

## Code-смелы (по убыванию severity)

### CRITICAL

#### C1. Утечка connection pool-ов: `_make_session()` без `engine.dispose()`
**Файлы:** `backend/app/collectors/tasks.py:31-36`, `backend/app/agents/tasks.py:27-32`.

```python
def _make_session():
    ...
    eng = create_async_engine(settings.DATABASE_URL, pool_size=2, max_overflow=0)
    return async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
```

В `workers/db_session.py:28-41` автор уже задокументировал проблему и сделал правильный `task_session()` с `finally: await eng.dispose()`. Но `collectors/tasks.py` и `agents/tasks.py` **не мигрированы** и продолжают использовать старый паттерн — каждый вызов создаёт AsyncEngine без освобождения. При `worker_max_tasks_per_child=50` это до 100 открытых connection-пулов на цикл жизни воркера, из них 2 postgres-коннекта каждый. Под нагрузкой быстро упираешься в `max_connections`.

Таких вызовов в `collectors/tasks.py`: 6 (`94`, `112`, `125`, `193`, `249`, `329`). В `agents/tasks.py`: 5 (`51`, `73`, `117`, `148`, `198`).

**Фикс:** заменить `_make_session()` на уже существующий `task_session()` / `task_session_factory()`. 2-4 часа работы.

#### C2. Sync Anthropic-клиент внутри async FastAPI handler
**Файл:** `backend/app/api/v1/chat.py:177` (`response = client.messages.create(...)`).

`get_client()` возвращает синхронный `anthropic.Anthropic` (`agents/llm_client.py:57`). В `call_with_tool` / `call_plain` это ок — они вызываются из sync Celery-воркера. Но `/sites/{id}/chat` — это async FastAPI handler, и sync-вызов SDK внутри него блокирует весь event-loop (все другие запросы ждут) на 5-60 секунд каждого LLM-вызова. При одновременных чатах 2-3 пользователей дашборд замораживается.

**Фикс:** либо использовать `anthropic.AsyncAnthropic` отдельным клиентом для async-кода, либо обернуть в `asyncio.to_thread(client.messages.create, ...)`. 30 минут работы.

#### C3. Отсутствие `task_time_limit` на всех Celery-задачах
**Файл:** `backend/app/workers/celery_app.py:11-45`.

Celery-конфиг задаёт `worker_cancel_long_running_tasks_on_connection_loss=False`, `visibility_timeout=3600`, `worker_max_tasks_per_child=50`, но **ни одна** таска не имеет `time_limit` / `soft_time_limit`. SERP discovery (`competitors_discover_site`) в худшем случае ходит в 30 запросов × 10,5 с = 5,25 минуты, плюс deep_dive и crawl — могут зависнуть на DNS/TLS-проблемах. Нет hard-limit → worker висит неограниченно.

**Фикс:** добавить в celery.conf `task_time_limit=300` (5 мин hard), `task_soft_time_limit=240` (4 мин graceful). Критичные задачи (SERP, crawl) переопределить декоратором.

### HIGH

#### H1. N+1 в Webmaster collector
**Файл:** `backend/app/collectors/webmaster.py:173-195`.

Цикл по 500 запросам делает на каждой итерации: (а) `pg_insert(SearchQuery).on_conflict_do_update`, (б) `select(SearchQuery.id).where(...)` — чтобы получить id для DailyMetric. Итого 1000 round-trip к postgres за один webmaster-прогон. Затем внутри идёт ещё до 7 апсертов DailyMetric на запрос (для дневной разбивки) — ~3500 round-trip.

**Фикс:** `RETURNING id` в upsert, собирать (query_text, id) батчами по 100, `bulk_insert_mappings` для DailyMetric. 3-4 часа.

#### H2. Блокирующий sync urllib в Celery с `time.sleep` в горячем цикле
**Файлы:** `backend/app/collectors/yandex_serp.py:227,232`, `backend/app/core_audit/demand_map/suggest.py:192`, `backend/app/core_audit/competitors/deep_dive.py:192`.

Celery-воркер синхронный, так что блокировать событийный цикл некого, но:
- `yandex_serp._get` в while-poll loop `time.sleep(0.7)` × до 15 попыток = 10,5 сек чистой блокировки одного рабочего слота.
- `discovery.py:194` запускает `ThreadPoolExecutor(max_workers=4)` поверх sync `fetch_serp` — normal паттерн, но `_post/_get` всё равно блокируются на TCP timeout 15 сек без retry.
- `deep_dive._fetch_html` — тоже sync stdlib urllib. В `competitors/tasks.py:492` это оборачивается в ThreadPoolExecutor(5).

Комбинация приемлема, но это хрупкий дизайн: любая сетевая задержка × 4-5 параллельных слотов = потерянная минутка worker-а. Лучше мигрировать на async httpx с timeout и правильным retry.

**Фикс:** переписать `yandex_serp.fetch_serp` на async `httpx.AsyncClient` с `asyncio.sleep`. 4-6 часов (плюс интеграционные тесты).

#### H3. `encrypted at rest` — мёртвый комментарий, шифрования нет
**Файл:** `backend/app/models/site.py:19`: `yandex_oauth_token: Mapped[str | None] = mapped_column(Text)  # encrypted at rest`.

В `config.py` задан `ENCRYPTION_KEY: str = ""`, но нигде в коде нет импорта `cryptography`, нет `Fernet`, нет любой обёртки шифрования (`grep -rn "Fernet\|encrypt\|decrypt"` ничего не находит). OAuth-токены Яндекса хранятся в БД **как есть**. Пакет `cryptography>=44.0.0` есть в `pyproject.toml:20`, но ни разу не импортирован.

**Фикс:** либо убрать комментарий (честный подход), либо реализовать EncryptedString TypeDecorator на базе Fernet. См. также audits/security.

#### H4. Sync `urllib` I/O в async Celery-оркестраторе через ThreadPoolExecutor
**Файл:** `backend/app/core_audit/competitors/tasks.py:492-499`.

Внутри `async def _inner()` запускается `ThreadPoolExecutor(max_workers=5)` и `fut.result()` блокируется в цикле. Хотя Celery-задача сама sync, `_inner()` запускается в asyncio event-loop через `_run(coro)`. `fut.result()` внутри async-функции даёт блокирующий wait в loop-е, который был специально для него создан — технически работает, но смешивает парадигмы и душит возможность параллельной async-работы (если бы мы попутно await-или другие stages).

**Фикс:** `await asyncio.to_thread(analyze_competitor_site, ...)` или `asyncio.gather` + async-версия `analyze_page`.

#### H5. Agent run-record не финализируется при раннем exit
**Файл:** `backend/app/agents/base.py:105-119`.

Если `load_data` возвращает `None`, `_complete_run` вызывается. Но если `load_data` сам бросит исключение ДО `run_record.started_at`, fallback в `except Exception` (`base.py:161`) пишет `run_record.status = "failed"` но код `await db.flush()` — нет `commit`. Если дальнейший `get_db()` middleware rollback-ает transaction (при 500-ответе), запись потеряется. Плюс если первая секция (сам `db.execute(select(Site))`) упадёт, `run_record` ещё не создан и в agent_runs не будет ни one record.

**Фикс:** atomic `run_record = AgentRun(status="running")` вне try/except, плюс `record_run_failure(exc)` в outer `finally`. 1-2 часа.

#### H6. Pydantic v2 смешан с v1-наследием и `: Any`-спамом
**Файл:** `backend/app/collectors/base.py:66,125`, `backend/app/core_audit/registry.py:24,44`, `backend/app/core_audit/onboarding/chat_agent.py:237,287,363,413` и др.

В коде 16 функций принимают `: Any` — в том числе для `params`, `profile`, `caller`, `geo`, `overlay`. Часть оправдана (`caller` — инъекция для тестов), но `params: Any` в HTTP-методе `base.py:66,125` — это просто не типизировано. Аналогично `profile: Any` в `draft_profile/builder.py:117` — это фактически `DraftProfile | None`, надо прописать.

**Фикс:** уточнить 8-10 самых подозрительных `Any`. 2 часа.

#### H7. Повсеместное `except Exception: # noqa: BLE001` (100 мест)
**Файлы:** везде. Распределение:
- `core_audit/competitors/tasks.py` — 10 голых except-ов.
- `core_audit/demand_map/tasks.py` — 5.
- `core_audit/onboarding/*.py` — 4.
- `agents/tasks.py` — 7 (в инлайн try/except для каждого site-а в "all" тасках).
- `collectors/tasks.py` — 5.

Политика "fail-open на что угодно" даёт устойчивость но душит диагностику: многие catch-блоки пишут warning и возвращают default. В результате реальные баги (типа `KeyError` в парсинге Webmaster-ответа) маскируются под "данные пустые".

**Фикс:** ввести типизированные исключения (`CollectorError`, `DiscoveryError`, `LLMError`), ловить их, а общий `Exception` пропускать наружу (retry на уровне Celery обработает). За 1 день можно переработать ключевые места: `collectors/*`, `competitors/tasks.py`, `agents/tasks.py`.

### MEDIUM

#### M1. 11× дубль `def _run(coro)` — 55 строк копипаста
**Файлы:** `agents/tasks.py:17`, `collectors/tasks.py:21`, `core_audit/business_truth/tasks.py:21`, `core_audit/draft_profile/tasks.py:25`, `core_audit/review/tasks.py:24`, `core_audit/report/tasks.py:20`, `core_audit/priority/tasks.py:17`, `core_audit/demand_map/tasks.py:43`, `core_audit/onboarding/tasks.py:43`, `core_audit/competitors/tasks.py:43`, `fingerprint/tasks.py:23`, `intent/tasks.py:20`.

Везде ровно одно и то же:

```python
def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
```

**Фикс:** один `app.workers.async_bridge.run_sync(coro)` и импорт везде. Плюс можно добавить exception-logger. 1 час.

#### M2. `datetime.utcnow()` deprecated в Python 3.12
**16 вызовов** в `collectors/site_crawler.py:223,224,238,239`, `core_audit/outcomes/tasks.py:45,46,98`, `models/outcome_snapshot.py:33,41`, `models/analysis_event.py:36`, `api/v1/admin_ops.py:57,193,281`, `api/v1/activity.py:124`, `api/v1/review.py:139`.

С 3.12 выдаёт `DeprecationWarning`. В 3.14 будет удалено. Наивные datetime без tzinfo — баги с DST и сравнением с tzaware-полями (`TimestampMixin` пишет tzaware).

**Фикс:** `datetime.now(timezone.utc)` везде. 30 минут sed + визуальная проверка.

#### M3. Нулевое использование `selectinload` / `joinedload`
Во всей кодовой базе app (30k LOC) **ноль** использований eager-loading. Грепу `joinedload|selectinload` совпадения есть только в одном комментарии `models/tenant.py:17`. SQL-запросы через `select(Model)` + `.scalars()` — ок, пока модель не лазит по relationship-ам в сериализации. Потенциальный риск: N+1 при итерации `site.tenant` в dashboard / sites endpoint (но пока тенант один).

**Фикс:** не срочно, но при выходе на multi-tenant обязательно.

#### M4. f-string внутри `logger.warning/error/info` — нет lazy-evaluation
**9 мест:** `collectors/site_crawler.py:96,104,186,246`, `agents/task_generator.py:325,384,427,440,502`.

```python
logger.warning(f"Sitemap fetch failed at {sitemap_path}: {e}")
```

Если логи на уровне INFO отключены, форматирование всё равно выполнится. Правильно: `logger.warning("Sitemap fetch failed at %s: %s", sitemap_path, e)`.

**Фикс:** мелкая, sed-автомат справится.

#### M5. `get_db` dependency коммитит за handler-а — неявная транзакционная семантика
**Файл:** `backend/app/database.py:42-49`.

```python
async def get_db():
    async with async_session() as session:
        try:
            yield session
            await session.commit()   # <-- после handler-а
        except Exception:
            await session.rollback()
            raise
```

В хэндлерах уже много `await db.commit()` вручную (admin_ops, admin_demand_map, review). Двойной commit не падает, но делает контракт "кто что коммитит" смазанным. В `dashboard.py:288` даже есть комментарий `# get_db() dependency handles commit`, но рядом другие хэндлеры вызывают commit прямым текстом.

**Фикс:** сделать `get_db` read-only (только rollback/close), все write-хэндлеры коммитят сами. Плюс добавить отдельный `get_db_rw` если не хочется трогать API. 2-3 часа + регресс-тесты.

#### M6. Hardcoded site-descriptions внутри LLM-prompt
**Файл:** `backend/app/agents/task_generator.py:538-560`.

```python
known = {
    "xn----jtbbjdhsdbbg3ce9iub.xn--p1ai": (
        "Южный Континент — экскурсионное бюро в Сочи с 2014 года..."
    ),
    "grandtourspirit.ru": (
        "Grand Tour Spirit (GTS) — премиальный клуб..."
    ),
}
```

Это должно жить в `sites.understanding` / `business_truth`, не в Python-коде. При добавлении нового сайта — нужно коммитить в репо и деплоить.

**Фикс:** перенести в БД (`sites.understanding.narrative_ru` уже есть). 1 час.

#### M7. Response_model редко используется (29/90 endpoint-ов)
Возвращаются сырые `dict[str, Any]`. При изменении DB-схемы клиент (frontend) ломается молча. Отсутствует OpenAPI-документация для 60 % API.

**Фикс:** добавить Pydantic-схемы в `schemas/` для хотя бы для `/dashboard/*`, `/queries/*`, `/activity/*`, `/business_truth/*`. 1-2 дня.

#### M8. `datasketch`, `scikit-learn`, `trafilatura`, `pymorphy3`, `scipy` — тяжёлые зависимости
Общий размер dev-образа backend на Python 3.12 с этим набором — ~600 МБ (scipy+numpy+sklearn базовые бинарники). Если что-то из списка используется только в одном модуле (fingerprint), стоит рассмотреть опциональные extras.

### LOW

#### L1. `TODO` в priority/service.py
**Файл:** `backend/app/core_audit/priority/service.py:443` — `# TODO: persist source_finding_id on PageReviewRecommendation`. Единственный "живой" TODO в коде. Нужно либо сделать, либо заккрыть тикетом.

#### L2. `pass` в except-блоках
**Файл:** `backend/app/core_audit/competitors/tasks.py:412,639` — `except Exception: pass  # best-effort`. Комментарий "best-effort — already logging the real error above" присутствует, но всё равно лучше хотя бы `log.debug("swallowed: %s", exc)`.

#### L3. Лог-хигиена: ✓/✗ в сообщениях
**Файл:** `backend/app/collectors/tasks.py:159,161,178,181`. `logger.info("✓ %s: %s", ...)`. Немного не-ASCII в прод-логах, но не критично.

#### L4. Неиспользуемые зависимости
- `structlog>=24.4.0` — импортируется 0 раз. Везде `logging.getLogger`.
- `cryptography>=44.0.0` — 0 импортов. "Encrypted at rest" комментарий ложный (см. H3).
- `python-telegram-bot>=21.0` — в `telegram/__init__.py` только пустой файл.
- `factory-boy>=3.3.0` в dev-deps — в тестах нет.

**Фикс:** либо начать использовать structlog для structured logs (рекомендую), либо выпилить из deps.

#### L5. `# type: ignore` отсутствует
Хорошая новость: grep по `# type: ignore` ничего не нашёл — нет костылей по типизации.

#### L6. Нет pre-commit / CI-линта (судя по отсутствию конфигов)
В `backend/` нет `.flake8`, `.ruff.toml`, `mypy.ini`, `pre-commit-config.yaml`. Качество кода держится только на дисциплине. Добавление ruff за час даст автоматическую проверку всего, что перечислено в L3-L4 выше.

---

## LLM / AI код

### Плюсы
- `agents/llm_client.py:46-58` — правильный singleton, настраиваемый `timeout=120`, `max_retries=2`. Cost tracking в `_compute_cost` идёт со всеми 4 статьями (input/output/cache_read/cache_write).
- Prompt caching на system-промптах через `cache_control: ephemeral` (`llm_client.py:103`).
- Streaming через `client.messages.stream` (`llm_client.py:108`) — обходит Vercel proxy timeout.
- Prompt hashing для идемпотентности (`llm_client.py:74`).

### Минусы
- **Нет монитора budget**. `settings.AI_MONTHLY_BUDGET_USD=10.0`, но нигде нет аггрегации "сколько уже потрачено в этом месяце → стоп". Агент может уйти в бесконечный retry и сжечь бюджет.
- **Нет rate-limiter-а Anthropic-вызовов.** Семафор `_semaphore = asyncio.Semaphore(3)` есть только для Яндекса (`collectors/base.py:16`). Для Claude — пусто.
- **Fallback logic в `_parse_output` проглатывает ValidationError** и возвращает `AgentOutput(issues=[], summary="Parse error")` — пустой вывод маскируется под "нет проблем на сайте". Лучше помечать флагом `parse_error=True` и показывать это в UI.
- **`api/v1/chat.py` вычисляет cost вручную:** `cost = (usage.input_tokens / 1_000_000) * 1.0 + ...` (`chat.py:191`) — должна вызываться та же `_compute_cost` из llm_client.py, иначе при смене цен расходится.

**Фикс:** (1) добавить `AgentRun.month_cost_usd_cumulative` + guard-rail перед call-ом; (2) вынести `_compute_cost` как публичный `llm_client.compute_cost`; (3) различать parse-error от empty-result в схеме.

---

## Тесты

### Что покрыто хорошо

| Модуль | Тестов | Оценка |
|---|---|---|
| `business_truth/*` (10 файлов) | 107 | **Отлично**. DTO, matcher, page_intent, query_picker_v2, query_selector, reconciler, auto_vocabulary, traffic_reader, understanding_reader, rebuild_task. 57 из них упомянутые в context — наверное только `test_matcher + test_page_intent + test_query_selector + test_reconciler`, остальные 50 добавились позже. |
| `demand_map/*` (10 файлов) | 89 | **Отлично**. Cartesian expander, Suggest fetcher (с мок-fetcher для HTTP-изоляции), guardrails, geo compatibility, relevance, rescoring, LLM expansion, tasks, quality, persistence. |
| `review/*` (4 файла) | 47 | **Хорошо**. Checks (pure python), LLM enricher + verifier, hash utils. |
| `intent/*` (4 файла) | 44 | **Хорошо**. classifier (regex heuristics, 27 тестов), page_classifier, coverage, decisioner phase D. |
| `competitors/*` (4 файла) | 29 | **Хорошо**. Pipeline terminal states (16 тестов — все инварианты hardening sprint-а), run_id isolation, shadow_mode, discovery_task_paths. |
| Стeps events (`test_stage_events.py` + `test_activity_log.py` + `test_task_messages.py`) | 11 | **Отлично для цели**. Покрыты критичные инварианты hardening sprint-а — crawl/webmaster/demand_map emit started+terminal, reconciliation, non-terminal rejection. |
| `fingerprint/*` | 22 | **Хорошо**. Repository (транзакционный intg-тест на БД) + unit-тесты для хэширования. |
| `priority/*` | 29 | **Хорошо**. Scorer, aggregator, scorer_phase_d. |
| `draft_profile/*` | 33 | **Хорошо**. Builder, confidence, extractors, tasks. |
| `report/*` | 19 | **Ок**. Diagnostic, helpers. |
| `golden/test_parity.py` | 3 | Слабо. Только 3 parity-теста. Файлы `fixtures.py` и `capture_baseline.py` — инфраструктура, но параметров мало. |

### Дыры (по приоритету)

1. **`backend/app/collectors/webmaster.py` — 0 тестов.** Критичнейший модуль — парсинг Yandex Webmaster API (который меняется). Сейчас если Я.изменит формат `indicators` (list vs aggregate), мы узнаем через несколько дней по жалобе пользователя. Нужны минимум: happy-path фикстурный JSON → аггрегаты в БД; `host_not_loaded`; смешанный ответ (некоторые запросы имеют daily breakdown, некоторые aggregate).
2. **`backend/app/collectors/metrica.py` — 0 тестов.**
3. **`backend/app/collectors/site_crawler.py` — 0 тестов.** Regex-extractors `_TITLE_RE`, `_META_DESC_RE`, `_H1_RE`, `_LINK_RE` — purely testable. Сейчас hidden.
4. **`backend/app/collectors/yandex_serp.py` — 0 тестов.** Хотя в `demand_map/test_suggest.py` похожий паттерн с fetcher-injection есть — можно копировать.
5. **`backend/app/services/issue_pipeline.py` — 0 тестов.** Главный оркестратор: detection → validation → storage. Ветви `skip_validation=True`, `suppressed/review/published` не проверены.
6. **`backend/app/services/operating_mode.py` — 0 тестов.** Guard-rail для "readonly" / "recommend".
7. **`backend/app/agents/base.py` — 0 тестов.** Базовый класс для всех агентов, покрывает ветку "Site not found", parse-error, failure recording.
8. **`backend/app/agents/search_visibility.py` + `technical_indexing.py` + `task_generator.py` + `validator.py` — 0 тестов.** Вся AI-детекция без unit-покрытия. Можно мокать `call_with_tool` (как в `demand_map/test_suggest.py` с fetcher-injection).
9. **`backend/app/api/v1/*.py` — 0 HTTP-тестов.** Ни один endpoint не имеет ни одной e2e / http-testclient проверки. Crypто-проблема в `sites.py` (encrypted=false) не будет поймана любым тестом. Аналогично `/dashboard/*`, `/chat/*`, `/review/*`, `/admin/*`.
10. **`backend/app/workers/db_session.py` — 0 тестов.** Но критичен — при утечке engine в staging это будет обнаружено только в проде.
11. **`backend/app/fingerprint/tasks.py` + `core_audit/outcomes/tasks.py` + `core_audit/health/tasks.py` — 0 тестов.** Именно Celery-задачи, которые идут в беспрекословный прод.

### Качество тестов

- **Transactional isolation:** `tests/conftest.py:32-57` — per-test Session в rollback-только транзакции. Инженерно грамотно. Минус: engine создаётся per-test, а не session-scoped — ~30-50 мс накладных на каждый тест × 450 = ~15-20 сек суммарно. При >1000 тестов ощутимо.
- **Fixtures:** `test_tenant`, `test_site` — минимальные, чистые. Good.
- **Mock-style:** `MagicMock/mock.patch` — 67 вхождений; средне. В `business_truth/*` и `demand_map/*` используется dependency-injection (`fetcher=`, `caller=`) — более надёжный паттерн чем monkeypatch.
- **Параметризация:** в `intent/test_classifier.py` — 27 тестов, много похожих ("classify как info_dest", "classify как local_geo"). Можно параметризировать для 5× компактности.
- **Плейсхолдеров (`assert True`) нет.** Это радует — все тесты имеют реальные assert-ы.
- **async hygiene в тестах:** `pytest-asyncio>=0.24`, `asyncio_mode = "auto"` — правильно.
- **Шифрование в test_site.py:** `yandex_oauth_token` не задан — в проде это реальный токен, в тестах не тестируется. Если реализуют шифрование, тесты продолжат работать без изменений (и не поймают регрессию).
- **Коммерческая тестируемость:** в `conftest.py:26` `DATABASE_URL` default читает dev-postgres (`db` хост) — тесты НЕ изолированы от реальной БД. Для CI потребуется отдельный postgres-контейнер. Не нашёл `docker-compose.test.yml` или GitHub Actions workflow — CI скорее всего ещё не настроен.

---

## Quick wins (можно за 2-4 часа)

1. **Миграция `_make_session()` → `task_session()` в `collectors/tasks.py` и `agents/tasks.py`.** 11 вызовов, механическая замена. Убирает leak connection pool-ов (CRITICAL #1). Регресс-тесты не нужны, поведение идентично.
2. **Добавить в `celery_app.conf.update(...)` — `task_time_limit=300`, `task_soft_time_limit=240`.** 2 строки кода, 0 риска (таски сейчас и так короткие).
3. **Заменить `datetime.utcnow()` → `datetime.now(timezone.utc)` во всех 16 местах.** sed-автомат + ручной просмотр 5 диффов.
4. **Консолидировать 11× `def _run(coro)` в `app/workers/async_bridge.py:run_sync`.** 1 час + импорты.
5. **Добавить ruff-конфиг `[tool.ruff]` в `pyproject.toml` с правилами BLE001, TRY, LOG.** Автоматически закроет 80% проблем из CODE-раздела.
6. **Обернуть `api/v1/chat.py:177` в `await asyncio.to_thread(client.messages.create, ...)`.** 2 строки, снимает блокировку event-loop.
7. **Убрать/документировать мёртвый `# encrypted at rest` в `site.py:19`.** Одна строка, техдолг визуально погашен.
8. **Вынести hardcoded site-descriptions из `task_generator.py:538-560` в БД.** 30 минут кода + миграция.
9. **Добавить smoke-тест на `collectors/site_crawler.py` regex-extractors.** `_TITLE_RE`, `_H1_RE`, `_META_DESC_RE` — pure-функции, мокать ничего не надо. 1 час.
10. **Добавить тест на `collectors/webmaster.py:collect_and_store` с mock WebmasterCollector (инжектом fake `fetch_popular_queries`).** 2-3 часа, закрывает главную слепую зону.

---

## Что сделано хорошо

- **`workers/db_session.py`** — эталонный паттерн. Автор разобрался с asyncpg event-loop isolation, написал docstring, объясняющий WHY. `task_session_factory()` отдельно для bulk-операций — грамотно. Хотел бы видеть такой уровень во всех новых модулях.
- **Pipeline-correctness invariants в `competitors/test_pipeline_terminal_states.py` (16 тестов)** — покрывают ВСЕ edge-cases hardening sprint-а: happy-path, failure, skip, concurrent runs, reconciliation, legacy-aliases. Это лучший test-файл в проекте.
- **BusinessTruth модуль (107 тестов)** — почти каждая публичная функция имеет unit-тест. `test_matcher.py` для матчинга русской морфологии покрывает сложные кейсы stem-коллизий ("судно" vs "судак").
- **Dependency injection в `demand_map/suggest.py:fetch_suggestions` через `fetcher=` параметр.** Позволяет тестировать оркестратор без network I/O. Тот же паттерн в `competitors/discovery.py:discover_competitors` — приходит `fetcher=fetch_serp`.
- **`agents/llm_client.py`** — streaming, prompt caching, cost tracking, Vercel-proxy workaround — всё продумано и задокументировано в docstring-ах.
- **Feature-flags правильно сделаны:** `USE_DEMAND_MAP_ENRICHMENT`, `USE_TARGET_DEMAND_MAP`, `USE_BUSINESS_TRUTH_DISCOVERY` в `config.py` — рассчитаны на shadow-mode deployment.
- **Celery resilience-config:** `broker_connection_retry_on_startup`, `worker_cancel_long_running_tasks_on_connection_loss=False`, `visibility_timeout=3600`, `socket_keepalive` — видно что прошли через реальный UNBLOCKED-инцидент и задокументировали (`workers/celery_app.py:21-29`).
- **Schema-валидация на границе LLM:** `_parse_output` в `agents/base.py:183` + Pydantic `AgentOutput` — правильная защита от hallucinated-JSON.
- **Advisory lock для идемпотентности:** `competitors/tasks.py:38-40` + `pg_try_advisory_lock` — двойной клик "Разведка" не запускает два параллельных SERP-сбора.
- **`TimestampMixin` + SQLAlchemy event-listener** (`database.py:36`) — чистая реализация updated_at.
- **`webmaster.py:HostNotLoadedError`** — типизированное исключение, которое пробрасывается мимо retry (`base.py:107`). Правильный паттерн — больше таких надо.

---

## Итоговая оценка

| Категория | Оценка | Комментарий |
|---|---|---|
| Архитектура | **A-** | async-first FastAPI, Celery-offload правильно отделён, feature-flags, advisory-locks. |
| Качество кода | **B-** | Дубли `_run/_make_session`, sync-в-async в 1 месте, магические числа, 16× deprecated `utcnow`, нет линтера. |
| Exception discipline | **C+** | 100 `except Exception`, 46 с `noqa: BLE001`. Политика "fail-open" спорна, диагностика страдает. |
| LLM-код | **B+** | Caching, streaming, cost-tracking — на уровне. Нет budget-limiter-а, разночтение cost-formula в `chat.py` vs `llm_client.py`. |
| DB hygiene | **B** | pg_insert/on_conflict правильный, advisory locks, но N+1 в webmaster, нулевой eager-loading, leak в collectors/agents tasks. |
| Celery | **B-** | Retries настроены, resilience есть, но нет time-limit-ов, idempotency только через advisory lock. |
| Тесты для нового кода | **A-** | business_truth, demand_map, review, competitors — покрыто отлично, инварианты зафиксированы. |
| Тесты для старого кода | **D** | collectors, api/v1, services, agents/* — полностью без покрытия. Регрессии в этих слоях долетят до прода. |
| Production-readiness | **B** | Celery resilience сделана грамотно; шифрования токенов нет; OpenAPI-схема неполная (32%). |

Общая оценка: **B-**. Код в хорошем направлении, новые модули написаны аккуратно, старые слои (collectors, api) требуют обновления дисциплины.

---

## Приоритет следующих 7 дней

1. **День 1-2 (Quick wins):** пункты #1, #2, #3, #6, #7 из Quick wins — закрытие connection leaks + time-limits + async-hygiene. ~4 часа суммарно. **Устраняет все 3 CRITICAL за полдня.**
2. **День 3-4:** тесты для `collectors/webmaster.py` + `collectors/site_crawler.py`. Mock-фикстуры из реальных Yandex-ответов. Закрывает главную слепую зону (Top-5 риск #5).
3. **День 5:** HTTP-тесты для `api/v1/sites.py` + `api/v1/dashboard.py` + `api/v1/chat.py` через `httpx.AsyncClient(app=app)`. Минимум 10 smoke-тестов на каждый роут (создание, получение, update, 404).
4. **День 6:** Ruff config + CI pipeline (GitHub Actions: `pytest + ruff check + mypy --strict` на `models/`, `schemas/`). Плюс docker-compose.test.yml с изолированным Postgres.
5. **День 7:** Консолидация `_run(coro)` (M1) + рефактор `_make_session()` (дополнение к C1, для полной зачистки).
