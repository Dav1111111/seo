"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useSWRConfig } from "swr";
import {
  Play,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Sparkles,
  Clock,
  ArrowRight,
  RotateCcw,
} from "lucide-react";

import { api } from "@/lib/api";
import { useSite } from "@/lib/site-context";

/**
 * One-click "run everything" button for the studio home.
 *
 * Calls `POST /admin/sites/{id}/pipeline/full` (a chord:
 *   crawl + webmaster + demand_map  →  pipeline_after_primary
 *   → competitors → opportunities → intent_decide → review →
 *     priorities → report)
 * then polls `GET /sites/{id}/activity/current-run` every 4s to render
 * a per-stage checklist. Once `stage="pipeline" status="done"|"failed"`
 * shows up, polling stops and SWR caches that depend on the run state
 * (brain-plan, pages list, etc.) are invalidated so the owner sees the
 * fresh result without a manual refresh.
 *
 * 2-minute server-side dedup means a double-click is harmless.
 */

const POLL_INTERVAL_MS = 4000;
const MAX_POLL_TIME_MS = 8 * 60 * 1000; // safety net — pipeline timeout is 6 min
const AVG_TOTAL_MS = 10 * 60 * 1000; // ~10 minutes typical end-to-end
const DONE_VISIBLE_MS = 3 * 60 * 1000; // big success card stays visible 3 min

// Stages we expect from a full run, in display order. Each stage
// surfaces both `started` and `done` events, so we render once per
// stage and switch on the latest status. `competitor_*`, `opportunities`,
// `indexnow` are conditionally skipped — we still display them so the
// owner sees "пропущено" rather than wondering why they're missing.
const STAGES: Array<{ key: string; label: string }> = [
  { key: "crawl", label: "Просматриваю твои страницы" },
  { key: "webmaster", label: "Спрашиваю Яндекс.Вебмастер про индексацию" },
  { key: "demand_map", label: "Собираю что ищут в нише" },
  {
    key: "business_truth",
    label:
      "Сравниваю что мы знаем, что есть на сайте, и что приносит трафик",
  },
  { key: "competitor_discovery", label: "Ищу твоих конкурентов в выдаче" },
  { key: "competitor_deep_dive", label: "Изучаю страницы конкурентов" },
  { key: "opportunities", label: "Нахожу где можно вырасти" },
  { key: "intent_decide", label: "Решаю что делать с каждым запросом" },
  { key: "review", label: "Проверяю качество твоих страниц" },
  { key: "priorities", label: "Расставляю приоритеты" },
  { key: "report", label: "Готовлю итоговый отчёт" },
];

// Stages that commonly skip together on small sites (insufficient
// "money queries"). When all three are skipped we collapse them into
// a single explanatory line.
const COMPETITOR_GROUP = new Set([
  "competitor_discovery",
  "competitor_deep_dive",
  "opportunities",
]);

const TERMINAL_STATUSES = new Set(["done", "failed", "skipped"]);

type StageState = "pending" | "running" | "done" | "failed" | "skipped";

interface StageRow {
  key: string;
  label: string;
  state: StageState;
}

function eventStateForStage(events: Array<{ stage: string; status: string }>, key: string): StageState {
  // Prefer the most recent event for this stage. Backend emits events
  // in chronological order, so the LAST one wins.
  let latest: { status: string } | null = null;
  for (const e of events) {
    if (e.stage === key) latest = e;
  }
  if (!latest) return "pending";
  if (latest.status === "started") return "running";
  if (latest.status === "done") return "done";
  if (latest.status === "skipped") return "skipped";
  return "failed";
}

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  if (m === 0) return `${s} сек`;
  return `${m} мин ${s.toString().padStart(2, "0")} сек`;
}

function stageHint(state: StageState): string {
  if (state === "running") return "Идёт…";
  if (state === "done") return "Готово";
  if (state === "skipped") return "Пропущено — недостаточно данных";
  if (state === "failed") return "Ошибка";
  return "Ожидает";
}

