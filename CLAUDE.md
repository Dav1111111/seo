# Yandex Growth Tower — guide for AI assistants

This file is the entry point for any AI/dev assistant working on this
repo. Read it first; it overrides assumptions from training data.

## Domain in one paragraph

Owner-facing SEO assistant tailored to **Yandex 2026** for **Russian
tourism** (current pilot: `grandtourspirit.ru` — экскурсии в Сочи).
Backend pulls Webmaster + Wordstat + Yandex SERP + Search API, crawls
the owner's site, classifies queries, finds competitors, generates
per-page recommendations with priority, and shows everything as one
"что делать на этой неделе" plan in a Studio UI.

Philosophy: **прозрачность над магией**. Every number has a source;
LLM is asked to summarize, never to fabricate.

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, Pydantic v2 |
| Workers | Celery 5 + Redis 7 |
| DB | Postgres 16 |
| LLM | Anthropic Claude (Haiku for cheap calls, Sonnet for hard ones) |
| Frontend | Next.js 16 (App Router), React 19, Tailwind 4, SWR, shadcn-style UI |
| Deploy | Single Jino VPS, Docker Compose, nginx (TLS terminated by Jino LB) |

## Where things live

- `backend/app/api/v1/` — FastAPI routes. **`studio.py` is a god-file**;
  prefer creating new routers if you add a domain.
- `backend/app/collectors/` — outbound: Yandex Webmaster, Wordstat,
  Search API, site crawler.
- `backend/app/core_audit/` — domain core. Sub-packages = modules:
  - `brain/` — snapshot + rules + battle plan + free chat. The
    "что делать" view.
  - `business_truth/` — three-source reconcile (understanding × content
    × traffic), finds blind spots.
  - `competitors/` — discovery (SERP), deep_dive (per-site crawl),
    opportunities, content_gap.
  - `demand_map/` — Wordstat-driven demand registry, target_clusters.
  - `review/` — per-page Python checks + optional LLM enrichment.
  - `priority/` — Impact × Confidence × Ease scorer + aggregator.
  - `outcomes/` — applied → 14-day delta tracking.
  - `behavioral/` — CTR-gap detector (added 2026-05-07; reads from
    daily_metrics, no Metrica required).
  - `lateral/` — weekly LLM proposal of adjacent query ideas (added
    2026-05-11, Block A of the autonomous-helper roadmap). Writes to
    `lateral_queries` table; owner triages via PATCH endpoint.
    Persistence helper protects owner status (accepted/rejected/
    promoted are immutable by the LLM).
- `backend/app/agents/` — LLM client + budget; `task_generator.py` is
  a separate per-task LLM path.
- `backend/app/profiles/tourism/` — vertical-specific rules: E-E-A-T
  (РТО, ИНН), commercial factors (бронь, цены), required H2.
- `frontend/app/studio/` — owner UI (replaces legacy `/competitors`
  `/priorities` `/reports`).
- `docs/studio/{CONCEPT,IMPLEMENTATION,IMPLEMENTATION-V2}.md` — current
  product vision.
- `audits/` — historical audits. `2026-05-02_full_project_audit.md` is
  the most recent comprehensive audit; `2026-05-07*` adds functional
  verification.

## How to run

**Tests** — only via Docker, the `backend/.venv` on dev machines is
typically out of sync.

```bash
# All backend tests
docker compose exec backend pytest tests -q

# Single module
docker compose exec backend pytest tests/core_audit/test_brain_snapshot.py -q

# With coverage by directory
docker compose exec backend pytest tests/priority -q
```

**Local backend (rare)**: requires Python 3.12 and `DATABASE_URL` env;
if your `python3 --version` is 3.10 or 3.11, recreate the venv.

**Frontend**:
```bash
cd frontend
npm run build      # production build (used in Docker)
npm run dev        # dev server, port 3000
npm run lint
```

**Production deploy**: `git pull` on the Jino VPS at `/root/seo`,
then `docker compose up -d --build`. nginx terminates HTTP on port 80;
Jino LB does TLS on the way in.

## Hard rules — do not break

1. **Pipeline cascade invariant.** Every `pipeline:started` must
   eventually receive a terminal (`done` / `failed` / `skipped`) on
   each queued stage. If you add a new error path in
   `competitors/tasks.py` or `pipeline/tasks.py`, you must call
   `_skip_after_competitor_stop` + `_queue_review_chain` (or emit
   terminals manually). Watchdog `pipeline_reconcile_sweep` cannot
   recover gaps where stages have no terminal events at all.
2. **Celery tasks always use `task_session()`** from
   `app/workers/db_session.py`. Never `async_sessionmaker` directly
   inside a task.
3. **`run_id` propagates** through every chained task as kwarg. Do
   not drop it; the activity feed groups events by run_id.
4. **No LLM call without budget guard** awareness. When adding a new
   LLM-using module, log to `agent_runs` table (cost_usd + model +
   site_id) so the owner can see what they're paying for.
5. **No fabrication in chat or recommendations.** `free_chat.py` has
   strict anti-hallucination rules; `review/` LLM enricher only
   rephrases findings the Python checks already detected.
