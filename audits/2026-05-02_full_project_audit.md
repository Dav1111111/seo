# Yandex Growth Tower — полный аудит проекта

Дата: 2026-05-02  
Скоуп: весь репозиторий `/Users/davidgevorgan/Desktop/yandex-growth-tower`  
Скиллы: `codebase-onboarding`, `tech-debt-tracker`, `code-reviewer`, `dependency-auditor`, `security-auditor`

## Короткий вывод

Проект не выглядит "заваленным мусором". Ядро продукта живое: FastAPI + Celery + Postgres/Redis backend, Next.js Studio frontend, доменная логика в `backend/app/core_audit`, уже есть run_id-based pipeline, `task_session`, Celery time limits, activity sweep и большой набор backend-тестов.

Главная проблема сейчас не мусор, а **готовность к эксплуатации**:

1. **Production frontend build сейчас не проходит** из-за TypeScript drift между backend response и frontend type.
2. **Локальная backend-проверка не воспроизводима**: проект требует Python >=3.12, на машине нет `python3.12`, а `backend/.venv` на Python 3.10 и без части зависимостей.
3. **Безопасность и multi-tenant заблокированы**: нет end-user auth, есть shared admin key, plaintext OAuth tokens, dev compose с открытыми Postgres/Redis портами.
4. **Deployment hygiene слабая**: нет `.dockerignore`, compose запускает dev-сервисы, frontend Dockerfile ожидает `.next/standalone`, но Next config не включает standalone output.
5. **Кодовая база уже переросла MVP-форму**: большие API/page файлы, frontend типы на `any`, legacy UI ждёт PR-S9.

## Метрики репозитория

- Tracked files: 458.
- Всего строк в tracked files: 90,999.
- App source files в `backend/app`, `frontend/app`, `frontend/components`, `frontend/lib`: 306.
- App source lines в этих директориях: 58,519.
- FastAPI routes: 112.
- Celery task decorators: 53.
- Backend test files: 70.
- Backend test functions: 690.
- Frontend unit/e2e tests: не найдены.
- Размер после чистки кэшей: `.` 356M, `backend` 28M, `frontend` 316M.

## Что уже хорошо

- `backend/app/workers/celery_app.py` уже имеет глобальные `task_soft_time_limit=480` и `task_time_limit=600`.
- `backend/app/workers/db_session.py` задаёт правильный паттерн `task_session()` с dispose engine.
- `backend/app/collectors/tasks.py` и `backend/app/agents/tasks.py` уже мигрированы на `task_session`, старый connection leak из прошлых аудитов в основном закрыт.
- `backend/app/core_audit/pipeline/tasks.py` теперь расширяет full pipeline после primary stages: BusinessTruth, competitors, review, priorities, report.
- `backend/app/api/v1/activity.py` больше не делает reconcile on every GET; backstop вынесен в Celery beat `pipeline_reconcile_sweep`.
- Есть migration `b3c7e4d1f520_unify_timestamps_and_activity_partial_idx.py`, которая закрывает timestamp drift и добавляет partial index.
- Backend покрыт большим количеством unit-тестов по доменной логике.

## P0 — блокеры

### P0-1. Frontend production build падает

Команда:

```bash
cd frontend && npm run build
```

Результат после разрешения сетевого доступа:

```text
Compiled successfully in 3.5min
Failed to type check.
./components/studio/brain-plan-card.tsx:230:16
Type error: Property 'in_focus' does not exist on type ...
```

Причина: backend уже отдаёт `in_focus` в `BrainActionOut` (`backend/app/api/v1/studio.py:2385`, `:2443`), UI его читает (`frontend/components/studio/brain-plan-card.tsx:230`), но type contract в `frontend/lib/api.ts:979` не включает поле `in_focus`.

Действие: добавить `in_focus: boolean` в тип `studioGetBrainPlan().actions[]`, затем снова прогнать `npm run build`.

