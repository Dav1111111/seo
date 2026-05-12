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

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import Link from "next/link";

import { api } from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";
import { pluralRu, fmtAge } from "@/lib/format";
import { getErrorMessage } from "@/lib/utils";
import { useTimeoutSetter } from "@/lib/hooks/use-timeout";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { FocusPill } from "@/components/studio/focus-pill";
import {
  ArrowLeft,
  AlertTriangle,
  CheckCircle2,
  HelpCircle,
  Search,
  Lightbulb,
  Brain,
  Info,
  ExternalLink,
  ChevronDown,
  ChevronUp,
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

  const [diagnosePending, setDiagnosePending] = useState(false);
  const [banner, setBanner] = useState<{
    kind: "ok" | "deduped" | "err";
    text: string;
  } | null>(null);
  const setSafeTimeout = useTimeoutSetter();

  // Mirror `diagnosePending` into a ref so SWR's refreshInterval
  // callback (which closes over the initial render scope) still sees
  // the latest value. Without this the polling never restarts.
  const diagnosePendingRef = useRef(false);
  useEffect(() => {
    diagnosePendingRef.current = diagnosePending;
  }, [diagnosePending]);

  const { data, error, isLoading, mutate } = useSWR(
    siteId ? studioKey("queries_harmful", siteId) : null,
    () => api.studioHarmfulVisibility(siteId),
    {
      // Read from ref so the closure always sees the latest state.
      // 5-sec poll while the Celery task is running, stop otherwise.
      refreshInterval: () => (diagnosePendingRef.current ? 5000 : 0),
    },
  );

  // Strategic focus — banner header + default focus-first ordering of
  // the harmful list. Hidden completely when no focus is set.
  const { data: focus } = useSWR(
    siteId ? studioKey("strategic_focus", siteId) : null,
    () => api.studioGetStrategicFocus(siteId),
  );

  // Stop polling when all candidates have a diagnosis — server-side
  // signal that the run completed, more reliable than a 5-min timeout.
  useEffect(() => {
    if (!diagnosePending || !data) return;
    const undiagnosedNow = data.items.filter((it) => !it.harmful_diagnosis).length;
    if (undiagnosedNow === 0) {
      setDiagnosePending(false);
    }
  }, [data, diagnosePending]);

  async function onDiagnose() {
    if (!siteId || diagnosePending) return;
    setDiagnosePending(true);
    setBanner(null);
    try {
      const res = await api.studioTriggerHarmfulDiagnose(siteId);
      if (res.deduped) {
        setBanner({
          kind: "deduped",
          text: `Разбор уже идёт (run_id ${res.run_id.slice(0, 8)}…). Подожди — карточки обновятся сами.`,
        });
      } else {
        setBanner({
          kind: "ok",
          text: `Запущен разбор · run_id ${res.run_id.slice(0, 8)}…. Для каждого запроса находим страницу через Search API и LLM объясняет причину. ~10 сек на запрос.`,
        });
        await mutate();
      }
    } catch (e: unknown) {
      setBanner({ kind: "err", text: getErrorMessage(e) });
    } finally {
      // Long cooldown — diagnose runs minutes, not seconds. Stop
      // polling after ~5 min unless a fresh user action restarts.
      setSafeTimeout(() => setDiagnosePending(false), 300_000);
    }
  }

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
  const rawItems = data?.items ?? [];
  // Focus-first ordering when a focus is active. Sort is stable so
  // within each bucket the server-supplied order (relevance + volume
  // desc) is preserved.
  const items = focus
    ? [...rawItems].sort(
        (a, b) => Number(!!b.in_focus) - Number(!!a.in_focus),
      )
    : rawItems;
  const hasInFocus = rawItems.some((it) => it.in_focus);

  // Count how many items still need a diagnosis run.
  const undiagnosed = items.filter((it) => !it.harmful_diagnosis).length;

  return (
    <div className="p-4 sm:p-6 space-y-5 max-w-4xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
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
            мусор или спорные. Жми «Разобрать причины» — система найдёт
            страницу что ранжируется и объяснит что переписать.
          </p>
        </div>
        {total > 0 && (
          <Button
            onClick={onDiagnose}
            disabled={diagnosePending || undiagnosed === 0}
            size="sm"
            title={
              undiagnosed === 0
                ? "Все запросы уже разобраны"
                : `Запустить разбор для ${undiagnosed} запросов: SERP-проба + LLM анализ страницы. ~10 сек на запрос, ~5 центов на 10 запросов.`
            }
          >
            <Brain
              className={cn(
                "h-4 w-4 mr-2",
                diagnosePending && "animate-pulse",
              )}
            />
            {diagnosePending
              ? "Разбираю…"
              : undiagnosed === 0
                ? "Все разобраны"
                : `Разобрать причины (${undiagnosed})`}
          </Button>
        )}
      </div>

      {/* Strategic-focus banner — only when focus is set AND at least
          one harmful item lands in the focus zone. */}
      {focus && hasInFocus && (
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

      {/* Trigger banner */}
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
          <Info className="h-4 w-4 mt-0.5 flex-shrink-0" />
          <span>{banner.text}</span>
        </div>
      )}

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
                <Card
                  key={it.query_id}
                  className={cn(
                    tone.border,
                    // Mute out-of-focus rows only when a focus is active.
                    focus && !it.in_focus && "opacity-60",
                  )}
                >
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
                      <FocusPill in_focus={it.in_focus} />
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

                    {/* Action / diagnosis: detailed if LLM ran,
                        simple rule-based hint otherwise. */}
                    {it.harmful_diagnosis ? (
                      <DiagnosisSection diag={it.harmful_diagnosis} diagnosedAt={it.harmful_diagnosed_at} />
                    ) : (
                      <div className="rounded-md border bg-muted/30 px-3 py-2 flex items-start gap-2 text-sm">
                        <Lightbulb className="h-4 w-4 mt-0.5 flex-shrink-0 text-amber-600" />
                        <div>
                          <div className="font-medium text-xs uppercase tracking-wide text-muted-foreground mb-0.5">
                            Что сделать (общий совет)
                          </div>
                          {it.suggested_action_ru}
                          <div className="text-xs text-muted-foreground mt-2">
                            Жми «Разобрать причины» сверху чтобы получить
                            полный разбор для этого запроса: какая страница
                            ранжируется, почему, и что переписать.
                          </div>
                        </div>
                      </div>
                    )}
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

// ── Detailed diagnosis section (LLM cause + concrete page edits) ───

function DiagnosisSection({
  diag,
  diagnosedAt,
}: {
  diag: {
    matched_url: string | null;
    matched_position: number | null;
    cause_ru: string;
    fixes: {
      title_change?: string | null;
      h1_change?: string | null;
      meta_description_change?: string | null;
      content_change_ru?: string | null;
      schema_recommendation?: string | null;
      noindex_recommended?: boolean;
    };
    skipped?: "no_match" | "no_page_in_db";
  };
  diagnosedAt: string | null;
}) {
  const [showFullCause, setShowFullCause] = useState(false);
  const fixes = diag.fixes ?? {};
  const hasFixes =
    fixes.title_change ||
    fixes.h1_change ||
    fixes.meta_description_change ||
    fixes.content_change_ru ||
    fixes.schema_recommendation ||
    fixes.noindex_recommended;

  return (
    <div className="rounded-md border bg-amber-50/60 border-amber-200 px-3 py-3 space-y-3 text-sm">
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <div className="font-medium text-xs uppercase tracking-wide text-amber-900 flex items-center gap-1.5">
          <Brain className="h-3.5 w-3.5" />
          Разбор причины
        </div>
        <div className="text-[10px] text-muted-foreground">
          {diagnosedAt ? `проверено ${fmtAge(diagnosedAt)}` : ""}
        </div>
      </div>

      {/* Matched page */}
      {diag.matched_url ? (
        <div className="text-xs">
          <span className="text-muted-foreground">Ранжируется страница: </span>
          <a
            href={diag.matched_url}
            target="_blank"
            rel="noreferrer"
            className="text-foreground hover:text-primary inline-flex items-center gap-1"
          >
            {diag.matched_url}
            <ExternalLink className="h-3 w-3" />
          </a>
          {diag.matched_position != null && (
            <span className="text-muted-foreground ml-2">
              (позиция {diag.matched_position})
            </span>
          )}
        </div>
      ) : (
        <div className="text-xs text-muted-foreground italic">
          Конкретный URL не определён — Search API не вернул совпадение по
          нашему домену в момент проверки.
        </div>
      )}

      {/* Cause */}
      <div className="space-y-1">
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
          Почему так
        </div>
        <p
          className={cn(
            "leading-snug",
            !showFullCause && diag.cause_ru.length > 220 && "line-clamp-3",
          )}
        >
          {diag.cause_ru}
        </p>
        {diag.cause_ru.length > 220 && (
          <button
            type="button"
            onClick={() => setShowFullCause((v) => !v)}
            className="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-0.5"
          >
            {showFullCause ? (
              <>
                <ChevronUp className="h-3 w-3" /> свернуть
              </>
            ) : (
              <>
                <ChevronDown className="h-3 w-3" /> показать целиком
              </>
            )}
          </button>
        )}
      </div>

      {/* Fixes */}
      {diag.skipped ? (
        <div className="rounded-md border bg-muted/30 px-2 py-1.5 text-xs text-muted-foreground">
          Полный разбор не сделан: {diag.skipped === "no_match" ? "не нашли URL через Search API — попробуй позже" : "URL не в БД — запусти crawl"}.
        </div>
      ) : hasFixes ? (
        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
            Что переписать
          </div>
          {fixes.noindex_recommended && (
            <FixBlock
              label="⚠️ Рекомендация: убрать страницу из индекса"
              value="Страница вообще не нужна на сайте — добавь meta name=robots content=noindex"
              tone="danger"
            />
          )}
          <FixBlock label="Title" value={fixes.title_change} />
          <FixBlock label="H1" value={fixes.h1_change} />
          <FixBlock
            label="Meta description"
            value={fixes.meta_description_change}
          />
          <FixBlock
            label="Что поменять в тексте"
            value={fixes.content_change_ru}
            multiline
          />
          <FixBlock
            label="Schema.org"
            value={fixes.schema_recommendation}
          />
        </div>
      ) : (
        <div className="text-xs text-muted-foreground italic">
          LLM не предложил конкретных правок. Проверь страницу руками или
          перезапусти разбор позже.
        </div>
      )}
    </div>
  );
}

function FixBlock({
  label,
  value,
  multiline = false,
  tone = "neutral",
}: {
  label: string;
  value: string | null | undefined;
  multiline?: boolean;
  tone?: "danger" | "neutral";
}) {
  if (!value) return null;
  return (
    <div className="text-xs">
      <div
        className={cn(
          "font-medium mb-0.5",
          tone === "danger" ? "text-rose-700" : "text-foreground",
        )}
      >
        {label}
      </div>
      <div
        className={cn(
          "rounded border bg-card px-2 py-1.5 text-foreground/90",
          multiline ? "whitespace-pre-line" : "truncate",
        )}
      >
        {value}
      </div>
    </div>
  );
}