export function RunFullAnalysisButton() {
  const { currentSite } = useSite();
  const { mutate } = useSWRConfig();
  const siteId = currentSite?.id;

  const [running, setRunning] = useState(false);
  const [stages, setStages] = useState<StageRow[] | null>(null);
  const [finalStatus, setFinalStatus] = useState<
    null | { kind: "ok" | "err"; msg: string; at: number }
  >(null);
  // 1-second tick so the elapsed/remaining UI updates smoothly while
  // running — independent of the 4s poll cadence.
  const [nowTick, setNowTick] = useState<number>(() => Date.now());

  // Polling guard — start time so we can bail out after MAX_POLL_TIME.
  const pollStartRef = useRef<number | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // run_id from the trigger response — we poll /activity (recent N
  // events of any status) and filter by this id. /activity/current-run
  // can't be used: it stops returning the run's events as soon as
  // pipeline:done lands, which is exactly the event we wait for.
  const runIdRef = useRef<string | null>(null);

  // Stop any in-flight polling on unmount.
  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    };
  }, []);

  // 1-second wall clock tick — only active while running or while the
  // success card is still in its visible window (so it can auto-collapse).
  useEffect(() => {
    const needsTick =
      running ||
      (finalStatus !== null && Date.now() - finalStatus.at < DONE_VISIBLE_MS);
    if (!needsTick) return;
    const id = setInterval(() => setNowTick(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running, finalStatus]);

  function clearPollTimer() {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }

  async function poll() {
    if (!siteId) return;
    try {
      // Pull a wide recent window (50 events) so we still catch
      // pipeline:done after the run is no longer "current". Then
      // filter to the run_id we got from the trigger.
      const res = await api.getActivity(siteId, 50);
      const all = res.events || [];
      const myRunId = runIdRef.current;
      const events = myRunId
        ? all.filter((e) => e.run_id === myRunId)
        : all;

      // Per-stage state from events.
      const rows: StageRow[] = STAGES.map((s) => ({
        key: s.key,
        label: s.label,
        state: eventStateForStage(events, s.key),
      }));
      setStages(rows);

      // Pipeline-level terminal: stage="pipeline" with done/failed.
      // events arrives newest-first from /activity, so iterate in chrono
      // order (reversed) to find the latest pipeline event for this run.
      const chrono = [...events].reverse();
      const pipelineEvents = chrono.filter((e) => e.stage === "pipeline");
      const lastPipeline = pipelineEvents[pipelineEvents.length - 1];

      if (lastPipeline && TERMINAL_STATUSES.has(lastPipeline.status)) {
        clearPollTimer();
        setRunning(false);
        if (lastPipeline.status === "failed") {
          setFinalStatus({
            kind: "err",
            msg: "Анализ прерван. Загляни в Activity справа — там детали.",
            at: Date.now(),
          });
        } else {
          setFinalStatus({
            kind: "ok",
            msg: "Обновил советы и приоритеты — данные свежие.",
            at: Date.now(),
          });
        }
        // Invalidate SWR caches that depend on this run.
        mutate(
          (key) => typeof key === "string" && (
            key.includes(siteId) || key.startsWith("studio-") || key.startsWith("brain-")
          ),
          undefined,
          { revalidate: true },
        );
        return;
      }

      // Safety bail-out so we never poll forever.
      if (
        pollStartRef.current &&
        Date.now() - pollStartRef.current > MAX_POLL_TIME_MS
      ) {
        clearPollTimer();
        setRunning(false);
        setFinalStatus({
          kind: "err",
          msg: "Слежу более 8 минут — что-то затянулось. Загляни в Activity.",
          at: Date.now(),
        });
        return;
      }

      pollTimerRef.current = setTimeout(poll, POLL_INTERVAL_MS);
    } catch (e) {
      // Don't break the UI on a transient network blip — just keep
      // trying until MAX_POLL_TIME or terminal event.
      pollTimerRef.current = setTimeout(poll, POLL_INTERVAL_MS);
    }
  }

  async function onRun() {
    if (!siteId || running) return;
    setRunning(true);
    setStages(null);
    setFinalStatus(null);
    pollStartRef.current = Date.now();
    setNowTick(Date.now());
    runIdRef.current = null;
    try {
      const res = await api.triggerFullAnalysis(siteId);
      // Pin the run_id we just got. Polling filters /activity events
      // by it so a stale prior run can't confuse the checklist.
      runIdRef.current = res.run_id || null;
      // Kick off polling immediately so the checklist appears right away.
      poll();
    } catch (e) {
      setRunning(false);
      pollStartRef.current = null;
      runIdRef.current = null;
      setFinalStatus({
        kind: "err",
        msg: e instanceof Error ? e.message : "Не удалось запустить анализ",
        at: Date.now(),
      });
    }
  }

  function resetToIdle() {
    setFinalStatus(null);
    setStages(null);
  }

  if (!siteId) return null;

  // ── Derive view state ─────────────────────────────────────────────
  const finishedRecently =
    !running &&
    finalStatus !== null &&
    nowTick - finalStatus.at < DONE_VISIBLE_MS;

  const view: "idle" | "running" | "done" | "error" | "stale-done" = running
    ? "running"
    : finishedRecently
    ? finalStatus!.kind === "ok"
      ? "done"
      : "error"
    : finalStatus !== null
    ? "stale-done"
    : "idle";

  // ── Running-state derived values ──────────────────────────────────
  const completed = stages
    ? stages.filter((s) => s.state === "done" || s.state === "skipped").length
    : 0;
  const total = STAGES.length;
  const progressPct = Math.min(
    100,
    Math.round((completed / total) * 100),
  );
  const elapsedMs = pollStartRef.current ? nowTick - pollStartRef.current : 0;
  const remainingMs = AVG_TOTAL_MS - elapsedMs;
  const remainingLabel =
    remainingMs > 60_000
      ? `осталось примерно ${Math.max(1, Math.round(remainingMs / 60_000))} мин`
      : remainingMs > 0
      ? "осталось меньше минуты"
      : "немного дольше обычного — ИИ ещё работает";

  // Collapse competitor group if all three are skipped together.
  const competitorRows = stages
    ? stages.filter((s) => COMPETITOR_GROUP.has(s.key))
    : [];
  const collapseCompetitors =
    competitorRows.length === 3 &&
    competitorRows.every((s) => s.state === "skipped");
  const visibleStages = stages
    ? collapseCompetitors
      ? stages.filter((s) => !COMPETITOR_GROUP.has(s.key))
      : stages
    : [];

  // ── Render ────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col gap-3 items-stretch sm:max-w-[420px] w-full">
      <div aria-live="polite" className="sr-only">
        {view === "running" && "Сейчас анализирую твой сайт"}
        {view === "done" && "Анализ готов"}
        {view === "error" && "Анализ завершился с ошибкой"}
      </div>

      {view === "idle" && (
        <IdleState onRun={onRun} />
      )}

      {view === "running" && (
        <RunningState
          completed={completed}
          total={total}
          progressPct={progressPct}
          elapsedLabel={formatElapsed(elapsedMs)}
          remainingLabel={remainingLabel}
          stages={visibleStages}
          collapseCompetitors={collapseCompetitors}
        />
      )}

      {view === "done" && (
        <DoneState onRunAgain={() => { resetToIdle(); onRun(); }} />
      )}

      {view === "error" && (
        <ErrorState msg={finalStatus!.msg} onRetry={() => { resetToIdle(); onRun(); }} />
      )}

      {view === "stale-done" && finalStatus!.kind === "ok" && (
        <StaleDoneLine onRun={onRun} />
      )}

      {view === "stale-done" && finalStatus!.kind === "err" && (
        <ErrorState msg={finalStatus!.msg} onRetry={() => { resetToIdle(); onRun(); }} />
      )}
    </div>
  );
}

