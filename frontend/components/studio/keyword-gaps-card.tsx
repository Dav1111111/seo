"use client";

/**
 * Brain dashboard card — Wordstat-driven keyword placement gaps.
 *
 * The owner sees a single ranked list: top pages where adding a
 * missing keyword to <title> / <h1> is expected to recover the most
 * clicks per month. Apply happens per-page on the page workspace —
 * this card just surfaces the opportunity + a quick link.
 *
 * Empty / never-ran / no-gaps are three distinct states because they
 * suggest different next actions for the owner.
 *
 * Backend contract: see frontend/lib/api.ts ::
 *   getKeywordGapsSummary, refreshKeywordGaps.
 */

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  Target,
  RefreshCw,
  ChevronRight,
  CheckCircle2,
  Loader2,
  Snowflake,
} from "lucide-react";

import {
  getKeywordGapsSummary,
  refreshKeywordGaps,
  type KeywordGapsSummary,
  type KeywordGapsTopPage,
} from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { cn, getErrorMessage } from "@/lib/utils";

function upliftToneClasses(uplift: number): string {
  if (uplift >= 10) return "bg-red-100 text-red-900 border border-red-300";
  if (uplift >= 5) return "bg-amber-100 text-amber-900 border border-amber-300";
  return "bg-muted text-muted-foreground border";
}

