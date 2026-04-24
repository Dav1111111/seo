"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Activity, CheckCircle2, Loader2, AlertCircle, Clock,
} from "lucide-react";
import { cn } from "@/lib/utils";

const STAGE_LABEL: Record<string, string> = {
  crawl: "краулинг сайта",
  webmaster: "Вебмастер",
  demand_map: "карта спроса",
  competitor_discovery: "разведка конкурентов",
  competitor_deep_dive: "глубокий анализ",
  opportunities: "точки роста",
  review: "проверка страниц",
  report: "отчёт",
  priorities: "приоритеты",
  outcome: "результат",
  pipeline: "полный анализ",
  onboarding: "онбординг",
};

function timeAgo(iso: string): string {
  // Backend returns naive UTC iso ("2026-04-22T17:19:25.193") without
  // timezone marker. JS Date() treats a marker-less ISO as LOCAL time,
  // so an MSK user would see events 3 hours in the past. Force UTC.
  const utcIso = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z";
  const then = new Date(utcIso).getTime();
  const diffSec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (diffSec < 60) return "только что";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} мин назад`;
  if (diffSec < 86_400) return `${Math.floor(diffSec / 3600)} ч назад`;
  return `${Math.floor(diffSec / 86_400)} д назад`;
}

function StatusIcon({ status }: { status: string }) {
  if (status === "done")
    return <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" />;
  if (status === "started" || status === "progress")
    return <Loader2 className="h-4 w-4 text-primary shrink-0 animate-spin" />;
  if (status === "failed")
    return <AlertCircle className="h-4 w-4 text-rose-500 shrink-0" />;
  return <Clock className="h-4 w-4 text-muted-foreground shrink-0" />;
}

export function ActivityFeed({ siteId }: { siteId: string }) {
  const { data, isLoading } = useSWR(
    siteId ? `current-run-feed-${siteId}` : null,
    () => api.getCurrentRun(siteId),
    { refreshInterval: 5_000 },
  );

  const events = data?.events ?? [];

  // "Running" = per stage, the most recent event is NOT terminal.
  // A single stale "progress" row left in the feed shouldn't keep the
  // whole dashboard in a spinning state forever.
  const TERMINAL = new Set(["done", "failed", "skipped"]);
  const latestPerStage = new Map<string, { id: number; status: string }>();
  // Walk newest → oldest; first hit per stage wins.
  for (const e of events) {
    if (!latestPerStage.has(e.stage)) {
      latestPerStage.set(e.stage, { id: e.id, status: e.status });
    }
  }
  const hasRunning = [...latestPerStage.values()].some(
    (entry) => !TERMINAL.has(entry.status),
  );
  const visibleEvents = events.filter((e) => {
    const latest = latestPerStage.get(e.stage);
    if (!latest) return true;
    const supersededTransient =
      latest.id !== e.id &&
      (e.status === "started" || e.status === "progress") &&
      TERMINAL.has(latest.status);
    return !supersededTransient;
  });

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Activity
            className={cn(
              "h-4 w-4",
              hasRunning && "text-primary animate-pulse",
            )}
          />
          Лента активности
          {hasRunning && (
            <Badge variant="outline" className="text-[10px] border-primary text-primary">
              работает
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-10" />)}
          </div>
        ) : visibleEvents.length === 0 ? (
          <p className="text-sm text-muted-foreground italic py-4 text-center">
            Событий пока нет. Нажми «Запустить полный анализ» — появятся строки о том,
            что платформа делает прямо сейчас.
          </p>
        ) : (
          <ul className="space-y-2">
            {visibleEvents.map((e) => (
              <li
                key={e.id}
                className="flex items-start gap-3 text-sm py-1.5 border-b last:border-0"
              >
                <StatusIcon status={e.status} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge variant="secondary" className="text-[10px]">
                      {STAGE_LABEL[e.stage] || e.stage}
                    </Badge>
                    <span className="text-[11px] text-muted-foreground">
                      {timeAgo(e.ts)}
                    </span>
                  </div>
                  <p className="leading-snug mt-0.5">{e.message}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
