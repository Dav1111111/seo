# Tech Debt & Dependencies Audit
**Дата:** 2026-04-24
**Аудитор:** Claude (Opus 4.7 @ 1M)
**Репозиторий:** `/Users/davidgevorgan/Desktop/yandex-growth-tower`
**Возраст кодовой базы:** 9 дней (первый коммит 2026-04-15 → последний 2026-04-24)
**Объём:** backend ~30 740 LOC Python (147 файлов), frontend ~5 232 LOC TS/TSX (33 файла), 663 MB node_modules, 49 test-файлов, 14 миграций Alembic.

---

## TL;DR

Общий долг: **LOW-MEDIUM**. Код молодой (< 2 недель), активно эволюционирует, но уже собралась заметная инфраструктурная рыхлость: невостребованные security-фичи, нарушенная миграция datetime на timezone-aware, мёртвые зависимости, хардкод tenant/site под мульти-тенант. Блокеров «горим сейчас» нет — блокеры для следующего мульти-тенантного спринта есть.

### Топ-3 вещи которые горят (под мульти-тенант)
1. **Нет аутентификации, только `ADMIN_API_KEY`, который захардкожен в `docker-compose.yml`.** `SECRET_KEY / ENCRYPTION_KEY / JWT_SECRET` объявлены в `app/config.py:35-37`, но нигде не используются. Добавить мульти-тенантность без auth — значит открыть чужие сайты по UUID.
2. **Хардкоженные `DEFAULT_TENANT_SLUG = "default"` + `_ensure_default_tenant` в `backend/app/api/v1/sites.py:13-23` + `NEXT_PUBLIC_SITE_ID` fallback в `frontend/lib/api.ts:5`.** Весь API ходит в единственный тенант; переход на N тенантов потребует миграции ручек + dependency injection tenant_id. 7 моделей уже имеют `tenant_id`, но ни один API endpoint его не фильтрует (проверил `grep -rn tenant_id backend/app/api/v1` — только `sites.py`).
3. **Незавершённая миграция `datetime.utcnow` → `datetime.now(timezone.utc)`.** 37 мест мигрировано (коммит `6fba003 Week 1 Item 5`), ещё 16 остались, включая `default=` в моделях (`analysis_event.py:36`, `outcome_snapshot.py:33,41`). Смешанная naive/aware арифметика — источник +/- 3ч багов, которые всплывут как раз при внедрении пользовательских таймзон.

### Топ-3 quick win (< 1 дня каждый)
1. Вынести `_require_admin` (сейчас дублируется в 3 файлах: `admin_ops.py:30`, `admin_demand_map.py:47`, `business_truth.py:22`) в общий модуль `api/v1/deps.py`.
2. Дропнуть 5 back-compat shim-ов `backend/app/intent/{classifier,decision_tree,page_classifier,safety_layer,standalone_test}.py` — они только реэкспортируют из `core_audit/*`. 10 оставшихся импортов легко переключить.
3. Вынести дублированный `formatRelativeTime(utcIso)` (4 копии в `activity-feed.tsx:32`, `last-run-summary.tsx:29`, `overview.tsx:382`, `priorities/page.tsx:30`, все одинаковые ~8 строк) в `frontend/lib/utils.ts`.

---

## Tech Debt

### Критические (блокирует мульти-тенант)

#### C1. Отсутствие аутентификации/авторизации
- `app/config.py:35-39`: `SECRET_KEY`, `ENCRYPTION_KEY`, `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_EXPIRY_HOURS` объявлены, но `grep -rn "JWT_SECRET\|ENCRYPTION_KEY\|SECRET_KEY" backend/app --include="*.py"` возвращает только сам config.py — их никто не читает. Нет JWT-middleware, нет пользовательских сессий, нет ролей.
- Единственный auth-механизм: статический `X-Admin-Key` в шапке, и тот дублируется в трёх местах (см. M3).
- **Docker compose хардкодит** `ADMIN_API_KEY: "admin_dev_secret_2026"` (строка 93 `docker-compose.yml`) — этот ключ в git, открыт в проде, если этот compose-файл когда-то деплоится.
- **Под мульти-тенант**: без owner-check любой пользователь, знающий UUID сайта, получит полный доступ.

