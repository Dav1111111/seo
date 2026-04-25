"use client";

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";

import { api } from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Search,
  Sparkles as SparklesIcon,
  Telescope,
  RefreshCw,
  CheckCircle2,
  Info,
  ArrowLeft,
} from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Studio · Запросы (PR-S2)
 *
 * Module page for the queries we know about for a site:
 * - Wordstat volume + 12-month trend (sparkline)
 * - last position from daily_metrics (Webmaster)
 * - 14-day impressions
 * - branded / cluster tags
 *
 * Two action buttons trigger backend long-running tasks:
 *   "Найти новые запросы"      → demand_map_build_site_task
 *   "Обновить объёмы Wordstat" → wordstat_refresh_site
 *
 * SWR cache keys are scoped via studioKey() so they don't collide with
 * legacy /priorities or /competitors caches that hit overlapping endpoints
 * (IMPLEMENTATION.md §2.2). No auto-refresh — owner clicks to refetch.
 *
 * Concept: docs/studio/CONCEPT.md
 */

type SortMode = "volume" | "recent" | "alpha" | "position";

const SORT_OPTIONS: Array<{ value: SortMode; label: string }> = [
  { value: "volume", label: "По объёму" },
  { value: "recent", label: "Свежие" },
  { value: "position", label: "По позиции" },
  { value: "alpha", label: "По алфавиту" },
];

type StatusKey =
  | "fresh"
  | "stale_30d+"
  | "never_fetched"
  | "fetch_returned_empty";

const STATUS_META: Record<
  StatusKey,
  { label: string; className: string }
> = {
  fresh: {
    label: "свежее",
    className: "bg-emerald-50 text-emerald-800 border-emerald-300",
  },
  "stale_30d+": {
    label: "устарело (>30 дней)",
    className: "bg-amber-50 text-amber-800 border-amber-300",
  },
  never_fetched: {
    label: "не собирали",
    className: "bg-muted text-muted-foreground border",
  },
  fetch_returned_empty: {
    label: "Wordstat вернул 0 (редкая фраза)",
    className: "bg-muted text-muted-foreground border",
  },
};

function StatusBadge({ status }: { status: StatusKey }) {
  const meta = STATUS_META[status];
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium whitespace-nowrap",
        meta.className,
      )}
    >
      {meta.label}
    </span>
  );
}

/** Inline 12-bar sparkline with no chart lib — pure flex children with
 *  relative heights. Months with null counts render as faded zero bars
 *  so gaps in the data are honest, not invisible. */
function Sparkline({
  trend,
}: {
  trend: Array<{ date: string; count: number | null }> | null;
}) {
  if (!trend || trend.length === 0) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }
  const counts = trend.map((t) => t.count ?? 0);
  const max = Math.max(...counts, 1);
  return (
    <div
      className="flex items-end gap-[2px] h-6"
      title={trend
        .map(
          (t) =>
            `${t.date.slice(0, 7)}: ${t.count == null ? "нет данных" : t.count}`,
        )
        .join("\n")}
      aria-label="Тренд за 12 месяцев"
    >
      {trend.map((t, i) => {
        const isNull = t.count == null;
        const value = t.count ?? 0;
        // 4..24 px height
        const h = isNull ? 4 : Math.max(4, Math.round((value / max) * 22) + 2);
        return (
          <div
            key={i}
            className={cn(
              "w-[3px] rounded-[1px]",
              isNull ? "bg-muted-foreground/20" : "bg-primary/70",
            )}
            style={{ height: `${h}px` }}
          />
        );
      })}
    </div>
  );
}

function formatNumber(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString("ru");
}

function formatPosition(p: number | null | undefined): string {
  if (p == null) return "—";
  return p.toFixed(1);
}

function getErrorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

