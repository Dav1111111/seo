"use client";

/**
 * Studio /queries/harmful — «Вредная видимость» (Studio v2 etap 5).
 *
 * Owner question: «по каким нерелевантным запросам я уже ранжируюсь
 * в топ-30?». Это потерянный crawl-budget Яндекса и размытая тема
 * сайта — пользователь, ищущий настоящие услуги, видит нас рядом с
 * «джинсами багги».
 *
 * Backend: backend/app/api/v1/studio.py · list_harmful_visibility.
 *
 * Layout:
 *   1. Шапка с totals (N spam + M disputed = K всего).
 *   2. Список карточек, отсортирован по убыванию объёма Wordstat —
 *      самое больное сверху.
 *   3. Каждая карточка: запрос, текущая позиция, объём, причина от
 *      классификатора, рекомендация что сделать.
 *
 * Зачем отдельная страница, а не секция на /studio/queries: список
 * вредных запросов читается как самостоятельный отчёт, а не как
 * фильтр обычной таблицы. Действие тут — «открыть страницу,
 * переписать title», не «посмотреть метрики».
 */

import useSWR from "swr";
import Link from "next/link";

import { api } from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";
import { pluralRu } from "@/lib/format";
import { getErrorMessage } from "@/lib/utils";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  ArrowLeft,
  AlertTriangle,
  CheckCircle2,
  HelpCircle,
  Search,
  Lightbulb,
} from "lucide-react";
import { cn } from "@/lib/utils";

const RELEVANCE_TONE = {
  spam: {
    label: "мусор",
    badge: "bg-muted text-muted-foreground border",
    border: "border-l-4 border-l-rose-400",
  },
  disputed: {
    label: "спорный",
    badge: "bg-amber-50 text-amber-800 border-amber-300",
    border: "border-l-4 border-l-amber-400",
  },
} as const;

const SET_BY_LABEL: Record<string, string> = {
  rules: "правило",
  llm: "LLM",
  user: "вручную",
};

function fmtNumber(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString("ru-RU");
}

function fmtPosition(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toFixed(1);
}