### P0-2. Backend tests не запускаются в текущем окружении

Команда:

```bash
cd backend && .venv/bin/pytest -q
```

Результат: collection прерван после 10 import errors. Отсутствуют `anthropic`, `celery`, `datasketch`, `httpx`. При этом:

- `backend/pyproject.toml` требует `requires-python = ">=3.12"`.
- `python3 --version` = 3.11.0.
- `python3.12` отсутствует.
- `backend/.venv/bin/python --version` = 3.10.5.
- `alembic` в `.venv` тоже отсутствует.

Действие: пересоздать backend venv на Python 3.12 или запускать тесты через Docker, как написано в `backend/pyproject.toml`. Без этого любой аудит логики остаётся частично статическим.

### P0-3. Нет нормальной аутентификации и tenant isolation

Факты:

- `backend/app/api/v1/sites.py:12` прямо говорит: hardcoded tenant до будущей auth-фазы.
- `GET/PATCH /sites/{site_id}` ищут `Site.id == site_id`, без user/tenant проверки.
- Большая часть non-admin API принимает `site_id` из URL и не проверяет владельца.
- Admin endpoints защищены shared header key, но `_require_admin` продублирован в `admin_ops.py`, `admin_demand_map.py`, `business_truth.py`, `studio.py`.
- Сравнение ключа обычное `x_admin_key != configured`, не `hmac.compare_digest`.
- `docker-compose.yml:93` хранит `ADMIN_API_KEY: "admin_dev_secret_2026"` в git.

Это приемлемо только для single-user dev/MVP. Для второго клиента или публичного доступа это IDOR/security incident.

Действие: минимум сейчас — закрыть весь API auth-gate на уровне nginx или FastAPI dependency. Для multi-tenant — `current_user/current_tenant`, `get_current_site_for_user(site_id)` и tenant-scoped SQL everywhere.

### P0-4. Dev compose экспонирует Postgres и Redis наружу

Факты:

- `docker-compose.yml:12` — `5432:5432`.
- `docker-compose.yml:23` — `6379:6379`.
- `docker-compose.yml:10` — default DB password `devpassword`.
- Redis без пароля.

Действие: убрать public `ports` у `db`/`redis`, оставить только internal Docker network. Если нужен доступ с хоста — bind to `127.0.0.1` или SSH tunnel. Redis — с password/TLS или только private network.

### P0-5. Yandex OAuth token хранится plaintext

Факты:

- `backend/app/models/site.py:19` — `yandex_oauth_token: Text  # encrypted at rest`.
- `backend/app/config.py:36` — `ENCRYPTION_KEY`, но он не используется.
- `backend/pyproject.toml:20` — `cryptography`, но импортов `cryptography/Fernet` в app нет.

Комментарий "encrypted at rest" сейчас неверный.

Действие: либо реализовать encryption helper/SQLAlchemy type через Fernet и миграцию данных, либо удалить комментарий и `cryptography`. С точки зрения безопасности правильный вариант — шифровать.

## P1 — важные риски

### P1-1. Docker/deploy hygiene слабая

Факты:

- Нет `frontend/.dockerignore` и `backend/.dockerignore`.
- Frontend Docker context может включить `.env.local`, `node_modules`, `.next`.
- Backend Docker context может включить `.venv`, cache/runtime файлы.
- `docker-compose.yml:33` запускает backend через `uvicorn ... --reload`.
- `docker-compose.yml:84` запускает frontend через `npm install && npm run dev`.
- `frontend/Dockerfile:11` копирует `/app/.next/standalone`, но `frontend/next.config.ts` не содержит `output: "standalone"`.

Действие: разделить dev/prod compose, добавить `.dockerignore`, добавить `output: "standalone"` или поправить Dockerfile, убрать `--reload`/dev server из prod.

### P1-2. Next.js root inference неверный

`npm run build` предупреждает:

```text
Next.js inferred your workspace root ...
selected /Users/davidgevorgan/package-lock.json
Detected additional lockfiles:
* /Users/davidgevorgan/Desktop/yandex-growth-tower/frontend/package-lock.json
```

Из-за лишнего `/Users/davidgevorgan/package-lock.json` Next считает root выше проекта. Это может ломать caching, file tracing и сборку.

Действие: задать `turbopack.root` в `frontend/next.config.ts` на директорию frontend или убрать лишний lockfile в home.

### P1-3. `next/font/google` делает сборку зависимой от сети

Первый `npm run build` без network access упал:

```text
Failed to fetch `Inter` from Google Fonts.
```

Файл: `frontend/app/layout.tsx:2`, `:7`.

Действие: либо использовать локально vendored font через `next/font/local`, либо обеспечить network в CI/build. Для воспроизводимых сборок лучше local font.

### P1-4. Dependency/security scanning неполный

`npm audit --omit=dev --audit-level=high --json`:

- 0 high.
- 0 critical.
- 3 moderate: `hono`, `postcss`, `next` via `postcss`.

Python side:

- `pip-audit` не установлен.
- `python3 -m pip_audit` недоступен.
- Из-за broken Python env backend dependency CVE audit не выполнен.

Действие: добавить CI job: `npm audit --omit=dev --audit-level=high`, `pip-audit`, lockfile validation.

### P1-5. Неиспользуемые backend dependencies

Статически не используются в app/tests:

- `structlog`
- `python-telegram-bot`
- `python-multipart`
- `respx`
- `factory-boy`

`cryptography` не используется, но нужен для правильного решения P0-5.

Действие: удалить unused deps после восстановления тестового окружения. `cryptography` оставить и применить для token encryption.

### P1-6. AI budget декларирован, но глобально не enforced

Факт:

- `backend/app/config.py:28` — `AI_MONTHLY_BUDGET_USD = 10.0`.
- По коду видно учёт `cost_usd`, но нет глобального circuit breaker по месячному бюджету.
- Есть локальный cap `PER_RUN_COST_CAP_USD = 0.10` в reviewer, но он закрывает только один контур.

Действие: единый budget guard перед LLM calls: month/site/tenant counters, hard stop, event in activity feed.

### P1-7. Multi-tenant schema ещё не готова

`tenant_id` есть на `sites`, но доменные таблицы в основном несут только `site_id`. Для настоящего multi-tenant лучше иметь `tenant_id` в горячих таблицах (`pages`, `search_queries`, `daily_metrics`, `analysis_events`, reviews, reports, outcomes, target_clusters) плюс composite indexes.

Действие: миграция tenant_id/backfill/indexes до включения второго клиента.

### P1-8. Frontend типизация и архитектура переросли MVP

Факты:

- `frontend/lib/api.ts` — 1170 строк, много `any`.
- Static checker по `frontend/app`: grade D, 323 smells.
- Большинство pages — client components; RSC почти не используется.
- Нет frontend tests.
- Нет OpenAPI-generated types, из-за чего уже появился build-blocker `in_focus`.

Действие: сгенерировать типы из FastAPI OpenAPI или завести вручную typed DTO layer; постепенно дробить `api.ts` по доменам.

### P1-9. Backend maintainability: крупные god files

Самые большие файлы:

- `backend/app/api/v1/studio.py` — 3060 строк.
- `backend/app/collectors/tasks.py` — 2023 строки.
- `frontend/lib/api.ts` — 1170 строк.
- `backend/app/playground/scenarios.py` — 1135 строк.
- `frontend/app/studio/competitors/page.tsx` — 972 строки.
- `frontend/app/studio/queries/page.tsx` — 925 строк.

Действие: не переписывать всё сразу. Сначала вынести сервисы/DTO из `studio.py`, затем разрезать frontend pages на feature components.

## P2 — cleanup и качество

### P2-1. Мусор, который можно убрать