#### C2. Хардкоженный single-tenant
- `backend/app/api/v1/sites.py:13` — `DEFAULT_TENANT_SLUG = "default"`, комментарий `# Temporary: hardcoded tenant for Phase 1 (replaced by auth in Phase 10)`. Функция `_ensure_default_tenant` (строки 16-23) вызывается в `list_sites` и `create_site`.
- `frontend/lib/api.ts:5` — `SITE_ID = process.env.NEXT_PUBLIC_SITE_ID || "1e11339f-c87e-4742-9d38-6f79463b0d16"`. 24 метода `api.ts` дефолтятся на `siteId = SITE_ID` — если SiteProvider не загрузился, всё идёт в этот конкретный сайт. `site-context.tsx:39` уже пробует починить через `localStorage`, но дефолт всё равно жив.
- Весь API слой (`/api/v1/*`) принимает `site_id` в URL без проверки принадлежности к тенанту — классический IDOR, если убрать single-tenant допущение.

#### C3. Незавершённая миграция timezone-aware datetime
- Состояние: 37 вхождений `datetime.now(timezone.utc)` + 16 вхождений `datetime.utcnow`. Pеально «горячие» оставшиеся места:
  - `backend/app/models/analysis_event.py:36` — `default=datetime.utcnow` в колонке, которая пишется в каждой задаче.
  - `backend/app/models/outcome_snapshot.py:33,41` — то же самое.
  - `backend/app/collectors/site_crawler.py:223-239` — 4 вхождения в хот-пасе краулера.
  - `backend/app/core_audit/outcomes/tasks.py:45-98` — cutoff-логика для follow-up, сравнивает `datetime.utcnow()` с полями из БД → если БД отдаст aware → `TypeError: can't compare offset-naive and offset-aware datetimes`.
  - `backend/app/api/v1/activity.py:29,124` + комментарий в `api/v1/activity.py:29`: *"ts is stored naive UTC in Postgres (datetime.utcnow). Append Z..."* — эту зависимость от naive-хранения знает только frontend; если рандомно мигрируем модель — фронт начнёт рисовать время в +3ч.
- Таблицы созданы как `TIMESTAMPTZ` (миграция `6f67765adf8f_initial_schema_with_timestamptz.py`), а модели объявлены как `DateTime` без `timezone=True` (0 моделей используют `DateTime(timezone=True)`). SQLAlchemy-алхимия рассинхронизирована с БД.

---

### Высокие

#### H1. Back-compat shim модули в `app/intent/`
- 5 файлов просто реэкспортируют из `app/core_audit/*`: `classifier.py`, `decision_tree.py`, `page_classifier.py`, `safety_layer.py`, `standalone_test.py`. В каждом явная маркировка *"Back-compat shim"*, размер < 2 KB.
- `grep -rn "from app.intent.(classifier|decision_tree|page_classifier|safety_layer|standalone_test)" backend` — только 10 импортов. Удалимо за час, но живёт уже неделю (с коммита `Phase E`).
- Одновременно `app/intent/*` содержит **реальные** модули (`coverage.py`, `models.py`, `service.py`, `decisioner.py`, `tasks.py`, `llm_classifier.py`, `taxonomy.py`, `enums.py`) — слой не умирает, но границы с `core_audit` размыты.

#### H2. Дубль `_require_admin`
- `api/v1/admin_ops.py:30`, `api/v1/admin_demand_map.py:47`, `api/v1/business_truth.py:22` — три идентичные копии функции проверки `X-Admin-Key`. При ротации ключа / смене на JWT придётся править в трёх местах. Должно жить в `api/v1/deps.py` (файл уже существует).

#### H3. Frontend типизация
- 31 вхождение `apiFetch<any>` в `frontend/lib/api.ts` (из 38 методов). Типы ответов частично описаны inline в методах (онбординг, competitors), остальное — `any`.
- `frontend/components/dashboard/business-truth-card.tsx`, `overview.tsx` — большие стейты с implicit `any` через `data?.items ?? []`.
- В отдельных местах — явный `any[]` (`reports/page.tsx:38`).
- Нет общего файла `frontend/lib/types.ts` с DTO, хотя backend уже имеет `app/schemas/` и `app/core_audit/*/dto.py`. Дублирование «типов, которых нет» между backend и frontend — и те и другие знают форму, никто её не документирует.

#### H4. Логирование: `structlog` в depencies, но не используется
- `pyproject.toml:19` — `"structlog>=24.4.0"`.
- `grep -rln "import structlog" backend/app` → 0 совпадений. Все 63 файла логируют через стандартный `logging.getLogger(__name__)`. Либо убрать зависимость, либо мигрировать на structlog ради JSON-логов (что актуально для прода).

