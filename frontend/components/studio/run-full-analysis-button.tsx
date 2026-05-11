"use client";

import { useEffect, useRef, useState } from "react";
import { useSWRConfig } from "swr";
import { Play, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";

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

// Stages we expect from a full run, in display order. Each stage
// surfaces both `started` and `done` events, so we render once per
// stage and switch on the latest status. `competitor_*`, `opportunities`,
// `indexnow` are conditionally skipped — we still display them so the
// owner sees "пропущено" rather than wondering why they're missing.
const STAGES: Array<{ key: string; label: string }> = [
  { key: "crawl", label: "Обхожу сайт" },
  { key: "webmaster", label: "Тяну Webmaster" },
  { key: "demand_map", label: "Карта спроса" },
  { key: "business_truth", label: "Сверяю три картины" },
  { key: "competitor_discovery", label: "Ищу конкурентов" },
  { key: "competitor_deep_dive", label: "Разбираю конкурентов" },
  { key: "opportunities", label: "Точки роста" },
  { key: "intent_decide", label: "Решения по запросам" },
  { key: "review", label: "Ревью страниц" },
  { key: "priorities", label: "Приоритеты" },
  { key: "report", label: "Отчёт" },
];

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

export function RunFullAnalysisButton() {
  const { currentSite } = useSite();
  const { mutate } = useSWRConfig();
  const siteId = currentSite?.id;

  const [running, setRunning] = useState(false);
  const [stages, setStages] = useState<StageRow[] | null>(null);
  const [finalStatus, setFinalStatus] = useState<
    null | { kind: "ok" | "err"; msg: string }
  >(null);

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
        const okStages = rows.filter(
          (r) => r.state === "done" || r.state === "skipped",
        ).length;
        if (lastPipeline.status === "failed") {
          setFinalStatus({
            kind: "err",
            msg: "Анализ прерван. Загляни в Activity справа — там детали.",
          });
        } else {
          setFinalStatus({
            kind: "ok",
            msg: `Готово. Прошло ${okStages} из ${STAGES.length} этапов. ` +
              `Обнови страницу или зайди в «Страницы» — данные свежие.`,
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
      });
    }
  }

  if (!siteId) return null;

  return (
    <div className="flex flex-col gap-2 items-stretch sm:items-end">
      <button
        type="button"
        onClick={onRun}
        disabled={running}
        className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
      >
        {running ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        ) : (
          <Play className="h-4 w-4" aria-hidden />
        )}
        {running ? "Анализ идёт…" : "Запустить полный анализ"}
      </button>
      <p className="text-[11px] text-muted-foreground sm:max-w-[260px] sm:text-right leading-tight">
        Один прогон: сайт обходится, Webmaster проверяется, ищутся конкуренты,
        находятся точки роста, пересчитываются приоритеты.
      </p>

      {stages && (
        <div className="rounded-md border bg-card px-3 py-2 sm:max-w-[340px] w-full">
          <ul className="space-y-1">
            {stages.map((s) => (
              <li
                key={s.key}
                className="flex items-center gap-2 text-xs leading-tight"
              >
                <StageIcon state={s.state} />
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
                  {s.state === "skipped" && (
                    <span className="ml-1 text-[10px]">(пропущено)</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {finalStatus && (
        <div
          className={`flex items-start gap-2 rounded-md border px-3 py-2 text-xs leading-snug sm:max-w-[340px] ${
            finalStatus.kind === "ok"
              ? "border-emerald-300/40 bg-emerald-50 text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200"
              : "border-red-300/40 bg-red-50 text-red-900 dark:bg-red-950/40 dark:text-red-200"
          }`}
        >
          {finalStatus.kind === "ok" ? (
            <CheckCircle2 className="h-4 w-4 flex-shrink-0 mt-0.5" />
          ) : (
            <AlertCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
          )}
          <span>{finalStatus.msg}</span>
        </div>
      )}
    </div>
  );
}

function StageIcon({ state }: { state: StageState }) {
  if (state === "running") {
    return <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-600" aria-hidden />;
  }
  if (state === "done") {
    return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" aria-hidden />;
  }
  if (state === "failed") {
    return <AlertCircle className="h-3.5 w-3.5 text-red-600" aria-hidden />;
  }
  if (state === "skipped") {
    return (
      <CheckCircle2
        className="h-3.5 w-3.5 text-muted-foreground/60"
        aria-hidden
      />
    );
  }
  // pending
  return (
    <span
      aria-hidden
      className="inline-block h-3.5 w-3.5 rounded-full border border-muted-foreground/40"
    />
  );
}