// ── State views ────────────────────────────────────────────────────

function IdleState({ onRun }: { onRun: () => void }) {
  return (
    <div className="flex flex-col gap-2 items-stretch">
      <button
        type="button"
        onClick={onRun}
        aria-label="Запустить полный анализ сайта"
        className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-5 py-3 text-base font-semibold text-primary-foreground shadow-sm hover:bg-primary/90 transition-colors"
      >
        <Play className="h-5 w-5" aria-hidden />
        Запустить полный анализ
      </button>
      <p className="text-sm text-muted-foreground leading-snug">
        За 5–15 минут помощник сам пройдёт по твоему сайту, проверит
        Яндекс.Вебмастер, найдёт конкурентов и обновит советы.
      </p>
      <p className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <Clock className="h-3.5 w-3.5" aria-hidden />
        обычно занимает 5–15 минут
      </p>
    </div>
  );
}

function RunningState({
  completed,
  total,
  progressPct,
  elapsedLabel,
  remainingLabel,
  stages,
  collapseCompetitors,
}: {
  completed: number;
  total: number;
  progressPct: number;
  elapsedLabel: string;
  remainingLabel: string;
  stages: StageRow[];
  collapseCompetitors: boolean;
}) {
  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50/60 dark:border-blue-900/40 dark:bg-blue-950/30 p-4 flex flex-col gap-3">
      <div className="flex items-start gap-2.5">
        <Loader2
          className="h-5 w-5 animate-spin text-blue-600 dark:text-blue-400 flex-shrink-0 mt-0.5"
          aria-hidden
        />
        <div className="flex flex-col">
          <h3 className="text-base font-semibold text-blue-900 dark:text-blue-100 leading-snug">
            Сейчас анализирую твой сайт…
          </h3>
          <p className="text-xs text-blue-900/70 dark:text-blue-200/70 mt-0.5">
            Можно закрыть это окно и зайти позже — анализ доделается сам.
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between text-xs font-medium text-blue-900 dark:text-blue-100">
          <span>{completed} из {total} этапов готово</span>
          <span className="text-blue-900/70 dark:text-blue-200/70 font-normal">
            идёт {elapsedLabel} · {remainingLabel}
          </span>
        </div>
        <div
          role="progressbar"
          aria-valuenow={progressPct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Прогресс анализа"
          className="h-2 w-full rounded-full bg-blue-100 dark:bg-blue-900/40 overflow-hidden"
        >
          <div
            className="h-full bg-blue-600 dark:bg-blue-400 transition-[width] duration-500 ease-out"
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      <ul className="flex flex-col gap-1.5 pt-1">
        {stages.map((s) => (
          <li
            key={s.key}
            className="flex items-start gap-2 text-xs leading-snug"
          >
            <StageIcon state={s.state} />
            <div className="flex flex-col">
              <span
                className={
                  s.state === "pending"
                    ? "text-muted-foreground"
                    : s.state === "running"
                    ? "text-foreground font-medium"
                    : s.state === "skipped"
                    ? "text-muted-foreground"
                    : s.state === "failed"
                    ? "text-red-700 dark:text-red-400"
                    : "text-foreground"
                }
              >
                {s.label}
              </span>
              <span
                className={
                  s.state === "running"
                    ? "text-[10px] text-blue-700 dark:text-blue-300"
                    : s.state === "failed"
                    ? "text-[10px] text-red-600 dark:text-red-400"
                    : "text-[10px] text-muted-foreground/80"
                }
              >
                {stageHint(s.state)}
              </span>
            </div>
          </li>
        ))}
        {collapseCompetitors && (
          <li className="flex items-start gap-2 text-xs leading-snug">
            <CheckCircle2
              className="h-3.5 w-3.5 text-muted-foreground/60 mt-0.5 flex-shrink-0"
              aria-hidden
            />
            <span className="text-muted-foreground">
              Конкуренты и точки роста — пропущены: у сайта мало запросов
              для разбора (нужно собрать ≥5 «денежных» запросов за последние
              90 дней).
            </span>
          </li>
        )}
      </ul>
    </div>
  );
}