#### H5. Незадействованные security-зависимости
- `pyproject.toml:20` — `"cryptography>=44.0.0"`. `grep -rn "cryptography\|Fernet" backend/app` → 0. Зависимость весом ~8 MB + C-расширения не нужна.
- `pyproject.toml:21` — `"python-telegram-bot>=21.0"`. Папка `backend/app/telegram/` содержит только `__init__.py`; `grep "from telegram"` → 0. Либо допилить Telegram-интеграцию (в env есть `TELEGRAM_BOT_TOKEN`), либо дропнуть.
- `pyproject.toml:22` — `"python-multipart>=0.0.18"`. `grep "File\(\|UploadFile\|Form\("` → 0. FastAPI не требует multipart для JSON-роутов.

---

### Средние

#### M1. Нет ARCHITECTURE.md / CONTRIBUTING.md / root README
- В `/` вообще нет README проекта. Есть два файла `GTS_SEO_REMAINING.md` и `GTS_SEO_ROADMAP.md` — это SEO-аудит **клиентского** сайта «Grand Tour Spirit» (ручная работа, не документация Growth Tower). Должны жить в `audits/` или в отдельном репо с клиентскими отчётами.
- `frontend/README.md` — дефолтный create-next-app (см. содержимое: ссылки на Vercel, никакой проектной информации).
- `frontend/AGENTS.md` и `frontend/CLAUDE.md` — 2 строки, warning о том, что «Next.js не тот, что знает модель». Полезно, но это пометка для LLM, не документация.
- `backend/` — нет README вообще.
- Для онбординга нового разработчика: единственный источник правды — коммиты и комментарии в коде.

#### M2. `celerybeat-schedule` закоммичен (16 KB)
- `backend/celerybeat-schedule` — бинарный runtime-файл celery beat, обновляется на каждый запуск. Трекается (`git ls-files` показал).
- Добавить `backend/celerybeat-schedule` в `.gitignore` + `git rm --cached`.
- Аналогично `frontend/tsconfig.tsbuildinfo` (211 KB) — не трекается, но и в `.gitignore` нет, значит залетит в следующий `git add .` + `frontend/.pytest_cache/` — тоже только у бэкенда должно быть.

#### M3. `.env.example` и `.env` рассинхронизированы
- `.env.example` перечисляет `ANTHROPIC_API_KEY`, `YANDEX_OAUTH_TOKEN`, Telegram, JWT, но **не содержит**:
  - `ADMIN_API_KEY` — реально используемый auth-ключ (читается в 3 ручках).
  - `ANTHROPIC_BASE_URL` — Cloudflare proxy URL (config.py:22).
  - `YANDEX_SEARCH_API_KEY`, `YANDEX_CLOUD_FOLDER_ID` — для SERP.
  - `USE_DEMAND_MAP_ENRICHMENT`, `USE_TARGET_DEMAND_MAP`, `USE_BUSINESS_TRUTH_DISCOVERY` — feature flags.
  - `AI_MONTHLY_BUDGET_USD`.
- Новый разработчик по `.env.example` получит нерабочую систему. Обновить → 15 минут.

#### M4. Branding inconsistency (фронт)
- «полный анализ» vs «Pipeline» vs `pipeline` как stage-name — один концепт, 3 названия:
  - `activity-feed.tsx:23`: `pipeline: "полный анализ"`
  - `traffic-chart.tsx:27`: *"Нет данных. Запустите сбор через Pipeline."* (английское слово в русском UI)
  - `last-run-summary.tsx:67,101`: `pipelineEvt` + `"Идёт анализ…"`
  - `activity-feed.tsx:108`: *"Нажми «Запустить полный анализ»..."*
  - `overview.tsx:143`: *"Быстрый анализ запущен..."* + отдельная кнопка «Быстрый анализ».
- «Быстрый анализ» и «Полный анализ» — два разных пайплайна? В activity-feed единый stage `pipeline`. Путано для пользователя.
- «анализ / прогон / run» — `last-run-summary.tsx:101` *"Идёт анализ"*, `:87` переменная `lastTs` описывает run, `competitors/page.tsx:311` читает `applied_at`. Выбрать один термин.

#### M5. Мёртвая Telegram-интеграция
- `backend/app/telegram/__init__.py` — единственный файл, пустой.
- В `.env.example`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_DEFAULT_CHAT_ID`.
- В `config.py:31-32`: соответствующие settings.
- В зависимостях: `python-telegram-bot>=21.0` (9 MB).
- Либо выпилить следы, либо включить в roadmap (ожидаемо: на weekly report уходят алерты).

#### M6. Дубль relative-time форматтера (frontend)
- Одинаковый 8-строчный хелпер в `components/dashboard/activity-feed.tsx:31-37`, `last-run-summary.tsx:28-33`, `overview.tsx:381-388`, `app/priorities/page.tsx:29-34`. Текст *"только что / мин назад / ч назад / д назад"* копипастится.
- Также `/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z"` — логика «если timestamp без tz-маркера, добавить Z» встречается в 5+ местах (`overview.tsx`, `last-run-summary.tsx`, `activity-feed.tsx`, `competitors/page.tsx:311`, `priorities/page.tsx`). Это та самая зависимость «БД отдаёт naive UTC», см. C3 — и она размазана по всему фронту.

