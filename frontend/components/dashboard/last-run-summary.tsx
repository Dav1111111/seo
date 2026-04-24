"use client";

import useSWR from "swr";
import Link from "next/link";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  CheckCircle2, Swords, Target, TrendingDown,
  ArrowRight, Clock, AlertCircle, FileText,
} from "lucide-react";
import { cn } from "@/lib/utils";

type Event = {
  id: number;
  stage: string;
  status: string;
  message: string;
  ts: string;
  extra: Record<string, unknown>;
  run_id: string | null;
};

function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function timeAgo(iso: string | undefined | null): string {
  if (!iso) return "ни разу";
  const utcIso = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z";
  const then = new Date(utcIso).getTime();
  const sec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (sec < 60) return "только что";
  if (sec < 3600) return `${Math.floor(sec / 60)} мин назад`;
  if (sec < 86_400) return `${Math.floor(sec / 3600)} ч назад`;
  return `${Math.floor(sec / 86_400)} д назад`;
}

const TERMINAL = new Set(["done", "failed", "skipped"]);
const WORK_STAGES = [
  "crawl", "webmaster", "demand_map",
  "business_truth",
  "competitor_discovery", "competitor_deep_dive", "opportunities",
];

export function LastRunSummary({ siteId }: { siteId: string }) {
  // Current-run endpoint scopes to the latest run_id server-side, so
  // two back-to-back pipeline clicks don't smear together in this card.
  // Still poll every 4s so progress updates are visible during a run.
  const { data, isLoading } = useSWR(
    siteId ? `current-run-${siteId}` : null,
    () => api.getCurrentRun(siteId),
    { refreshInterval: 4_000 },
  );

  if (isLoading) return <Skeleton className="h-48" />;

  const events: Event[] = data?.events ?? [];
  if (events.length === 0) return null;

  // Build per-stage latest for this run
  const byStage = new Map<string, Event>();
  for (const e of events) {
    if (!byStage.has(e.stage)) byStage.set(e.stage, e);
  }

  const opps = byStage.get("opportunities");
  const discovery = byStage.get("competitor_discovery");
  const dive = byStage.get("competitor_deep_dive");
  const pipelineEvt = byStage.get("pipeline");

  // "Running" in THIS run only. Pipeline:started without pipeline:done
  // is the cleanest signal — but if the run has no pipeline wrapper
  // (standalone button press), fall back to checking work stages.
  const running = pipelineEvt
    ? !TERMINAL.has(pipelineEvt.status)
    : WORK_STAGES.some((name) => {
        const s = byStage.get(name);
        return s && !TERMINAL.has(s.status);
      });

  // Pull useful numbers from extras (from this run's events only)
  const oppsCount = asNumber(opps?.extra?.opportunities);
  const ownPages = asNumber(opps?.extra?.own_pages);
  const crawled = asNumber(opps?.extra?.competitors_crawled);
  const compFound = asNumber(discovery?.extra?.competitors_found);
  const top3 = asStringArray(discovery?.extra?.top3);
  const cost = asNumber(discovery?.extra?.cost_usd);

  const lastTs = pipelineEvt?.ts ?? opps?.ts ?? discovery?.ts;

  return (
    <Card className={cn(
      "border-emerald-300 bg-emerald-50/50",
      running && "border-primary/40 bg-primary/5",
    )}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <CardTitle className="text-base flex items-center gap-2">
            <CheckCircle2 className={cn(
              "h-5 w-5",
              running ? "text-primary" : "text-emerald-600",
            )} />
            {running ? "Идёт анализ…" : "Результат последнего анализа"}
          </CardTitle>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Clock className="h-3.5 w-3.5" />
            {running ? (
              <span className="text-primary font-medium">работает сейчас</span>
            ) : (
              <span>{timeAgo(lastTs)}</span>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <MetricTile
            icon={Target}
            value={oppsCount ?? "—"}
            label="точек роста"
            link="/competitors"
            tone="primary"
          />
          <MetricTile
            icon={Swords}
            value={compFound ?? "—"}
            label="конкурентов"
            link="/competitors"
            tone="amber"
          />
          <MetricTile
            icon={TrendingDown}
            value={crawled ?? "—"}
            label="сайтов разобрано"
            link="/competitors"
            tone="slate"
          />
          <MetricTile
            icon={FileText}
            value={ownPages ?? "—"}
            label="твоих страниц"
            link="/competitors"
            tone="slate"
          />
        </div>

        {top3.length > 0 && (
          <div className="text-xs">
            <span className="text-muted-foreground">Топ-3 конкурента: </span>
            {top3.map((d, i) => (
              <span key={d}>
                <a
                  href={`https://${d}`}
                  target="_blank"
                  rel="noreferrer"
                  className="font-mono hover:underline"
                >
                  {d}
                </a>
                {i < top3.length - 1 && <span className="text-muted-foreground">, </span>}
              </span>
            ))}
            {cost != null && (
              <span className="text-muted-foreground">
                {" "}· стоимость анализа: ${Number(cost).toFixed(3)}
              </span>
            )}
          </div>
        )}

        {/* Status chip per stage of THIS run */}
        <div className="flex flex-wrap gap-1.5 pt-1">
          <StageChip stage={byStage.get("crawl")}                label="краулинг" />
          <StageChip stage={byStage.get("webmaster")}            label="вебмастер" />
          <StageChip stage={byStage.get("demand_map")}           label="карта спроса" />
          <StageChip stage={byStage.get("business_truth")}       label="понимание бизнеса" />
          <StageChip stage={discovery}                           label="разведка" />
          <StageChip stage={dive}                                label="глубокий анализ" />
          <StageChip stage={opps}                                label="точки роста" />
        </div>

        <div className="pt-2 flex gap-2 flex-wrap">
          <Link href="/competitors">
            <Button size="sm">
              Посмотреть что делать <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
          </Link>
          <Link href="/priorities">
            <Button size="sm" variant="outline">
              Приоритеты по страницам и запросам <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

function MetricTile({
  icon: Icon, value, label, link, tone,
}: {
  icon: React.ComponentType<{ className?: string }>;
  value: string | number;
  label: string;
  link: string;
  tone: "primary" | "amber" | "slate";
}) {
  const toneClasses = {
    primary: "bg-primary/10 border-primary/30 text-foreground",
    amber: "bg-amber-100 border-amber-300 text-foreground",
    slate: "bg-slate-100 border-slate-200 text-foreground",
  }[tone];
  return (
    <Link
      href={link}
      className={cn(
        "rounded-lg border px-3 py-2.5 flex items-start gap-2 transition hover:shadow-sm cursor-pointer",
        toneClasses,
      )}
    >
      <Icon className="h-4 w-4 shrink-0 mt-0.5" />
      <div className="min-w-0 flex-1">
        <div className="text-xl font-bold leading-none">{value}</div>
        <div className="text-[11px] text-muted-foreground mt-1 leading-tight">{label}</div>
      </div>
    </Link>
  );
}

function StageChip({
  stage, label,
}: {
  stage: Event | undefined;
  label: string;
}) {
  if (!stage) {
    return (
      <Badge variant="outline" className="text-[10px] text-muted-foreground">
        {label}: —
      </Badge>
    );
  }
  const tone =
    stage.status === "done"
      ? "bg-emerald-100 text-emerald-800 border-emerald-300"
      : stage.status === "failed"
      ? "bg-rose-100 text-rose-800 border-rose-300"
      : stage.status === "skipped"
      ? "bg-slate-100 text-slate-700 border-slate-300"
      : "bg-blue-100 text-blue-800 border-blue-300";
  const icon =
    stage.status === "done" ? (
      <CheckCircle2 className="h-3 w-3 mr-1 inline" />
    ) : stage.status === "failed" ? (
      <AlertCircle className="h-3 w-3 mr-1 inline" />
    ) : null;
  return (
    <Badge variant="outline" className={cn("text-[10px]", tone)}>
      {icon}
      {label}
    </Badge>
  );
}