function formatComputedAt(iso: string): string {
  try {
    return new Date(iso).toLocaleString("ru-RU", {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

/**
 * `siteId` is optional — when omitted, the card reads the current
 * site from `SiteContext`. The brain dashboard mounts it without
 * props (server component → client component) and the per-site
 * switcher controls which site is active.
 */
export function KeywordGapsCard({ siteId: siteIdProp }: { siteId?: string } = {}) {
  const { currentSite, loading: siteLoading } = useSite();
  const siteId = siteIdProp || currentSite?.id || "";

  const swrKey = siteId ? studioKey("keyword-gaps-summary", siteId) : null;
  const { data, error, isLoading, mutate } = useSWR<KeywordGapsSummary | null>(
    swrKey,
    () => getKeywordGapsSummary(siteId),
    { refreshInterval: 0 },
  );

  const [refreshing, setRefreshing] = useState(false);
  const [refreshErr, setRefreshErr] = useState<string | null>(null);

  async function onRefresh() {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshErr(null);
    // Optimistic placeholder: keep current data visible while the
    // refresh task is queued. Roll back on error per CLAUDE.md
    // frontend rule #4.
    const before = data;
    try {
      await refreshKeywordGaps(siteId);
      // Backend runs async — fire a single revalidation now so the
      // owner sees the spinner state flip and we'll catch the fresh
      // payload on the next focus/refresh cycle.
      await mutate(undefined, { revalidate: true });
    } catch (e) {
      // Roll back the SWR cache to the pre-click value.
      await mutate(before, { revalidate: false });
      setRefreshErr(getErrorMessage(e));
    } finally {
      setRefreshing(false);
    }
  }

  if (siteLoading) return null;
  if (!siteId) return null;

  if (isLoading) {
    return (
      <Card>
        <CardContent className="pt-2 space-y-3">
          <Skeleton className="h-6 w-72" />
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-10 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card className="border-red-300 bg-red-50/50">
        <CardContent className="pt-2 text-sm text-red-900">
          Не удалось загрузить ключевые возможности: {getErrorMessage(error)}
        </CardContent>
      </Card>
    );
  }

  // SWR may briefly return `undefined` between mutate() and the next
  // settled fetch (during optimistic rollback). Treat that as
  // never-ran too — same CTA is appropriate.
  if (data == null) {
    return (
      <Card className="border-dashed">
        <CardContent className="pt-2 space-y-3">
          <div className="flex items-start gap-2">
            <Target className="h-5 w-5 text-primary mt-0.5" />
            <div className="flex-1 min-w-0">
              <div className="font-medium">Ключевые слова в title/H1</div>
              <p className="text-xs text-muted-foreground mt-1 max-w-2xl">
                Сопоставим запросы из Wordstat с текущим текстом каждой
                страницы и покажем, где не хватает ключевых лемм в title
                и H1. Запускается раз — потом сам обновляется.
              </p>
            </div>
          </div>
          <div>
            <button
              type="button"
              onClick={onRefresh}
              disabled={refreshing}
              className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-60 cursor-pointer"
            >
              {refreshing ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Target className="h-4 w-4" />
              )}
              {refreshing ? "Запускаю…" : "Найти возможности"}
            </button>
            {refreshErr && (
              <p className="text-xs text-red-700 mt-2">{refreshErr}</p>
            )}
          </div>
        </CardContent>
      </Card>
    );
  }

  // No gaps — celebrate, but still show a quiet «Перепроверить» so
  // the owner can re-run after editing the site.
  if (data.total_gaps === 0) {
    return (
      <Card className="border-emerald-200 bg-emerald-50/30">
        <CardContent className="pt-2 space-y-2">
          <div className="flex items-start gap-2">
            <CheckCircle2 className="h-5 w-5 text-emerald-700 mt-0.5" />
            <div className="flex-1">
              <div className="font-medium text-emerald-900">
                В title и H1 страниц все ключевые слова на месте — поздравляем!
              </div>
              <p className="text-xs text-emerald-900/70 mt-1">
                Wordstat-запросы из demand_map покрыты на страницах. Если
                добавишь новые услуги или регионы, перепроверим.
              </p>
            </div>
          </div>
          <div className="flex items-center justify-between gap-3 flex-wrap pt-1">
            <span className="text-[11px] text-muted-foreground">
              данные собраны: {formatComputedAt(data.computed_at)}
            </span>
            <button
              type="button"
              onClick={onRefresh}
              disabled={refreshing}
              className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground cursor-pointer disabled:opacity-60"
            >
              {refreshing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              {refreshing ? "Запускаю…" : "Перепроверить"}
            </button>
          </div>
          {refreshErr && (
            <p className="text-xs text-red-700">{refreshErr}</p>
          )}
        </CardContent>
      </Card>
    );
  }

  // Have gaps — main render.
  const topPages = data.top_pages.slice(0, 5);
  const totalUplift = data.total_potential_clicks_per_month;

  return (
    <Card>
      <CardContent className="pt-2 space-y-3">
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <div>
            <h2 className="font-medium text-lg flex items-center gap-2">
              <Target className="h-5 w-5 text-primary" />
              {data.total_gaps}{" "}
              {pluralWord(data.total_gaps, [
                "возможность",
                "возможности",
                "возможностей",
              ])}{" "}
              добавить ключевые слова
            </h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Потенциал: <span className="font-medium text-foreground">
                +{Math.round(totalUplift)} кликов/мес
              </span>{" "}
              · затронуто страниц: {data.pages_with_gaps}
            </p>
          </div>
          <span className="text-[11px] text-muted-foreground">
            данные собраны: {formatComputedAt(data.computed_at)}
          </span>
        </div>

        <div className="space-y-2">
          {topPages.map((row) => (
            <TopPageRow key={row.page_id} row={row} />
          ))}
        </div>

        <div className="flex items-center justify-between gap-3 flex-wrap pt-1 border-t">
          <span className="text-[11px] text-muted-foreground">
            {data.top_pages.length > topPages.length
              ? `показаны топ ${topPages.length} из ${data.top_pages.length}`
              : "открой страницу, чтобы увидеть все пробелы и применить правки"}
          </span>
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground cursor-pointer disabled:opacity-60"
          >
            {refreshing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            {refreshing ? "Запускаю…" : "Перепроверить"}
          </button>
        </div>

        {refreshErr && (
          <p className="text-xs text-red-700">{refreshErr}</p>
        )}
      </CardContent>
    </Card>
  );
}

function TopPageRow({ row }: { row: KeywordGapsTopPage }) {
  const g = row.top_gap;
  const positionLabel =
    g.current_position == null ? "нет в выдаче" : `поз. ${g.current_position}`;
  return (
    <div className="rounded-md border bg-card px-3 py-2 hover:border-primary/40 transition-colors">
      <div className="flex items-start gap-3 flex-wrap">
        <div className="flex-1 min-w-0 space-y-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={cn(
                "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium tabular-nums",
                upliftToneClasses(g.expected_clicks_uplift),
              )}
              title="Прогноз: сколько кликов в месяц прибавится, если страница выйдет в топ-5"
            >
              +{Math.round(g.expected_clicks_uplift)} кликов/мес
            </span>
            {row.gaps_count > 1 && (
              <Badge variant="outline" className="text-[10px]">
                +ещё {row.gaps_count - 1} запрос
                {pluralWord(row.gaps_count - 1, ["", "а", "ов"])} на странице
              </Badge>
            )}
            {g.is_off_season && (
              <span
                className="inline-flex items-center gap-1 rounded-full bg-muted text-muted-foreground border px-2 py-0.5 text-[10px]"
                title="Сейчас межсезонье — спрос ниже годового пика"
              >
                <Snowflake className="h-3 w-3" />
                межсезонье
              </span>
            )}
          </div>
          <div className="text-sm leading-snug">
            <span className="font-medium">«{g.query}»</span>{" "}
            <span className="text-xs text-muted-foreground tabular-nums">
              Wordstat {g.wordstat_volume.toLocaleString("ru-RU")}/мес · {positionLabel}
            </span>
          </div>
          <div className="text-xs text-muted-foreground truncate">
            {row.page_title || row.page_url}
          </div>
        </div>
        <Link
          href={`/studio/pages/${row.page_id}`}
          className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline cursor-pointer self-center"
        >
          Открыть страницу
          <ChevronRight className="h-3.5 w-3.5" />
        </Link>
      </div>
    </div>
  );
}

// Russian plural helper — picks one of [singular, paucal, plural]
// according to standard Russian numeric agreement rules.
function pluralWord(n: number, forms: [string, string, string]): string {
  const abs = Math.abs(n);
  const mod10 = abs % 10;
  const mod100 = abs % 100;
  if (mod10 === 1 && mod100 !== 11) return forms[0];
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return forms[1];
  return forms[2];
}
