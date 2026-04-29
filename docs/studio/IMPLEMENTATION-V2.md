# Studio v2 — план и статус

> **Статус документа:** живой. Обновляется по мере выполнения этапов.
>
> Связанные документы:
> - [`CONCEPT.md`](./CONCEPT.md) — стабильный контракт продукта
> - [`IMPLEMENTATION.md`](./IMPLEMENTATION.md) — Studio v1 (закрыт)

---

## Зачем нужен v2

Studio v1 сделал **модули по сущностям** (запросы, страницы, конкуренты, аналитика, outcomes). Каждый модуль показывает свои данные честно и даёт действия. Это закрывает базу.

Studio v2 — следующий слой: **умный анализ, который соединяет модули** и говорит владельцу не «вот данные», а «вот что делать первым». Ключевые проблемы которые v2 решает:

1. **Индексация показывает не то что есть.** Сейчас «N страниц в индексе» = один запрос в Search API. Нужна сверка 4 источников.
2. **Запросы перемешаны.** Wordstat и Webmaster дают сырой список, непонятно где «наш», где «спорный», где «мусор».
3. **Сайт ранжируется по нерелевантным запросам** («джинсы багги») — нужен отдельный отчёт «вредная видимость».
4. **Есть коммерческие сущности без посадочных** (тур в попапе без отдельного URL) — нужен поиск missing landings.
5. **Кнопки действий не везде.** Пользователь видит «без ревью» и не понимает что нажать.

---

## Этапы (по порядку выполнения)

Сделано / не сделано / в работе. Каждый этап — самостоятельный PR (1-3 дня). Обновляется live.

### Этап 1+2 · Честная индексация и URL-таблица

- [ ] **Backend.** Endpoint `GET /admin/studio/sites/{id}/indexation/sources` возвращает 4 числа: sitemap_count / crawler_count / webmaster_count / search_api_count + расхождения.
- [ ] **Backend.** Per-URL endpoint `GET .../indexation/urls` — таблица каждой страницы по 8 сигналам (sitemap / crawler / Webmaster / Search API / canonical / noindex / HTTP / последний обход).
- [ ] **Frontend.** На `/studio/indexation` добавить «Сверка источников» сверху + развёрнутая таблица URL.
- [ ] **Тесты.** Минимум на агрегацию + per-URL мерж.

**Оценка: 2-3 дня.** Влияние: высокое — закрывает основной UX-долг.

**Открытый вопрос:** Webmaster API per-host-statistics уже подключён или нужно добавить? — проверить перед стартом.

---

### Этап 3 · Кнопки в /studio/pages

- [ ] «Запустить ревью этой страницы» (триггер `review_run_page_task` — backend есть)
- [ ] «Обновить crawl этой страницы» (триггер `crawl_site` с фильтром по URL — может потребоваться новый task)
- [ ] «Проверить индексацию этой страницы» (тот же `studio_indexation_run`, но scope=page)
- [ ] Объяснения почему «без ревью» (нет fingerprint / контент короткий / страница не связана с запросами)
- [ ] Список действий «проверить все страницы без ревью» в `/studio/pages` index

**Оценка: 1-2 дня.** Backend tasks по большей части готовы, только UI и dedup-кнопки.

---

### Этап 4 · Классификатор запросов (own / adjacent / disputed / spam)

- [ ] **Миграция БД.** В `search_queries` добавить:
  - `relevance` enum: own / adjacent / disputed / spam / unclassified
  - `relevance_set_by` enum: rules / llm / user
  - `relevance_set_at` timestamp
  - `relevance_reason_ru` text (короткое объяснение от LLM или правила)
- [ ] **Правила-классификатор.** Дешёвый первый проход: содержит `primary_product` И регион → own; содержит явный мусорный паттерн (бренды конкурентов) → spam; иначе unclassified.
- [ ] **LLM-классификатор.** Haiku обрабатывает unclassified пакетами по 50 запросов. Промпт включает: профиль бизнеса (`narrative_ru`), services, geo, и список запросов. Возвращает класс + объяснение.
- [ ] **Override.** Endpoint `PATCH /admin/studio/sites/{id}/queries/{qid}/relevance` — пользователь говорит «нет, это мой запрос», запись с `set_by=user` **никогда не перезаписывается**.
- [ ] **UI.** В `/studio/queries`: badge со статусом, кнопка «пометить как мусор» / «как смежный» / «как наш». Фильтр по статусу.
- [ ] **Celery.** `classify_queries_site_task` раз в неделю + по триггеру.
- [ ] **Тесты.** На правила (синхронные), мок на LLM-вызов, проверка что user override не затирается.

