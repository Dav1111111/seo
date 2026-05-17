"use client";

/**
 * Per-query SERP snapshot panel — expanded inline on /studio/queries.
 *
 * Shows the latest Yandex top-10 we have for one query: our position,
 * top-3 non-our competitors, the full top-10 table and a mini «было →
 * стало» trend across the last 8 snapshots. A «🔄 Обновить» button
 * fires a manual probe and the panel polls every 8s until a fresher
 * snapshot lands.
 *
 * Three states:
 *   1. `null` (404)  — query never probed. CTA «Проверить сейчас».
 *   2. has snapshot  — header + trend + table + refresh button.
 *   3. error_tag set — red banner, but the existing snapshot is still
 *                      rendered (the last successful state is more
 *                      useful than nothing).
 *
 * Backend contract: lib/api.ts :: getQuerySerpSnapshot,
 * refreshQuerySerpSnapshot. Endpoint lives under /admin/studio/...
 * — the admin proxy adds the /admin/ prefix on the server.
 */

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import {
  AlertCircle,
  ExternalLink,
  Loader2,
  Minus,
  RefreshCw,
  TrendingDown,
  TrendingUp,
} from "lucide-react";

import {
  getQuerySerpSnapshot,
  refreshQuerySerpSnapshot,
  type QuerySerpResponse,
} from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { fmtAge } from "@/lib/format";
import { cn, getErrorMessage } from "@/lib/utils";

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

/** Pretty-print «225» → «Россия (225)» for the handful of common regions;
 *  fall back to the raw id otherwise so we never lie about geography. */
function formatRegion(region: string): string {
  const known: Record<string, string> = {
    "225": "Россия",
    "213": "Москва",
    "2": "Санкт-Петербург",
    "239": "Сочи",
  };
  const label = known[region];
  return label ? `${label} (${region})` : `регион ${region}`;
}

function formatPosition(p: number | null): string {
  if (p == null) return "вне топ-10";
  return `№${p}`;
}

export interface SerpSnapshotPanelProps {
  siteId: string;
  queryId: string;
}

