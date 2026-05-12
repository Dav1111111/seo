"use client";

/**
 * Studio v2 etap 7 — brain plan card on /studio.
 *
 * Phase A — owner-friendly version. Each action shows:
 *   - title:   «У тебя N страниц нет в индексе» (не «Верни в индекс N»)
 *   - body_ru: 2-3 предложения на нормальном языке, что и почему
 *   - examples: реальные строки из БД (URLs / queries / service names)
 *   - what_to_do_ru: один императив «открой X, нажми Y»
 *   - link to module
 *   - evidence «receipt» (collapsible) — для тех, кто хочет цифры
 *
 * No LLM in render. Every word in title/body/examples comes either
 * from the rule template or directly from the database row.
 */

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  Brain,
  ChevronRight,
  ChevronDown,
  Clock,
  Info,
  Quote,
  Link2,
  AlertCircle,
  MessageCircle,
} from "lucide-react";

import { api } from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn, getErrorMessage } from "@/lib/utils";

import { BrainActionChat } from "./brain-action-chat";

const SEV_STYLE: Record<string, string> = {
  critical: "border-red-300 bg-red-50",
  high: "border-amber-300 bg-amber-50",
  medium: "border-yellow-200 bg-yellow-50/40",
  low: "border-emerald-200 bg-emerald-50/40",
};

const SEV_LABEL: Record<string, string> = {
  critical: "критично",
  high: "важно",
  medium: "средне",
  low: "несрочно",
};

const SEV_DOT: Record<string, string> = {
  critical: "bg-red-500",
  high: "bg-amber-500",
  medium: "bg-yellow-500",
  low: "bg-emerald-500",
};

// Pick the OLDEST of the three data-source anchors — that's the
// real "as of when" of the plan. `computed_at` is just the moment
// the SQL ran, which is always "now" and misleading. Returns the
// ISO timestamp plus how stale it is in days (rounded down).
function oldestDataAnchor(
  webmaster: string | null,
  wordstat: string | null,
  crawl: string | null,
): { iso: string; ageDays: number } | null {
  const isos = [webmaster, wordstat, crawl].filter(
    (x): x is string => typeof x === "string" && x.length > 0,
  );
  if (isos.length === 0) return null;
  let oldestMs = Number.POSITIVE_INFINITY;
  let oldestIso = "";
  for (const iso of isos) {
    const ms = new Date(iso).getTime();
    if (!Number.isFinite(ms)) continue;
    if (ms < oldestMs) {
      oldestMs = ms;
      oldestIso = iso;
    }
  }
  if (!oldestIso) return null;
  const ageDays = Math.floor((Date.now() - oldestMs) / (1000 * 60 * 60 * 24));
  return { iso: oldestIso, ageDays };
}

