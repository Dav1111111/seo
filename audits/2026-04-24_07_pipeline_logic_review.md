# Аудит логики 6-шагового пайплайна
**Дата:** 2026-04-24
**Скоуп:** end-to-end «Быстрый анализ» — от клика по кнопке в Обзоре до появления рекомендаций
**Аудитор:** Claude (Opus 4.7, 1M)
**Контекст:** аудит дополняет уже зафиксированные находки из `2026-04-24_00_SUMMARY.md`, `…_01_architecture.md`, `…_03_backend_code_tests.md`, `…_04_db_performance.md` — кросс-ссылки даются вместо повторного описания.

---

## TL;DR (5 пунктов)

1. **Общая оценка пайплайна: 3/5.** Архитектура event-driven с `run_id` и `emit_terminal` — зрелая и закрыта тестами. Но **«6-шаговый пайплайн» — это полу-миф**: `trigger_full_pipeline` (`backend/app/api/v1/admin_ops.py:108-111`) запускает **только 3 Celery-задачи** (crawl, webmaster, demand_map). BusinessTruth, конкуренты, приоритеты, ревью, репорт — отдельные триггеры/расписания. Владелец видит 6 «чипов стейджей» в `last-run-summary.tsx:171-176`, но 3 из них (BT, priorities, opportunities) физически не в одном запуске.

2. **Самый сильный шаг: Шаг 4 — BusinessTruth.** Чистая композиция, 107 тестов, грамотное разделение «оркестратор без Celery → таска со streaming событий», единственный шаг с автоматической деривацией словаря из реальных данных вместо доверия LLM-онбордингу.

3. **Самый слабый шаг: Шаг 6 — Рекомендации.** Реально это **три параллельных шага** без единого оркестратора: priorities (`priority_rescore_site`), review (`review_all_nightly`), report (`report_build_site`), opportunities (внутри competitors_deep_dive). Между собой не зависят, никто не дёргает их по факту нажатия «Быстрый анализ» — владельцу приходится ждать ночного расписания.

4. **Главный архитектурный риск:** **связки между шагами не enforce'ены в коде**. BusinessTruth должен запускаться **после** crawl+webmaster, иначе он работает на устаревших данных — но `trigger_full_pipeline` его не запускает вообще, а `business_truth_rebuild_site` (`backend/app/api/v1/business_truth.py:53-77`) — отдельная кнопка. Результат: после клика «Быстрый анализ» BusinessTruth остаётся на данных предыдущего прогона. То же для priorities (читает page_reviews, которые обновляет review_site, который не запущен).

5. **Что нужно починить в первую очередь:**
   - **R1.** Добавить в `trigger_full_pipeline` цепочку: после успешного crawl+webmaster+demand_map → `business_truth_rebuild_site` → competitors_discover_site → priority_rescore_site (по аналогии с тем, как `competitors_discover_site` уже chain-ит deep_dive через `apply_async`).
   - **R2.** Закрыть pipeline по факту приёма всех **6 терминалов**, а не 3 (расширить `queued` в `admin_ops.py:98`).
   - **R3.** Закрыть `task_time_limit` (см. CRIT-1 из `…_04_db_performance.md`) — иначе любой завис в SERP/deep_dive держит pipeline:started > 10 мин и `_active_pipeline_started` (cutoff = 10 мин) не закроет его даже при правильных терминалах.
   - **R4.** Заменить `_make_session()` на `task_session()` в `collectors/tasks.py` (см. M-010 в SUMMARY) — два из трёх «честных» шагов пайплайна (crawl, webmaster) стартуют через дырявый engine; третий (demand_map) уже на чистом `task_session`.

---

## Краткая карта пайплайна

```
                    Owner clicks «Быстрый анализ» (frontend/components/dashboard/overview.tsx)
                                                         │
                                                         ▼
                       POST /admin/sites/{id}/pipeline/full
                          (admin_ops.py:40-113)
                          • dedup okno 2 мин (admin_ops.py:57-75)
                          • generate run_id
                          • log_event('pipeline','started', queued=[crawl,webmaster,demand_map])
                          │
       ┌─────────────────┼─────────────────┐    ВСЁ ОСТАЛЬНОЕ — НЕ В ЭТОМ ВЫЗОВЕ:
       │                 │                 │
       ▼                 ▼                 ▼
   Шаг 1            Шаг 2             Шаг 3            ┌─ Шаг 4 (BT) — отдельная кнопка
   crawl_site       collect_site_     demand_map_      │  /admin/sites/{id}/business-truth/rebuild
   (collectors/     webmaster         build_site       │
    tasks.py:238)   (collectors/      (core_audit/     ├─ Шаг 5 (Конкуренты) — отдельная кнопка
   • sitemap        tasks.py:186)      demand_map/      │  POST /collectors/sites/{id}/competitors/discover
   • httpx fetch    • Webmaster API    tasks.py:53)     │  (или раз в неделю — beat)
   • upsert pages   • upsert search_   • Cartesian      │  → chain'ит deep_dive автоматом
   • emit done      queries+metrics    • Suggest+LLM    │  → emit_terminal('opportunities','done')
   ├─ chain'ит      • emit done/       • rescore        │
   │  fingerprint    │ skipped (host_  • persist        ├─ Шаг 6.1 priorities — отдельная кнопка
   │  через          │ not_loaded)     • emit done       │  POST /priorities/sites/{id}/rescore
   │  countdown=10s  │                                   │  • Запускает review-only chain
   │                 │                 │                 │
   ▼                 ▼                 ▼                 ├─ Шаг 6.2 review — НИКТО НЕ ДЁРГАЕТ кроме nightly
                                                         │  beat: review_all_nightly (04:45 UTC)
                                                         │
                                                         └─ Шаг 6.3 report — еженедельно (Mon 07:00 UTC)
       │                 │                 │
       └─────────────────┼─────────────────┘
                         ▼
              emit_terminal('<stage>','done|failed|skipped')
                         │
                         ▼
              core_audit/activity.py:emit_terminal (230-294)
              • _active_pipeline_started (lookup пo run_id)
              • если все 3 queued стейджа достигли терминала
                → log_event('pipeline','done|failed|skipped')

UI poll:
  • activity-feed.tsx:54 → /activity (5s)
  • last-run-summary.tsx:50 → /activity/current-run (4s)
  • overview.tsx:128 → /activity/last (10s)
  • КАЖДЫЙ GET → reconcile_open_pipelines (см. CRIT-2 в …_04)
```

**Ключевая находка**: то, что владелец видит как «6 шагов» (`last-run-summary.tsx:171-177` рисует 6 чипов), — это **3 реально запускаемых стейджа** + **3 чипа от чужих запусков** (competitor_discovery / competitor_deep_dive / opportunities). После клика «Быстрый анализ» 3 последних чипа остаются с прошлого прогона (или вообще пустыми, если конкурентов никогда не запускали).

---

## Шаг 1 — Краулит сайт

**Что делает (простым языком):** платформа пробегает по карте сайта (sitemap.xml), скачивает каждую страницу, вытаскивает заголовок, описание, тексты — чтобы потом понять, о чём вообще сайт и какие страницы у него есть. Делает это через свой HTTP-клиент (как браузер), сохраняет в БД до 50 страниц.

**Код:**
- Точка входа Celery: `backend/app/collectors/tasks.py:238-303` (`crawl_site`).
- Кравлер: `backend/app/collectors/site_crawler.py:76-248` (`SiteCrawler`).
- Сериализатор: `backend/app/models/page.py`.
- Цепочка: после успешного crawl_site → `fingerprint_site.apply_async(countdown=10)` (`collectors/tasks.py:300`).