function DoneState({ onRunAgain }: { onRunAgain: () => void }) {
  return (
    <div className="rounded-lg border border-emerald-300/60 bg-emerald-50 dark:bg-emerald-950/40 dark:border-emerald-900/50 p-4 flex flex-col gap-3">
      <div className="flex items-start gap-2.5">
        <Sparkles
          className="h-5 w-5 text-emerald-600 dark:text-emerald-400 flex-shrink-0 mt-0.5"
          aria-hidden
        />
        <div className="flex flex-col">
          <h3 className="text-base font-semibold text-emerald-900 dark:text-emerald-100 leading-snug">
            Анализ готов!
          </h3>
          <p className="text-sm text-emerald-900/80 dark:text-emerald-200/80 mt-0.5">
            Обновил советы и приоритеты — данные свежие.
          </p>
        </div>
      </div>

      <div className="flex flex-col sm:flex-row gap-2">
        <Link
          href="/studio"
          aria-label="Смотреть свежие рекомендации"
          className="inline-flex items-center justify-center gap-2 rounded-md bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-emerald-700 transition-colors"
        >
          Смотреть свежие рекомендации
          <ArrowRight className="h-4 w-4" aria-hidden />
        </Link>
        <button
          type="button"
          onClick={onRunAgain}
          aria-label="Запустить анализ ещё раз"
          className="inline-flex items-center justify-center gap-1.5 rounded-md border border-emerald-300/60 bg-transparent px-4 py-2.5 text-sm font-medium text-emerald-900 dark:text-emerald-100 hover:bg-emerald-100/60 dark:hover:bg-emerald-900/30 transition-colors"
        >
          <RotateCcw className="h-4 w-4" aria-hidden />
          Запустить ещё раз
        </button>
      </div>
    </div>
  );
}