#### M7. Большие файлы (кандидаты на расщепление)
Backend top-5:
- `app/api/v1/admin_demand_map.py` — 860 LOC
- `app/core_audit/competitors/tasks.py` — 682 LOC
- `app/agents/task_generator.py` — 612 LOC
- `app/profiles/tourism/seed_templates.py` — 562 LOC (скорее всего просто данные, OK)
- `app/core_audit/onboarding/chat_agent.py` — 501 LOC

Frontend top-3:
- `app/competitors/page.tsx` — 537 LOC (всё UI + fetch + состояния в одном файле)
- `lib/api.ts` — 478 LOC (плоский объект, без группировки по доменам)
- `app/onboarding/[siteId]/page.tsx` — 434 LOC

---

### Низкие / Косметика

#### L1. 4 TODO/FIXME во всём коде
- `app/core_audit/priority/service.py:443` — `# TODO: persist source_finding_id on PageReviewRecommendation` (реальный тикет).
- `app/core_audit/competitors/opportunities.py:73` — `"XXX"` в placeholder цене (ложное срабатывание).
- `app/agents/task_generator.py:365` — `НЕ используй плейсхолдеры типа XXX` в промте (ложное срабатывание).
- `app/profiles/tourism/commercial_factors.py:26` — `+7 (XXX)` в описании фактора (ложное срабатывание).
- Реальный TODO один. Для 30 KLOC это очень чисто.

#### L2. Почти нет закомментированного кода
- `grep -rn "^\s*#\s*\(def\|class\|if\|for\|return\|import\|from\)" backend/app` → 3. Frontend: мало `//` мёртвых строк.

#### L3. 6 `console.error` без unified error reporting
- Все в `frontend/app/onboarding/[siteId]/page.tsx` и `reports/[id]/page.tsx`. Ошибки теряются. Для MVP OK, для платформы — нужен Sentry/аналог.

#### L4. 11 `print(` в backend — ложное срабатывание
- Grep поймал `upsert_fingerprint(...)`, `get_fingerprint(...)`, `invalidate_fingerprint(...)` etc. Собственно `print` как дебаг-вывод — 0.

#### L5. 65 `# noqa:` и 1 `# type: ignore`
- Большинство `# noqa: BLE001` (bare except) и `# noqa: E712` (сравнение с True в SQLAlchemy-фильтрах — идиоматично). Ничего критического, но стоит проверить, все ли `except Exception` оправданы.

---

## Dependencies

### Backend (Python) — `backend/pyproject.toml`, зависимости ranged (`>=`)

| Пакет                      | Объявлено    | Latest (на 2026-04) | Дельта | Риск   | Заметка |
|----------------------------|--------------|---------------------|--------|--------|---------|
| fastapi                    | >=0.115.0    | ~0.118              | minor  | Low    | Используется |
| uvicorn[standard]          | >=0.32.0     | ~0.34               | minor  | Low    | Используется |
| sqlalchemy[asyncio]        | >=2.0.36     | ~2.0.42             | patch  | Low    | Активно |
| asyncpg                    | >=0.30.0     | ~0.30+              | —      | Low    | Используется |
| psycopg[binary]            | >=3.2.0      | ~3.2+               | —      | Low    | Для Alembic |
| alembic                    | >=1.14.0     | ~1.14+              | —      | Low    | Используется |
| celery[redis]              | >=5.4.0      | ~5.4                | —      | Low    | Используется, custom tuning |
| redis                      | >=5.2.0      | ~5.2+               | —      | Low    | Используется |
| httpx                      | >=0.28.0     | ~0.28+              | —      | Low    | Используется |
| anthropic                  | >=0.40.0     | ~0.73               | major  | **Medium** | Sonnet 4.6 API шёл через >= 0.55+ версии; рекомендую pin до апгрейда |
| pydantic                   | >=2.10.0     | 2.13+               | minor  | Low    | Используется широко |
| pydantic-settings          | >=2.7.0      | ~2.13               | minor  | Low    | config.py |
| python-dotenv              | >=1.0.0      | 1.2+                | minor  | Low    | OK |
| **structlog**              | >=24.4.0     | ~25                 | —      | **Low (unused)** | **Не используется, убрать** |
| **cryptography**           | >=44.0.0     | ~45                 | —      | **Low (unused)** | **Не используется, убрать** |
| **python-telegram-bot**    | >=21.0       | ~21.7               | —      | **Low (unused)** | **Не используется, убрать или довести** |
| **python-multipart**       | >=0.0.18     | ~0.0.20             | —      | **Low (unused)** | **Не используется, убрать** |
| datasketch                 | >=1.6.5      | ~1.6.5              | —      | Low    | Используется в fingerprint/minhash.py |
| trafilatura                | >=1.12.2     | ~2.0 (breaking)     | major  | **Medium** | Есть major обновление, но 1.x работает |
| pymorphy3                  | >=2.0.2      | 2.0.3+              | patch  | Low    | Used via lazy import |
| pymorphy3-dicts-ru         | >=2.4.417150 | —                   | —      | Low    | Справочник |
| scikit-learn               | >=1.4.0      | ~1.6                | minor  | Low    | HashingVectorizer в fingerprint/ngrams.py |
| numpy                      | >=1.26.0     | ~2.1 (breaking)     | major  | **Medium** | numpy 2.x — ABI break, может сломать scipy/sklearn, проверить в CI |
| scipy                      | >=1.12.0     | ~1.14               | minor  | Low    | csr_matrix в fingerprint/ngrams.py |