6. **Migrations under load.** Avoid bare `ALTER COLUMN ... TYPE` on
   tables >100k rows. Use add-column + backfill in batches + swap.
   Set `lock_timeout` on any long DDL.
7. **Tenant scoping.** `Site.tenant_id` exists but `_site_or_404` in
   `studio.py` does NOT yet filter by it (single-tenant pilot). Before
   onboarding a second client, this MUST be enforced.

## Hard rules — frontend

1. **Default to Server Components.** Add `'use client'` only when you
   need state, effects, or browser APIs.
2. **No new `: any` in `lib/api.ts`.** When adding an endpoint, type
   its response. Migrating to `openapi-typescript` codegen is on the
   roadmap; in the meantime, type by hand.
3. **All studio data fetching uses `studioKey(siteId, ...)`** from
   `lib/api.ts`. SWR keys must be stable per site.
4. **Optimistic updates with rollback.** When mutating a list, follow
   the pattern in `studio/queries/page.tsx` (line ~358): apply local
   change, call API, on error revert.
5. **UI components from `components/ui/*` only.** No inline copy of
   button/card/badge styles.
6. **Money paths** (apply recommendation → outcome snapshot → 14-day
   measure) are atomic from the user's POV. If the second step fails,
   roll back the first and show an error — see
   `studio/pages/[page_id]/page.tsx` `changeRecStatus`.

## Glossary (project-specific terms)

- **strategic_focus** — owner-set narrowing: "сейчас работаем только
  над {products} в {regions}". Lives in `target_config`. Brain plan
  sorts focus-matching actions first.
- **business_truth** — the three-source reconcile (understanding ×
  content × traffic). Direction = service × geo. Finds blind spots
  (`is_blind_spot`, `is_traffic_only`, `is_content_only`,
  `is_aspiration`).
- **decision_tree** — LEAVE / STRENGTHEN / MERGE / SPLIT / CREATE /
  BLOCK_CREATE for every (intent, page) pair. In
  `core_audit/decision_tree.py`.
- **harmful visibility** — ranking on queries the site shouldn't be
  ranking for (relevance = `disputed` / `spam`). Surfaced by the brain
  as a critical action.
- **CTR-gap** (added 2026-05-07) — page ranks at position N but
  under-clicks the CTR benchmark for that position. Cheapest behavioral
  win — rewrite title/meta. See `behavioral/ctr_gap.py`.
- **strengthen-only philosophy** — for harmful or under-coverage
  queries, the system always recommends improving the existing page,
  not penalizing or hiding (anti-fraud-friendly).
- **lateral query** (added 2026-05-11) — an LLM-proposed query idea
  the site doesn't already track but plausibly should. Tagged
  direct/related/info/weak. Owner triages: accept → may promote to
  demand_map; reject → silenced forever. Once status leaves `new`,
  the LLM cannot re-touch it.

## What this project does NOT do (and won't, this iteration)

- **Backlinks / ссылочная масса** — no integration with Ahrefs,
  Megaindex, etc. Module out of scope.
- **Yandex Maps / Бизнес карточка** — not yet integrated. **This is
  the biggest gap for tourism**; on the roadmap.
- **Real Yandex Metrica behavioral data** — the collector is a stub;
  CTR-gap uses Webmaster (which is already in DB), not Metrica.
- **PageSpeed / Core Web Vitals** — not measured.
- **Yandex.Direct** — placeholder in the concept, not implemented.
- **Google** — Search Console + GA4 not wired.

## Quick commands cheat sheet

```bash
# Live prod check (replace with your actual host/key in ~/.ssh/config)
ssh jino 'docker ps && cd /root/seo && git log -1 --oneline'

# Tail backend logs
ssh jino 'docker logs -f --tail 100 seo-backend-1'

# Run a single task by hand on prod
ssh jino 'docker compose -f /root/seo/docker-compose.yml exec backend \
  python -c "from app.collectors.tasks import collect_webmaster_all_task; \
             collect_webmaster_all_task.apply_async()"'

# DB query on prod
ssh jino 'docker exec seo-db-1 psql -U tower -d growthtower -c "<sql>"'
```

## Recent significant changes

- **2026-05-07** — discovery cascade fix (intent_decide + downstream
  stages now always receive terminals on transient failures); SSRF
  allowlist in competitors/deep_dive; admin auth centralized via
  `secrets.compare_digest`; new `core_audit/behavioral/` package with
  CTR-gap detector and brain rule; pre-season + winter seasonality
  in priority scorer; nginx restricts /docs and /openapi.json to
  loopback + docker bridge.
- **2026-05-02** — full audit (`audits/2026-05-02_*`); P0 prod-readiness
  fixes (ports, secrets, standalone build).
- **2026-04-25 / 04-22** — strategic focus + chat phases A-E; visible
  platform sprint; hardening sprint with run_id and stage events.

When in doubt, read `audits/2026-05-02_full_project_audit.md` for the
big picture, then the relevant module's `__init__.py` for entry points.