Низкий риск:

- `backend/celerybeat-schedule` — runtime file tracked by git.
- `frontend/public/file.svg`, `globe.svg`, `next.svg`, `vercel.svg`, `window.svg` — default Next assets, references not found.
- `backend/app/api/v1/deps.py` — `get_session()` wrapper unused.
- `PROJECT_PROMPT.md`, `PROMPT.md` — untracked prompt dumps; перенести в docs/audits или удалить.

После тестов:

- `backend/app/intent/decision_tree.py`
- `backend/app/intent/safety_layer.py`
- `backend/app/intent/taxonomy.py`
- `backend/app/schemas/query.py`
- `backend/app/telegram/__init__.py` вместе с `python-telegram-bot`, если Telegram не планируется.

### P2-2. Legacy UI держать до PR-S9

Документ `docs/studio/IMPLEMENTATION.md` говорит, что legacy UI удалять после стабилизации и не раньше 2026-05-11:

- `/competitors`
- `/priorities`
- `/reports`
- старые dashboard/report components
- возможно `/playground`, если уже не нужен владельцу

Не резать вслепую до проверки activity/usage.

### P2-3. Нет root README и CI

Факты:

- Root `README.md` отсутствует.
- Есть только `frontend/README.md`, похожий на default Next README.
- `.github/workflows`/CI config не найден.
- Docker daemon локально недоступен: `Cannot connect to the Docker daemon`.

Действие: root README с setup/run/test/troubleshooting, GitHub Actions или другой CI: backend tests, frontend build, dependency audits.

### P2-4. Nginx production hardening

`nginx/nginx.conf` проксирует frontend/backend и публично отдаёт `/docs` + `/openapi.json`. Security headers/rate-limit/basic auth на docs не настроены.

Действие: закрыть docs в prod, добавить security headers, rate limits на admin/api, явно задокументировать TLS termination на Jino.

## Проверки, которые я запускал

```bash
python3 codebase_analyzer.py --json
python3 code_quality_checker.py backend/app --language python --json
python3 code_quality_checker.py frontend/app --language typescript --json
docker compose ps
cd backend && .venv/bin/pytest -q
cd frontend && npm audit --omit=dev --audit-level=high --json
cd frontend && npm run build
pip-audit --version
python3 -m pip_audit --version
```

Результаты:

- Docker daemon не запущен/недоступен.
- Backend pytest: не прошёл collection из-за неполного Python env.
- npm audit: high/critical нет, есть 3 moderate.
- frontend build: после network access compiled, но type check failed на `in_focus`.
- pip-audit недоступен.

## Рекомендованный порядок работ

### Сегодня

1. Починить `in_focus` type в `frontend/lib/api.ts`.
2. Задать `turbopack.root` или убрать лишний home-level `package-lock.json`.
3. Решить `next/font/google`: local font или network in CI.
4. Убрать hardcoded `ADMIN_API_KEY` из `docker-compose.yml`.
5. Закрыть public ports Postgres/Redis в compose.
6. Добавить `.dockerignore` в backend/frontend.
7. Убрать tracked `backend/celerybeat-schedule`.

### Эта неделя

1. Восстановить backend dev env на Python 3.12.
2. Добавить CI: pytest, frontend build, npm audit, pip-audit.
3. Реализовать encryption для `yandex_oauth_token`.
4. Централизовать admin auth dependency и `hmac.compare_digest`.
5. Добавить минимум global auth-gate перед публичным API.
6. Удалить низкорисковый мусор и unused deps.

### Перед multi-tenant

1. Настоящая user/session/JWT auth.
2. Tenant scoping в каждом endpoint/service.
3. `tenant_id` в доменных таблицах + composite indexes.
4. Per-tenant AI budget/circuit breaker.
5. OpenAPI-generated frontend types.
6. PR-S9 legacy UI cleanup после фактической стабилизации Studio.