Dev deps: pytest, pytest-asyncio, httpx, respx, factory-boy — все в рабочих версиях, OK.

**Замечания:**
- Версии указаны через `>=`, нет lockfile для backend (нет `poetry.lock` / `pdm.lock` / `uv.lock` / `requirements.lock`). При пересборке Docker-образа можно подтянуть случайный major — unstable. Рекомендую добавить `uv.lock` (uv сейчас де-факто стандарт) или закрепить в `requirements.txt`.
- Локальный `backend/.venv` собран под Python 3.10 и содержит огрызок (только pydantic + sqlalchemy). Проект требует `>=3.12`. `pip list --outdated` не запускал — venv нерелевантен. Запустить вручную: `docker compose exec backend pip list --outdated`.

### Frontend (Node) — `frontend/package.json`, package-lock.json v3

| Пакет                       | package.json   | В node_modules | Latest    | Дельта    | Риск    | Заметка |
|-----------------------------|----------------|----------------|-----------|-----------|---------|---------|
| next                        | 16.2.3 (pin)   | 16.2.3         | ~16.2.x   | —         | Medium  | Next 16 релиз свежий — возможны regression, см. `frontend/AGENTS.md` *"This is NOT the Next.js you know"* |
| react                       | 19.2.4 (pin)   | 19.2.4         | 19.2.x    | —         | Low     | — |
| react-dom                   | 19.2.4 (pin)   | 19.2.4         | 19.2.x    | —         | Low     | — |
| @base-ui/react              | ^1.4.0         | 1.4.0          | ~1.4      | —         | Low     | shadcn primitives |
| class-variance-authority    | ^0.7.1         | 0.7.1          | 0.7       | —         | Low     | — |
| clsx                        | ^2.1.1         | 2.1.1          | 2.1       | —         | Low     | — |
| lucide-react                | ^1.8.0         | 1.8.0          | 1.8+      | —         | Low     | — |
| recharts                    | ^3.8.1         | 3.8.1          | 3.x       | —         | Low     | Используется только в `traffic-chart.tsx` |
| shadcn                      | ^4.2.0         | 4.2.0          | 4.2       | —         | **Low (unused)** | **CLI-пакет, в рантайме не импортируется. Должен быть в devDependencies** |
| swr                         | ^2.4.1         | 2.4.1          | ~2.4      | —         | Low     | Основной data-fetcher |
| tailwind-merge              | ^3.5.0         | 3.5            | 3.x       | —         | Low     | — |
| tw-animate-css              | ^1.4.0         | 1.4            | 1.x       | —         | Low     | Imported в `globals.css` |
| eslint                      | ^9             | 9.39.4         | ~9.x      | —         | Low     | — |
| eslint-config-next          | 16.2.3 (pin)   | 16.2.3         | = next    | —         | Low     | — |
| typescript                  | ^5             | 5.9.3          | ~5.9      | —         | Low     | — |
| tailwindcss                 | ^4             | 4.2.2          | ~4.x      | —         | Low     | v4 новый, OK |
| @tailwindcss/postcss        | ^4             | n/a            | ~4.x      | —         | Low     | — |
| @types/node                 | ^20            | 20.x           | 22 LTS    | minor     | Low     | Dev-only, OK |
| @types/react, react-dom     | ^19            | 19.x           | 19.x      | —         | Low     | — |

