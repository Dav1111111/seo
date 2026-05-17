"use client";

import { Fragment, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { api } from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";
import { pluralRu } from "@/lib/format";
import { useTimeoutSetter } from "@/lib/hooks/use-timeout";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { FocusPill } from "@/components/studio/focus-pill";
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
  Wand2,
  CheckCircle2,
  Info,
  ArrowLeft,
  Brain,
  Check,
  X as XIcon,
  HelpCircle,
  AlertTriangle,
  ChevronRight,
  ChevronDown,
} from "lucide-react";
import { cn, getErrorMessage } from "@/lib/utils";
import { SerpSnapshotPanel } from "@/components/studio/serp-snapshot-panel";

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

// All five tri-state buckets the backend can emit. Backend is mid-
// rollout — both `stale_30d_plus` (new) and `stale_30d+` (legacy)
// arrive in the wild, so we keep both keys mapped to the same meta.
type StatusKey =
  | "fresh"
  | "stale_30d_plus"
  | "stale_30d+"
  | "never_fetched"
  | "fetch_returned_empty"
  | "invalid_phrase";

const STATUS_META: Record<
  StatusKey,
  { label: string; className: string }
> = {
  fresh: {
    label: "свежее",
    className: "bg-emerald-50 text-emerald-800 border-emerald-300",
  },
  stale_30d_plus: {
    label: "устарело >30 дней",
    className: "bg-amber-50 text-amber-800 border-amber-300",
  },
  "stale_30d+": {
    label: "устарело >30 дней",
    className: "bg-amber-50 text-amber-800 border-amber-300",
  },
  never_fetched: {
    label: "ещё не собирали",
    className: "bg-muted text-muted-foreground border",
  },
  fetch_returned_empty: {
    label: "Wordstat: нет спроса",
    className: "bg-sky-50 text-sky-800 border-sky-300",
  },
  invalid_phrase: {
    label: "невалидная фраза",
    className: "bg-amber-50 text-amber-800 border-amber-300",
  },
};

function StatusBadge({ status }: { status: StatusKey }) {
  const meta = STATUS_META[status] ?? STATUS_META.never_fetched;
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

const COVERAGE_META: Record<
  string,
  { label: string; className: string }
> = {
  covered: {
    label: "страница есть",
    className: "bg-emerald-50 text-emerald-800 border-emerald-300",
  },
  weak: {
    label: "слабое покрытие",
    className: "bg-amber-50 text-amber-800 border-amber-300",
  },
  missing: {
    label: "нет страницы",
    className: "bg-rose-50 text-rose-800 border-rose-300",
  },
  ignored: {
    label: "не работаем",
    className: "bg-muted text-muted-foreground border",
  },
  unknown: {
    label: "неизвестно",
    className: "bg-slate-50 text-slate-700 border-slate-300",
  },
};

function CoverageBadge({
  status,
  score,
}: {
  status: string;
  score: number;
}) {
  const meta = COVERAGE_META[status] ?? COVERAGE_META.unknown;
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium whitespace-nowrap",
        meta.className,
      )}
      title={`Покрытие запроса: ${score}/100`}
    >
      {meta.label}
      {status !== "ignored" && status !== "unknown" && (
        <span className="ml-1 tabular-nums opacity-70">{score}</span>
      )}
    </span>
  );
}

/** True for rows whose `query_text` is actually a URL (e.g. an upstream
 *  data-quality issue where a sitemap entry leaked into the query
 *  pipeline). We render these with a «системная запись» badge so the
 *  owner can spot them without us hiding the row entirely. */
function isUrlLikeQuery(text: string): boolean {
  if (!text) return false;
  const t = text.trim().toLowerCase();
  return /^https?:\/\//.test(t) || (t.startsWith("www.") && t.includes("."));
}

// ── Relevance (Studio v2 etap 4) ────────────────────────────────────

type RelevanceKey =
  // Legacy taxonomy — still emitted while older rows haven't been backfilled.
  | "own"
  | "adjacent"
  | "disputed"
  | "spam"
  | "unclassified"
  // Funnel taxonomy — produced by the rewritten classifier (commit 13481f2).
  | "direct_product"
  | "funnel_warm"
  | "funnel_top"
  | "out_of_market";

const RELEVANCE_KEYS: RelevanceKey[] = [
  "own",
  "adjacent",
  "disputed",
  "spam",
  "unclassified",
  "direct_product",
  "funnel_warm",
  "funnel_top",
  "out_of_market",
];

