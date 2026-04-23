"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useSite } from "@/lib/site-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { TrafficChart } from "@/components/dashboard/traffic-chart";
import { ActivityFeed } from "@/components/dashboard/activity-feed";
import { LastRunSummary } from "@/components/dashboard/last-run-summary";
import { BusinessTruthCard } from "@/components/dashboard/business-truth-card";
import {
  TrendingUp, TrendingDown, Eye, MousePointer,
  MapPin, FileSearch, ArrowRight, Flame, Play,
} from "lucide-react";
import { cn } from "@/lib/utils";

function KpiCard({
  title, value, change, icon: Icon, unit = "",
}: {
  title: string;
  value: string | number;
  change?: number | null;
  icon: React.ComponentType<{ className?: string }>;
  unit?: string;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}{unit}</div>
        {change != null && (
          <p className={`text-xs mt-1 flex items-center gap-1 ${change >= 0 ? "text-green-600" : "text-red-500"}`}>
            {change >= 0 ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
            {change >= 0 ? "+" : ""}{change}% к прошлой неделе
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function HealthBadge({ score }: { score: number }) {
  const tone =
    score >= 80 ? "bg-emerald-100 text-emerald-800 border-emerald-300"
    : score >= 50 ? "bg-amber-100 text-amber-800 border-amber-300"
    : "bg-rose-100 text-rose-800 border-rose-300";
  return (
    <span className={`inline-flex items-center rounded-full border px-3 py-1 text-sm font-semibold ${tone}`}>
      Health {score}
    </span>
  );
}

type WeeklyPlanItem = {
  recommendation_id: string;
  priority: "critical" | "high" | "medium" | "low" | string;
  priority_score: number;
  category: string;
  page_url?: string | null;
  reasoning_ru: string;
};

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

export function OverviewPage() {
  const { currentSite, loading: siteLoading } = useSite();
  const siteId = currentSite?.id || "";
  const router = useRouter();
  const [nowTs, setNowTs] = useState(0);

  useEffect(() => {
    const updateNow = () => setNowTs(Date.now());
    updateNow();
    const timer = window.setInterval(updateNow, 60_000);
    return () => window.clearInterval(timer);
  }, []);

  // Onboarding gate — if wizard isn't finished, redirect to it.
  // Everything else on the dashboard assumes an active profile.
  const { data: onbState } = useSWR(
    siteId ? `onb-check-${siteId}` : null,
    () => api.onboardingState(siteId).catch(() => null),
    { refreshInterval: 0 },
  );
  const onboardingActive = onbState?.onboarding_step === "active";

  useEffect(() => {
    if (onbState && !onboardingActive && siteId) {
      router.replace(`/onboarding/${siteId}`);
    }
  }, [onbState, onboardingActive, siteId, router]);

  const { data: dash, isLoading } = useSWR(
    siteId && onboardingActive ? `dashboard-${siteId}` : null,
    () => api.dashboard(siteId),
    { refreshInterval: 60_000 },
  );

  const { data: latestReport } = useSWR(
    siteId && onboardingActive ? `latest-report-${siteId}` : null,
    () => api.reportLatest(siteId).catch(() => null),
    { refreshInterval: 0 },
  );

  const { data: plan } = useSWR(
    siteId && onboardingActive ? `overview-plan-${siteId}` : null,
    () => api.weeklyPlan(siteId, 3, 2).catch(() => null),
    { refreshInterval: 0 },
  );

  const { data: activityByStage } = useSWR(
    siteId && onboardingActive ? `activity-last-${siteId}` : null,
    () => api.getActivityByStage(siteId).catch(() => null),
    { refreshInterval: 10_000 },
  );

  const [runningFull, setRunningFull] = useState(false);
  const [banner, setBanner] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  async function runQuickAnalysis() {
    if (!siteId || runningFull) return;
    setRunningFull(true);
    setBanner(null);
    try {
      await api.triggerFullAnalysis(siteId);
      setBanner({
        kind: "ok",
        msg:
          "Быстрый анализ запущен — 4 задачи одновременно. Обычно готово за 15–30 секунд. " +
          "Карта спроса и часть метрик используют вчерашние данные, " +
          "они обновляются автоматически ночью.",
      });
    } catch (error: unknown) {
      setBanner({ kind: "err", msg: getErrorMessage(error) });
    } finally {
      setRunningFull(false);
    }
  }

  if (siteLoading) {
    return null;
  }

  if (!currentSite) {
    return (
      <Card className="border-dashed max-w-2xl">
        <CardHeader>
          <CardTitle>Сайт Не Выбран</CardTitle>
          <p className="text-sm text-muted-foreground">
            Дашборд появится после выбора сайта. Если список пустой, проверь,
            что backend отвечает и сайт создан в системе.
          </p>
        </CardHeader>
        <CardContent>
          <Button size="sm" variant="outline" onClick={() => window.location.reload()}>
            Обновить Страницу
          </Button>
        </CardContent>
      </Card>
    );
  }

  // While we wait on the redirect decision, show nothing instead of a
  // half-rendered dashboard that flashes and then disappears.
  if (!onbState || !onboardingActive) {
    return (
      <Card className="border-dashed max-w-2xl">
        <CardHeader>
          <CardTitle>Подготавливаю Дашборд</CardTitle>
          <p className="text-sm text-muted-foreground">
            Проверяю онбординг и загружаю состояние сайта. Это займёт несколько секунд.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-8 w-40" />
          <Skeleton className="h-24" />
        </CardContent>
      </Card>
    );
  }

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-48" />
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-28" />)}
        </div>
        <Skeleton className="h-64" />
      </div>
    );
  }

  const kpis = dash?.kpis ?? {};
  const diagnostic = latestReport?.payload?.diagnostic;
  const hasDiagnostic = diagnostic?.available;
  const planItems: WeeklyPlanItem[] = plan?.items ?? [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">Обзор</h1>
          <p className="text-sm text-muted-foreground">{currentSite?.domain ?? "—"}</p>
        </div>
        <div className="flex items-start gap-3">
          {latestReport?.health_score != null && <HealthBadge score={latestReport.health_score} />}
          <div className="flex flex-col items-end gap-1">
            <Button size="sm" onClick={runQuickAnalysis} disabled={runningFull}>
              <Play className={cn("mr-2 h-4 w-4", runningFull && "animate-pulse")} />
              {runningFull ? "Запускаю…" : "Быстрый анализ"}
            </Button>
            <p className="text-[11px] text-muted-foreground max-w-[240px] text-right leading-tight">
              Свежие конкуренты + точки роста за ~20 сек.
              Полное обновление карты спроса и Вебмастера — ночью автоматически.
            </p>
          </div>
        </div>
      </div>

      {banner && (
        <div
          className={cn(
            "rounded border px-3 py-2 text-sm",
            banner.kind === "ok"
              ? "border-emerald-300 bg-emerald-50 text-emerald-900"
              : "border-red-300 bg-red-50 text-red-900",
          )}
        >
          {banner.msg}
        </div>
      )}

      {/* Last-updated strip */}
      {activityByStage?.by_stage && Object.keys(activityByStage.by_stage).length > 0 && (
        <StageTimestamps byStage={activityByStage.by_stage} nowTs={nowTs} />
      )}

      {/* Root problem */}
      {hasDiagnostic ? (
        <Card className="border-primary/40 bg-primary/5">
          <CardHeader className="flex flex-row items-start justify-between gap-4">
            <div>
              <CardTitle className="flex items-center gap-2 text-lg">
                🧭 Корневая проблема
                <Badge variant="outline" className="text-xs font-normal">
                  {diagnostic.root_problem_classification}
                </Badge>
              </CardTitle>
              <p className="text-xs text-muted-foreground mt-1">
                Отчёт от {latestReport?.week_end}
              </p>
            </div>
            <Link href={`/reports/${latestReport.id}`}>
              <Button size="sm" variant="outline">
                Открыть отчёт <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            </Link>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-base leading-relaxed">{diagnostic.root_problem_ru}</p>
            {diagnostic.recommended_first_actions_ru?.length > 0 && (
              <div>
                <div className="text-xs font-semibold uppercase text-muted-foreground mb-1">
                  Что делать в первую очередь
                </div>
                <ol className="list-decimal pl-5 text-sm space-y-0.5">
                  {diagnostic.recommended_first_actions_ru.slice(0, 3).map((s: string, i: number) => (
                    <li key={i}>{s}</li>
                  ))}
                </ol>
              </div>
            )}
          </CardContent>
        </Card>
      ) : !latestReport ? (
        <Card className="border-dashed">
          <CardContent className="py-4 text-sm text-muted-foreground">
            Отчётов пока нет. После первого сбора данных ночью — появится первый еженедельный
            отчёт с корневой проблемой и планом.
          </CardContent>
        </Card>
      ) : null}

      {/* KPIs */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard title="Показы (7д)"     value={kpis.impressions?.toLocaleString("ru") ?? "—"} change={kpis.impressions_change_pct} icon={Eye} />
        <KpiCard title="Клики (7д)"      value={kpis.clicks?.toLocaleString("ru") ?? "—"}      change={kpis.clicks_change_pct}      icon={MousePointer} />
        <KpiCard title="Средняя позиция" value={kpis.avg_position ?? "—"}                       icon={MapPin} />
        <KpiCard title="Проиндексировано" value={kpis.pages_indexed ?? "—"} unit=" стр."         icon={FileSearch} />
      </div>

      {/* Top priorities */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Flame className="h-4 w-4" /> План на эту неделю
            {plan?.total_in_backlog != null && (
              <Badge variant="secondary" className="ml-1">в бэклоге: {plan.total_in_backlog}</Badge>
            )}
          </CardTitle>
          <Link href="/priorities">
            <Button size="sm" variant="ghost">Все приоритеты <ArrowRight className="ml-2 h-4 w-4" /></Button>
          </Link>
        </CardHeader>
        <CardContent className="space-y-2">
          {planItems.length === 0 ? (
            <p className="text-sm text-muted-foreground italic">
              План пустой. Нажми «Пересчитать» в Приоритетах.
            </p>
          ) : (
            planItems.slice(0, 3).map((it, i) => (
              <div key={it.recommendation_id} className="rounded border p-3 text-sm space-y-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-bold text-muted-foreground">#{i + 1}</span>
                  <Badge variant="outline" className={cn(
                    "text-xs",
                    it.priority === "critical" ? "bg-rose-100 text-rose-800 border-rose-300"
                    : it.priority === "high" ? "bg-orange-100 text-orange-800 border-orange-300"
                    : it.priority === "medium" ? "bg-amber-100 text-amber-800 border-amber-300"
                    : "bg-slate-100 text-slate-700 border-slate-300",
                  )}>
                    {it.priority} · {it.priority_score.toFixed(1)}
                  </Badge>
                  <Badge variant="secondary" className="text-xs">{it.category}</Badge>
                </div>
                {it.page_url && (
                  <div className="text-xs text-muted-foreground truncate">{it.page_url}</div>
                )}
                <p className="leading-snug">{it.reasoning_ru}</p>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <LastRunSummary siteId={siteId} />

      <BusinessTruthCard siteId={siteId} />

      <ActivityFeed siteId={siteId} />

      <TrafficChart siteId={siteId} />
    </div>
  );
}

function StageTimestamps({
  byStage,
  nowTs,
}: {
  byStage: Record<string, { ts: string; stage: string; status: string; message: string }>;
  nowTs: number;
}) {
  const rows = [
    { key: "crawl",                label: "Страницы сайта" },
    { key: "webmaster",            label: "Вебмастер" },
    { key: "demand_map",           label: "Карта спроса" },
    { key: "competitor_discovery", label: "Конкуренты" },
    { key: "opportunities",        label: "Точки роста" },
    { key: "report",               label: "Отчёт" },
  ];

  function ago(iso: string): string {
    // Backend serves naive UTC — force-parse as UTC. See activity-feed.tsx.
    const utcIso = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z";
    const then = new Date(utcIso).getTime();
    if (!nowTs) return "недавно";
    const sec = Math.max(0, Math.floor((nowTs - then) / 1000));
    if (sec < 60) return "только что";
    if (sec < 3600) return `${Math.floor(sec / 60)} мин`;
    if (sec < 86_400) return `${Math.floor(sec / 3600)} ч`;
    const days = Math.floor(sec / 86_400);
    return `${days} д`;
  }

  return (
    <Card>
      <CardContent className="py-3 px-4">
        <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs">
          {rows.map(({ key, label }) => {
            const ev = byStage[key];
            return (
              <div key={key} className="flex items-center gap-1.5">
                <span className="text-muted-foreground">{label}:</span>
                {ev ? (
                  <>
                    <span className="font-medium">{ago(ev.ts)} назад</span>
                    {ev.status === "failed" && (
                      <Badge variant="destructive" className="text-[10px]">ошибка</Badge>
                    )}
                  </>
                ) : (
                  <span className="text-muted-foreground italic">ни разу</span>
                )}
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