**Замечания:**
- Все major-версии свежайшие (Next 16, React 19, Tailwind 4). Следующий апгрейд — когда выйдут 17/20/5 соответственно, не скоро.
- `npm audit` не запускал (не осмелился трогать без ваших флагов). **Запустить вручную**: `cd frontend && npm audit --production` (read-only).
- `package-lock.json` (v3) присутствует, ОК. Lockfile bundle не проверял — 505 пакетов в node_modules, 663 MB.

### Docker base images — `docker-compose.yml`, `Dockerfile`

| Service        | Image              | Tag            | Latest      | Risk | Заметка |
|----------------|--------------------|----------------|-------------|------|---------|
| db             | postgres           | 16-alpine      | 17.x        | Low  | Postgres 16 — LTS-подобен, поддержка до 2028 |
| redis          | redis              | 7-alpine       | 7.4 / 8     | Low  | 7-alpine свежий |
| backend        | build (python:3.12-slim) | 3.12-slim | 3.13        | Low  | 3.12 стабилен |
| celery-worker  | build (shared)     | —              | —           | Low  | То же |
| celery-beat    | build (shared)     | —              | —           | Low  | То же |
| frontend       | **node:20-alpine** (dev compose) + **node:20-alpine** (Dockerfile) | 20-alpine | 22 LTS | Low | Рассинхрон с `@types/node:^20`. Stable, но LTS 22 доступен |
| nginx          | nginx              | alpine         | 1.27        | **Medium** | **Floating `:alpine` тег — непредсказуемый апгрейд при pull**. Пиннить на `1.27-alpine` |

**Замечание:** `docker-compose.yml` — **dev compose**: `--reload` у uvicorn, `npm install && npm run dev` у фронта, `ADMIN_API_KEY` захардкожен. Нет prod-варианта (`docker-compose.prod.yml`). В проде либо тот же dev compose (плохо), либо отдельно — нужно уточнить.

### Дубли / потенциально неиспользуемое

| Что | Где | Вердикт |
|-----|-----|---------|
| `structlog` | pyproject.toml | не используется — дропнуть или внедрить |
| `cryptography` | pyproject.toml | не используется — дропнуть |
| `python-telegram-bot` | pyproject.toml | не используется — дропнуть или допилить telegram-интеграцию |
| `python-multipart` | pyproject.toml | не используется — дропнуть |
| `shadcn` (CLI) | frontend/package.json dependencies | в deps, а не devDeps — переложить |
| `clsx` + `tailwind-merge` | lib/utils.ts | обе нужны, это стандартный shadcn-паттерн |

**Тяжёлые зависимости frontend:**
- `recharts` ^3 — 200+ KB gzipped, импортируется только в `traffic-chart.tsx`. Если нужен один линейный чарт — можно заменить на uPlot / ~2 KB inline-SVG. Для текущего MVP — норм.
- `@base-ui/react` — shadcn-новая primitive-library (заменила Radix UI). Компактна, модульная — OK.

---

## TODO / FIXME сводка

| Сторона     | TODO | FIXME | XXX | HACK | Всего | Real actionable |
|-------------|------|-------|-----|------|-------|-----------------|
| Backend     | 1    | 0     | 3   | 0    | 4     | 1 |
| Frontend    | 0    | 0     | 0   | 0    | 0     | 0 |

Единственный настоящий: `app/core_audit/priority/service.py:443 # TODO: persist source_finding_id on PageReviewRecommendation`. Остальное — `XXX` в placeholder'ах текста.

**Классификация actionable TODO:** correctness (потеря traceability между finding → recommendation). Severity — medium, не блокер, не касается мульти-тенанта.

---

## Миграции Alembic

- **14 миграций**, одна цепочка, один head → `a1b2c3d4e5f6_add_run_id_to_analysis_events.py`. Никакого split-head дивергенс-долга, никаких нескольких `down_revision = None`.
- Цепочка: `6f67 → bc74 → a7b3 → c8a1 → d9f2 → f3b2 → f5c9 → a12b → b7d8 → c4d5 → d9e0 → e1a2 → f7e8 → a1b2`.
- Все миграции используют `sa.DateTime(timezone=True)` — в БД tz-aware. Модели при этом не используют `timezone=True` нигде (см. C3).
- В миграциях нет явных `# TODO` или временных таблиц.
- `alembic/env.py` — 1249 байт, стандартный. `script.py.mako` — дефолтный. Нет naming convention для индексов/FK → при автогенерации Alembic может давать разные имена для одной и той же колонки.

---

## Тестовый долг (high-level)