export default function StudioQueriesPage() {
  const { currentSite, loading: siteLoading } = useSite();
  const siteId = currentSite?.id || "";
  const [sort, setSort] = useState<SortMode>("volume");

  // Trigger UI state — local, no SWR. Each button locks for ~3s after
  // click so a double-click doesn't hammer the dedup window unnecessarily.
  const [discoverPending, setDiscoverPending] = useState(false);
  const [refreshPending, setRefreshPending] = useState(false);
  const [banner, setBanner] = useState<{
    kind: "ok" | "deduped" | "err";
    text: string;
  } | null>(null);

  const { data, error, isLoading, mutate } = useSWR(
    siteId ? studioKey("queries", siteId, sort) : null,
    () => api.studioListQueries(siteId, sort, 200),
  );

  async function onDiscover() {
    if (!siteId || discoverPending) return;
    setDiscoverPending(true);
    setBanner(null);
    try {
      const res = await api.studioDiscoverQueries(siteId);
      if (res.deduped) {
        setBanner({
          kind: "deduped",
          text: `Уже идёт другой запуск (run_id ${res.run_id.slice(0, 8)}…). Подожди, он закончится.`,
        });
      } else {
        setBanner({
          kind: "ok",
          text: `Запущен поиск новых запросов · run_id ${res.run_id.slice(0, 8)}…`,
        });
      }
    } catch (e: unknown) {
      setBanner({ kind: "err", text: getErrorMessage(e) });
    } finally {
      // 3-second cooldown so the dedup-guard message stays visible
      setTimeout(() => setDiscoverPending(false), 3000);
    }
  }

  async function onRefresh() {
    if (!siteId || refreshPending) return;
    setRefreshPending(true);
    setBanner(null);
    try {
      const res = await api.studioRefreshWordstat(siteId);
      if (res.deduped) {
        setBanner({
          kind: "deduped",
          text: `Wordstat уже обновляется (run_id ${res.run_id.slice(0, 8)}…). Подожди, он закончится.`,
        });
      } else {
        setBanner({
          kind: "ok",
          text: `Запущено обновление объёмов Wordstat · run_id ${res.run_id.slice(0, 8)}…. Это займёт ~${data?.total ?? "несколько"} секунд.`,
        });
      }
    } catch (e: unknown) {
      setBanner({ kind: "err", text: getErrorMessage(e) });
    } finally {
      setTimeout(() => setRefreshPending(false), 3000);
    }
  }

  // ── Render guards ────────────────────────────────────────────────

  if (siteLoading) {
    return (
      <div className="p-6 space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-32 w-full" />
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
              Выбери сайт в свитчере слева — модуль «Запросы» работает в
              контексте конкретного сайта.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  // ── Header ───────────────────────────────────────────────────────

  const coverage = data?.coverage;
  const subtitle = coverage
    ? `${coverage.total} запросов · ${coverage.with_volume} с объёмом · ${coverage.stale} устарели`
    : "загружаю…";

  // ── Empty-state messaging (CONCEPT §5: explain WHY) ──────────────

  const showEmptyAll =
    data && coverage && coverage.total === 0;
  const showEmptyVolumes =
    data && coverage && coverage.total > 0 && coverage.with_volume === 0;

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
            <Search className="h-6 w-6 text-primary" /> Запросы
          </h1>
          <p className="text-sm text-muted-foreground mt-1">{subtitle}</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <Button
            variant="outline"
            size="sm"
            onClick={onDiscover}
            disabled={discoverPending}
          >
            <Telescope
              className={cn(
                "h-4 w-4 mr-2",
                discoverPending && "animate-pulse",
              )}
            />
            {discoverPending ? "Запускаю…" : "Найти новые запросы"}
          </Button>
          <Button
            size="sm"
            onClick={onRefresh}
            disabled={refreshPending}
          >
            <RefreshCw
              className={cn(
                "h-4 w-4 mr-2",
                refreshPending && "animate-spin",
              )}
            />
            {refreshPending ? "Запускаю…" : "Обновить объёмы Wordstat"}
          </Button>
        </div>
      </div>

      {/* Banner — trigger feedback */}
      {banner && (
        <div
          className={cn(
            "rounded-md border px-3 py-2 text-sm flex items-start gap-2",
            banner.kind === "ok" &&
              "border-emerald-300 bg-emerald-50 text-emerald-900",
            banner.kind === "deduped" &&
              "border-amber-300 bg-amber-50 text-amber-900",
            banner.kind === "err" && "border-red-300 bg-red-50 text-red-900",
          )}
        >
          {banner.kind === "ok" && (
            <CheckCircle2 className="h-4 w-4 mt-0.5 flex-shrink-0" />
          )}
          {banner.kind === "deduped" && (
            <Info className="h-4 w-4 mt-0.5 flex-shrink-0" />
          )}
          {banner.kind === "err" && (
            <Info className="h-4 w-4 mt-0.5 flex-shrink-0" />
          )}
          <span>{banner.text}</span>
        </div>
      )}

      {/* Sort */}
      <div className="flex items-center gap-1 text-sm">
        <span className="text-muted-foreground mr-1">Сортировка:</span>
        {SORT_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            onClick={() => setSort(opt.value)}
            className={cn(
              "rounded-md px-3 py-1 text-xs transition-colors",
              sort === opt.value
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-accent-foreground border",
            )}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {/* Body */}
      {error ? (
        <Card>
          <CardContent className="pt-6 text-sm text-red-800">
            Не удалось загрузить запросы: {getErrorMessage(error)}
          </CardContent>
        </Card>
      ) : isLoading ? (
        <div className="space-y-2">
          {[...Array(8)].map((_, i) => (
            <Skeleton key={i} className="h-10" />
          ))}
        </div>
      ) : showEmptyAll ? (
        <Card className="border-dashed">
          <CardContent className="pt-6 space-y-3">
            <div className="font-medium flex items-center gap-2">
              <SparklesIcon className="h-4 w-4 text-primary" />
              Запросов пока нет
            </div>
            <p className="text-sm text-muted-foreground">
              Нажми «Найти новые запросы» — модуль соберёт фразы из карты
              спроса и запросит их в Webmaster.
            </p>
          </CardContent>
        </Card>
      ) : showEmptyVolumes ? (
        <Card className="border-dashed">
          <CardContent className="pt-6 space-y-3">
            <div className="font-medium flex items-center gap-2">
              <Info className="h-4 w-4 text-amber-600" />
              Объёмы Wordstat ещё не собраны
            </div>
            <p className="text-sm text-muted-foreground">
              Запросов в БД: <strong>{coverage?.total}</strong>, но ни одного
              с заполненным <code>wordstat_volume</code>. Нажми «Обновить
              объёмы Wordstat» — это займёт ~{coverage?.total ?? 0} секунд (по
              одному запросу в секунду).
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="min-w-[260px]">Запрос</TableHead>
                <TableHead className="text-right">Объём</TableHead>
                <TableHead>Статус</TableHead>
                <TableHead className="text-right">Позиция</TableHead>
                <TableHead className="text-right">Показы 14д</TableHead>
                <TableHead>Тренд</TableHead>
                <TableHead>Кластер</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.items.map((row) => (
                <TableRow key={row.query_id}>
                  <TableCell className="font-medium">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span>{row.query_text}</span>
                      {row.is_branded && (
                        <Badge
                          variant="outline"
                          className="text-[10px] bg-blue-50 text-blue-800 border-blue-300"
                        >
                          бренд
                        </Badge>
                      )}
                    </div>
                  </TableCell>
                  <TableCell
                    className={cn(
                      "text-right tabular-nums",
                      row.wordstat_volume == null && "text-muted-foreground/50",
                    )}
                  >
                    {formatNumber(row.wordstat_volume)}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={row.wordstat_status as StatusKey} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatPosition(row.last_position)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {formatNumber(row.last_impressions_14d)}
                  </TableCell>
                  <TableCell>
                    <Sparkline trend={row.wordstat_trend} />
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {row.cluster ?? "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      {/* Manual refresh button — table is not auto-refresh; after a
          long-running task fires an event the user can re-pull explicitly. */}
      {data && data.items.length > 0 && (
        <div className="flex justify-end">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => mutate()}
            className="text-xs text-muted-foreground"
          >
            <RefreshCw className="h-3 w-3 mr-1" /> Перечитать таблицу
          </Button>
        </div>
      )}
    </div>
  );
}