function StaleDoneLine({ onRun }: { onRun: () => void }) {
  return (
    <div className="flex flex-col gap-2 items-stretch">
      <button
        type="button"
        onClick={onRun}
        aria-label="Запустить полный анализ сайта"
        className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-5 py-3 text-base font-semibold text-primary-foreground shadow-sm hover:bg-primary/90 transition-colors"
      >
        <Play className="h-5 w-5" aria-hidden />
        Запустить полный анализ
      </button>
      <p className="inline-flex items-center gap-1.5 text-xs text-emerald-700 dark:text-emerald-400">
        <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
        Прошлый анализ завершён успешно.
      </p>
    </div>
  );
}

function ErrorState({ msg, onRetry }: { msg: string; onRetry: () => void }) {
  return (
    <div className="rounded-lg border border-red-300/60 bg-red-50 dark:bg-red-950/40 dark:border-red-900/50 p-4 flex flex-col gap-3">
      <div className="flex items-start gap-2.5">
        <AlertCircle
          className="h-5 w-5 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5"
          aria-hidden
        />
        <div className="flex flex-col">
          <h3 className="text-base font-semibold text-red-900 dark:text-red-100 leading-snug">
            Анализ не доделался
          </h3>
          <p className="text-sm text-red-900/80 dark:text-red-200/80 mt-1">
            {msg}
          </p>
          <p className="text-xs text-red-900/70 dark:text-red-200/70 mt-2">
            Попробуй запустить ещё раз. Если повторится — загляни в Activity
            справа: там видно, на каком шаге сорвалось.
          </p>
        </div>
      </div>
      <button
        type="button"
        onClick={onRetry}
        aria-label="Запустить анализ ещё раз"
        className="inline-flex items-center justify-center gap-1.5 rounded-md bg-red-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-red-700 transition-colors self-start"
      >
        <RotateCcw className="h-4 w-4" aria-hidden />
        Запустить ещё раз
      </button>
    </div>
  );
}

function StageIcon({ state }: { state: StageState }) {
  if (state === "running") {
    return (
      <Loader2
        className="h-3.5 w-3.5 animate-spin text-blue-600 dark:text-blue-400 mt-0.5 flex-shrink-0"
        aria-hidden
      />
    );
  }
  if (state === "done") {
    return (
      <CheckCircle2
        className="h-3.5 w-3.5 text-emerald-600 mt-0.5 flex-shrink-0"
        aria-hidden
      />
    );
  }
  if (state === "failed") {
    return (
      <AlertCircle
        className="h-3.5 w-3.5 text-red-600 mt-0.5 flex-shrink-0"
        aria-hidden
      />
    );
  }
  if (state === "skipped") {
    return (
      <CheckCircle2
        className="h-3.5 w-3.5 text-muted-foreground/60 mt-0.5 flex-shrink-0"
        aria-hidden
      />
    );
  }
  // pending
  return (
    <span
      aria-hidden
      className="inline-block h-3.5 w-3.5 rounded-full border border-muted-foreground/40 mt-0.5 flex-shrink-0"
    />
  );
}