**Оценка: 3-4 дня.** Стоимость LLM: ~30 центов на сайт/неделю.

**Открытый вопрос:** какой LLM-промпт качественнее всего разделяет тур-операторские запросы. Нужны ~50 размеченных рукой примеров для калибровки.

---

### Этап 5 · Отчёт «вредная видимость»

- [x] **Backend.** Endpoint `GET /admin/studio/sites/{id}/queries/harmful` возвращает запросы где `relevance ∈ {spam, disputed}` И `last_position <= 30`.
- [x] **Backend.** Каждый item содержит позицию, показы 14д, объём Wordstat, причину классификации, и rule-based `suggested_action_ru` (severity-aware: жёстче для топ-10).
- [x] **Frontend.** Отдельная страница `/studio/queries/harmful` — totals + карточки сортированы по объёму. Spam-карточки с rose-границей, disputed — с amber. Топ-10 позиции подсвечены danger-цветом.
- [x] **Frontend.** Cross-link на `/studio/queries` — амбер-баннер с количеством, виден только когда есть что чинить.

**Сделано ✅** 2026-04-28. Per-page conn (top-3 конкурента, страница на сайте) deferred — нужен page↔query link table, который пока не строится.

---

### Этап 6 · Missing landing pages

- [x] **Light-режим (LLM-сравнение).** `core_audit/missing_landings.py` строит business_signal из `understanding.narrative_ru` + `observed_facts` + `target_config`, отдаёт LLM (tool_use, JSON-schema) вместе со списком реальных URL. Хранится в **отдельном** slot `target_config.missing_landings` — НЕ перетирает competitor `growth_opportunities`.
- [x] **Anti-hallucination фильтр.** Каждый `evidence_quote` проверяется на substring-вхождение в business_signal (нормализация: lowercase + NFKC + удаление пунктуации). Если цитата фабрикация — item отбрасывается. Это главная гарантия модуля.
- [x] **Celery task** `missing_landings_scan_task` + activity events `stage="missing_landings"`. Идемпотентность через `_recent_started_event` (60s).
- [x] **Endpoints** `POST /admin/studio/sites/{id}/missing-landings/scan` + `GET .../missing-landings`.
- [x] **Frontend.** Новая секция «Услуги без посадочных страниц» в `/studio/competitors`, рядом с «Что делать». Три явных состояния: never run / clean / has gaps. Карточки показывают цитату из narrative (доказательство, что система не выдумала), suggested_url_path и closest_existing_url.
- [x] **Тесты:** 12 unit-тестов на business_signal, evidence-substring gate, full-run с мок-LLM (kept vs dropped, total fabrication, item cap).
- [ ] **Heavy-режим (Playwright crawler).** Не нужен — light-режим на grandtourspirit нашёл 5 валидных пропусков (Крым, яхты, вертолёты, Каньоны Красной Поляны, гастрономия), 3 выдумки отброшены фильтром. Остаётся в backlog на случай если на другом сайте light не сработает.

**Сделано ✅** 2026-04-29. Live-test grandtourspirit: 22 страницы, 32 секунды, $0.06, 5 accepted / 3 rejected.

---

### Этап 7 · Intelligence layer (мозг поверх всех модулей)

**ВАЖНО:** не начинать пока этапы 1-6 не работают в проде ≥2 недели.

- [ ] Endpoint `GET /admin/studio/sites/{id}/intelligence` — синтезирует данные всех модулей в один отчёт «приоритеты».
- [ ] Логика приоритизации: критичные техпроблемы (indexation, harmful visibility) → high-impact opportunities → mid-priority recs.
- [ ] **Не делать через LLM.** Мозг — это правила поверх готовых классификаций (этап 4) и сигналов (этап 1+5+6). LLM здесь = шум.
- [ ] **Frontend.** Главная страница `/studio` или новая `/studio/plan` показывает «top 5 действий на этой неделе».

**Оценка: 2-3 дня.** Зависит от качества этапов 1-6.

---

### Этап 8 · LangGraph / Multi-Agent System

