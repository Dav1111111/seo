"use client";

/**
 * Studio /analytics — module page (PR-S6).
 *
 * Owner question: «как меня сейчас находят, что улучшается, что
 * проседает». Four daily series side by side, each with totals header
 * and lag-honest footnote.
 *
 * Backend: backend/app/api/v1/studio.py · get_analytics. Single
 * endpoint returns all series so the page renders from one fetch
 * (no waterfall).
 *
 * Charts (top → bottom by descending owner-action value):
 *   1. Видимость в Яндексе  — impressions + clicks (bar+line)
 *   2. Средняя позиция     — avg_position (line, lower=better)
 *   3. Поведение посетителей — visits + pageviews (line) + bounce%
 *   4. Индексация           — pages_indexed snapshot trend (line)
 *
 * Empty states explain WHY (CONCEPT §5):
 *   - no Webmaster data → Метрика-only
 *   - no Metrica counter → Webmaster-only
 *   - no anything → "сначала запусти конвейер"
 */

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";

import { api } from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ArrowLeft, BarChart3, Info } from "lucide-react";
import { cn } from "@/lib/utils";

import {
  ResponsiveContainer,
  LineChart,
  Line,
  BarChart,
  Bar,
  ComposedChart,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";

// ── Helpers ──────────────────────────────────────────────────────────

function fmtAge(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const ms = Date.now() - d.getTime();
  const day = Math.floor(ms / (24 * 60 * 60 * 1000));
  if (day < 1) return "сегодня";
  if (day === 1) return "1 день назад";
  return `${day} дн назад`;
}

function fmtNumber(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString("ru-RU");
}

function fmtPct(n: number | null | undefined): string {
  if (n == null) return "—";
  return `${n.toFixed(1)}%`;
}

// Recharts wants a specific date format — short dd.mm for axis labels.
function shortDate(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${d}.${m}`;
}

// ── Page component ──────────────────────────────────────────────────

const RANGE_OPTIONS: Array<{ value: number; label: string }> = [
  { value: 30, label: "30 дней" },
  { value: 90, label: "90 дней" },
  { value: 180, label: "6 мес" },
  { value: 365, label: "1 год" },
];

export default function StudioAnalyticsPage() {
  const { currentSite, loading: siteLoading } = useSite();
  const siteId = currentSite?.id || "";
  const [days, setDays] = useState(90);

  const { data, error, isLoading } = useSWR(
    siteId ? studioKey("analytics", siteId, days) : null,
    () => api.studioGetAnalytics(siteId, days),
  );

  if (siteLoading) {
    return (
      <div className="p-6 space-y-3">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!currentSite) {
    return (
      <div className="p-6">
        <Card className="border-dashed max-w-2xl">
          <CardContent className="pt-6 space-y-2">
            <div className="font-medium">Сайт не выбран</div>
            <p className="text-sm text-muted-foreground">
              Выбери сайт в свитчере слева — модуль «Аналитика» работает в
              контексте конкретного сайта.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const series = data?.series || [];
  const totals = data?.totals;
  const hasSearch = (totals?.days_with_search_data ?? 0) > 0;
  const hasTraffic = (totals?.days_with_traffic_data ?? 0) > 0;

  // Pre-format series for chart-friendly axis labels.
  const formatted = series.map((p) => ({ ...p, _label: shortDate(p.date) }));

  return (
    <div className="p-6 space-y-5 max-w-6xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <Link
            href="/studio"
            className="inline-flex items-center text-xs text-muted-foreground hover:text-foreground mb-1"
          >
            <ArrowLeft className="h-3 w-3 mr-1" /> К Студии
          </Link>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <BarChart3 className="h-6 w-6 text-primary" /> Аналитика
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Видимость в поиске, поведение посетителей, индексация — за{" "}
            {RANGE_OPTIONS.find((r) => r.value === days)?.label || `${days} дней`}.
          </p>
        </div>

        <div className="flex items-center gap-1 text-sm">
          {RANGE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setDays(opt.value)}
              className={cn(
                "rounded-md px-3 py-1 text-xs transition-colors",
                days === opt.value
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground border",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Lag indicator (CONCEPT §5: be honest about staleness) */}
      {data && (data.webmaster_latest_date || data.metrica_latest_date) && (
        <div className="text-xs text-muted-foreground rounded-md border border-dashed px-3 py-2 flex items-start gap-2">
          <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
          <span>
            Данные обновляются раз в сутки.{" "}
            {data.webmaster_latest_date && (
              <>
                Webmaster (показы / позиции / индексация) до{" "}
                <strong>{data.webmaster_latest_date}</strong> ·{" "}
                {fmtAge(data.webmaster_latest_date + "T00:00:00Z")}
                {". "}
              </>
            )}
            {data.metrica_latest_date && (
              <>
                Метрика (визиты / поведение) до{" "}
                <strong>{data.metrica_latest_date}</strong> ·{" "}
                {fmtAge(data.metrica_latest_date + "T00:00:00Z")}.
              </>
            )}
          </span>
        </div>
      )}

      {isLoading && (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-64 w-full" />
          ))}
        </div>
      )}

      {error && (
        <Card className="border-red-300 bg-red-50">
          <CardContent className="pt-6 text-sm text-red-900">
            Не удалось загрузить аналитику:{" "}
            {error instanceof Error ? error.message : String(error)}
          </CardContent>
        </Card>
      )}

      {/* Empty bootstrap */}
      {!isLoading && !error && data && series.length === 0 && (
        <Card className="border-dashed">
          <CardContent className="pt-6 space-y-2">
            <div className="font-medium">
              За последние {days} дней данных нет
            </div>
            <p className="text-sm text-muted-foreground">
              Это значит, что ни Webmaster, ни Метрика ещё не наполнили
              таблицу `daily_metrics`. Запусти конвейер на дашборде Студии
              (или подожди ночного автозапуска) — данные начнут появляться
              в течение 24 часов.
            </p>
          </CardContent>
        </Card>
      )}

      {/* 1. Visibility (impressions + clicks) */}
      {!isLoading && hasSearch && (
        <ChartSection
          title="Видимость в Яндексе"
          subtitle={`${fmtNumber(totals?.impressions_sum)} показов · ${fmtNumber(totals?.clicks_sum)} кликов · CTR ${
            totals?.impressions_sum
              ? (
                  ((totals?.clicks_sum || 0) / totals.impressions_sum) *
                  100
                ).toFixed(2)
              : "—"
          }%`}
          source="Webmaster"
        >
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart
              data={formatted}
              margin={{ top: 8, right: 12, left: 0, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,0.06)" />
              <XAxis
                dataKey="_label"
                tick={{ fontSize: 11 }}
                interval="preserveStartEnd"
              />
              <YAxis
                yAxisId="left"
                tick={{ fontSize: 11 }}
                width={50}
              />
              <YAxis
                yAxisId="right"
                orientation="right"
                tick={{ fontSize: 11 }}
                width={40}
              />
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar
                yAxisId="left"
                dataKey="impressions"
                name="Показы"
                fill="rgb(99,102,241)"
                opacity={0.5}
              />
              <Line
                yAxisId="right"
                dataKey="clicks"
                name="Клики"
                stroke="rgb(34,197,94)"
                strokeWidth={2}
                dot={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </ChartSection>
      )}

      {/* 2. Avg position */}
      {!isLoading && hasSearch && (
        <ChartSection
          title="Средняя позиция"
          subtitle={
            totals?.avg_position_mean != null
              ? `средняя за период: ${totals.avg_position_mean.toFixed(1)} (чем меньше, тем лучше)`
              : "пока нет данных"
          }
          source="Webmaster"
        >
          <ResponsiveContainer width="100%" height={200}>
            <LineChart
              data={formatted}
              margin={{ top: 8, right: 12, left: 0, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,0.06)" />
              <XAxis
                dataKey="_label"
                tick={{ fontSize: 11 }}
                interval="preserveStartEnd"
              />
              {/* Reverse: lower position = higher on chart */}
              <YAxis
                tick={{ fontSize: 11 }}
                width={40}
                reversed
                domain={["auto", 1]}
              />
              <Tooltip />
              <Line
                dataKey="avg_position"
                name="Позиция"
                stroke="rgb(244,114,182)"
                strokeWidth={2}
                dot={false}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        </ChartSection>
      )}

      {/* 3. Visitor behaviour (Metrica) */}
      {!isLoading && hasTraffic ? (
        <ChartSection
          title="Поведение посетителей"
          subtitle={`${fmtNumber(totals?.visits_sum)} визитов · ${fmtNumber(totals?.pageviews_sum)} просмотров · отказы ${fmtPct(totals?.avg_bounce_rate_mean)}`}
          source="Метрика"
        >
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart
              data={formatted}
              margin={{ top: 8, right: 12, left: 0, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,0.06)" />
              <XAxis
                dataKey="_label"
                tick={{ fontSize: 11 }}
                interval="preserveStartEnd"
              />
              <YAxis
                yAxisId="left"
                tick={{ fontSize: 11 }}
                width={50}
              />
              <YAxis
                yAxisId="right"
                orientation="right"
                tick={{ fontSize: 11 }}
                width={40}
              />
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line
                yAxisId="left"
                dataKey="visits"
                name="Визиты"
                stroke="rgb(99,102,241)"
                strokeWidth={2}
                dot={false}
              />
              <Line
                yAxisId="left"
                dataKey="pageviews"
                name="Просмотры"
                stroke="rgb(168,85,247)"
                strokeWidth={2}
                dot={false}
              />
              <Line
                yAxisId="right"
                dataKey="bounce_rate"
                name="Отказы %"
                stroke="rgb(244,114,182)"
                strokeWidth={1.5}
                dot={false}
                strokeDasharray="3 3"
              />
            </ComposedChart>
          </ResponsiveContainer>
        </ChartSection>
      ) : (
        !isLoading &&
        data && (
          <Card className="border-dashed">
            <CardContent className="pt-6 space-y-2">
              <div className="font-medium">Метрика-данных нет</div>
              <p className="text-sm text-muted-foreground">
                За последние {days} дней Метрика не вернула ни одного дня
                с визитами. Возможные причины: счётчик не подключён,
                OAuth-токен истёк, или сайт ещё не получил трафика. Проверь
                на{" "}
                <Link
                  href="/studio/connections"
                  className="underline hover:text-foreground"
                >
                  /studio/connections
                </Link>
                .
              </p>
            </CardContent>
          </Card>
        )
      )}

      {/* 4. Indexation trend */}
      {!isLoading && totals?.indexed_latest != null && (
        <ChartSection
          title="Индексация"
          subtitle={`сейчас в индексе: ${fmtNumber(totals.indexed_latest)} страниц`}
          source="Webmaster"
        >
          <ResponsiveContainer width="100%" height={200}>
            <LineChart
              data={formatted}
              margin={{ top: 8, right: 12, left: 0, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,0.06)" />
              <XAxis
                dataKey="_label"
                tick={{ fontSize: 11 }}
                interval="preserveStartEnd"
              />
              <YAxis tick={{ fontSize: 11 }} width={40} />
              <Tooltip />
              <Line
                dataKey="pages_indexed"
                name="Страниц в индексе"
                stroke="rgb(34,197,94)"
                strokeWidth={2}
                dot={false}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        </ChartSection>
      )}
    </div>
  );
}

// ── Sub-component ───────────────────────────────────────────────────

function ChartSection({
  title,
  subtitle,
  source,
  children,
}: {
  title: string;
  subtitle?: string;
  source: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardContent className="pt-5 pb-3 space-y-3">
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <div>
            <h2 className="font-medium">{title}</h2>
            {subtitle && (
              <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>
            )}
          </div>
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground rounded-full border px-2 py-0.5">
            {source}
          </span>
        </div>
        {children}
      </CardContent>
    </Card>
  );
}