const RELEVANCE_META: Record<
  RelevanceKey,
  { label: string; short: string; className: string; dotColor: string }
> = {
  // — Legacy
  own: {
    label: "наш запрос",
    short: "наш",
    className: "bg-emerald-50 text-emerald-800 border-emerald-300",
    dotColor: "bg-emerald-500",
  },
  adjacent: {
    label: "смежный — клиент может искать",
    short: "смежный",
    className: "bg-blue-50 text-blue-800 border-blue-300",
    dotColor: "bg-blue-500",
  },
  disputed: {
    label: "спорный — нужна проверка",
    short: "спорный",
    className: "bg-amber-50 text-amber-800 border-amber-300",
    dotColor: "bg-amber-500",
  },
  spam: {
    label: "мусор — не наша тема",
    short: "мусор",
    className: "bg-muted text-muted-foreground border line-through opacity-70",
    dotColor: "bg-muted-foreground",
  },
  unclassified: {
    label: "не классифицирован",
    short: "—",
    className: "bg-muted text-muted-foreground border border-dashed",
    dotColor: "bg-muted-foreground/40",
  },
  // — Funnel taxonomy
  direct_product: {
    label: "горячий — готов покупать",
    short: "горячий",
    className: "bg-emerald-50 text-emerald-800 border-emerald-300",
    dotColor: "bg-emerald-500",
  },
  funnel_warm: {
    label: "тёплый — выбирает активность",
    short: "тёплый",
    className: "bg-blue-50 text-blue-800 border-blue-300",
    dotColor: "bg-blue-500",
  },
  funnel_top: {
    label: "верх воронки — турист ищет что делать",
    short: "верх воронки",
    className: "bg-violet-50 text-violet-800 border-violet-300",
    dotColor: "bg-violet-500",
  },
  out_of_market: {
    label: "чужой регион — не твой рынок",
    short: "чужой регион",
    className: "bg-slate-50 text-slate-700 border-slate-300 opacity-70",
    dotColor: "bg-slate-400",
  },
};

// Safety net: backend may emit a future-unknown value during rollout or
// migrations. Hitting `RELEVANCE_META[unknown]` returned undefined and
// any `.label` access crashed the whole queries page. Use this helper
// instead of direct indexing.
const RELEVANCE_FALLBACK: typeof RELEVANCE_META[RelevanceKey] = {
  label: "—",
  short: "—",
  className: "bg-muted text-muted-foreground border border-dashed",
  dotColor: "bg-muted-foreground/40",
};
function relevanceMeta(key: string | null | undefined) {
  if (!key) return RELEVANCE_FALLBACK;
  return (RELEVANCE_META as Record<string, typeof RELEVANCE_FALLBACK>)[key]
    ?? RELEVANCE_FALLBACK;
}

function normaliseRelevanceKey(value: string | null): RelevanceKey | null {
  if (!value) return null;
  return (RELEVANCE_KEYS as string[]).includes(value)
    ? (value as RelevanceKey)
    : null;
}

const SET_BY_LABEL: Record<string, string> = {
  rules: "правило",
  llm: "LLM",
  user: "вручную",
};

function RelevanceBadge({
  relevance,
  setBy,
  reason,
}: {
  relevance: RelevanceKey;
  setBy: string | null;
  reason: string | null;
}) {
  const meta = relevanceMeta(relevance);
  const titleParts: string[] = [meta.label];
  if (setBy) titleParts.push(`источник: ${SET_BY_LABEL[setBy] ?? setBy}`);
  if (reason) titleParts.push(`«${reason}»`);
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium whitespace-nowrap",
        meta.className,
      )}
      title={titleParts.join(" · ")}
    >
      {meta.short}
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

// getErrorMessage moved to lib/utils — see import at top of file.