**ВАЖНО:** делаем только когда этапы 1-7 стабильно работают и появится понятная нужда в оркестрации (которой сейчас нет).

- [ ] Решить: нужен ли вообще. Если есть простая task-graph в Celery — может быть достаточно.
- [ ] Если решили делать: Intelligence → Planner → Writer → Auditor агенты.

**Оценка: непредсказуемо. Скорее всего 2-4 недели полноценной работы.**

---

### Этап 9 · UI-actions везде (принцип, не отдельный PR)

Применяется по мере роста модулей: каждый новый отчёт получает кнопки действий.

- [ ] /studio/queries: «пометить как мусор» (этап 4), «создать посадочную» (этап 6)
- [ ] /studio/indexation: «перепроверить URL», «сравнить с sitemap»
- [ ] /studio/pages: см. этап 3

---

## Что нужно добавить ДО старта (чтобы план был реализуем)

Эти пункты не покрыты твоим оригинальным планом, но без них этапы 4-7 «потекут».

### 1. Колонки `relevance` + `set_by` в БД

Новая Alembic-миграция. Сделать **до** этапа 4. Без этого классификатор негде хранить.

### 2. Бюджет LLM

| Этап | Частота | Стоимость/сайт/мес |
|---|---|---|
| Этап 4: classification | раз в неделю | ~$1.20 |
| Этап 5: harmful visibility | бесплатно (правило) | $0 |
| Этап 6: missing landings | раз в неделю | ~$0.40 |
| Этап 7: intelligence | бесплатно (правила) | $0 |
| **Итого** | | **~$1.60** |

На 10 сайтов = ~$16/мес. Терпимо.

### 3. Редактор профиля для владельца (`target_config`)

**Без этого классификатор унаследует мусор из профиля** (помнишь «прокат» в багги-сайте, который потянул левые фразы из Wordstat).

- [x] Endpoint `GET /admin/studio/sites/{id}/profile` + `PUT .../profile` — читает/пишет primary_product / services / secondary_products / geo_primary / geo_secondary / narrative_ru. Валидация: непустой primary_product, непустой geo_primary, лимиты длины.
- [x] Frontend: страница `/studio/profile` — chip-редакторы для массивов, textarea для narrative, dirty-banner, индикатор «отредактировано вручную vs LLM».
- [x] Карточка «Профиль бизнеса» в индексе Студии (поверх остальных модулей — намекает что это надо посмотреть в первую очередь).
- [x] Activity-маркер в `target_config._profile_edited` — telemetry для отслеживания drift между LLM и owner-редакцией.

**Сделано ✅** 2026-04-27.

### 4. Тесты на реальных данных

Этап 4 без тестов на твоих 12+60 запросах нельзя выкатывать. Минимум 50 размеченных рукой запросов как ground truth.

### 5. Версионирование решений

Если LLM решил «джинсы багги = мусор», а через месяц передумал — owner должен видеть **что и когда поменялось**. Реализуется через колонку `relevance_set_at` + activity events stage="classify_queries".

---

## Очередь (рекомендованный порядок)

```
Сейчас ────── Studio v1 закрыт ✅
       │
       │  (пауза 2-4 недели прод-стабилизации
       │   v1 — это правило из IMPLEMENTATION.md §2.2)
       │
       ├─ DO-ДО: миграция БД (relevance fields)            +0.5 дня
       ├─ DO-ДО: редактор профиля                           +1 день
       │
       ├─ Этап 1+2: честная индексация                     +2-3 дня
       ├─ Этап 3: кнопки в /studio/pages                   +1-2 дня
       ├─ Этап 4: классификатор запросов                   +3-4 дня
       ├─ Этап 5: вредная видимость                        +0.5 дня
       ├─ Этап 6: missing landings (light)                 +1-2 дня
       │
       │  (пауза 2-4 недели прод-стабилизации v2-частей)
       │
       ├─ Этап 7: Intelligence layer                       +2-3 дня
       │
       │  (рассмотрение нужен ли LangGraph)
       │
       └─ Этап 8: LangGraph/MAS — если понадобится         +2-4 недели
```

**Итого Studio v2 без этапа 8:** ~3 недели чистой работы + 2-4 недели прод-наблюдения.

---

## Журнал решений

(пополняется по мере принятия)

