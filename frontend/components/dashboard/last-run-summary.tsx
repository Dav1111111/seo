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

export function LastRunSummary({ siteId }: { siteId: string }) {
  const { data, isLoading } = useSWR(
    siteId ? `last-run-${siteId}` : null,
    () => api.getActivityByStage(siteId),
    { refreshInterval: 10_000 },
  );

  if (isLoading) return <Skeleton className="h-48" />;

  const stages = data?.by_stage ?? {};
  const opps = stages["opportunities"];
  const discovery = stages["competitor_discovery"];
  const dive = stages["competitor_deep_dive"];

  // Nothing has ever run — don't show the empty card
  if (!opps && !discovery) return null;

  const TERMINAL = new Set(["done", "failed", "skipped"]);
  const running = Object.values(stages).some(
    (s: any) => !TERMINAL.has(s.status),
  );

  // Pull useful numbers from extras
  const oppsCount = opps?.extra?.opportunities ?? null;
  const ownPages = opps?.extra?.own_pages ?? null;
  const crawled = opps?.extra?.competitors_crawled ?? null;
  const compFound = discovery?.extra?.competitors_found ?? null;
  const top3: string[] = discovery?.extra?.top3 ?? [];
  const cost = discovery?.extra?.cost_usd ?? null;

  const lastTs = opps?.ts ?? discovery?.ts;

  return (
    <Card className="border-emerald-300 bg-emerald-50/50">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <CardTitle className="text-base flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-emerald-600" />
            Результат последнего анализа
          </CardTitle>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Clock className="h-3.5 w-3.5" />
            {running ? (
              <span className="text-primary font-medium">идёт сейчас…</span>
            ) : (
              <span>{timeAgo(lastTs)}</span>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Headline numbers */}
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

        {/* Top-3 snapshot */}
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

        {/* Status chip per stage */}
        <div className="flex flex-wrap gap-1.5 pt-1">
          <StageChip stage={stages["crawl"]} label="краулинг" />
          <StageChip stage={stages["webmaster"]} label="вебмастер" />
          <StageChip stage={stages["demand_map"]} label="карта спроса" />
          <StageChip stage={discovery} label="разведка" />
          <StageChip stage={dive} label="глубокий анализ" />
          <StageChip stage={opps} label="точки роста" />
        </div>

        {/* CTA */}
        <div className="pt-2 flex gap-2 flex-wrap">
          <Link href="/competitors">
            <Button size="sm">
              Посмотреть что делать <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
          </Link>
          <Link href="/priorities">
            <Button size="sm" variant="outline">
              Приоритеты по странам и запросам <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

function MetricTile({
  icon: Icon,
  value,
  label,
  link,
  tone,
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
  stage,
  label,
}: {
  stage: { status: string; ts: string } | undefined;
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