49 test-файлов vs 147 source-файлов. Покрыто хорошо:
- `business_truth/` — 9 тестов на модуль целиком (активно развивается).
- `demand_map/` — 9 тестов.
- `draft_profile/` — 5.
- `intent/`, `competitors/`, `review/`, `priority/`, `report/`, `fingerprint/` — от 2 до 4 тестов каждый.
- В корне: `test_stage_events.py`, `test_activity_log.py`, `test_task_messages.py` — hardening sprint инварианты.

Покрыто слабо / нет вообще:
- `app/agents/` (12 файлов, `task_generator.py` = 612 LOC) — нет тестов. Только `tests/golden/test_parity.py` косвенно.
- `app/collectors/` — нет тестов вообще. Критично: `webmaster.py`, `metrica.py`, `yandex_serp.py` — вся интеграция с Яндексом без regression-тестов (хотя есть `respx` в dev-deps).
- `app/core_audit/activity.py` (390 LOC) — нет отдельного test-файла, только `tests/test_activity_log.py` в корне тестов.
- `app/core_audit/outcomes/tasks.py` — нет тестов (risk: datetime.utcnow сравнения).
- `app/core_audit/health/tasks.py` — нет тестов.
- `app/api/v1/*.py` — нет integration-тестов на ручки (тест через TestClient / respx). При рефакторе под мульти-тенант будет страшно.
- `app/workers/celery_app.py` — beat schedule 17 задач, ни одна не проверяется тестом.

Detail — в отдельном `backend-tests` аудите.

---

## Состояние документов в корне

| Файл | Размер | Актуальность | Вердикт |
|------|--------|--------------|---------|
| `GTS_SEO_REMAINING.md` | 10.5 KB | Апрель 17, ручной аудит клиентского сайта Grand Tour Spirit | Не документация проекта. Перенести в `audits/` или в отдельный клиентский репо |
| `GTS_SEO_ROADMAP.md` | 26 KB | Апрель 16, SEO-план для того же сайта | То же |
| `frontend/README.md` | 1.5 KB | Дефолтный create-next-app | Переписать или удалить |
| `frontend/AGENTS.md` | 327 B | Полезный warning про Next 16 | OK |
| `frontend/CLAUDE.md` | 11 B | `@AGENTS.md` | OK |

Нет ни одного `README.md` для самого проекта Growth Tower, нет `ARCHITECTURE.md`, нет `CONTRIBUTING.md`, нет `DEPLOY.md`. Это блокер для любого нового человека, не Давида.

---

## Инфраструктурные заметки

- **Docker images в dev-compose**: `backend`, `celery-worker`, `celery-beat` собираются из одного `./backend/Dockerfile`. Dockerfile делает `pip install --no-cache-dir -e .` — каждый пересбор тянет все зависимости без кэша слоя. Улучшимо: разделить `COPY pyproject.toml` + `pip install` до `COPY . .`, чтобы слой с deps не инвалидировался на каждое изменение кода.
- **Frontend dev-compose** делает `npm install && npm run dev` в контейнере поверх volume — это 663 MB `node_modules` в named-volume `/app/node_modules`. Медленный старт, но рабочий.
- **Nginx `resolver 127.0.0.11 valid=10s`** — хорошо, Docker DNS + tcp keepalive для celery.
- **Healthchecks есть** для db, redis, celery-worker. У backend/frontend — нет. Для backend: `/api/v1/health` уже есть (см. `api/v1/health.py`) → добавить в compose.

---

## Roadmap remediation (фазы по 1-2 недели)

### Фаза 1 — Quick wins (3-5 дней, можно в текущий спринт)
1. Дропнуть неиспользуемые зависимости: `structlog`, `cryptography`, `python-telegram-bot`, `python-multipart`. Если `structlog` нужен для прод-логов — внедрить сразу за ~1 день.
2. Вынести `_require_admin` в `api/v1/deps.py`, удалить 3 дубля.
3. Вынести `formatRelativeTime` + `iso+Z helper` в `frontend/lib/utils.ts`, заменить 4 копии.
4. Дропнуть 5 `app/intent/*` shim-файлов + переключить 10 импортов на `core_audit`.
5. Обновить `.env.example` (+ ADMIN_API_KEY, ANTHROPIC_BASE_URL, YANDEX_SEARCH_API_KEY, feature flags).
6. Добавить `celerybeat-schedule`, `.pytest_cache/`, `*.tsbuildinfo` в `.gitignore`, `git rm --cached backend/celerybeat-schedule`.
7. Зачистить корень: перенести `GTS_SEO_*.md` в `audits/` или `clients/grand-tour-spirit/`.
8. Пиннить `nginx:alpine` → `nginx:1.27-alpine`.

