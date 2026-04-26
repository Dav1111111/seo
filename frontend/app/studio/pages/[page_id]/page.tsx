"use client";

/**
 * Studio /pages/[page_id] — page workspace (PR-S4).
 *
 * Owner-facing question: «вот моя страница X — что про неё думает
 * система, что с ней делать, что я уже сделал и что это дало».
 *
 * Composition (top-down):
 *   - Page header: title, URL (clickable), index/sitemap/HTTP badges
 *   - Cross-links row: Queries (active) · Indexation (active) ·
 *                      Competitors / Outcomes (DisabledLink) per
 *                      IMPLEMENTATION.md §2.1
 *   - Page metadata card: h1, meta description, word_count, schema
 *   - Latest review card: status, model, cost, page-level summary
 *   - Recommendations list: priority sorted, with three action buttons
 *     (Применил & замерить, Отложить, Отклонить). Optimistic UI on
 *     PATCH. "Применил" also writes an outcome_snapshot via
 *     markApplied — that's the trigger PR-S8 reads from.
 *   - Outcomes timeline: list of OutcomeSnapshots filtered by page_url.
 *     If a delta is computed (snapshot.followup_at NOT NULL) we render
 *     impressions/clicks/position deltas with up/down badges.
 *
 * What's intentionally NOT in v1 (CONCEPT.md §5: explain absences):
 *   - Position graph per query for this page — there is no
 *     page↔query link table, so the "trustworthy" version of this
 *     belongs in PR-S5 / S6.
 *   - True before/after content diff — we only persist the latest
 *     crawl, not historical versions.
 *   - Per-page outcome baseline — current OutcomeSnapshot._baseline_
 *     metrics is site-wide; converting it is its own scope.
 */

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { useParams } from "next/navigation";

import { api } from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";
import { fmtAge, pluralRu } from "@/lib/format";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { DisabledLink } from "@/components/studio/disabled-link";
import {
  ArrowLeft,
  ExternalLink,
  CheckCircle2,
  Clock,
  X,
  TrendingUp,
  TrendingDown,
  Minus,
  Search as SearchIcon,
  Telescope,
  Swords,
  History as HistoryIcon,
  Info,
} from "lucide-react";
import { cn } from "@/lib/utils";

// ── Helpers ──────────────────────────────────────────────────────────

function getErrorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

const PRIORITY_STYLE: Record<string, string> = {
  critical: "border-red-300 bg-red-50 text-red-900",
  high: "border-amber-300 bg-amber-50 text-amber-900",
  medium: "border-amber-200 bg-amber-50 text-amber-800",
  low: "border-emerald-300 bg-emerald-50 text-emerald-900",
};

const PRIORITY_LABEL: Record<string, string> = {
  critical: "критично",
  high: "важно",
  medium: "средне",
  low: "несрочно",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "ждёт действия",
  applied: "применено",
  deferred: "отложено",
  dismissed: "отклонено",
};

const CATEGORY_LABEL: Record<string, string> = {
  title: "title",
  meta_description: "meta description",
  h1_structure: "H1",
  schema: "Schema.org",
  eeat: "E-E-A-T",
  commercial: "коммерческие сигналы",
  over_optimization: "переоптимизация",
  internal_linking: "перелинковка",
};

// ── Page component ───────────────────────────────────────────────────