export function SerpSnapshotPanel({ siteId, queryId }: SerpSnapshotPanelProps) {
  // Local state — we just clicked refresh and are waiting for the
  // backend to publish a new snapshot. While true, SWR polls every
  // 8s and we compare taken_at to detect arrival.
  const [waitingForRefresh, setWaitingForRefresh] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [optimisticQueued, setOptimisticQueued] = useState(false);
  // Snapshot of `taken_at` at the moment we clicked refresh — we exit
  // the polling loop once SWR returns a different value (or moves from
  // null → string).
  const takenAtBeforeRefresh = useRef<string | null>(null);

  const swrKey = studioKey("serp-snapshot", siteId, queryId);
  const { data, error, isLoading, mutate } = useSWR<QuerySerpResponse | null>(
    swrKey,
    () => getQuerySerpSnapshot(siteId, queryId),
    {
      // Two polling modes:
      //   - data === null (never probed) AND we triggered a probe → poll
      //   - data exists AND we just clicked refresh → poll until taken_at
      //     advances past the value we captured at click time.
      // Otherwise 0 = no polling (the panel is open but quiet).
      refreshInterval: waitingForRefresh ? 8000 : 0,
    },
  );

  // Stop the polling loop when a fresher snapshot arrives.
  useEffect(() => {
    if (!waitingForRefresh) return;
    if (!data) return; // still null → keep waiting
    const before = takenAtBeforeRefresh.current;
    if (data.taken_at && data.taken_at !== before) {
      setWaitingForRefresh(false);
      setOptimisticQueued(false);
      takenAtBeforeRefresh.current = null;
    }
  }, [data, waitingForRefresh]);

  async function handleRefresh() {
    if (waitingForRefresh) return;
    setRefreshError(null);
    setOptimisticQueued(true);
    takenAtBeforeRefresh.current = data?.taken_at ?? null;
    try {
      const res = await refreshQuerySerpSnapshot(siteId, queryId);
      // `deduped` means another probe is already running — we still
      // poll, since the in-flight task will eventually publish.
      setWaitingForRefresh(true);
      if (res.status === "deduped") {
        setRefreshError(
          "Уже идёт другая проверка этой выдачи — ждём её результат.",
        );
      }
      // Nudge SWR once so the polling loop starts on a fresh fetch.
      await mutate();
    } catch (e: unknown) {
      setRefreshError(getErrorMessage(e));
      setOptimisticQueued(false);
    }
  }

  // ── Render ─────────────────────────────────────────────────────────

  if (isLoading) {
    return (
      <div className="space-y-2 py-2" aria-live="polite" aria-busy="true">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900 flex items-start gap-2">
        <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
        <div className="flex-1">
          Не удалось загрузить выдачу: {getErrorMessage(error)}
        </div>
      </div>
    );
  }

  // Empty state — endpoint returned 404 (mapped to null).
  if (!data) {
    return (
      <div className="rounded-md border border-dashed bg-muted/30 px-4 py-4 space-y-3">
        <div className="text-sm text-muted-foreground">
          Эта выдача ещё не проверялась — Яндекс топ-10 для этого запроса
          у нас не снят.
        </div>
        <div>
          <Button
            type="button"
            size="sm"
            variant="default"
            onClick={handleRefresh}
            disabled={waitingForRefresh}
          >
            {waitingForRefresh ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            )}
            {waitingForRefresh ? "Проверяю…" : "Проверить сейчас"}
          </Button>
        </div>
        {refreshError && (
          <div className="text-xs text-red-700">{refreshError}</div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3 py-2">
      {/* Header — позиция · регион · возраст */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className="font-medium">
          Позиция: {formatPosition(data.our_position)}
        </span>
        <span className="text-muted-foreground">·</span>
        <span className="text-muted-foreground">{formatRegion(data.region)}</span>
        <span className="text-muted-foreground">·</span>
        <span className="text-muted-foreground">
          обновлено {fmtAge(data.taken_at)}
        </span>
        {data.our_url && (
          <a
            href={data.our_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-primary underline-offset-2 hover:underline ml-auto"
            title={data.our_url}
          >
            наша страница
            <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </div>

      {/* Error banner — last probe failed. Snapshot below is the
          previous successful one (if any). */}
      {data.error_tag && (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-900 flex items-start gap-2">
          <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
          <div className="flex-1">
            Не удалось получить выдачу:{" "}
            <span className="font-mono">{data.error_tag}</span>
          </div>
        </div>
      )}

      {/* Trend — было → стало across last 8 snapshots */}
      <TrendLine trend={data.trend} />

      {/* Top-10 results */}
      {data.results.length === 0 ? (
        <div className="rounded-md border border-dashed bg-muted/20 px-3 py-3 text-xs text-muted-foreground">
          В этом снимке нет результатов
          {data.error_tag ? " (вероятно из-за ошибки выше)." : "."}
        </div>
      ) : (
        <div className="rounded-md border bg-card overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-12 text-center">№</TableHead>
                <TableHead className="min-w-[160px]">Домен</TableHead>
                <TableHead className="min-w-[260px]">Заголовок</TableHead>
                <TableHead className="w-10" aria-label="Открыть" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.results.map((row) => {
                const isOurs =
                  data.our_url != null && row.url === data.our_url;
                const isTop3NonOurs = !isOurs && row.position <= 3;
                return (
                  <TableRow
                    key={`${row.position}-${row.url}`}
                    className={cn(
                      isOurs && "bg-emerald-50 hover:bg-emerald-100/70",
                    )}
                  >
                    <TableCell className="text-center tabular-nums">
                      <span
                        className={cn(
                          "inline-flex h-6 min-w-6 items-center justify-center rounded-full px-1.5 text-xs font-medium",
                          isTop3NonOurs
                            ? "bg-amber-100 text-amber-900 border border-amber-300"
                            : isOurs
                              ? "bg-emerald-200 text-emerald-900"
                              : "bg-muted text-muted-foreground",
                        )}
                      >
                        {row.position}
                      </span>
                    </TableCell>
                    <TableCell className="text-xs">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span
                          className={cn(
                            "font-mono",
                            isOurs && "font-semibold text-emerald-900",
                          )}
                        >
                          {row.domain}
                        </span>
                        {isOurs && (
                          <Badge
                            variant="default"
                            className="bg-emerald-600 text-white"
                          >
                            вы
                          </Badge>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs">
                      <div
                        className={cn(
                          "max-w-[420px] truncate",
                          isOurs ? "text-emerald-900" : "text-foreground",
                        )}
                        title={row.title || row.headline || row.url}
                      >
                        {row.title || row.headline || row.url}
                      </div>
                    </TableCell>
                    <TableCell>
                      <a
                        href={row.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center justify-center h-6 w-6 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted"
                        title="Открыть в новой вкладке"
                        aria-label={`Открыть ${row.domain} в новой вкладке`}
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Refresh button + status */}
      <div className="flex items-center gap-3 flex-wrap">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={handleRefresh}
          disabled={waitingForRefresh}
        >
          {waitingForRefresh ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          )}
          {waitingForRefresh ? "Обновляется…" : "Обновить SERP"}
        </Button>
        {optimisticQueued && waitingForRefresh && (
          <span className="text-xs text-muted-foreground">
            проверка запущена, ждём новый снимок (опрос каждые 8 сек)
          </span>
        )}
        {refreshError && (
          <span className="text-xs text-amber-700">{refreshError}</span>
        )}
      </div>
    </div>
  );
}

// ── Trend mini-line ──────────────────────────────────────────────────

/** Simple «было N → стало M» delta — no chart lib. When only one point
 *  exists we say «первый снимок» so the line isn't misleadingly empty. */
function TrendLine({
  trend,
}: {
  trend: Array<{ taken_at: string; our_position: number | null }>;
}) {
  if (!trend || trend.length === 0) {
    return (
      <div className="text-xs text-muted-foreground italic">
        история позиций пока пустая
      </div>
    );
  }
  if (trend.length === 1) {
    return (
      <div className="text-xs text-muted-foreground inline-flex items-center gap-1.5">
        <Minus className="h-3 w-3" />
        первый снимок этой выдачи
      </div>
    );
  }
  // Backend may return trend in either chronological order; treat the
  // earliest non-null entry as «было» and the latest as «стало». If
  // every point is null we just say so.
  const sorted = [...trend].sort(
    (a, b) =>
      new Date(a.taken_at).getTime() - new Date(b.taken_at).getTime(),
  );
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  const before = first.our_position;
  const after = last.our_position;
  // Calendar day diff between first and last snapshot — used for the
  // «за N дн» suffix; we floor to avoid showing «0 дн» for two probes
  // taken the same day.
  const dayMs = 24 * 60 * 60 * 1000;
  const spanDays = Math.max(
    1,
    Math.floor(
      (new Date(last.taken_at).getTime() -
        new Date(first.taken_at).getTime()) /
        dayMs,
    ),
  );

  const fmt = (p: number | null) => (p == null ? "вне топ-10" : `№${p}`);

  // Direction — lower number = better (rank 1 is best).
  let Icon = Minus;
  let colour = "text-muted-foreground";
  if (before != null && after != null) {
    if (after < before) {
      Icon = TrendingUp;
      colour = "text-emerald-700";
    } else if (after > before) {
      Icon = TrendingDown;
      colour = "text-red-700";
    }
  } else if (before == null && after != null) {
    Icon = TrendingUp;
    colour = "text-emerald-700";
  } else if (before != null && after == null) {
    Icon = TrendingDown;
    colour = "text-red-700";
  }

  return (
    <div
      className={cn(
        "inline-flex items-center gap-1.5 text-xs",
        colour,
      )}
      title={`${sorted.length} ${
        sorted.length === 1 ? "снимок" : "снимков"
      } за ${spanDays} дн`}
    >
      <Icon className="h-3.5 w-3.5" />
      <span>
        было <span className="font-medium">{fmt(before)}</span> → стало{" "}
        <span className="font-medium">{fmt(after)}</span> за {spanDays} дн
      </span>
    </div>
  );
}