### 2026-04-27 · Studio v2 как продолжение, не replace
**Выбрано:** v2 это **надстройка** над v1, не переписывание. Все модули v1 продолжают работать; v2 добавляет умные слои поверх.
**Альтернатива:** переписать с нуля под новую архитектуру.
**Почему:** v1 уже стабилен в проде. Переписывание = потеря 6 недель работы и риск регрессии. Лучше дотачивать.

### 2026-04-27 · Этапы 7-8 не делаем сразу
**Выбрано:** Intelligence layer и LangGraph откладываем минимум на месяц после этапов 1-6.
**Почему:** «мозг» — это **результат** хорошо сделанных модулей, а не feature. Если этапы 1-6 сделаны качественно — мозг сам собирается из их данных. Если плохо — мозг суммирует кашу.

### 2026-04-27 · Редактор профиля как обязательная предтеча этапа 4
**Выбрано:** до классификатора запросов сделать UI редактирования `target_config`.
**Почему:** все классификации зависят от качества профиля. Если в профиле «прокат» (которое не услуга grandtourspirit) — classifier пометит «прокат сочи» как «own», что неверно. Без owner-override профиля v2 будет наследовать ошибки v1-онбординга.

---

## История релизов v2

(пополняется по мере PR-ов)

| Дата | Этап | Коммит | Что вошло |
|---|---|---|---|
| 2026-04-27 | Профиль-редактор (предтеча Этапа 4) | e570db4 | endpoint `GET/PUT /admin/studio/sites/{id}/profile`, страница `app/studio/profile/page.tsx` с chip-редакторами и dirty-banner, карточка в индексе Студии, маркер `_profile_edited` для telemetry |
| 2026-04-27 | Этап 4 День 2 — миграция + rules-классификатор | 7bdd252 | alembic `c4d8e9f1a2b3` добавляет `relevance / relevance_set_by / relevance_set_at / relevance_reason_ru` в `search_queries` + индекс `(site_id, relevance)` + CHECK constraints. `app/core_audit/relevance.py` — ProfileSlice + classify_by_rules (whole-word match, only `own` verdict). 16 тестов |
| 2026-04-28 | Этап 4 День 3 — LLM классификатор + Celery + endpoint | 29ad401 | `app/core_audit/relevance_llm.py` (Haiku via tool_use, structured output). `classify_queries_site_task` в `app/collectors/tasks.py` — rules first, LLM batches of 30, never overwrites set_by='user'. Endpoint `POST .../queries/classify`. Live-test: 45 запросов на grandtourspirit за 33 сек, $0.048, 0 failures. Распределение: 8 own / 6 adjacent / 9 disputed / 22 spam |
| 2026-04-28 | Этап 4 День 4 — UI релевантности | 5ab44cc | `QueryRow` + `relevance_counts` в ответе list_queries. PATCH `/queries/{qid}/relevance` для override. Frontend: бейджи per-row, фильтр-чипы, кнопка «Классифицировать», popover override, 👤 marker для user-set, spam с line-through |
| 2026-04-28 | Этап 5 — вредная видимость | (commit pending) | endpoint `GET .../queries/harmful` (top-30 cut, severity-aware suggested_action_ru), страница `app/studio/queries/harmful/page.tsx` с totals + карточками, cross-link с амбер-баннером из `/studio/queries` |
| 2026-04-29 | Этап 3 — кнопка «Запустить ревью» на странице | 349bc31, 52d9d48, 2c36d2c | `studio_review_page_task` оборачивает `Reviewer.review_page` с activity events. Endpoint `POST /admin/studio/sites/{id}/pages/{page_id}/review`. Frontend кнопки на page workspace + auto-poll. Honest 45s safety timeout с пояснением «эта страница не идёт в ревью» когда Reviewer skip-ает |
| 2026-04-29 | Этап 6 — missing landing pages (light-mode) | 4a6d45c | `core_audit/missing_landings.py` (business_signal + evidence-substring gate), `missing_landings_scan_task`, endpoints `scan`+`get`, секция в `/studio/competitors` с цитатами из narrative. 12 тестов, 100% pass. Live-test grandtourspirit: 5 валидных пропусков (Крым, яхты, вертолёты, Каньоны Красной Поляны, гастрономия), 3 LLM-выдумки отброшены фильтром, $0.06, 32 сек |