export default function StudioQueriesPage() {
  const { currentSite, loading: siteLoading } = useSite();
  const siteId = currentSite?.id || "";
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const layerFromUrl = normaliseRelevanceKey(searchParams.get("layer"));
  // `sort === null` ⇒ no explicit user sort yet → default to focus-first
  // (items where `in_focus=true` float to the top, then volume order).
  // User clicking a sort chip sets a concrete mode and we honour it.
  const [sort, setSort] = useState<SortMode | null>(null);
  const effectiveSort: SortMode = sort ?? "volume";

  // Trigger UI state — local, no SWR. Each button locks for ~3s after
  // click so a double-click doesn't hammer the dedup window unnecessarily.
  const [discoverPending, setDiscoverPending] = useState(false);
  const [refreshPending, setRefreshPending] = useState(false);
  const [wsDiscoverPending, setWsDiscoverPending] = useState(false);
  const [classifyPending, setClassifyPending] = useState(false);
  const [overrideBusy, setOverrideBusy] = useState<Record<string, boolean>>({});
  // Filter — by default hide spam (the whole point of classification).
  // Toggling a chip flips that class in/out of the visible set.
  const [hidden, setHidden] = useState<Set<RelevanceKey>>(
    () => new Set(["spam"]),
  );
  // Wordstat coverage filter — separate from relevance filtering, so
  // owner can drill into «никогда не собирали» without losing their
  // own/adjacent/disputed selection.
  type CoverageFilter =
    | "all"
    | "has_demand"
    | "no_demand"
    | "never_fetched"
    | "invalid";
  const [coverageFilter, setCoverageFilter] = useState<CoverageFilter>("all");
  // SERP-snapshot disclosure — only one row expanded at a time so the
  // page doesn't fire N parallel SWR loops (the table can hit 100+ rows).
  const [expandedQueryId, setExpandedQueryId] = useState<string | null>(null);
  const [banner, setBanner] = useState<{
    kind: "ok" | "deduped" | "err";
    text: string;
  } | null>(null);
  const setSafeTimeout = useTimeoutSetter();

  const { data, error, isLoading, mutate } = useSWR(
    siteId
      ? studioKey("queries", siteId, effectiveSort, layerFromUrl ?? "all")
      : null,
    () => api.studioListQueries(
      siteId,
      effectiveSort,
      1000,
      layerFromUrl ?? undefined,
    ),
  );

  function clearLayerFilter() {
    const params = new URLSearchParams(searchParams.toString());
    params.delete("layer");
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname);
  }

  // Strategic focus — drives the «Сейчас в фокусе» banner + the
  // default focus-first ordering. Hidden entirely when no focus.
  const { data: focus } = useSWR(
    siteId ? studioKey("strategic_focus", siteId) : null,
    () => api.studioGetStrategicFocus(siteId),
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
      setSafeTimeout(() => setDiscoverPending(false), 3000);
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
          text: `Запущено обновление объёмов Wordstat · run_id ${res.run_id.slice(0, 8)}…. Это займёт ~${data?.total ?? "несколько"} ${pluralRu(data?.total ?? 0, ["секунду", "секунды", "секунд"])}.`,
        });
      }
    } catch (e: unknown) {
      setBanner({ kind: "err", text: getErrorMessage(e) });
    } finally {
      setSafeTimeout(() => setRefreshPending(false), 3000);
    }
  }

  async function onClassify() {
    if (!siteId || classifyPending) return;
    setClassifyPending(true);
    setBanner(null);
    try {
      const res = await api.studioClassifyQueries(siteId);
      if (res.deduped) {
        setBanner({
          kind: "deduped",
          text: `Классификация уже идёт (run_id ${res.run_id.slice(0, 8)}…). Подожди, она закончится — таблица обновится автоматически.`,
        });
      } else {
        setBanner({
          kind: "ok",
          text: `Запущена классификация · run_id ${res.run_id.slice(0, 8)}…. Правила бесплатно, LLM Haiku пакетами по 30 — это займёт ~30-60 секунд.`,
        });
      }
    } catch (e: unknown) {
      setBanner({ kind: "err", text: getErrorMessage(e) });
    } finally {
      setSafeTimeout(() => setClassifyPending(false), 3000);
    }
  }

  async function onOverrideRelevance(
    queryId: string,
    nextRelevance: RelevanceKey,
  ) {
    if (!siteId || overrideBusy[queryId]) return;
    setOverrideBusy((b) => ({ ...b, [queryId]: true }));
    setBanner(null);

    // Optimistic flip: badge becomes the new class immediately, then
    // PATCH fires. On success we revalidate; on failure we rollback.
    await mutate(
      (current) => {
        if (!current) return current;
        return {
          ...current,
          items: current.items.map((row) =>
            row.query_id === queryId
              ? {
                  ...row,
                  relevance: nextRelevance,
                  relevance_set_by: "user" as const,
                  relevance_reason_ru:
                    "Помечено вручную владельцем — классификатор не перезатрёт.",
                }
              : row,
          ),
        };
      },
      { revalidate: false },
    );

    try {
      await api.studioOverrideRelevance(siteId, queryId, nextRelevance);
      // Re-fetch after a tick — relevance_counts on the server side
      // is the canonical truth for the filter strip.
      await mutate();
    } catch (e: unknown) {
      setBanner({ kind: "err", text: getErrorMessage(e) });
      // Rollback by re-fetching server state (the optimistic patch
      // is gone after revalidation).
      await mutate();
    } finally {
      setOverrideBusy((b) => {
        const out = { ...b };
        delete out[queryId];
        return out;
      });
    }
  }

  async function onWordstatDiscover() {
    if (!siteId || wsDiscoverPending) return;
    setWsDiscoverPending(true);
    setBanner(null);
    // Pre-check: Wordstat-discovery builds a seed-plan from product,
    // services and geo. Without services/geo_primary the backend job
    // immediately no-ops — fail fast in the UI instead of silently
    // burning a run_id.
    try {
      const profileResp = await api.studioGetProfile(siteId);
      const services = profileResp.profile?.services ?? [];
      const geoPrimary = profileResp.profile?.geo_primary ?? [];
      if (services.length === 0 || geoPrimary.length === 0) {
        setBanner({
          kind: "err",
          text: "Сначала заполни Профиль (раздел /studio/profile): нужны услуги и регион.",
        });
        setSafeTimeout(() => setWsDiscoverPending(false), 0);
        return;
      }
    } catch (e: unknown) {
      setBanner({ kind: "err", text: getErrorMessage(e) });
      setSafeTimeout(() => setWsDiscoverPending(false), 0);
      return;
    }
    try {
      const res = await api.studioWordstatDiscover(siteId);
      if (res.deduped) {
        setBanner({
          kind: "deduped",
          text: `Wordstat-discovery уже идёт (run_id ${res.run_id.slice(0, 8)}…). Подожди, он закончится.`,
        });
      } else {
        setBanner({
          kind: "ok",
          text: `Запущен поиск через Wordstat · run_id ${res.run_id.slice(0, 8)}…. Сервис строит seed-план из продукта, услуг, регионов и смежных туристических интентов; результаты появятся в таблице по мере поступления.`,
        });
      }
    } catch (e: unknown) {
      setBanner({ kind: "err", text: getErrorMessage(e) });
    } finally {
      setSafeTimeout(() => setWsDiscoverPending(false), 3000);
    }
  }

  // ── Render guards ────────────────────────────────────────────────

  if (siteLoading) {
    return (
      <div className="p-4 sm:p-6 space-y-4">
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

  // Tri-state coverage buckets. Prefer the new backend fields
  // (with_demand / no_demand / never_fetched / invalid) when present;
  // fall back to deriving from per-row data so this UI keeps working
  // while the backend rollout is in flight.
  const buckets = (() => {
    const rows = data?.items ?? [];
    const total = coverage?.total ?? rows.length;
    let with_demand = coverage?.with_demand;
    let no_demand = coverage?.no_demand;
    let never_fetched = coverage?.never_fetched;
    let invalid = coverage?.invalid;
    if (
      with_demand == null ||
      no_demand == null ||
      never_fetched == null ||
      invalid == null
    ) {
      let wd = 0, nd = 0, nf = 0, iv = 0;
      for (const r of rows) {
        if (r.wordstat_volume != null && r.wordstat_volume > 0) wd += 1;
        else if (r.wordstat_volume === 0) nd += 1;
        else if (r.wordstat_status === "invalid_phrase") iv += 1;
        else if (
          r.wordstat_status === "fetch_returned_empty" &&
          r.wordstat_updated_at != null
        )
          nd += 1;
        else nf += 1;
      }
      with_demand = with_demand ?? wd;
      no_demand = no_demand ?? nd;
      never_fetched = never_fetched ?? nf;
      invalid = invalid ?? iv;
    }
    return { total, with_demand, no_demand, never_fetched, invalid };
  })();

  const subtitle = coverage
    ? `${buckets.total} ${pluralRu(buckets.total, ["запрос", "запроса", "запросов"])} · ${buckets.with_demand} с подтверждённым спросом`
    : "загружаю…";

  // ── Empty-state messaging (CONCEPT §5: explain WHY) ──────────────

  const showEmptyAll =
    data && coverage && buckets.total === 0;
  const showEmptyVolumes =
    data && coverage && buckets.total > 0 && buckets.with_demand === 0;

  return (
    <div className="p-4 sm:p-6 space-y-5 max-w-6xl">
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
            variant="default"
            size="sm"
            onClick={onClassify}
            disabled={classifyPending}
            title="Классифицирует все запросы по релевантности (наш / смежный / спорный / мусор). Правила бесплатно, остальное через Haiku — около 5 центов на 100 запросов."
          >
            <Brain
              className={cn(
                "h-4 w-4 mr-2",
                classifyPending && "animate-pulse",
              )}
            />
            {classifyPending ? "Запускаю…" : "Классифицировать"}
          </Button>
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
            variant="outline"
            size="sm"
            onClick={onWordstatDiscover}
            disabled={wsDiscoverPending}
            title="Семантическое расширение через Wordstat: для каждой пары «услуга × регион» из профиля сайта подтягиваем «что ищут со словом X»."
          >
            <Wand2
              className={cn(
                "h-4 w-4 mr-2",
                wsDiscoverPending && "animate-pulse",
              )}
            />
            {wsDiscoverPending ? "Запускаю…" : "Найти через Wordstat"}
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

      {/* Strategic-focus banner — only when focus is active AND the
          current page actually has any matching item. Hidden otherwise
          so non-focused sites don't see a no-op badge. */}
      {focus && (data?.items.some((it) => it.in_focus) ?? false) && (
        <div
          className="rounded-md border border-primary/40 bg-primary/5 px-3 py-2 text-sm flex items-start gap-2"
          title="Управление фокусом — /studio/profile"
        >
          <span className="font-medium text-primary whitespace-nowrap">
            Сейчас в фокусе:
          </span>
          <span className="flex-1">
            {focus.label}. Не в фокусе — серым.
          </span>
        </div>
      )}

      {layerFromUrl && (
        <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900 flex items-center gap-3">
          <div className="flex-1">
            Открыт слой:{" "}
            <strong>{relevanceMeta(layerFromUrl).label}</strong>. Таблица
            показывает только этот класс, а чипы ниже оставлены как карта
            всего спроса.
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={clearLayerFilter}
            className="h-7 bg-white/70"
          >
            Показать все
          </Button>
        </div>
      )}

      {/* Wordstat coverage banner — explains the tri-state breakdown
          so «—» in the volume column stops looking like an error.
          Hidden when there's no data yet. */}
      {data && buckets.total > 0 && (
        <div className="rounded-md border bg-slate-50 border-slate-200 px-3 py-2 text-sm text-slate-700 flex items-center gap-3">
          <span className="text-base" aria-hidden>📊</span>
          <div className="flex-1">
            <strong>{buckets.with_demand}</strong>{" "}
            {pluralRu(buckets.with_demand, ["запрос", "запроса", "запросов"])} с
            подтверждённым спросом из <strong>{buckets.total}</strong>.
            {buckets.never_fetched > 0 && (
              <> Ещё <strong>{buckets.never_fetched}</strong> ждут сбора
              Wordstat (объёмы обновляются по вторникам, новая разведка
              запускается по средам).</>
            )}
            {buckets.no_demand > 0 && (
              <> Wordstat вернул «нет спроса» для{" "}
              <strong>{buckets.no_demand}</strong> — это нормально для редких/брендовых
              фраз.</>
            )}
            {buckets.invalid > 0 && (
              <> Невалидных фраз: <strong>{buckets.invalid}</strong> —
              проверь, как они попали в карту спроса.</>
            )}
          </div>
        </div>
      )}

      {/* Wordstat coverage filter chips — orthogonal to the relevance
          filter below. Drills the table into one bucket of the banner. */}
      {data && buckets.total > 0 && (
        <div className="flex flex-wrap gap-1.5 text-xs">
          {(
            [
              ["all", `Все (${buckets.total})`],
              ["has_demand", `С объёмом (${buckets.with_demand})`],
              ["no_demand", `Без спроса (${buckets.no_demand})`],
              ["never_fetched", `Не собирали (${buckets.never_fetched})`],
              ...(buckets.invalid > 0
                ? [["invalid", `Невалидные (${buckets.invalid})`] as const]
                : []),
            ] as Array<readonly [CoverageFilter, string]>
          ).map(([key, label]) => {
            const count =
              key === "all"
                ? buckets.total
                : key === "has_demand"
                  ? buckets.with_demand
                  : key === "no_demand"
                    ? buckets.no_demand
                    : key === "never_fetched"
                      ? buckets.never_fetched
                      : buckets.invalid;
            const isActive = coverageFilter === key;
            const disabled = key !== "all" && count === 0;
            return (
              <button
                key={key}
                type="button"
                onClick={() => !disabled && setCoverageFilter(key)}
                disabled={disabled}
                className={cn(
                  "rounded-md border px-2 py-0.5 transition-colors",
                  isActive
                    ? "bg-primary text-primary-foreground border-primary"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                  disabled && "opacity-40 cursor-not-allowed",
                )}
              >
                {label}
              </button>
            );
          })}
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

      {/* Harmful-visibility cross-link — only shown when there's
          something to fix. Computed cheaply on the client from the
          existing data — counts spam+disputed rows where we have a
          position (proxy for «we rank somewhere»). The /harmful page
          does the real top-30 cut on the backend. */}
      {data && (() => {
        const candidates = data.items.filter(
          (it) =>
            (it.relevance === "spam" || it.relevance === "disputed") &&
            it.last_position != null &&
            it.last_position <= 30,
        );
        if (candidates.length === 0) return null;
        return (
          <Link
            href="/studio/queries/harmful"
            className="block rounded-md border border-amber-300 bg-amber-50 hover:bg-amber-100 transition-colors px-3 py-2 text-sm flex items-center gap-2"
          >
            <AlertTriangle className="h-4 w-4 text-amber-600 flex-shrink-0" />
            <span className="flex-1">
              <strong>{candidates.length}</strong>{" "}
              {pluralRu(candidates.length, ["запрос", "запроса", "запросов"])} с
              вредной видимостью —{" "}
              {pluralRu(candidates.length, ["ранжируется", "ранжируются", "ранжируются"])}{" "}
              в топ-30, но классификатор пометил{" "}
              {pluralRu(candidates.length, ["его", "их", "их"])} как мусор/спорный
            </span>
            <ChevronRight className="h-4 w-4 text-amber-700 flex-shrink-0" />
          </Link>
        );
      })()}

      {/* Relevance filter chips — clicking toggles a class out of view.
          Default: spam hidden (the whole reason classifier exists). */}
      {data?.relevance_counts && (
        <div className="flex items-center gap-1.5 text-sm flex-wrap">
          <span className="text-muted-foreground mr-1">Показывать:</span>
          {(
            [
              // Show all the values the backend can emit — both legacy
              // and the new funnel taxonomy. Counts read from the same
              // `relevance_counts` dict keyed by relevance string.
              "direct_product",
              "funnel_warm",
              "funnel_top",
              "out_of_market",
              "own",
              "adjacent",
              "disputed",
              "unclassified",
              "spam",
            ] as RelevanceKey[]
          ).map((key) => {
            const count = data.relevance_counts[key] ?? 0;
            // Skip chips for legacy values when there are zero rows —
            // post-backfill «own»/«adjacent» go to 0 and a row of empty
            // chips is just noise. Always show the new taxonomy.
            const isLegacy = ["own", "adjacent", "disputed", "unclassified"].includes(key);
            if (isLegacy && count === 0) return null;
            const meta = relevanceMeta(key);
            const isHidden = hidden.has(key);
            return (
              <button
                key={key}
                onClick={() =>
                  setHidden((prev) => {
                    const next = new Set(prev);
                    if (next.has(key)) next.delete(key);
                    else next.add(key);
                    return next;
                  })
                }
                disabled={count === 0}
                className={cn(
                  "rounded-md border px-2 py-0.5 text-xs inline-flex items-center gap-1.5 transition-opacity",
                  count === 0 && "opacity-40 cursor-not-allowed",
                  isHidden && count > 0 && "opacity-50",
                )}
                title={
                  count === 0
                    ? `Нет запросов класса «${meta.short}»`
                    : isHidden
                      ? `Кликни чтобы показать «${meta.short}»`
                      : `Кликни чтобы спрятать «${meta.short}»`
                }
              >
                <span
                  className={cn(
                    "h-2 w-2 rounded-full flex-shrink-0",
                    meta.dotColor,
                  )}
                />
                <span>{meta.short}</span>
                <span className="text-muted-foreground tabular-nums">
                  {count}
                </span>
              </button>
            );
          })}
          {data.relevance_counts.unclassified > 0 && (
            <span className="text-xs text-amber-700 ml-2">
              <HelpCircle className="h-3 w-3 inline mr-0.5" />
              {data.relevance_counts.unclassified} ещё не классифицированы — нажми «Классифицировать»
            </span>
          )}
        </div>
      )}

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
              Запросов в БД: <strong>{buckets.total}</strong>, но ни одного
              с подтверждённым спросом. Нажми «Обновить объёмы Wordstat» —
              это займёт ~{buckets.total}{" "}
              {pluralRu(buckets.total, ["секунду", "секунды", "секунд"])}{" "}
              (по одному запросу в секунду).
            </p>
          </CardContent>
        </Card>
      ) : (
        (() => {
          const effectiveHidden = layerFromUrl
            ? new Set<RelevanceKey>()
            : hidden;
          let visible = (data?.items || []).filter(
            (row) => !effectiveHidden.has(row.relevance as RelevanceKey),
          );
          // Coverage chip filter (orthogonal to relevance hidden-set).
          if (coverageFilter !== "all") {
            visible = visible.filter((row) => {
              const v = row.wordstat_volume;
              const updated = row.wordstat_updated_at;
              const status = row.wordstat_status;
              if (coverageFilter === "has_demand")
                return v != null && v > 0;
              if (coverageFilter === "no_demand")
                return (
                  v === 0 ||
                  (status === "fetch_returned_empty" && updated != null)
                );
              if (coverageFilter === "never_fetched")
                return (
                  updated == null && status !== "invalid_phrase" && v == null
                );
              if (coverageFilter === "invalid")
                return status === "invalid_phrase";
              return true;
            });
          }
          // Default ordering: focus-first when no explicit user sort.
          // Sort is stable in modern JS, so within each focus bucket
          // the server-supplied order (volume desc) is preserved.
          if (sort === null) {
            visible = [...visible].sort(
              (a, b) => Number(!!b.in_focus) - Number(!!a.in_focus),
            );
          }
          if (visible.length === 0) {
            return (
              <Card className="border-dashed">
                <CardContent className="pt-6 text-sm text-muted-foreground">
                  По текущим фильтрам ничего не показано. Все запросы
                  спрятаны через чипы выше — кликни нужный класс чтобы
                  его показать.
                </CardContent>
              </Card>
            );
          }
          return (
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    {/* Chevron column — small fixed width so the
                        disclosure toggle doesn't push other columns. */}
                    <TableHead className="w-8" aria-label="Открыть выдачу" />
                    <TableHead className="min-w-[260px]">Запрос</TableHead>
                    <TableHead>Класс</TableHead>
                    <TableHead className="text-right">Объём</TableHead>
                    <TableHead>Статус</TableHead>
                    <TableHead className="text-right">Позиция</TableHead>
                    <TableHead className="text-right">Показы 14д</TableHead>
                    <TableHead>Тренд</TableHead>
                    <TableHead>Кластер</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {visible.map((row) => {
                    const isExpanded = expandedQueryId === row.query_id;
                    return (
                    <Fragment key={row.query_id}>
                    <TableRow
                      aria-expanded={isExpanded}
                      className={cn(
                        // Mute out-of-focus rows only when a focus is
                        // actually active — otherwise everything looks
                        // grayed out.
                        focus && !row.in_focus && "opacity-60",
                      )}
                    >
                      {/* Chevron — separate button, click does NOT
                          bubble to row-level handlers (relevance
                          popover etc.). One row at a time. */}
                      <TableCell className="w-8 p-1 text-center align-middle">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setExpandedQueryId((cur) =>
                              cur === row.query_id ? null : row.query_id,
                            );
                          }}
                          aria-expanded={isExpanded}
                          aria-controls={`serp-panel-${row.query_id}`}
                          aria-label={
                            isExpanded
                              ? "Скрыть выдачу Яндекса"
                              : "Показать выдачу Яндекса"
                          }
                          title={
                            isExpanded
                              ? "Скрыть выдачу"
                              : "Показать снимок выдачи Яндекса"
                          }
                          className="inline-flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                        >
                          {isExpanded ? (
                            <ChevronDown className="h-4 w-4" />
                          ) : (
                            <ChevronRight className="h-4 w-4" />
                          )}
                        </button>
                      </TableCell>
                      <TableCell className="font-medium">
                        <div className="flex items-center gap-2 flex-wrap">
                          {isUrlLikeQuery(row.query_text) ? (
                            <>
                              <Badge
                                variant="outline"
                                className="text-[10px] bg-amber-50 text-amber-800 border-amber-300"
                              >
                                системная запись
                              </Badge>
                              <span
                                className="font-mono text-xs text-muted-foreground truncate max-w-[240px]"
                                title={row.query_text}
                              >
                                {row.query_text}
                              </span>
                            </>
                          ) : (
                            <span
                              className={cn(
                                row.relevance === "spam" && "text-muted-foreground line-through",
                              )}
                            >
                              {row.query_text}
                            </span>
                          )}
                          <FocusPill in_focus={row.in_focus} />
                          {row.is_branded && (
                            <Badge
                              variant="outline"
                              className="text-[10px] bg-blue-50 text-blue-800 border-blue-300"
                            >
                              бренд
                            </Badge>
                          )}
                        </div>
                        <div
                          className="mt-1 text-[11px] leading-snug text-muted-foreground max-w-[420px]"
                          title={`${row.strategy_reason_ru}. ${row.strategy_action_ru}`}
                        >
                          <span className="font-medium text-foreground/70">
                            {row.strategy_label_ru}
                          </span>
                          {" · "}
                          {row.strategy_action_ru}
                        </div>
                        <div
                          className="mt-1 flex items-center gap-1.5 flex-wrap text-[11px] leading-snug text-muted-foreground max-w-[420px]"
                          title={`${row.coverage_reason_ru}. ${row.coverage_action_ru}`}
                        >
                          <CoverageBadge
                            status={row.coverage_status}
                            score={row.coverage_score}
                          />
                          {row.best_page_id && row.best_page_url ? (
                            <Link
                              href={`/studio/pages/${row.best_page_id}`}
                              className="underline-offset-2 hover:underline truncate max-w-[300px]"
                              title={row.best_page_url}
                            >
                              {row.best_page_title || row.best_page_url}
                            </Link>
                          ) : (
                            <span>{row.coverage_action_ru}</span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        <RelevanceCell
                          row={row}
                          busy={!!overrideBusy[row.query_id]}
                          onOverride={(next) =>
                            onOverrideRelevance(row.query_id, next)
                          }
                        />
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
                    {isExpanded && (
                      <TableRow
                        id={`serp-panel-${row.query_id}`}
                        className="bg-muted/20 hover:bg-muted/20"
                      >
                        {/* colSpan = 9 (chevron + 8 original columns).
                            Single cell holds the panel so the layout
                            isn't constrained to the table's columns. */}
                        <TableCell colSpan={9} className="p-3">
                          <SerpSnapshotPanel
                            siteId={siteId}
                            queryId={row.query_id}
                          />
                        </TableCell>
                      </TableRow>
                    )}
                    </Fragment>
                    );
                  })}
                </TableBody>
              </Table>
            </Card>
          );
        })()
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

// ── Relevance cell — badge + override popover ───────────────────────

type QueryRowShape = {
  query_id: string;
  relevance: RelevanceKey;
  relevance_set_by: "rules" | "llm" | "user" | null;
  relevance_reason_ru: string | null;
};

function RelevanceCell({
  row,
  busy,
  onOverride,
}: {
  row: QueryRowShape;
  busy: boolean;
  onOverride: (next: RelevanceKey) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={busy}
        className="inline-flex items-center gap-1 disabled:opacity-50"
        title="Кликни чтобы поправить класс — твой выбор закрепится и не перезапишется автоматическим классификатором"
      >
        <RelevanceBadge
          relevance={row.relevance}
          setBy={row.relevance_set_by}
          reason={row.relevance_reason_ru}
        />
        {row.relevance_set_by === "user" && (
          <span
            title="Класс установлен вручную владельцем — классификатор не перезатрёт"
            className="text-[10px] text-muted-foreground"
          >
            👤
          </span>
        )}
      </button>

      {open && (
        <>
          {/* click-outside catcher */}
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-10 cursor-default"
            aria-label="Закрыть меню"
          />
          <div className="absolute z-20 mt-1 right-0 rounded-md border bg-popover shadow-md p-1 text-xs min-w-[160px]">
            <div className="px-2 py-1 text-[10px] uppercase text-muted-foreground tracking-wide">
              Поменять класс
            </div>
            {(
              [
                // New funnel taxonomy — the future. Show first.
                ["direct_product", "горячий"],
                ["funnel_warm", "тёплый"],
                ["funnel_top", "верх воронки"],
                ["out_of_market", "чужой регион"],
                ["disputed", "спорный"],
                ["spam", "мусор"],
              ] as Array<[RelevanceKey, string]>
            ).map(([val, label]) => {
              const meta = relevanceMeta(val);
              const isCurrent = row.relevance === val;
              return (
                <button
                  key={val}
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    if (!isCurrent) onOverride(val);
                  }}
                  disabled={isCurrent}
                  className={cn(
                    "w-full flex items-center gap-2 px-2 py-1.5 rounded-sm text-left",
                    !isCurrent && "hover:bg-accent",
                    isCurrent && "opacity-60",
                  )}
                >
                  <span
                    className={cn("h-2 w-2 rounded-full", meta.dotColor)}
                  />
                  <span>{label}</span>
                  {isCurrent && (
                    <Check className="h-3 w-3 ml-auto text-muted-foreground" />
                  )}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