export default function HarmfulVisibilityPage() {
  const { currentSite, loading: siteLoading } = useSite();
  const siteId = currentSite?.id || "";

  const { data, error, isLoading } = useSWR(
    siteId ? studioKey("queries_harmful", siteId) : null,
    () => api.studioHarmfulVisibility(siteId),
  );

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
              Выбери сайт в свитчере слева — отчёт работает в контексте
              конкретного сайта.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const total = data?.counts.total ?? 0;
  const items = data?.items ?? [];

  return (
    <div className="p-4 sm:p-6 space-y-5 max-w-4xl">
      {/* Header */}
      <div>
        <Link
          href="/studio/queries"
          className="inline-flex items-center text-xs text-muted-foreground hover:text-foreground mb-1"
        >
          <ArrowLeft className="h-3 w-3 mr-1" /> К запросам
        </Link>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <AlertTriangle className="h-6 w-6 text-amber-600" /> Вредная видимость
        </h1>
        <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
          Запросы где мы стоим в топ-30, но классификатор пометил их как
          мусор или спорные. Это потерянный crawl-budget и размытая тема
          сайта. Каждая карточка — что переписать.
        </p>
      </div>

      {/* Methodology footnote */}
      <div className="text-xs text-muted-foreground rounded-md border border-dashed px-3 py-2 flex items-start gap-2">
        <HelpCircle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
        <span>
          Берём запросы где <code>relevance ∈ {"{spam, disputed}"}</code> И{" "}
          <code>last_position ≤ 30</code>. Запросы по которым мы вне топ-30
          сюда не попадают — там нечего чинить, Яндекс нас и так не показывает.
          Если что-то здесь на самом деле твоё — кликни бейдж класса в общей{" "}
          <Link
            href="/studio/queries"
            className="underline hover:text-foreground"
          >
            таблице запросов
          </Link>{" "}
          и пометь «наш».
        </span>
      </div>

      {/* Body */}
      {isLoading && (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </div>
      )}

      {error && (
        <Card className="border-red-300 bg-red-50">
          <CardContent className="pt-6 text-sm text-red-900">
            Не удалось загрузить отчёт: {getErrorMessage(error)}
          </CardContent>
        </Card>
      )}

      {!isLoading && !error && data && total === 0 && (
        <Card className="border-dashed">
          <CardContent className="pt-6 flex items-start gap-3">
            <CheckCircle2 className="h-5 w-5 text-emerald-600 mt-0.5 flex-shrink-0" />
            <div className="space-y-1">
              <div className="font-medium">Вредной видимости нет</div>
              <p className="text-sm text-muted-foreground">
                Либо классификатор ещё не запускался, либо все запросы
                по которым мы стоим в топ-30 — наши или смежные. Это
                хороший знак: сайт ранжируется именно по тем фразам где
                его можно встретить целевой аудитории. Если кажется что
                это слишком хорошо — открой{" "}
                <Link
                  href="/studio/queries"
                  className="underline hover:text-foreground"
                >
                  /studio/queries
                </Link>{" "}
                и нажми «Классифицировать».
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {!isLoading && !error && data && total > 0 && (
        <>
          {/* Summary header */}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <SummaryCard
              label="всего вредной видимости"
              value={String(total)}
              tone="critical"
            />
            <SummaryCard
              label="мусорных"
              value={String(data.counts.spam)}
              hint={`${pluralRu(data.counts.spam, ["запрос", "запроса", "запросов"])} вне темы`}
            />
            <SummaryCard
               label="спорных"
              value={String(data.counts.disputed)}
              hint="нужно решение от тебя"
            />
          </div>

          {/* List */}
          <div className="space-y-3">
            {items.map((it) => {
              const tone = RELEVANCE_TONE[it.relevance];
              return (
                <Card key={it.query_id} className={cn(tone.border)}>
                  <CardContent className="pt-4 pb-4 space-y-3">
                    {/* Top line */}
                    <div className="flex items-baseline gap-2 flex-wrap">
                      <span
                        className={cn(
                          "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium",
                          tone.badge,
                        )}
                      >
                        {tone.label}
                      </span>
                      <h3 className="font-medium">
                        <Search className="h-4 w-4 inline mr-1 text-muted-foreground" />
                        {it.query_text}
                      </h3>
                    </div>

                    {/* Stats grid */}
                    <div className="grid grid-cols-3 gap-3 text-xs">
                      <Stat
                        label="позиция"
                        value={fmtPosition(it.last_position)}
                        tone={
                          it.last_position != null && it.last_position <= 10
                            ? "danger"
                            : "neutral"
                        }
                        hint={
                          it.last_position != null && it.last_position <= 10
                            ? "в топ-10 — больно"
                            : undefined
                        }
                      />
                      <Stat
                        label="показы 14д"
                        value={fmtNumber(it.last_impressions_14d)}
                      />
                      <Stat
                        label="объём Wordstat"
                        value={fmtNumber(it.wordstat_volume)}
                        hint="фразу часто ищут?"
                      />
                    </div>

                    {/* Reason from classifier */}
                    {it.relevance_reason_ru && (
                      <div className="text-xs text-muted-foreground">
                        <span className="font-medium">
                          Почему {tone.label}:
                        </span>{" "}
                        {it.relevance_reason_ru}
                        {it.relevance_set_by && (
                          <span className="text-muted-foreground/70 ml-1">
                            ({SET_BY_LABEL[it.relevance_set_by] ?? it.relevance_set_by})
                          </span>
                        )}
                      </div>
                    )}

                    {/* Suggested action */}
                    <div className="rounded-md border bg-muted/30 px-3 py-2 flex items-start gap-2 text-sm">
                      <Lightbulb className="h-4 w-4 mt-0.5 flex-shrink-0 text-amber-600" />
                      <div>
                        <div className="font-medium text-xs uppercase tracking-wide text-muted-foreground mb-0.5">
                          Что сделать
                        </div>
                        {it.suggested_action_ru}
                      </div>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────────────

function SummaryCard({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "critical" | "neutral";
}) {
  return (
    <Card>
      <CardContent className="pt-4 pb-3 space-y-0.5">
        <div
          className={cn(
            "text-2xl font-semibold tabular-nums",
            tone === "critical" && "text-rose-700",
          )}
        >
          {value}
        </div>
        <div className="text-xs text-muted-foreground">{label}</div>
        {hint && (
          <div className="text-[10px] text-muted-foreground/80 mt-1">{hint}</div>
        )}
      </CardContent>
    </Card>
  );
}

function Stat({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "danger" | "neutral";
}) {
  return (
    <div className="rounded-md border bg-card px-2 py-1.5">
      <div className="text-[10px] text-muted-foreground uppercase tracking-wide">
        {label}
      </div>
      <div
        className={cn(
          "font-medium tabular-nums",
          tone === "danger" && "text-rose-700",
        )}
      >
        {value}
      </div>
      {hint && <div className="text-[10px] text-muted-foreground">{hint}</div>}
    </div>
  );
}