export default function StudioPageWorkspace() {
  const params = useParams<{ page_id: string }>();
  const pageId = params?.page_id || "";
  const { currentSite, loading: siteLoading } = useSite();
  const siteId = currentSite?.id || "";

  const { data, error, isLoading, mutate } = useSWR(
    siteId && pageId ? studioKey("page_detail", siteId, pageId) : null,
    () => api.studioGetPage(siteId, pageId),
  );

  // Local optimistic UI state for in-flight rec actions.
  // key = rec_id, value = label of the action we're firing
  const [busy, setBusy] = useState<Record<string, string>>({});
  const [errMsg, setErrMsg] = useState<string | null>(null);

  async function changeRecStatus(
    recId: string,
    nextStatus: "applied" | "deferred" | "dismissed",
    pageUrl: string,
  ) {
    if (busy[recId]) return;
    setBusy((b) => ({ ...b, [recId]: nextStatus }));
    setErrMsg(null);
    try {
      await api.patchRecommendation(recId, { user_status: nextStatus });
      // "Применил" also creates an outcome_snapshot — single source of
      // truth for PR-S8. Idempotent server-side on (site_id, rec_id).
      if (nextStatus === "applied") {
        try {
          await api.markApplied(siteId, recId, "priority", pageUrl);
        } catch (e) {
          // Don't unwind the PATCH — the outcome record being missing
          // is recoverable later, but flipping back the user_status is
          // confusing. Log + tell the user, leave PATCH applied.
          setErrMsg(
            `Статус сохранён, но замер до/после не запустился: ${getErrorMessage(e)}. Можно позже повторить через /studio/outcomes.`,
          );
        }
      }
      await mutate();
    } catch (e: unknown) {
      setErrMsg(getErrorMessage(e));
    } finally {
      setBusy((b) => {
        const next = { ...b };
        delete next[recId];
        return next;
      });
    }
  }

  // ── Render guards ──────────────────────────────────────────────

  if (siteLoading) {
    return (
      <div className="p-6 space-y-3">
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
              Выбери сайт в свитчере слева — workspace страницы работает в
              контексте конкретного сайта.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="p-6 space-y-3 max-w-6xl">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-6 max-w-6xl">
        <Card className="border-red-300 bg-red-50">
          <CardContent className="pt-6 text-sm text-red-900">
            Не удалось загрузить страницу:{" "}
            {error ? getErrorMessage(error) : "нет данных"}
          </CardContent>
        </Card>
      </div>
    );
  }

  const review = data.review;
  const recs = review?.recommendations ?? [];
  const xLinks = data.cross_links ?? {};

  return (
    <div className="p-6 space-y-5 max-w-6xl">
      {/* Header */}
      <div>
        <Link
          href="/studio/pages"
          className="inline-flex items-center text-xs text-muted-foreground hover:text-foreground mb-1"
        >
          <ArrowLeft className="h-3 w-3 mr-1" /> К списку страниц
        </Link>
        <h1 className="text-2xl font-bold leading-tight">
          {data.title || data.path}
        </h1>
        <a
          href={data.url}
          target="_blank"
          rel="noreferrer"
          className="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1 mt-1 break-all"
        >
          {data.url}
          <ExternalLink className="h-3 w-3 flex-shrink-0" />
        </a>
        <div className="flex items-center gap-2 flex-wrap mt-2">
          {data.in_index ? (
            <Badge
              variant="outline"
              className="border-emerald-300 bg-emerald-50 text-emerald-800"
            >
              в индексе Яндекса
            </Badge>
          ) : (
            <Badge
              variant="outline"
              className="border-amber-300 bg-amber-50 text-amber-800"
            >
              не в индексе
            </Badge>
          )}
          {data.in_sitemap ? (
            <Badge variant="outline">в sitemap.xml</Badge>
          ) : (
            <Badge
              variant="outline"
              className="border-amber-200 bg-amber-50 text-amber-700"
            >
              нет в sitemap
            </Badge>
          )}
          {data.http_status && data.http_status >= 400 && (
            <Badge
              variant="outline"
              className="border-red-300 bg-red-50 text-red-800"
            >
              HTTP {data.http_status}
            </Badge>
          )}
          <span className="text-xs text-muted-foreground ml-auto">
            crawl {fmtAge(data.last_crawled_at)}
          </span>
        </div>
      </div>

      {/* Cross-links row */}
      <div className="flex items-center gap-3 flex-wrap text-sm border-y py-2">
        <span className="text-xs text-muted-foreground">Связанные модули:</span>
        {xLinks.queries ? (
          <Link
            href="/studio/queries"
            className="inline-flex items-center gap-1 text-foreground hover:text-primary"
          >
            <SearchIcon className="h-3.5 w-3.5" /> Запросы
          </Link>
        ) : (
          <DisabledLink reason="Модуль «Запросы» ещё не выпущен">
            Запросы
          </DisabledLink>
        )}
        {xLinks.indexation ? (
          <Link
            href="/studio/indexation"
            className="inline-flex items-center gap-1 text-foreground hover:text-primary"
          >
            <Telescope className="h-3.5 w-3.5" /> Индексация
          </Link>
        ) : (
          <DisabledLink reason="Модуль «Индексация» ещё не выпущен">
            Индексация
          </DisabledLink>
        )}
        {xLinks.competitors ? (
          <Link
            href="/studio/competitors"
            className="inline-flex items-center gap-1 text-foreground hover:text-primary"
          >
            <Swords className="h-3.5 w-3.5" /> Конкуренты
          </Link>
        ) : (
          <DisabledLink reason="Модуль «Конкуренты» в очереди — PR-S5">
            Конкуренты
          </DisabledLink>
        )}
        {xLinks.outcomes ? (
          <Link
            href="/studio/outcomes"
            className="inline-flex items-center gap-1 text-foreground hover:text-primary"
          >
            <HistoryIcon className="h-3.5 w-3.5" /> До / После
          </Link>
        ) : (
          <DisabledLink reason="Модуль «До / После» в очереди — PR-S8 (после 14 дней работы PR-S4)">
            До / После
          </DisabledLink>
        )}
      </div>

      {/* Inline error banner from rec actions */}
      {errMsg && (
        <div className="rounded-md border border-red-300 bg-red-50 text-red-900 px-3 py-2 text-sm flex items-start gap-2">
          <Info className="h-4 w-4 mt-0.5 flex-shrink-0" />
          <span>{errMsg}</span>
        </div>
      )}

      {/* Page metadata */}
      <Card>
        <CardContent className="pt-6 space-y-3 text-sm">
          <div className="font-medium">Содержимое страницы</div>
          <DataRow label="H1" value={data.h1} />
          <DataRow label="Meta description" value={data.meta_description} />
          <DataRow
            label="Слов в тексте"
            value={
              data.word_count != null ? data.word_count.toLocaleString("ru-RU") : null
            }
          />
          <DataRow
            label="Schema.org микроразметка"
            value={data.has_schema ? "есть" : "нет"}
            valueClassName={
              data.has_schema
                ? "text-emerald-700"
                : "text-amber-700"
            }
          />
        </CardContent>
      </Card>

      {/* Review block */}
      {review ? (
        <Card>
          <CardContent className="pt-6 space-y-3">
            <div className="flex items-baseline justify-between gap-3 flex-wrap">
              <div className="font-medium">Ревью</div>
              <div className="text-xs text-muted-foreground">
                {review.reviewer_model} · {fmtAge(review.reviewed_at)} ·{" "}
                стоимость ${review.cost_usd.toFixed(4)}
              </div>
            </div>

            {review.skip_reason && (
              <div className="text-sm rounded-md border border-amber-300 bg-amber-50 text-amber-900 px-3 py-2">
                Ревью пропущено: <code>{review.skip_reason}</code>
                {review.skip_reason === "content_unchanged"
                  ? " — содержимое страницы не менялось с прошлого ревью, мы не платим за повторный анализ."
                  : ""}
              </div>
            )}

            {review.page_level_summary && (
              <PageSummary summary={review.page_level_summary} />
            )}
          </CardContent>
        </Card>
      ) : (
        <Card className="border-dashed">
          <CardContent className="pt-6 space-y-2">
            <div className="font-medium">Ревью ещё не запускалось</div>
            <p className="text-sm text-muted-foreground">
              Эта страница в БД есть (crawler её нашёл), но LLM-ревью
              для неё ещё не было. Запустить можно из общего конвейера
              на дашборде Студии — он прогонит ревью по всем страницам,
              у которых либо нет ревью, либо контент изменился с
              прошлого раза.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Recommendations */}
      {review && (
        <div className="space-y-3">
          <div className="flex items-baseline justify-between">
            <h2 className="font-medium">
              Рекомендации
              <span className="text-muted-foreground font-normal ml-2">
                ({recs.length})
              </span>
            </h2>
            {recs.length > 0 && (
              <span className="text-xs text-muted-foreground">
                сначала «ждёт действия», далее по убыванию приоритета
              </span>
            )}
          </div>

          {recs.length === 0 && (
            <Card className="border-dashed">
              <CardContent className="pt-6 text-sm text-muted-foreground">
                Ревью не нашло, что улучшить — страница в порядке. Если
                это удивляет, проверь дату ревью выше: возможно, оно
                старое и пора перезапускать.
              </CardContent>
            </Card>
          )}

          {recs.map((r) => (
            <RecCard
              key={r.rec_id}
              rec={r}
              busy={busy[r.rec_id] || null}
              onAction={(status) =>
                changeRecStatus(r.rec_id, status, data.url)
              }
            />
          ))}
        </div>
      )}

      {/* Outcomes timeline */}
      <div className="space-y-3">
        <div className="flex items-baseline justify-between">
          <h2 className="font-medium">
            Что я уже применил для этой страницы
            <span className="text-muted-foreground font-normal ml-2">
              ({data.outcomes.length})
            </span>
          </h2>
        </div>
        {data.outcomes.length === 0 ? (
          <Card className="border-dashed">
            <CardContent className="pt-6 text-sm text-muted-foreground">
              Пока ни одной применённой правки. Когда нажмёшь «Применил
              & замерить эффект» на рекомендации выше, мы зафиксируем
              базовые метрики и через 14 дней посчитаем дельту — это и
              есть будущий модуль «До / После».
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-2">
            {data.outcomes.map((o) => (
              <OutcomeRow key={o.snapshot_id} outcome={o} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────────────

function DataRow({
  label,
  value,
  valueClassName,
}: {
  label: string;
  value: string | number | null | undefined;
  valueClassName?: string;
}) {
  return (
    <div className="flex items-baseline gap-3">
      <span className="text-xs text-muted-foreground w-44 flex-shrink-0">
        {label}
      </span>
      <span
        className={cn(
          "text-sm break-words",
          (value === null || value === undefined || value === "") &&
            "text-muted-foreground italic",
          valueClassName,
        )}
      >
        {value === null || value === undefined || value === ""
          ? "не заполнено"
          : value}
      </span>
    </div>
  );
}

function PageSummary({ summary }: { summary: Record<string, unknown> }) {
  const intent = summary.intent_match as string | undefined;
  const headline = summary.headline as string | undefined;
  const verdict = summary.verdict as string | undefined;
  const issues = summary.top_issues as string[] | undefined;

  if (!intent && !headline && !verdict && !issues) return null;

  return (
    <div className="text-sm space-y-2 leading-snug">
      {headline && <p className="font-medium">{headline}</p>}
      {verdict && <p>{verdict}</p>}
      {intent && (
        <p className="text-muted-foreground">
          Соответствие интенту: {intent}
        </p>
      )}
      {issues && issues.length > 0 && (
        <ul className="list-disc list-inside text-muted-foreground space-y-0.5">
          {issues.slice(0, 5).map((i, idx) => (
            <li key={idx}>{i}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RecCard({
  rec,
  busy,
  onAction,
}: {
  rec: {
    rec_id: string;
    category: string;
    priority: string;
    user_status: string;
    before_text: string | null;
    after_text: string | null;
    reasoning_ru: string;
    priority_score: number | null;
  };
  busy: string | null;
  onAction: (s: "applied" | "deferred" | "dismissed") => void;
}) {
  const ps = PRIORITY_STYLE[rec.priority] || PRIORITY_STYLE.medium;
  return (
    <Card
      className={cn(
        rec.user_status !== "pending" && "opacity-70",
      )}
    >
      <CardContent className="pt-5 space-y-3">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span
            className={cn(
              "text-[10px] uppercase tracking-wide rounded-full border px-2 py-0.5",
              ps,
            )}
          >
            {PRIORITY_LABEL[rec.priority] || rec.priority}
          </span>
          <span className="text-xs text-muted-foreground">
            {CATEGORY_LABEL[rec.category] || rec.category}
          </span>
          {rec.priority_score != null && (
            <span className="text-xs text-muted-foreground tabular-nums">
              · score {rec.priority_score.toFixed(2)}
            </span>
          )}
          <span className="ml-auto text-xs">
            <Badge
              variant="outline"
              className={cn(
                rec.user_status === "applied" &&
                  "border-emerald-300 bg-emerald-50 text-emerald-800",
                rec.user_status === "deferred" &&
                  "border-amber-300 bg-amber-50 text-amber-800",
                rec.user_status === "dismissed" &&
                  "border-muted bg-muted/30 text-muted-foreground",
              )}
            >
              {STATUS_LABEL[rec.user_status] || rec.user_status}
            </Badge>
          </span>
        </div>

        <p className="text-sm leading-snug">{rec.reasoning_ru}</p>

        {(rec.before_text || rec.after_text) && (
          <div className="grid sm:grid-cols-2 gap-2 text-xs">
            {rec.before_text && (
              <div className="rounded-md border bg-red-50/40 border-red-200 p-2">
                <div className="text-[10px] uppercase tracking-wide text-red-800/80 mb-1">
                  Было
                </div>
                <div className="whitespace-pre-wrap break-words">
                  {rec.before_text}
                </div>
              </div>
            )}
            {rec.after_text && (
              <div className="rounded-md border bg-emerald-50/40 border-emerald-200 p-2">
                <div className="text-[10px] uppercase tracking-wide text-emerald-800/80 mb-1">
                  Предлагается
                </div>
                <div className="whitespace-pre-wrap break-words">
                  {rec.after_text}
                </div>
              </div>
            )}
          </div>
        )}

        {rec.user_status === "pending" && (
          <div className="flex items-center gap-2 flex-wrap pt-2 border-t">
            <Button
              size="sm"
              onClick={() => onAction("applied")}
              disabled={!!busy}
              title="Сохранит статус и зафиксирует метрики «до» — через 14 дней автоматом посчитаем дельту"
            >
              <CheckCircle2 className="h-4 w-4 mr-1.5" />
              {busy === "applied" ? "Сохраняю…" : "Применил & замерить эффект"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => onAction("deferred")}
              disabled={!!busy}
            >
              <Clock className="h-4 w-4 mr-1.5" />
              {busy === "deferred" ? "…" : "Отложить"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => onAction("dismissed")}
              disabled={!!busy}
            >
              <X className="h-4 w-4 mr-1.5" />
              {busy === "dismissed" ? "…" : "Не подходит"}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function OutcomeRow({
  outcome,
}: {
  outcome: {
    snapshot_id: string;
    recommendation_id: string;
    applied_at: string;
    followup_at: string | null;
    delta: Record<string, unknown> | null;
    note_ru: string | null;
  };
}) {
  const delta = outcome.delta || {};
  const impressions = delta.impressions_pct as number | undefined;
  const clicks = delta.clicks_pct as number | undefined;
  const position = delta.position_delta as number | undefined;

  return (
    <Card>
      <CardContent className="py-3 flex items-baseline gap-3 flex-wrap text-sm">
        <span className="text-xs text-muted-foreground tabular-nums w-32 flex-shrink-0">
          {fmtAge(outcome.applied_at)}
        </span>
        <span className="text-xs text-muted-foreground font-mono truncate flex-1 min-w-0">
          rec {outcome.recommendation_id.slice(0, 8)}…
        </span>
        {outcome.followup_at ? (
          <div className="flex items-center gap-3 ml-auto">
            <DeltaBadge label="показы" pct={impressions} />
            <DeltaBadge label="клики" pct={clicks} />
            <DeltaBadge label="позиция" delta={position} reverse />
          </div>
        ) : (
          <span className="text-xs text-amber-700 ml-auto">
            замер через {daysUntilFollowup(outcome.applied_at)}
          </span>
        )}
      </CardContent>
    </Card>
  );
}

function daysUntilFollowup(appliedAt: string): string {
  const target = new Date(appliedAt).getTime() + 14 * 24 * 60 * 60 * 1000;
  const days = Math.ceil((target - Date.now()) / (24 * 60 * 60 * 1000));
  if (days <= 0) return "скоро";
  return `${days} ${pluralRu(days, ["день", "дня", "дней"])}`;
}

function DeltaBadge({
  label,
  pct,
  delta,
  reverse,
}: {
  label: string;
  pct?: number;
  delta?: number;
  reverse?: boolean;
}) {
  const v = pct ?? delta;
  if (v == null) return null;
  // For position: lower is better, so reverse the colour direction.
  const better = reverse ? v < 0 : v > 0;
  const worse = reverse ? v > 0 : v < 0;
  const Icon = v === 0 ? Minus : better ? TrendingUp : TrendingDown;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-xs",
        better && "text-emerald-700",
        worse && "text-red-700",
        v === 0 && "text-muted-foreground",
      )}
    >
      <Icon className="h-3 w-3" />
      <span>{label}</span>
      <span className="tabular-nums">
        {pct != null
          ? (v >= 0 ? "+" : "") + v.toFixed(1) + "%"
          : (v >= 0 ? "+" : "") + v.toFixed(1)}
      </span>
    </span>
  );
}
