"use client";

import useSWR from "swr";
import { api, SITE_ID } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { TrafficChart } from "@/components/dashboard/traffic-chart";
import {
  TrendingUp, TrendingDown, Eye, MousePointer,
  MapPin, FileSearch, AlertTriangle, Zap,
} from "lucide-react";

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
        <div className="text-2xl font-bold">
          {value}{unit}
        </div>
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

function SeasonBadge({ season, note }: { season: string; note: string }) {
  const colorMap: Record<string, string> = {
    summer_peak: "bg-orange-100 text-orange-800",
    winter_peak: "bg-blue-100 text-blue-800",
    spring_shoulder: "bg-green-100 text-green-800",
    autumn_shoulder: "bg-amber-100 text-amber-800",
  };
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${colorMap[season] || "bg-gray-100 text-gray-800"}`}>
      {note}
    </span>
  );
}

export function OverviewPage() {
  const { data: dash, isLoading } = useSWR(
    `dashboard-${SITE_ID}`,
    () => api.dashboard(SITE_ID),
    { refreshInterval: 60_000 }
  );

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
  const issues = dash?.issues ?? {};
  const season = dash?.season;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Обзор</h1>
          <p className="text-sm text-muted-foreground">grandtourspirit.ru</p>
        </div>
        {season && <SeasonBadge season={season.season} note={season.note} />}
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard
          title="Показы (7д)"
          value={kpis.impressions?.toLocaleString("ru") ?? "—"}
          change={kpis.impressions_change_pct}
          icon={Eye}
        />
        <KpiCard
          title="Клики (7д)"
          value={kpis.clicks?.toLocaleString("ru") ?? "—"}
          change={kpis.clicks_change_pct}
          icon={MousePointer}
        />
        <KpiCard
          title="Средняя позиция"
          value={kpis.avg_position ?? "—"}
          icon={MapPin}
        />
        <KpiCard
          title="Проиндексировано"
          value={kpis.pages_indexed ?? "—"}
          unit=" стр."
          icon={FileSearch}
        />
      </div>

      {/* Issues summary */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {[
          { label: "Открытые", count: issues.open, color: "destructive" },
          { label: "На проверке", count: issues.review, color: "secondary" },
          { label: "Решены", count: issues.resolved, color: "outline" },
          { label: "Алертов сегодня", count: dash?.alerts_today ?? 0, color: "secondary" },
        ].map(({ label, count, color }) => (
          <Card key={label} className="flex items-center gap-4 p-4">
            <AlertTriangle className="h-5 w-5 text-muted-foreground shrink-0" />
            <div>
              <p className="text-sm text-muted-foreground">{label}</p>
              <p className="text-xl font-bold">{count ?? 0}</p>
            </div>
          </Card>
        ))}
      </div>

      {/* Traffic chart */}
      <TrafficChart siteId={SITE_ID} />

      {/* Last run */}
      {dash?.last_run?.agent && (
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Zap className="h-4 w-4" />
              Последний запуск: <span className="font-medium text-foreground">{dash.last_run.agent}</span>
              {dash.last_run.completed_at && (
                <span>{new Date(dash.last_run.completed_at).toLocaleString("ru")}</span>
              )}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