function formatDataAnchor(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("ru-RU", {
      day: "numeric",
      month: "short",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}

export function BrainPlanCard() {
  const { currentSite, loading: siteLoading } = useSite();
  const siteId = currentSite?.id || "";

  const { data, error, isLoading } = useSWR(
    siteId ? studioKey("brain_plan", siteId) : null,
    () => api.studioGetBrainPlan(siteId),
    {
      // Plan is cheap to compute (six SQL counts) — refresh on focus
      // is enough; no interval polling.
      refreshInterval: 0,
    },
  );

  if (siteLoading) return null;

  if (!currentSite) {
    return (
      <Card className="border-dashed">
        <CardContent className="pt-6 space-y-2">
          <div className="font-medium flex items-center gap-2">
            <Brain className="h-5 w-5 text-primary" />
            План на эту неделю
          </div>
          <p className="text-sm text-muted-foreground">
            Выбери сайт в свитчере слева — план собирается под
            конкретный сайт.
          </p>
        </CardContent>
      </Card>
    );
  }

  if (isLoading) {
    return (
      <Card>
        <CardContent className="pt-6 space-y-3">
          <Skeleton className="h-6 w-64" />
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (error || !data) {
    return (
      <Card className="border-red-300 bg-red-50/50">
        <CardContent className="pt-6 text-sm text-red-900">
          Не удалось собрать план: {error ? getErrorMessage(error) : "нет данных"}
        </CardContent>
      </Card>
    );
  }

  const noActions = data.actions.length === 0;

  return (
    <Card>
      <CardContent className="pt-6 space-y-4">
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <div>
            <h2 className="font-medium text-lg flex items-center gap-2">
              <Brain className="h-5 w-5 text-primary" />
              Что я бы сделал на твоём месте
            </h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              По одной задаче — что не так, почему это важно и куда
              нажать. Без воды.
            </p>
          </div>
          {(() => {
            // Data-freshness badge — anchored to the OLDEST of the
            // three source pulls. `computed_at` is when the SQL ran
            // (always "now"); it hides the fact that underlying data
            // can be 2 weeks stale. >7 days old → yellow warning.
            const anchor = oldestDataAnchor(
              data.last_webmaster_at ?? null,
              data.last_wordstat_at ?? null,
              data.last_crawl_at ?? null,
            );
            if (!anchor) {
              return (
                <span className="text-xs text-muted-foreground inline-flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  данные ещё не собирались
                </span>
              );
            }
            const stale = anchor.ageDays >= 7;
            return (
              <span
                className={cn(
                  "text-xs inline-flex items-center gap-1 rounded-md px-2 py-0.5",
                  stale
                    ? "border border-amber-300 bg-amber-50 text-amber-900"
                    : "text-muted-foreground",
                )}
                title={`webmaster: ${data.last_webmaster_at ?? "—"}\nwordstat: ${data.last_wordstat_at ?? "—"}\ncrawl: ${data.last_crawl_at ?? "—"}`}
              >
                <Clock className="h-3 w-3" />
                данные собраны: {formatDataAnchor(anchor.iso)}
                {stale && (
                  <span className="ml-1 font-medium">
                    · пора пересобрать данные
                  </span>
                )}
              </span>
            );
          })()}
        </div>

        {noActions ? (
          <div className="rounded-md border border-emerald-200 bg-emerald-50/50 px-3 py-2 text-sm text-emerald-900">
            Срочных действий не вижу. Если у тебя есть свежие правки —
            запусти ревью + классификацию запросов, я их подхвачу.
          </div>
        ) : (
          <div className="space-y-3">
            {data.actions.map((a) => (
              <ActionCard key={a.id} action={a} siteId={siteId} />
            ))}
          </div>
        )}

        {data.diagnostics.length > 0 && (
          <div className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground space-y-1">
            <div className="font-medium text-foreground/80 inline-flex items-center gap-1.5">
              <Info className="h-3.5 w-3.5" />
              Чего система пока не знает
            </div>
            <ul className="list-disc list-inside space-y-0.5">
              {data.diagnostics.map((d, i) => (
                <li key={i}>{d}</li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Action card (Phase A: conversational tone + drilldown) ──────────

type Action = Awaited<
  ReturnType<typeof api.studioGetBrainPlan>
>["actions"][number];

function ActionCard({
  action: a,
  siteId,
}: {
  action: Action;
  siteId: string;
}) {
  const [showReceipt, setShowReceipt] = useState(false);
  // Phase B: chat about this specific action. Independent state per
  // card — opening one doesn't close another. New conversation each
  // time it's opened (we deliberately don't persist).
  const [chatOpen, setChatOpen] = useState(false);
  const hasExamples = (a.examples?.length || 0) > 0;
  return (
    <div
      className={cn(
        "rounded-lg border p-4 space-y-3",
        SEV_STYLE[a.severity] || SEV_STYLE.medium,
      )}
    >
      <div className="flex items-start gap-3">
        <span
          className={cn(
            "h-2.5 w-2.5 rounded-full mt-1.5 flex-shrink-0",
            SEV_DOT[a.severity] || SEV_DOT.medium,
          )}
          aria-hidden="true"
        />
        <div className="flex-1 min-w-0 space-y-2">
          <div className="flex items-baseline gap-2 flex-wrap">
            <h3 className="font-medium text-base leading-snug">
              {a.title}
            </h3>
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground/80">
              {SEV_LABEL[a.severity] || a.severity}
            </span>
            {a.in_focus && (
              <span
                className="text-[10px] uppercase tracking-wide rounded-full border border-primary/40 bg-primary/10 text-primary px-2 py-0.5"
                title="В зоне твоего стратегического фокуса"
              >
                в фокусе
              </span>
            )}
          </div>
          <p className="text-sm leading-relaxed text-foreground/90 whitespace-pre-line">
            {a.body_ru}
          </p>

          {hasExamples && (
            <div className="rounded-md border border-foreground/10 bg-background/60 px-3 py-2 space-y-1.5">
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                Например:
              </div>
              <ul className="space-y-1">
                {a.examples.map((ex, i) => (
                  <ExampleRow key={i} example={ex} />
                ))}
              </ul>
            </div>
          )}

          <div className="rounded-md border border-primary/20 bg-primary/5 px-3 py-2 text-sm">
            <div className="text-[11px] uppercase tracking-wide text-primary/80 mb-0.5">
              Что делать
            </div>
            <p className="leading-snug">{a.what_to_do_ru}</p>
          </div>

          <div className="flex items-center gap-3 flex-wrap pt-1">
            <Link
              href={a.link_to}
              className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline cursor-pointer"
            >
              {a.link_label}
              <ChevronRight className="h-4 w-4" />
            </Link>
            <button
              type="button"
              onClick={() => setChatOpen((s) => !s)}
              className={cn(
                "inline-flex items-center gap-1 text-xs cursor-pointer",
                chatOpen
                  ? "text-primary"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <MessageCircle className="h-3.5 w-3.5" />
              {chatOpen ? "Закрыть чат" : "Спросить"}
            </button>
            <button
              type="button"
              onClick={() => setShowReceipt((s) => !s)}
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground cursor-pointer"
            >
              {showReceipt ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
              {showReceipt ? "Скрыть основание" : "Показать основание"}
            </button>
          </div>

          {showReceipt && <Receipt evidence={a.evidence} />}

          {chatOpen && siteId && (
            <BrainActionChat
              siteId={siteId}
              actionId={a.id}
              onClose={() => setChatOpen(false)}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function ExampleRow({
  example,
}: {
  example: Action["examples"][number];
}) {
  // For URL examples — show as a clickable link (opens in new tab so
  // owner doesn't lose the plan view).
  if (example.kind === "url") {
    return (
      <li className="flex items-start gap-2 text-sm">
        <Link2 className="h-3.5 w-3.5 mt-1 flex-shrink-0 text-muted-foreground" />
        <a
          href={example.label}
          target="_blank"
          rel="noreferrer"
          className="text-foreground hover:text-primary break-all cursor-pointer"
        >
          {example.label}
        </a>
      </li>
    );
  }
  // Spam / disputed query — show with reason if any.
  if (example.kind === "spam" || example.kind === "disputed") {
    const tagText =
      example.kind === "spam" ? "не моя тема" : "сомнительно";
    const tagStyle =
      example.kind === "spam"
        ? "border-rose-300 bg-rose-50 text-rose-800"
        : "border-amber-300 bg-amber-50 text-amber-800";
    return (
      <li className="flex items-start gap-2 text-sm">
        <AlertCircle className="h-3.5 w-3.5 mt-1 flex-shrink-0 text-muted-foreground" />
        <div className="flex-1 min-w-0">
          <span className="font-medium">«{example.label}»</span>
          <span
            className={cn(
              "ml-2 text-[10px] uppercase tracking-wide rounded-full border px-1.5 py-0.5",
              tagStyle,
            )}
          >
            {tagText}
          </span>
          {example.hint ? (
            <div className="text-xs text-muted-foreground mt-0.5">
              {example.hint}
            </div>
          ) : null}
        </div>
      </li>
    );
  }
  // Missing-landings priority badge: high|medium|low — service name + quote
  return (
    <li className="flex items-start gap-2 text-sm">
      <Quote className="h-3.5 w-3.5 mt-1 flex-shrink-0 text-muted-foreground" />
      <div className="flex-1 min-w-0">
        <span className="font-medium">{example.label}</span>
        {example.kind === "high" && (
          <span className="ml-2 text-[10px] uppercase tracking-wide rounded-full border border-red-300 bg-red-50 text-red-800 px-1.5 py-0.5">
            важно
          </span>
        )}
        {example.hint ? (
          <div className="text-xs text-muted-foreground mt-0.5 italic">
            «{example.hint}»
          </div>
        ) : null}
      </div>
    </li>
  );
}

function Receipt({ evidence }: { evidence: Record<string, unknown> }) {
  const entries = Object.entries(evidence).filter(
    ([, v]) =>
      v !== null
      && v !== undefined
      && (typeof v === "string"
        || typeof v === "number"
        || typeof v === "boolean"),
  );
  if (entries.length === 0) return null;
  return (
    <div className="flex items-center gap-x-3 gap-y-1 flex-wrap text-[11px] text-muted-foreground border-t pt-2">
      <span className="font-medium uppercase tracking-wide">
        основание:
      </span>
      {entries.map(([k, v]) => (
        <span key={k} className="tabular-nums">
          <span className="text-muted-foreground/70">{k}</span>={" "}
          <span className="text-foreground/80">{String(v)}</span>
        </span>
      ))}
    </div>
  );
}
