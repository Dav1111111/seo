"use client";

/**
 * Studio /outcomes — module page (PR-S8).
 *
 * Owner question: «то что я применял за последние недели — оно
 * сработало?». Список outcome_snapshots с дельтой через 14 дней.
 *
 * Backend contract: backend/app/api/v1/studio.py · list_outcomes
 *
 * Композиция:
 *   1. Stats шапка: всего / ждут замера / замерены + средние дельты
 *   2. Списки группированы по статусу:
 *      - «Замерено» (followup_at не пустой) — карточки с дельтами
 *      - «Ждут замера» (применил <14 дней назад) — карточки с обратным
 *        отсчётом дней до замера
 *
 * Поведение измерения честное (PR-S6.1): baseline берётся из окна
 * [today-14, today-7] чтобы попасть в зону реальных данных Webmaster
 * (лаг 5–10 дней). Та же константа в followup-таске.
 */

import useSWR from "swr";
import Link from "next/link";

import { api } from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";
import { fmtAge, pluralRu } from "@/lib/format";

import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ArrowLeft,
  History as HistoryIcon,
  TrendingUp,
  TrendingDown,
  Minus,
  ExternalLink,
  Clock,
  CheckCircle2,
  Info,
} from "lucide-react";
import { cn } from "@/lib/utils";

// ── Helpers ──────────────────────────────────────────────────────────

function fmtPct(n: number | null | undefined): string {
  if (n == null) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
}