### Фаза 2 — Timezone closure (3-5 дней)
1. Дозакончить `datetime.utcnow → datetime.now(timezone.utc)` в оставшихся 16 местах.
2. Поменять все модели на `DateTime(timezone=True)` (Postgres и так хранит timestamptz — только SQLAlchemy-аннотация должна догнать).
3. Переписать naive-aware сравнения в `outcomes/tasks.py` и `collectors/site_crawler.py`.
4. Вынести `iso + "Z"` костыль на фронте — когда API начнёт возвращать явный `Z`-suffix.
5. Обновить комментарий в `activity.py:29` либо убрать costyl вместе с багом.
6. Покрыть тестом — timezone-boundary test (выполняется в tz=America/Los_Angeles, проверяет что follow-up cutoff не уплывает).

### Фаза 3 — Auth + мульти-тенант прелюдия (1-2 недели)
1. Внедрить JWT auth (использовать уже объявленные `JWT_SECRET`, `JWT_EXPIRY_HOURS`). Минимум: /login endpoint + middleware + `Depends(current_user)`.
2. Добавить `tenant_id` в `Depends(current_user)` dependency.
3. Пройти по `api/v1/*` и везде где есть `site_id`-parameter — проверять `site.tenant_id == current_user.tenant_id`. ~20 ручек.
4. Уничтожить `_ensure_default_tenant` из `sites.py`, `DEFAULT_TENANT_SLUG` из констант, `NEXT_PUBLIC_SITE_ID` из `api.ts`.
5. Мигрировать `ADMIN_API_KEY` → role `admin` в JWT. Убрать хардкод из `docker-compose.yml`.
6. Добавить integration-тесты на IDOR (user1 пытается прочитать site user2 → 404).

### Фаза 4 — Технический долг второго порядка (1 неделя)
1. Добавить `uv.lock` или `requirements.lock` для детерминированной сборки backend.
2. Добавить integration-тесты на все `api/v1/*.py` ручки (респкс + тестовый клиент).
3. Типизировать `frontend/lib/api.ts`: вынести DTO в `lib/types.ts`, сгенерировать из OpenAPI (`openapi-typescript`).
4. Унифицировать терминологию: `pipeline` | «полный анализ» | «быстрый анализ» — выбрать один словарь.
5. Добавить `README.md` / `ARCHITECTURE.md` / `CONTRIBUTING.md` в корень. Описать: бизнес-модель, модули, как запустить, куда класть миграции, как дебажить celery.
6. Добавить prod docker-compose (без `--reload`, без volumes поверх кода, с secrets).
7. Разбить `api/v1/admin_demand_map.py` (860 LOC) и `core_audit/competitors/tasks.py` (682 LOC) на логические файлы.

### Фаза 5 — Observability + hardening (1 неделя, после auth)
1. Внедрить structured logging (structlog уже в deps или дропнуть) — JSON-логи в прод.
2. Добавить Sentry / аналог для frontend console.error и backend exceptions.
3. Backend healthcheck в compose (`/api/v1/health`).
4. Фронт: централизованная обработка ошибок API (сейчас throw Error без классификации).
5. Naming convention в Alembic `env.py` (`naming_convention`) чтобы автоген давал стабильные имена FK/idx.
6. `npm audit --production` в CI + `pip-audit` в CI.

---

## Полезные file:line ссылки

- Auth hardcode: `docker-compose.yml:93`
- Single-tenant coupling: `backend/app/api/v1/sites.py:13-23`, `frontend/lib/api.ts:5`
- datetime долг: `backend/app/models/analysis_event.py:36`, `backend/app/models/outcome_snapshot.py:33,41`, `backend/app/collectors/site_crawler.py:223-239`, `backend/app/core_audit/outcomes/tasks.py:45-98`, `backend/app/api/v1/activity.py:29,124`
- Дубль `_require_admin`: `api/v1/admin_ops.py:30`, `api/v1/admin_demand_map.py:47`, `api/v1/business_truth.py:22`
- Shim файлы: `backend/app/intent/{classifier,decision_tree,page_classifier,safety_layer,standalone_test}.py`
- Секреты в config без использования: `backend/app/config.py:35-39`
- Настоящий TODO: `backend/app/core_audit/priority/service.py:443`
- Дубль форматтера времени: `components/dashboard/activity-feed.tsx:31`, `last-run-summary.tsx:28`, `overview.tsx:381`, `app/priorities/page.tsx:29`
- Закоммиченный runtime-файл: `backend/celerybeat-schedule`
- Рассинхрон .env.example: `backend/app/config.py:22,47,51,58,65,74` — сравнить с `.env.example`
- Placeholder README: `frontend/README.md`
- SEO клиентский мусор в корне: `GTS_SEO_REMAINING.md`, `GTS_SEO_ROADMAP.md`