**Сильные стороны:**
- **Async httpx + семафор на 4 параллельных запроса** (`site_crawler.py:189`) — корректная асинхронная реализация. На 50 страниц при средней задержке 200-300 мс получается 3-5 секунд wall-time.
- **Идемпотентный upsert** через `pg_insert(...).on_conflict_do_update(index_elements=["site_id","url"])` (`site_crawler.py:210-241`) — повторный краул не плодит дубликатов.
- **Чёткие emit_terminal на обеих ветках** (success + crash, `tasks.py:280-293` / `272-278`) — стейдж всегда закрывается.
- **Custom User-Agent** `GrowthTower SEO Crawler/1.0 (+https://growthtower.ru)` (`site_crawler.py:172`) — корректная вежливость.
- **Truncate content_text до 10k символов** (`site_crawler.py:131-132`) — защищает от страниц-монстров.
- **Fallback на homepage**, если sitemap пуст (`site_crawler.py:181-184`).

**Слабости:**
- **Использует `_make_session()` без `dispose()`** (`tasks.py:249`) — connection-pool leak. См. **M-010 в SUMMARY** / **C1 в backend_code_tests** / **A-004 в architecture**. Это тот же файл, который в hardening sprint забыли мигрировать на `task_session`.
- **Регексп-парсинг HTML** (`site_crawler.py:40-53`) вместо BeautifulSoup/selectolax — хрупко на нестандартной разметке, не обрабатывает `<title>` с CDATA, `&amp;`-entities, многострочные `<h1>`. **Нет ни одного теста на эти регекспы** (см. дыру #3 в `…_03_backend_code_tests.md`).
- **`logger.warning(f"Sitemap fetch failed at {sitemap_path}: {e}")`** (`site_crawler.py:96, 104, 186, 246`) — f-string в лог-форматтере, нарушает lazy-eval (см. **M4 в backend_code_tests**).
- **`datetime.utcnow()`** в 4 местах (`site_crawler.py:223,224,238,239`) — naive datetime, deprecated в 3.12. См. **H-09 в SUMMARY**.
- **`httpx.AsyncClient(timeout=20)` без retry-policy** (`site_crawler.py:176`) — медленный сайт (Тильда, иногда отвечает 25+ сек) даст `pages_failed=N` без второй попытки. Конкретно для 50 URL при 4 параллельных одна ошибка timeout = 25 сек простоя в семафоре.
- **Чейнинг fingerprint** через `apply_async(countdown=10)` (`tasks.py:300`) — fire-and-forget без отслеживания результата. Если fingerprint упал, никто не узнает; pipeline это не учитывает (fingerprint не в `queued`).
- **`max_pages=50`** хардкод (`tasks.py:269`) — для сайта на 200+ страниц (что не редкость даже у tour-оператора с лендингами под направления) теряем 75% контента. Конфиг не выводится в settings.
- **Запрос всех колонок при чтении в next stage** — далее в `business_truth/rebuild.py:114-118` и `competitors/tasks.py:569-573` идёт `select(Page.url, Page.path, Page.title, Page.h1, Page.meta_description, Page.content_text)`. **`content_text` (до 10k символов на страницу × 50 страниц = 500KB)** тянется чтобы тут же усечь до 500/600 — см. **HIGH-3 в db_performance**.

**Тесты:**
- **0 unit-тестов на `site_crawler.py`**. См. дыру #3 в `…_03`. Регекспы (`_TITLE_RE`, `_META_DESC_RE`, `_H1_RE`, `_LINK_RE`) — pure-функции, тестировать тривиально.
- Только integration через `test_stage_events.py:35-50` (имитация emit, не реальный кравл).

**Хрупкости:**
- Sitemap.xml **может не существовать** или быть в `robots.txt` по нестандартному пути → молча fall back на homepage. Если homepage редиректит на лендинг с лишним JS, мы получим 1 пустую страницу и pipeline пройдёт «успешно».
- HTML-парсинг регекспами хрупок к **современным React-сайтам** (контент в JS) — title/h1 обычно есть, но `content_text` будет почти пустой → BusinessTruth не сможет классифицировать страницы.
- **Нет `task_time_limit`** (см. **CRIT-1 в db_performance**) — медленный сайт + 50 URL × 25s timeout = 20+ минут блокировки воркера в худшем случае.

**Оценка: 3/5** — рабочая реализация с хорошими async-паттернами, но: дырявый engine, хрупкий regex-парсер, ноль тестов, нет таймаута, чейнинг fingerprint без обратной связи. Это «надёжный MVP», но не «production-grade».

---

## Шаг 2 — Тянет Вебмастер

**Что делает (простым языком):** Подключается к Яндекс-Вебмастеру по API через OAuth-токен владельца, забирает список запросов, по которым сайт показывался последние 30 дней (показы, клики, средняя позиция), плюс данные индексации (сколько страниц 2xx/4xx/5xx). Нужно, чтобы понимать, **что Яндекс реально знает** про сайт.

**Код:**
- Целевая Celery: `backend/app/collectors/tasks.py:186-235` (`collect_site_webmaster`).
- Коллектор: `backend/app/collectors/webmaster.py:23-262` (`WebmasterCollector`).
- Хелпер сообщений: `tasks.py:39-84` (`_format_webmaster_result`).

**Сильные стороны:**
- **Типизированное исключение `HostNotLoadedError`** (`tasks.py:153-163` / `webmaster.py` импортирует) пробрасывается мимо retry-логики и попадает в `_format_webmaster_result` как `status='skipped'` — **owner получает понятное сообщение «открой Webmaster UI и загрузи хост»** вместо «500 internal server error».
- **`_format_webmaster_result`** грамотно отделяет 3 ветки (host_not_loaded / empty_window / done) и возвращает structured `extra` для активити-фида.
- **Поддержка двух форматов ответа Яндекса** (`webmaster.py:204-241`) — aggregated numbers vs daily breakdown — обрабатываются явно с явным комментарием в коде.
- **`webmaster.py:138`** — учёт 5-дневного лага Webmaster (`end_date = today - timedelta(days=5)`) — это знание из практики, документировано в коде.
- **`pg_insert(...).on_conflict_do_update`** (`webmaster.py:173-194`) — идемпотентный upsert.

**Слабости:**
- **N+1 round-trips: UPSERT + SELECT id для каждой query × 500 запросов = 1000 RTT.** См. **H-01 в SUMMARY** / **CRIT-3 в db_performance** / **H1 в backend_code_tests**. На каждой итерации `webmaster.py:182-191` делает `pg_insert(...).on_conflict_do_update` потом отдельный `select(SearchQuery.id)`. Решается одним `RETURNING id` в upsert. Это самая дорогая операция всего пайплайна по wall time после сетевых вызовов Яндекса.
- **`_make_session()` без dispose** (`tasks.py:193`) — то же что в crawl. Связано с **M-010**.
- **OAuth-токен читается plain-text из БД** (`tasks.py:211`) — см. **M-003 в SUMMARY** (mёртвый комментарий «encrypted at rest»).
- **Семафор rate-limit `asyncio.Semaphore(3)` локальный** (`collectors/base.py:16`) — **на самом деле 3×N процессов воркера** (см. **CRIT-4 в db_performance**). Перерасход API-квоты Яндекса при concurrency > 1.
- **Внутри `_run` на failure** (`tasks.py:217-223`) emit_terminal **до** `raise` — но `raise` поднимает за пределы async-context, который потом схлопнется без commit. Сейчас работает (потому что emit_terminal сделал свой commit), но антипаттерн.
- **Колонка `dimension_id=sq_id`** для `query_performance` метрики (`webmaster.py:221, 248`) — это UUID search_query.id, но колонка ещё используется и для Metrica с `dimension_id=NULL`. JOIN дальше в `business_truth/rebuild.py:140-141` идёт через сложное условие — рушится при добавлении новых метрик-типов с тем же `dimension_id` slot.
- **Нет тестов на `webmaster.collect_and_store`** — см. дыру #1 в `…_03`. **Это самая критичная слепая зона** платформы: формат ответа Яндекса меняется без предупреждения.

**Тесты:**
- **0 unit-тестов на webmaster collector**. Парсинг `indicators` (list vs aggregate, dates как `2026-04-06T00:00:00`) — самый хрупкий код в платформе, и **полностью без покрытия**. Можно мокать через injectable HTTP fetcher, как сделано в `demand_map/test_suggest.py`.
- Smoke через `test_stage_events.py:64-79` — только проверяет emit pattern, не парсинг.

**Хрупкости:**
- **Изменение формата ответа Webmaster API** — Яндекс несколько раз менял структуру `indicators` (см. комментарий в `webmaster.py:196-198`). Без тестов — узнаем через жалобу владельца, что «вебмастер опять пустой».
- **`query_text=""`** или дубль на стороне Яндекса (известный баг) → `if not query_text: continue` (line 169) **теряет данные** молча.
- **`HostNotLoadedError`** — проброс exception в `pg_insert` ниже **может откатить всю транзакцию**, если `await db.execute(stmt)` уже падал. Сейчас не падает (выходим в except раньше), но порядок хрупкий.
- **Memory pressure**: 500 запросов × до 30 дней × 4 поля JSON-ответа = ~10 MB JSON в памяти. Не ужасно, но при истории > 90 дней может ударить.

**Оценка: 3/5** — функциональность правильная, edge-cases (host_not_loaded, empty window) обработаны, но **N+1 в горячем пути** + **0 тестов** + **дырявый engine** + **plain-text token** делают шаг хрупким и медленным.

---

## Шаг 3 — Строит карту спроса

**Что делает (простым языком):** Из услуг и гео из онбординга платформа порождает «декартово произведение» — что-то вроде: «багги × Адлер», «багги × Сочи», «джип-туры × Адлер» и т.д. Потом дополняет это подсказками Яндекс-Suggest и одним вызовом ИИ, чтобы получить реальные народные формулировки. Пересчитывает релевантность с учётом того, какие запросы уже приводят показы.

**Код:**
- Точка входа: `backend/app/core_audit/demand_map/tasks.py:53-182` (`demand_map_build_site_task`).
- Алгоритм: `expander.py` (446 строк), `suggest.py` (239 строк), `llm_expansion.py` (312 строк), `rescoring.py` (130 строк), `persistence.py` (155 строк).
- Feature flag: `settings.USE_DEMAND_MAP_ENRICHMENT` (`demand_map/tasks.py:120`).

**Сильные стороны:**
- **Уже мигрирован на `task_session`** (`demand_map/tasks.py:72`) — единственный из 3 шагов пайплайна без leak.
- **Fail-open архитектура с явными комментариями** (`tasks.py:121-150`): Suggest-выпал — продолжаем без него, LLM-ошибка — без неё, observed_load_failed — пустой список. Документировано: «hard-failure mode is DB unavailability» (line 13-14).
- **Чёткие 3 ветки emit_terminal** (`tasks.py:75-79` site_not_found, `83-93` no_target_config, `109-114` expansion failed, `159-173` happy path) — каждая закрывает стейдж.
- **Идемпотентный persist через delete-then-insert** (`persistence.py`) — повторный запуск не плодит дубликатов кластеров.
- **89 тестов** (`backend/tests/demand_map/`) — лучшее покрытие из всех шагов пайплайна (на equal с BusinessTruth). Тесты для guardrails, geo_compatibility, relevance, rescoring, persistence отдельно.
- **Dependency injection в `suggest.py:fetch_suggestions(fetcher=...)`** — тесты без реального HTTP. Эталонный паттерн.

**Слабости:**
- **`enrich_clusters_with_suggest` (sync)** дёргается из async кода без `asyncio.to_thread` (`tasks.py:126`) — внутри HTTP `urllib` + `time.sleep(0.7)`×retry (см. **H2 в backend_code_tests**, `suggest.py:192`). Это **синхронный блокирующий I/O в async-таске**, дающий до 10 секунд блокировки event loop таски.
- **`expand_with_llm`** аналогично sync (`tasks.py:136`) — один вызов Claude через `client.messages.create()` без `asyncio.to_thread`. До 30 секунд блокировки.
- **Skip без target_config** (`tasks.py:82-93`) — корректное поведение, но `await emit_terminal('demand_map','skipped')` происходит до `log_event('demand_map','started')`. UI получит skipped без started — **не битo, но нарушает «started → terminal» инвариант**.
- **`demand_map_build_site` не зависит от crawl/webmaster в цепочке** — все 3 запускаются одновременно через `send_task` (`admin_ops.py:108-111`). Для самой demand_map это нормально (она не читает свежий crawl), но для **Шага 4 (BusinessTruth)** это критично — он читает результат crawl+webmaster, но **не запускается из этого pipeline**.
- **`from app.config import settings as _settings`** (`competitors/tasks.py:290`) — видно, что в горячем коде settings импортируется лениво. В demand_map — глобально, **в тестах сложно мокать USE_DEMAND_MAP_ENRICHMENT**.

**Тесты:**
- **89 тестов** в 10 файлах. Покрыты: cartesian expansion, geo compatibility, guardrails (брендовые-фильтры), relevance scoring, rescoring с observed overlap, persistence, suggest (через injectable fetcher), llm_expansion, quality, end-to-end task через mock-DB.
- Дыра: тесты предполагают, что Suggest и LLM работают; нет регресса на «Suggest вернул HTTP 500 → флоу проходит».

**Хрупкости:**
- **`USE_DEMAND_MAP_ENRICHMENT=False` (default?)** — если выключен, шаг работает только Phase A (Cartesian) — карта получается жёсткой, без народных формулировок. UI этого не сигналит.
- **`expand_with_llm`** один вызов Haiku — при сбое прокси Anthropic фейлится молча в except (`tasks.py:142`), но cluster-набор остаётся «как было после Suggest». Owner не узнает, что 30% карты потерял.
- **`load_observed_queries`** читает 14-дневную историю — пустая БД (новый сайт) дает empty observed → `rescore_with_observed_overlap` возвращает кластеры без буста. Не баг, но subtle: **первая неделя у нового сайта demand_map гораздо менее точная**, чем после первого collect_webmaster.

**Оценка: 4/5** — лучший по тестам, грамотный fail-open, единственный в пайплайне на `task_session`. Минусы — sync I/O в async-обёртке + лёгкая инверсия порядка emit_terminal/log_event при skip.

---

## Шаг 4 — Восстанавливает «три картины» (BusinessTruth)

**Что делает (простым языком):** Сравнивает три источника, чтобы понять, **чем сайт реально занят и что ищут люди**: (1) что владелец сказал в онбординге; (2) что реально написано на страницах сайта; (3) по каким запросам показывается в Яндексе. Если все три совпадают — направление подтверждено. Если только 1-2 — это «слепое пятно» или «нераскрытый спрос». Ничего не выдумывает: словарь услуг и гео достаёт из реальных данных, не из онбординг-фраз.

**Код:**
- Celery task: `backend/app/core_audit/business_truth/tasks.py:30-86` (`business_truth_rebuild_site_task`).
- Композиция: `backend/app/core_audit/business_truth/rebuild.py:87-237` (`rebuild_business_truth`) — 8 подмодулей, чистая функция (можно тестировать без воркера).
- Подмодули: `auto_vocabulary.py` (412), `matcher.py` (156), `page_intent.py` (46), `query_picker_v2.py` (161), `query_selector.py` (191), `reconciler.py` (91), `traffic_reader.py` (175), `understanding_reader.py` (125).
- API кнопки rebuild: `backend/app/api/v1/business_truth.py:53-77`.

**Сильные стороны:**
- **Чистая композиция «оркестратор без Celery → таска со streaming событий»** — `rebuild_business_truth` (`rebuild.py:87`) можно вызвать из теста напрямую с фикстурным `db`, без подмены Celery. Эталонный паттерн для domain-package.
- **Авто-деривация словаря** (`rebuild.py:115-160`) из реальных страниц + запросов вместо доверия онбординг-фразам — **закрывает классическую проблему LLM-онбординга** (комментарий в коде: «Onboarding LLMs hallucinated 'экскурсии' и 'туры' for Grand Tour that were never real services»). Очень зрелое решение.
- **Фильтрация target_config через auto_vocab** (`rebuild.py:167-171`) — silently дропаются сервисы из target_config, которые не подтверждены реальными данными. Это и сильная сторона (не верим LLM), и риск (если кравлер пустил мимо страницы → потеряли legitimate сервис).
- **107 тестов** (10 файлов) — лучший по покрытию. Включает ручные кейсы стемминг-коллизий («судно» vs «судак»), реальные русскоязычные edge-cases.
- **Persist в `target_config.business_truth`** (`rebuild.py:223-235`) идёт одной транзакцией с `db.commit()`. Отдельная таблица не нужна (см. возможный refactor в **A-016 / HIGH-4 в db_performance**).
- **`emit_terminal` с осмысленным `extra`** (`tasks.py:69-75`) — UI получает `directions/confirmed/blind_spots/traffic_only` — конкретные числа.

**Слабости:**
- **Крупнейший из подмодулей — `auto_vocabulary.py` (412 строк)** — вся логика стемминга, фильтрации морфологии, dedup на одном файле. Тесты есть (`test_auto_vocabulary.py`), но рефактор в `vocab/` подпакет (services/geos/dedup) сделает его читаемым.
- **`from datetime import date, timedelta` локально внутри функции** (`rebuild.py:130`) — обычный анти-паттерн, на горячем пути не страшно.
- **`(r.content_text or "")[:500]`** (`rebuild.py:79, 124`) — Python-side truncate после того, как Postgres уже отправил всю TEXT-колонку (`content_text` до 10k символов). См. **HIGH-3 в db_performance**. Решение: `func.substr(Page.content_text, 1, 600)` в SELECT.
- **Запускается по отдельной кнопке `/admin/sites/{id}/business-truth/rebuild`** или из админ-чата onboarding (`admin_demand_map.py:849-851`). **НЕ запускается из `trigger_full_pipeline`**. Таким образом, после клика «Быстрый анализ» BusinessTruth остаётся **со старыми данными** (предыдущего прогона), даже если crawl+webmaster обновили исходные источники.
- **Нет dependency check** — если crawl-only вернул 0 страниц (новый сайт без sitemap), `_build_content_map` вернёт пустой dict; reconciler продолжит работать, но `directions` будет неинформативным. Owner получит `0 направлений` без объяснения «нужно сначала закрыть онбординг + дождаться crawl».
- **Двойной select(Page) в одной функции** (`rebuild.py:114-118` и `_build_content_map` `rebuild.py:65-69`) — два почти идентичных запроса, можно было вытащить в один. На 50 страниц копейки, но при росте ощутимо.
- **`max_retries=0`** на таске (`tasks.py:30`) — любое падение БД = окончательный fail без автоматического retry.

**Тесты:**
- **107 тестов** в 10 файлах. Покрыто: matcher (русская морфология), page_intent классификация, query_picker_v2 (новый picker для discovery), query_selector, reconciler 3-source merge, auto_vocabulary, traffic_reader, understanding_reader, end-to-end через `test_rebuild_task.py`.
- Дыра: нет теста «empty content_map → empty directions с диагностическим extra».

**Хрупкости:**
- **Зависит от свежести Шагов 1+2** — если crawl упал, BT работает на устаревших страницах. Если webmaster пустой (host_not_loaded), traffic_distribution пустой → directions без strength_traffic. **Нет explicit dependency** в коде.
- **`auto_vocab["services"]`/`geos`** — пустые → пустые `content_map`/`traffic` → пустой truth. Owner видит «0 направлений», не понимает почему.
- **`site.target_config` JSONB полная перезапись** (`rebuild.py:233`) — если кто-то параллельно обновляет другой ключ (competitor_profile из шага 5), последний победит. **Нет advisory lock**, как в `competitors/tasks.py:38-40`. **Race condition на JSONB**.
- **`build_business_truth` + `competitors_discover_site` могут одновременно писать в `site.target_config`** — оба делают `cfg = dict(site.target_config or {})` → mutate → `site.target_config = cfg` → commit. Кто последний — тот и прав. **Это active race condition.**

**Оценка: 4/5** — самый зрелый домен по разделению, тестам, дизайну. Минусы — JSONB race, не интегрирован в pipeline, тяжёлый auto_vocabulary.py.

---

## Шаг 5 — Смотрит конкурентов

**Что делает (простым языком):** Берёт топ-30 запросов, по которым сайт уже что-то ищет (или из карты спроса), пробивает их через Яндекс.Поиск (Cloud Search API), смотрит, какие домены показываются на первых местах. Это и есть конкуренты — те, кто реально борется за те же запросы. После — заходит на топ-5 их сайтов и проверяет: есть ли цены, кнопка «Забронировать», отзывы, schema.org. Сравнивает с твоим сайтом → выдаёт «точки роста».

**Код:**
- Discovery task: `backend/app/core_audit/competitors/tasks.py:228-416` (`competitors_discover_site_task`).
- Deep-dive task: `backend/app/core_audit/competitors/tasks.py:419-643` (`competitors_deep_dive_site_task`).
- Алгоритм discovery: `discovery.py` (299 строк, pure function).
- Алгоритм deep-dive: `deep_dive.py` (276), `content_gap.py` (135), `opportunities.py` (445), `page_match.py` (149).
- API ручки: `backend/app/api/v1/collectors.py` (триггер) + автоцепочка из discovery.

**Сильные стороны:**
- **Идемпотентность через advisory lock** (`tasks.py:38-40, 242-255`) — `pg_try_advisory_lock(int_from_uuid)`. Двойной клик «Разведка» = второй просто emit'ит skipped, не плодит SERP-вызовов. **Очень грамотно.**
- **Auto-chain `competitors_discover_site` → `competitors_deep_dive_site`** через `apply_async(kwargs={'run_id': run_id})` (`tasks.py:361-364`) — chain'ит правильно, прокидывая `run_id`. Это единственный реальный «chain» в коде платформы.
- **ThreadPoolExecutor(max_workers=4)** для параллельных SERP-вызовов (`discovery.py:194-202`) — wall-time 30 запросов сжимается с 30×3.5s = 105s до ~10s.
- **Shadow mode для нового picker'а** (`tasks.py:163-225`) — `_compute_shadow_picks` запускается всегда, разница логируется в `extra.shadow_diff`, но реально используется `business_truth_v2` только если `USE_BUSINESS_TRUTH_DISCOVERY=True`. **Безопасный rollout новой логики** — эталонный паттерн.
- **Outer try/except + emit_terminal в фолбеке** (`tasks.py:398-414`) — даже при unexpected crash снаружи async-context закрывает stage. UI не виснет в «идёт сейчас».
- **Excluded domains** хардкод (`discovery.py:65-81`) — `yandex.ru, avito.ru, wildberries.ru, vk.com…` — выкидываем агрегаторов, видим реальных vertical-конкурентов. Знание из практики, документировано.
- **Per-query SERP cache** (`discovery.py:138, 220-224`) — записывает компактный snapshot SERP в `query_serps`, чтобы deep-dive переиспользовал данные без второго хода в API.
- **29 тестов** (4 файла) — `test_pipeline_terminal_states.py` (16, инварианты), `test_run_id_isolation.py` (concurrent run isolation), `test_shadow_mode.py`, `test_discovery_task_paths.py`. **Лучшее тестовое покрытие event-driven корректности**.

**Слабости:**
- **`competitors/tasks.py` — 682 строки** — самый большой task-файл проекта. Discovery + deep-dive + opportunities + chain + shadow-picks в одном файле. Кандидат на split (см. **A-016 / Метрики в `…_01_architecture`**).
- **Sync `urllib` через ThreadPoolExecutor внутри async-таски** (`competitors/tasks.py:492-510`) — см. **H-04 в SUMMARY** / **H4 в backend_code_tests**. Технически работает, но смешивает парадигмы.
- **`fetch_serp` poll-loop с `time.sleep(0.7)`×15** (`yandex_serp.py:227,232`) — 10 секунд блокировки слота воркера на одну SERP-операцию (см. **H2 в backend_code_tests**).
- **`select(Page.url, ..., Page.content_text)` для всех страниц** при каждом deep-dive (`tasks.py:569-573`) — тянет полные TEXT-колонки чтобы сразу обрезать до 600 (line 581). См. **HIGH-3 в db_performance**.
- **`pg_try_advisory_lock` — int signed 64-bit** (`tasks.py:38-40`) — `int(site_id.hex[:16], 16) - (1 << 63)`. Корректно по диапазону, но **collision возможна** для разных site_id с одинаковым префиксом hex (теоретически 1 на 2^64, на практике никогда).
- **Намеренно не в auto-chain pipeline** (`admin_ops.py:88-92`): «competitors_discover_site is intentionally NOT in the auto-chain — SERP probing without enough money-queries pulls generic aggregators». **Это правильно для нового сайта без traffic, но для зрелого сайта владелец вынужден жать вторую кнопку**. Нет промежуточного решения.
- **JSONB race** с BusinessTruth и onboarding (`tasks.py:332-338`): тоже `cfg = dict(site.target_config or {}); cfg["competitor_profile"] = ...; site.target_config = cfg; await db.commit()`. Параллельный rebuild_business_truth перезапишет → потеряем competitor_profile.
- **`max_retries=0`** на discover (`tasks.py:228`) — Яндекс отдал 500 → один раз и всё. Deep-dive тоже `max_retries=0` (`tasks.py:419`).

**Тесты:**
- **29 тестов**. Покрыты: pipeline terminal states (16 тестов — инварианты), run_id isolation (concurrent runs не закрывают друг друга), shadow mode (диффы старого/нового picker'a), discovery task paths.
- Не покрыто: реальный SERP-парсинг (моки используются), gap-analyzer на реальных query → SERP, opportunities из feature_diff.

**Хрупкости:**
- **Pipeline closes before deep-dive** — `_active_pipeline_started` cutoff = 10 минут (`activity.py:97-99`). Если discovery занял 8 мин + deep-dive 3 мин = `pipeline:done` будет emit'нут когда `pipeline:started` уже за пределами окна → **pipeline останется открытым в БД, UI покажет «идёт сейчас»**, пока reconcile не починит.
- **`emit_terminal('opportunities','done')`** (`tasks.py:601`) закрывает pipeline только если `_should_close_pipeline('opportunities','done')` → True (это в `activity.py:73`). Но если pipeline был запущен с `queued=[crawl,webmaster,demand_map]` (а opportunities там нет), новая логика `if run_id is not None and isinstance(queued, list) and queued` выбирает first branch и **opportunities не закроет pipeline** — закроет только legacy fallback `_should_close_pipeline`. Это работает **только потому что**, у legacy режима `opportunities → True`. Тонко.
- **При manual «Конкуренты» (без pipeline:started)** — discovery emit'ит `opportunities:done` → `_active_pipeline_started` → None → `emit_terminal` просто пишет stage event и выходит. Это ок, но subtle.
- **ThreadPoolExecutor поверх async** — `fut.result()` внутри `async def _inner()` (`tasks.py:501-504`) блокирует текущий event loop, который был создан для этой таски. Работает, но при попытке `asyncio.gather([_inner(), other_async])` всё бы повисло.
- **Опять тот же `select(Page.url, ..., Page.content_text)`** в двух местах (deep_dive `tasks.py:569-573` и BusinessTruth `rebuild.py:114-118`) — копипаст, не вынесено в общий PageRepository.

**Оценка: 4/5** — самый «продакшн-зрелый» по pipeline-correctness и идемпотентности. Минусы — sync I/O, race на JSONB, файл слишком большой.

---

## Шаг 6 — Делает рекомендации

**Что делает (простым языком):** На основе всего собранного — выдаёт три типа результата: (a) «точки роста» от конкурентов (что у них есть, а у тебя нет), (b) «приоритеты» — какие страницы и запросы нужно улучшать в первую очередь, (c) еженедельный отчёт с health-score.

**Код:**
- **6.1 Opportunities** — внутри Шага 5 (`competitors/tasks.py:586-613` + `opportunities.py`).
- **6.2 Priorities** — `priority/tasks.py:44-80` (`priority_rescore_site`) + `priority/service.py:42-...` + scorer/aggregator.
- **6.3 Review** — `review/tasks.py:33-91` (`review_page_task`, `review_site_decisions_task`, `review_all_nightly_task`) + `reviewer.py` (374 строки).
- **6.4 Report** — `report/tasks.py:29-58` (`report_build_site`, `report_build_all_weekly`) + `report/builder.py`.

**Сильные стороны:**
- **Каждый под-шаг независимо триггерим API-кнопкой**: `/priorities/sites/{id}/rescore` (`priority.py:28-32`), `/review/sites/{id}/...` (через `review_site_decisions_task.delay`), `/reports/sites/{id}/build` (`report.py:27-29`).
- **`PriorityService.rescore_site` zero out older review recs** (`priority/service.py:64-83`) — старые рекомендации уже не появятся в priority list. Грамотно.
- **`review_site_decisions_task` chain'ит `priority_rescore_site`** (`review/tasks.py:65-69`) автоматом — после ревью свежие рекомендации скорятся сразу.
- **`report/builder.py:46-90`** — чистая последовательная сборка 6 секций (Diagnostic, Coverage, QueryTrends, PageFindings, Technical, ActionPlan), один LLM-вызов для prose, fail-open на template.
- **47 тестов на review** (4 файла) + **29 тестов на priority** (3 файла) + **19 на report** (2 файла) — покрытие domain-логики хорошее.

**Слабости:**
- **«Шаг 6» — это 4 разных вызова, не один stage.** Pipeline `trigger_full_pipeline` вообще не дёргает priorities/review/report. После клика «Быстрый анализ» ничего из 6.2/6.3/6.4 не запускается — owner видит результаты прошлой ночи или должен жать ещё 3 кнопки.
- **`priority_rescore_site` не имеет `run_id`** (`priority/tasks.py:45-47`) — если бы pipeline его и запустил, нет связки с pipeline:started. emit_terminal без run_id → fallback на time-window lookup (legacy). **Это значит, что добавить priority в `queued`, как часть pipeline, требует кода + теста.**
- **Аналогично `review_site_decisions_task`, `report_build_site`** — нет аргумента `run_id` (`review/tasks.py:55`, `report/tasks.py:30`). При chain'е из pipeline `run_id` потеряется.
- **`review/tasks.py:66-69` — chain через `delay()` без передачи `run_id`** (priority chain). Когда ставишь pipeline над review, priority не присоединится к группе.
- **`report.py:32-45` (см. `…_03 H3`)** возвращает `payload` целиком (50-500 KB JSONB) на каждый GET `/reports/sites/{id}/latest`. UI делает SWR + revalidate. Не оптимально, но не критично.
- **Opportunities — внутри `competitors_deep_dive_site_task`** (`tasks.py:586-613`), не отдельная таска. Если opportunities-логика упадёт, эмит fallback `pass` (line 639) **молча проглотит**.
- **`review_all_nightly`** дёргается **только из Celery beat 04:45 UTC** (`celery_app.py:101`). Если voc owner ждёт «свежие рекомендации после Быстрый анализ» — он получит их **завтра утром**.
- **`max_retries=1`** на priority (`priority/tasks.py:44`), report (`report/tasks.py:29`), review (`review/tasks.py:33`) — один retry. На сетевых ошибках Anthropic это мало.
- **Health score хардкоден** в `report/health_score.py:51` — не учитывает growth_opportunities count, BusinessTruth confirmed/blind_spots ratio.

**Тесты:**
- **47 тестов на review** + **29 на priority** + **19 на report** — итого 95 тестов на «шаг 6». Покрыто: scorer, aggregator, scorer_phase_d, llm_enricher, llm_verify, hash_utils, diagnostic, helpers.
- Не покрыто: integration «pipeline → review_site → priority_rescore» chain. Дыра: 0 HTTP-тестов на `/priorities/sites/{id}/rescore`.

**Хрупкости:**
- **`opportunities` пишется в `target_config.growth_opportunities`** (`competitors/tasks.py:593`) — тот же JSONB race с BusinessTruth и competitor_profile. **3 параллельных писателя в один JSONB**.
- **Review depends на `target_clusters`** (`PriorityService._build_phase_d_site_context` — это значит, что если demand_map пустой, scorer работает в legacy-режиме без knowing что делать с фразами без связки кластер↔intent.
- **Report зависит от week_end** хардкод-логики `default_week_end` — генерация в среду посреди недели даст частично пустые секции.
- **PriorityService.rescore_site** не emit'ит started/done в `priority/tasks.py:50-77` — emit делается на уровне task, но `priority/service.py:42-110` сам по себе может занять 30+ секунд на сайте с 1000 рекомендаций. На owner-side только финальный «Приоритеты пересчитаны» сообщение.

**Оценка: 2/5** — формально 95 тестов на domain logic, но: **пайплайн его вообще не запускает**, **3 разных таска без shared run_id**, **JSONB race**, **opportunities прячется внутри deep_dive**, **`review_all_nightly` — единственный реальный enforcement**. UX-разрыв: владелец жмёт «Быстрый анализ» и получает рекомендации **вчерашней ночи**. Это не реализация шага, это симуляция.

---

## Оркестрация в целом

### Зависимости между шагами

**Идеальная схема (логическая):**
```
Шаг 1 (crawl) ──┐
Шаг 2 (webmaster) ──┼─→ Шаг 3 (demand_map) ──→ Шаг 4 (BusinessTruth) ──→ Шаг 5 (competitors) ──→ Шаг 6 (recommendations)
```

**Реальная схема в `trigger_full_pipeline`:**
```
Шаг 1 (crawl) ─┐
Шаг 2 (webmaster) ─┼─ запущены ОДНОВРЕМЕННО, без зависимостей
Шаг 3 (demand_map) ─┘

Шаг 4, 5, 6 — НЕ ЗАПУСКАЮТСЯ из этой кнопки.
```

Зависимости НЕ enforced:
- **Шаг 4 (BusinessTruth) читает Page (Шаг 1) + SearchQuery (Шаг 2) + DailyMetric (Шаг 2)**, но запускается отдельной кнопкой `/admin/sites/{id}/business-truth/rebuild`. Если владелец жмёт rebuild сразу после «Быстрый анализ» — Celery worker может сделать BT **раньше**, чем закончился crawl/webmaster (concurrency=2). **Race без enforcement**.
- **Шаг 5 (competitors) читает SearchQuery + TargetCluster (Шаг 3)**, но triggered отдельно из `/competitors` страницы.
- **Шаг 6.1 (opportunities) внутри Шага 5**, требует Шаг 1 (own_pages). При плохом краwle (0 страниц) opportunities скорее всего пустой.
- **Шаг 6.2 (priorities) читает PageReview**, который пишется в Шаге 6.3 (review). 6.3 запускается **только в beat 04:45 UTC** или ручной кнопкой. **Между «Быстрый анализ» и priorities лежит ночной cron.**

**Что должно быть:** Celery `chain()` или явный chord:
```python
chain(
    group(crawl_site.s(sid, run_id=rid), collect_site_webmaster.s(sid, run_id=rid)),
    demand_map_build_site_task.s(sid, run_id=rid),
    business_truth_rebuild_site_task.s(sid, run_id=rid),
    competitors_discover_site_task.s(sid, run_id=rid),
    review_site_decisions_task.s(sid),
    priority_rescore_site.s(sid),
    report_build_site.s(sid),
)()
```
Сейчас вместо chain'а — `for task in queued: send_task` (`admin_ops.py:108-111`) — fire-and-forget без зависимостей.

### Поведение при провале

- **Crawl упал** → `emit_terminal('crawl','failed')` (`collectors/tasks.py:273-278`). pipeline:started ждёт остальные 2 стейджа; webmaster и demand_map продолжают (демand_map даже без свежего crawl работает на target_config). Когда все 3 закроются — `_pipeline_terminal_status(['failed','done','done']) → 'failed'` (`activity.py:36-37`). **Pipeline закроется как failed.**
- **Webmaster `host_not_loaded`** → `emit_terminal('webmaster','skipped')`. Стейдж считается терминальным. Pipeline завершится как `skipped` (priority skipped > done в `_pipeline_terminal_status`).
- **demand_map `no_target_config`** → skipped без started. Pipeline ждёт всех 3, при `[done, done, skipped]` → `skipped`.
- **demand_map crash в expansion** (`demand_map/tasks.py:108-114`) → emit_terminal failed → raise → Celery retry (`max_retries=1`) → может породить второй emit_terminal failed → всё равно invariant держится (latest = failed).
- **BusinessTruth не запущен** → не пострадает, потому что его в queued нет. UI не отобразит «BT обновлён».
- **Competitors не запущен** → owner получит pipeline:done через 14-17 секунд, но «точки роста» — старые.
- **Priority/review/report** — не пострадают, не в pipeline.

**Deadletter:** в коде явно нет dead-letter queue. `max_retries=1` на большинстве задач, после — задача завершается с failure, emit_terminal failed, **никаких алертов наружу** кроме `analysis_event` row.

### Идемпотентность

| Шаг | Идемпотентность | Механизм |
|---|---|---|
| 1 crawl | **Полная** | `pg_insert(...).on_conflict_do_update` на (site_id, url) (`site_crawler.py:226-241`). Повторный краwl обновит, не задвоит. |
| 2 webmaster | **Полная** | `pg_insert(...).on_conflict_do_update` на (site_id, query_text) и (site_id, date, metric_type, dimension_id). |
| 3 demand_map | **Полная** | delete-then-insert внутри `persist_demand_map`. |
| 4 business_truth | **Полная** | Полная перезапись `target_config.business_truth` (`rebuild.py:223-235`). |
| 5 competitors | **Частичная** | `pg_try_advisory_lock` блокирует параллельный запуск (`tasks.py:242-255`). Сама запись — full overwrite competitor_profile/competitor_brands/growth_opportunities. **Но advisory_lock освобождается только при exit context, при kill-9 worker'a — лок остаётся до session timeout.** |
| 6.1 opportunities | **Полная** (через 5) | Перезапись growth_opportunities. |
| 6.2 priorities | **Полная** | rescore_site обнуляет старые скоры, перезаписывает новые. |
| 6.3 review | **С caveat** | Per-page hash check (`hash_utils.py`) пропускает если контент не изменился. Двойной запуск в один день ничего не поломает, но **новые review row's появятся, старые останутся** — ranking использует latest per (page_id, intent). |
| 6.4 report | **Через UNIQUE** | UNIQUE(site_id, week_end, builder_version) — повторный билд для той же недели апсертит. |

**Pipeline как целое — идемпотентность через 2-минутный dedup** (`admin_ops.py:57-75`): второй клик в течение 2 минут возвращает `{deduped: true, run_id: existing}`. После 2 минут — новый run_id, всё перезапустится. Реализация хрупкая: если первый pipeline застрял (pipeline:started без терминала), на 3-й минуте появляется второй, и UI смешает их — собственно для этого и нужен `current-run` endpoint с фильтром по `run_id` (`activity.py:104-133`).

### Согласованность данных

**Передача данных между шагами идёт через БД** — никаких `kwargs` с payload (только `site_id` + `run_id`). Это правильно:
- **Плюс:** независимый restart любой таски, нет проблем сериализации.
- **Минус:** если Шаг 4 запустился, пока Шаг 1 в полёте → читает старые Page rows. **Снимков/версионирования нет.**

**Критичный race condition: 3 параллельных writer'а в `site.target_config` (JSONB)**:
- `business_truth_rebuild_site` пишет `target_config.business_truth` (`rebuild.py:223-235`).
- `competitors_discover_site` пишет `target_config.competitor_profile`, `competitor_brands` (`competitors/tasks.py:332-338`).
- `competitors_deep_dive_site` пишет `target_config.competitor_deep_dive`, `growth_opportunities` (`competitors/tasks.py:519-595`).
- Onboarding chat пишет `target_config_draft.onboarding_chat` (`admin_demand_map.py:623-639` — отдельный draft столбец, тут безопасно).

Все три первых читают `cfg = dict(site.target_config or {})` → mutate → `site.target_config = cfg` → commit. **Last-write-wins**. При параллельном выполнении (а advisory lock на competitors — только между двумя competitors, не между competitors и BT) можно потерять competitor_profile, если BT успел commit раньше.

**Решение:** использовать JSONB partial update via SQL: `UPDATE sites SET target_config = jsonb_set(target_config, '{business_truth}', ...)`. Или вынести крупные блобы в отдельные таблицы (см. **HIGH-4 в db_performance**).

### Видимость для пользователя

**Каждый шаг emit'ит активити-события:**

| Шаг | started | progress | done | failed | skipped |
|---|---|---|---|---|---|
| 1 crawl | ✓ (`tasks.py:262`) | — | ✓ (`tasks.py:280`) | ✓ (`tasks.py:273`) | — |
| 2 webmaster | ✓ (`tasks.py:206`) | — | ✓/skipped (`tasks.py:228`) | ✓ (`tasks.py:218`) | ✓ (через `_format_webmaster_result`) |
| 3 demand_map | ✓ (`tasks.py:96`) | — | ✓ (`tasks.py:159`) | ✓ (`tasks.py:109`) | ✓ (`tasks.py:75, 84`) |
| 4 BT | ✓ (`tasks.py:42`) | — | ✓ (`tasks.py:62`) | ✓ (`tasks.py:51`) | — |
| 5.discovery | ✓ (`competitors/tasks.py:314`) | — | ✓ (`tasks.py:342`) | ✓ (`tasks.py:407`) | ✓ (`tasks.py:246, 297`) |
| 5.deep_dive | ✓ (`tasks.py:466`) | ✓ (`tasks.py:505`) | ✓ (`tasks.py:536`) | ✓ (`tasks.py:634`) | ✓ (`tasks.py:455`) |
| 5.opportunities | — | — | ✓ (`tasks.py:601`) | (через deep_dive failed) | — |
| 6.1 priorities | ✓ (`priority/tasks.py:50`) | — | ✓ (`tasks.py:70`) | ✓ (`tasks.py:60`) | — |
| 6.2 review | — (`review/tasks.py` — нет emit_terminal) | — | — | — | — |
| 6.3 report | — (`report/tasks.py` — нет emit_terminal) | — | — | — | — |

**Дыры в видимости:**
- **Review** (`review/tasks.py:33-91`) — **не emit'ит ни started, ни terminal**. Reviewer внутри тоже не пишет в analysis_events. Owner не видит, что ревью идёт. Это серьёзный gap, особенно для `review_site_decisions_task`, который занимает минуты.
- **Report** (`report/tasks.py:29-58`) — **не emit'ит ничего**. UI узнаёт о готовом отчёте через poll `/reports/sites/{id}/latest`.
- **Opportunities как сабстейдж** — есть только `done`, нет `started`. UI рисует чип «точки роста: —» пока deep_dive в процессе.
- **Crawl chain'ит fingerprint без emit** — `fingerprint_site.apply_async(countdown=10)` (`tasks.py:300`). Fingerprint task сам по себе emit'ит, но в pipeline он не считается.

**UI poll-частота** (см. **A-015 / LOW-3 в db_performance**):
- activity-feed.tsx → 5s
- last-run-summary.tsx → 4s
- overview.tsx → 10s
- competitors/page.tsx → 5-8s
- priorities/page.tsx → 5s

Каждый poll → `reconcile_open_pipelines` (см. **CRIT-2 / M-009 в SUMMARY**). На пустой БД — копейки. На зависшем pipeline (N>0 unclosed) — 18 reconciles/мин/viewer × (1+3N) запросов.

**Frontend chip-mapping** (`last-run-summary.tsx:38-41`):
```ts
const WORK_STAGES = ["crawl", "webmaster", "demand_map",
                     "competitor_discovery", "competitor_deep_dive", "opportunities"];
```
Это **6 чипов**, которые owner видит. Но `trigger_full_pipeline` запускает **3 первых**. После клика «Быстрый анализ» оставшиеся 3 чипа отображают данные **прошлого запуска competitors** (или «—», если никогда не было). Это и есть **mismatch между UX-нарративом «6 шагов» и реальностью «3 шага»**.

---

## Сводная таблица оценок

| Шаг | Реализация | Тесты | Хрупкость | Видимость в UI | Итог |
|---|---|---|---|---|---|
| 1 crawl | 3/5 | 1/5 (0 unit) | 3/5 (нет timeout, regex-парсинг) | 4/5 | **3/5** |
| 2 webmaster | 3/5 | 1/5 (0 unit) | 2/5 (N+1, plain token, формат API) | 4/5 | **3/5** |
| 3 demand_map | 4/5 | 5/5 (89 тестов) | 3/5 (sync I/O) | 4/5 | **4/5** |
| 4 BusinessTruth | 4/5 | 5/5 (107 тестов) | 3/5 (JSONB race, не в pipeline) | 4/5 | **4/5** |
| 5 competitors | 4/5 | 4/5 (29 + invariants) | 3/5 (race, sync urllib) | 5/5 | **4/5** |
| 6 recommendations | 2/5 | 4/5 (95 тестов на logic) | 2/5 (нет run_id, JSONB race, не в pipeline) | 1/5 (review/report тихие) | **2/5** |

**Среднее: 3.3/5.** Округлённо — **3/5** для всего пайплайна (см. TL;DR).

---

## Что в логике пайплайна сделано хорошо

1. **`run_id` контракт** (`admin_ops.py:82-111`) и его прокидка через `kwargs` во все 3 цепочки — UI отделяет «текущий клик» от «прошлый», тест `test_run_id_isolation.py` закреплён инвариантом. Эталон.

2. **`emit_terminal` invariant + 16 тестов в `test_pipeline_terminal_states.py`** — лучший test-файл в проекте. Покрывает happy path, failure, skip, concurrent runs, reconciliation, double-close, non-terminal rejection.

3. **`reconcile_open_pipelines` как самоисцеление** (`activity.py:297-382`) — даже если эмит-логика дала сбой (worker crash mid-task), backfill починит state через CRON-podstavku, **который пока стоит в GET endpoints** (см. **M-009**) и должен переехать в beat job.

4. **Auto-chain `competitors_discover_site → competitors_deep_dive`** (`competitors/tasks.py:361-364`) с прокидкой run_id — единственный правильно реализованный chain в коде.

5. **Advisory lock в competitors** (`tasks.py:38-40, 242-255`) — двойной клик «Разведка» возвращает skipped без SERP-вызовов.

6. **2-минутный dedup в `trigger_full_pipeline`** (`admin_ops.py:57-75`) — owner двойной щелчок не порождает два параллельных pipeline.

7. **Skipped как полноценный терминал** (`activity.py:24, 38-39`) — `host_not_loaded`, `no_target_config`, `no_queries_available` корректно обработаны как «закрылись без новых данных», а не как failure. Это **уважение к нетехническому owner'у**.

8. **`onboarding/gate.py:onboarded_site_ids`** (`celery_app.py:beat → review_all_nightly → onboarded_site_ids_with`) — nightly job берёт **только** сайты с `onboarding_step="active"`. Manual triggers намеренно bypass'ят. **Защищает от рекомендаций для сайтов в полу-онбординге.**

9. **`emit_terminal` raises на non-terminal status** (`activity.py:251-254`) — нельзя случайно вызвать с `status="started"`. Контракт enforced на уровне runtime.

10. **2-режимная логика закрытия pipeline** (`activity.py:262-283`) — новая через `queued`-контракт + legacy fallback. **Можно постепенно мигрировать существующие pipeline-task'ы**, не ломая совместимость с историческими событиями.

---

## Топ-5 что починить в первую очередь

### R1. Расширить `trigger_full_pipeline` до настоящего «6-шагового» (1-2 дня)

Сейчас pipeline = 3 шага. UX-нарратив = 6 шагов. Это разрыв ожиданий. Минимально:

```python
# admin_ops.py:trigger_full_pipeline
task_to_stage = {
    "crawl_site": "crawl",
    "collect_site_webmaster": "webmaster",
    "demand_map_build_site": "demand_map",
}
queued = list(task_to_stage.values()) + [
    "business_truth", "competitor_discovery",
    "competitor_deep_dive", "opportunities",
    "priorities",
]
# Шаги 1-3 параллельно, потом chain BT → competitors → priorities
chain(
    group(*[celery_app.signature(t, args=[sid], kwargs={"run_id": rid})
            for t in task_to_stage]),
    business_truth_rebuild_site_task.s(sid, run_id=rid),
    competitors_discover_site_task.s(sid, run_id=rid),
    priority_rescore_site.s(sid),  # нужен run_id - добавить kwarg
)()
```

Параллельно добавить `run_id` параметр в `priority_rescore_site` (`priority/tasks.py:45`), `review_site_decisions_task` (`review/tasks.py:55`), `report_build_site` (`report/tasks.py:30`) + emit_terminal в каждом.

### R2. Добавить `task_time_limit=420` (15 минут) — см. CRIT-1

Прямое решение **зависающего pipeline**: `_active_pipeline_started` cutoff = 10 мин. Если crawl/deep_dive виснет на 15+ мин → cutoff истёк → emit_terminal не закроет pipeline → UI висит в «идёт сейчас», пока reconcile не починит.

```python
# workers/celery_app.py
celery_app.conf.update(
    task_soft_time_limit=300,  # 5 min graceful
    task_time_limit=420,       # 7 min hard SIGKILL
)
```

### R3. Мигрировать `_make_session()` → `task_session()` в `collectors/tasks.py` (4 часа)

11 вызовов в 2 файлах. См. **M-010 в SUMMARY**. Прямое влияние на стабильность Шагов 1+2 — сейчас они единственные стейджи на дырявом engine.

### R4. Вытащить `reconcile_open_pipelines` в beat job (2 часа)

Прямо влияет на видимость. Сейчас reconcile дёргается на каждом GET activity (3 endpoints × 5s poll = 18/мин/viewer). Дополнительная нагрузка + write на каждом read. См. **M-009 / CRIT-2**.

```python
# celery_app.beat_schedule
"reconcile-pipelines-30s": {
    "task": "reconcile_all_open_pipelines",
    "schedule": 30.0,
},
```

И убрать `await reconcile_open_pipelines(db, site_id)` из `activity.py:53, 71, 104`.

### R5. Закрыть JSONB race на `site.target_config` (1 день)

3 параллельных writer'а в один JSONB. Минимально — один advisory lock на `target_config` updates:

```python
# helper в activity.py
async def update_site_config(db, site_id, key, value):
    lock_key = _advisory_key(site_id) + 1  # offset чтобы не коллидировать с competitors lock
    async with _advisory_lock(db, lock_key):
        site = await db.get(Site, site_id, with_for_update=True)
        cfg = dict(site.target_config or {})
        cfg[key] = value
        site.target_config = cfg
        await db.commit()
```

Затем заменить 3 места: `business_truth/rebuild.py:223-235`, `competitors/tasks.py:332-338`, `competitors/tasks.py:519-595`.

Альтернатива (правильнее, но дороже): вынести `business_truth`, `competitor_profile`, `growth_opportunities` в отдельные таблицы. См. **HIGH-4 в db_performance**.

---

## Финальное замечание

Пайплайн **не плох** — но он **наполовину достроен**. Hardening sprint закрыл event-driven корректность (run_id, emit_terminal, reconcile, инварианты в тестах) — это видно и закреплено. Но **сам набор шагов, которые попадают в pipeline, — это 3 из 6 заявленных**. Остальные 3 живут на отдельных кнопках или ночных cron'ах.

Это **сознательное решение** (см. комментарий в `admin_ops.py:88-92` про competitors и smelter-spam) или **техдолг** — судить по комментариям сложно. Но в любом случае текущее положение даёт **разрыв между UX-нарративом «жми и получишь анализ» и реальностью «жми, дальше жми ещё, потом жди ночи»**. Закрытие R1 (single-click chain'ит все 6 шагов) — это, IMHO, главный sprint following hardening.

Параллельно фундаментальные находки **уже зафиксированы в существующих аудитах** и не требуют повторной работы:
- M-001..M-008 (security) — см. SUMMARY.
- CRIT-1..3, HIGH-1..6 (DB) — см. db_performance.
- C1..C3, H1..H5 (backend code) — см. backend_code_tests.

Их закрытие даст пайплайну **B+** оценку без изменения архитектуры.