function fmtPosDelta(n: number | null | undefined): string {
  if (n == null) return "—";
  // Position delta: positive = position improved (lower number → higher rank)
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}`;
}

const SOURCE_LABEL: Record<string, string> = {
  priority: "правка страницы",
  opportunity: "opportunity",
};

// ── Page component ───────────────────────────────────────────────────

export default function StudioOutcomesPage() {
  const { currentSite, loading: siteLoading } = useSite();
  const siteId = currentSite?.id || "";

  const { data, error, isLoading } = useSWR(
    siteId ? studioKey("outcomes_list", siteId) : null,
    () => api.studioListOutcomes(siteId),
  );

  if (siteLoading) {
    return (
      <div className="p-4 sm:p-6 space-y-3">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (!currentSite) {
    return (
      <div className="p-4 sm:p-6">
        <Card className="border-dashed max-w-2xl">
          <CardContent className="pt-6 space-y-2">
            <div className="font-medium">Сайт не выбран</div>
            <p className="text-sm text-muted-foreground">
              Выбери сайт в свитчере слева — модуль «До / После» работает в
              контексте конкретного сайта.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const items = data?.items || [];
  const stats = data?.stats;
  const measured = items.filter((i) => i.followup_at !== null);
  const awaiting = items.filter((i) => i.followup_at === null);

  return (
    <div className="p-4 sm:p-6 space-y-5 max-w-6xl">
      {/* Header */}
      <div>
        <Link
          href="/studio"
          className="inline-flex items-center text-xs text-muted-foreground hover:text-foreground mb-1"
        >
          <ArrowLeft className="h-3 w-3 mr-1" /> К Студии
        </Link>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <HistoryIcon className="h-6 w-6 text-primary" /> До / После
        </h1>
        <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
          Каждое применённое изменение фиксирует базовые метрики и через
          14 дней автоматически считает дельту. Если дельта около нуля —
          правка не сработала; если плюсовая — сработала.
        </p>
      </div>

      {/* Methodology explainer (CONCEPT §5: be honest about how we measure) */}
      <div className="text-xs text-muted-foreground rounded-md border border-dashed px-3 py-2 flex items-start gap-2">
        <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
        <span>
          Webmaster отдаёт данные с лагом 5–10 дней, поэтому baseline и
          followup берутся из окна <strong>7–14 дней</strong> назад от
          даты клика — это самый свежий участок реальных данных. Замер
          site-wide (не per-page) — атрибуция по странице придёт в Studio v2.
        </span>
      </div>

      {isLoading && (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      )}

      {error && (
        <Card className="border-red-300 bg-red-50">
          <CardContent className="pt-6 text-sm text-red-900">
            Не удалось загрузить outcomes:{" "}
            {error instanceof Error ? error.message : String(error)}
          </CardContent>
        </Card>
      )}

      {/* Empty state — no snapshots yet */}
      {!isLoading && !error && data && items.length === 0 && (
        <Card className="border-dashed">
          <CardContent className="pt-6 space-y-2">
            <div className="font-medium">Применённых правок пока нет</div>
            <p className="text-sm text-muted-foreground">
              Открой{" "}
              <Link
                href="/studio/pages"
                className="underline hover:text-foreground"
              >
                /studio/pages
              </Link>{" "}
              или{" "}
              <Link
                href="/studio/competitors"
                className="underline hover:text-foreground"
              >
                /studio/competitors
              </Link>
              , нажми «Применил & замерить эффект» на рекомендации или
              opportunity. Snapshot базовых метрик зафиксируется сразу,
              а через 14 дней система автоматически посчитает дельту и
              отобразит её здесь.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Stats summary */}
      {stats && items.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <StatCard
            label="всего правок"
            value={String(stats.total)}
          />
          <StatCard
            label="ждут замера"
            value={String(stats.awaiting_followup)}
            hint="14 дней не прошли"
          />
          <StatCard
            label="замерено"
            value={String(stats.measured)}
            hint={
              stats.avg_impressions_pct != null
                ? `средние показы ${fmtPct(stats.avg_impressions_pct)}`
                : undefined
            }
          />
          <StatCard
            label="средняя позиция"
            value={fmtPosDelta(stats.avg_position_delta)}
            hint={
              stats.avg_position_delta != null
                ? "положительное = выросли"
                : "ещё нет замеров"
            }
            tone={
              stats.avg_position_delta == null
                ? "neutral"
                : stats.avg_position_delta > 0
                  ? "positive"
                  : stats.avg_position_delta < 0
                    ? "negative"
                    : "neutral"
            }
          />
        </div>
      )}

      {/* Measured outcomes */}
      {measured.length > 0 && (
        <section className="space-y-3">
          <h2 className="font-medium text-lg">
            Замерено
            <span className="text-muted-foreground font-normal ml-2 text-base">
              ({measured.length})
            </span>
          </h2>
          <div className="space-y-2">
            {measured.map((o) => (
              <OutcomeCard key={o.snapshot_id} outcome={o} />
            ))}
          </div>
        </section>
      )}

      {/* Awaiting followup */}
      {awaiting.length > 0 && (
        <section className="space-y-3">
          <h2 className="font-medium text-lg">
            Ждут замера
            <span className="text-muted-foreground font-normal ml-2 text-base">
              ({awaiting.length})
            </span>
          </h2>
          <div className="space-y-2">
            {awaiting.map((o) => (
              <OutcomeCard key={o.snapshot_id} outcome={o} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────────────

function StatCard({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "positive" | "negative" | "neutral";
}) {
  return (
    <Card>
      <CardContent className="pt-4 pb-3 space-y-0.5">
        <div
          className={cn(
            "text-2xl font-semibold tabular-nums",
            tone === "positive" && "text-emerald-700",
            tone === "negative" && "text-red-700",
          )}
        >
          {value}
        </div>
        <div className="text-xs text-muted-foreground">{label}</div>
        {hint && (
          <div className="text-[10px] text-muted-foreground/80 mt-1">
            {hint}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function OutcomeCard({
  outcome,
}: {
  outcome: {
    snapshot_id: string;
    recommendation_id: string;
    source: "priority" | "opportunity";
    page_url: string | null;
    applied_at: string;
    followup_at: string | null;
    baseline_metrics: Record<string, unknown> | null;
    delta: Record<string, unknown> | null;
    days_since_applied: number;
  };
}) {
  const isMeasured = outcome.followup_at !== null;
  const delta = outcome.delta || {};
  const baseline = outcome.baseline_metrics || {};

  const impPct = (delta.impressions_pct as number | undefined) ?? null;
  const clkPct = (delta.clicks_pct as number | undefined) ?? null;
  const posDelta = (delta.position_delta as number | undefined) ?? null;

  const baseImp = (baseline.impressions_7d as number | undefined) ?? null;
  const baseClk = (baseline.clicks_7d as number | undefined) ?? null;
  const basePos = (baseline.avg_position as number | undefined) ?? null;

  const daysUntil = Math.max(0, 14 - outcome.days_since_applied);

  return (
    <Card className={cn(!isMeasured && "border-dashed")}>
      <CardContent className="pt-4 pb-4 space-y-3">
        <div className="flex items-baseline gap-2 flex-wrap">
          <Badge variant="outline" className="text-[10px]">
            {SOURCE_LABEL[outcome.source] || outcome.source}
          </Badge>
          <span className="text-xs text-muted-foreground">
            применено {fmtAge(outcome.applied_at)}
          </span>
          {outcome.page_url && (
            <a
              href={outcome.page_url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-foreground hover:text-primary inline-flex items-center gap-1 truncate ml-auto max-w-[60%]"
            >
              <span className="truncate">{outcome.page_url}</span>
              <ExternalLink className="h-3 w-3 flex-shrink-0" />
            </a>
          )}
        </div>

        {!isMeasured ? (
          <div className="text-sm text-muted-foreground flex items-center gap-2">
            <Clock className="h-4 w-4" />
            <span>
              Замер через{" "}
              <strong className="text-foreground">
                {daysUntil} {pluralRu(daysUntil, ["день", "дня", "дней"])}
              </strong>{" "}
              {daysUntil === 0
                ? "(сегодня ночью)"
                : `— ровно через 14 дней после клика`}
              .
            </span>
          </div>
        ) : (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm text-emerald-900">
              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
              <span>
                Замерено {fmtAge(outcome.followup_at)}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <DeltaCell
                label="показы"
                pct={impPct}
                base={baseImp}
              />
              <DeltaCell
                label="клики"
                pct={clkPct}
                base={baseClk}
              />
              <DeltaCell
                label="позиция"
                deltaAbs={posDelta}
                base={basePos}
                positionMode
              />
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function DeltaCell({
  label,
  pct,
  deltaAbs,
  base,
  positionMode = false,
}: {
  label: string;
  pct?: number | null;
  deltaAbs?: number | null;
  base?: number | null;
  positionMode?: boolean;
}) {
  const value = pct ?? deltaAbs;

  if (value == null) {
    return (
      <div className="rounded-md border bg-muted/30 px-3 py-2">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="text-sm text-muted-foreground">нет данных</div>
      </div>
    );
  }

  // For position: positive delta = improvement (lower position number).
  // For impressions/clicks: positive % = improvement.
  const better = value > 0;
  const worse = value < 0;
  const Icon = value === 0 ? Minus : better ? TrendingUp : TrendingDown;

  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2",
        better && "border-emerald-300 bg-emerald-50",
        worse && "border-red-300 bg-red-50",
        value === 0 && "border-muted bg-muted/20",
      )}
    >
      <div className="text-xs text-muted-foreground flex items-center gap-1">
        <Icon className="h-3 w-3" />
        {label}
      </div>
      <div
        className={cn(
          "text-base font-semibold tabular-nums",
          better && "text-emerald-700",
          worse && "text-red-700",
        )}
      >
        {positionMode
          ? `${value >= 0 ? "+" : ""}${value.toFixed(1)}`
          : `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`}
      </div>
      {base != null && (
        <div className="text-[10px] text-muted-foreground mt-0.5">
          было {positionMode ? base.toFixed(1) : base.toLocaleString("ru-RU")}
        </div>
      )}
    </div>
  );
}
