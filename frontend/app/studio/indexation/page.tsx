"use client";

/**
 * Studio /indexation — module page (PR-S3).
 *
 * Owner question this module answers: "сколько моих страниц в индексе
 * Яндекса, и если мало — почему?"
 *
 * Backend contract: backend/app/api/v1/studio.py
 *   GET  /admin/studio/sites/{id}/indexation        → IndexationState
 *   POST /admin/studio/sites/{id}/indexation/check  → trigger
 *
 * Trigger task does both the SERP probe AND the diagnostic in one go,
 * so the result either shows healthy "N pages indexed" + the page list,
 * or a single-card root-cause verdict ("robots.txt блокирует весь сайт")
 * with a concrete action.
 */

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";

import { api } from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Telescope,
  RefreshCw,
  ArrowLeft,
  CheckCircle2,
  AlertTriangle,
  AlertOctagon,
  Info,
  ExternalLink,
} from "lucide-react";
import { cn } from "@/lib/utils";

function fmtAge(iso: string | null): string {
  if (!iso) return "никогда";
  const d = new Date(iso);
  const ms = Date.now() - d.getTime();
  const min = Math.floor(ms / 60000);
  if (min < 1) return "только что";
  if (min < 60) return `${min} мин назад`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} ч назад`;
  const day = Math.floor(hr / 24);
  return `${day} дн назад`;
}

function getErrorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

const SEVERITY_STYLE: Record<
  "critical" | "high" | "medium" | "low",
  { wrap: string; icon: typeof AlertOctagon; label: string }
> = {
  critical: {
    wrap: "border-red-300 bg-red-50 text-red-900",
    icon: AlertOctagon,
    label: "критично",
  },
  high: {
    wrap: "border-amber-300 bg-amber-50 text-amber-900",
    icon: AlertTriangle,
    label: "важно",
  },
  medium: {
    wrap: "border-amber-200 bg-amber-50 text-amber-900",
    icon: AlertTriangle,
    label: "средне",
  },
  low: {
    wrap: "border-emerald-300 bg-emerald-50 text-emerald-900",
    icon: Info,
    label: "несрочно",
  },
};

export default function StudioIndexationPage() {
  const { currentSite, loading: siteLoading } = useSite();
  const siteId = currentSite?.id || "";

  const [pending, setPending] = useState(false);
  const [banner, setBanner] = useState<{
    kind: "ok" | "deduped" | "err";
    text: string;
  } | null>(null);

  const { data, isLoading, mutate } = useSWR(
    siteId ? studioKey("indexation", siteId) : null,
    () => api.studioGetIndexation(siteId),
    {
      // While a check is running we want the page to update without
      // the user having to refresh manually.
      refreshInterval: (latest) =>
        latest && (latest as { is_running: boolean }).is_running ? 4000 : 0,
    },
  );

  async function onCheck() {
    if (!siteId || pending) return;
    setPending(true);
    setBanner(null);
    try {
      const res = await api.studioTriggerIndexationCheck(siteId);
      if (res.deduped) {
        setBanner({
          kind: "deduped",
          text: `Проверка уже идёт (run_id ${res.run_id.slice(0, 8)}…). Подожди, она закончится — таблица обновится сама.`,
        });
      } else {
        setBanner({
          kind: "ok",
          text: `Проверка запущена · run_id ${res.run_id.slice(0, 8)}…. Это займёт 10–30 секунд (probe + диагностика, если страниц мало).`,
        });
        // Trigger an immediate refetch so the running state shows up.
        await mutate();
      }
    } catch (e: unknown) {
      setBanner({ kind: "err", text: getErrorMessage(e) });
    } finally {
      setTimeout(() => setPending(false), 3000);
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
              Выбери сайт в свитчере слева — модуль «Индексация» работает в
              контексте конкретного сайта.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  // ── Main render ─────────────────────────────────────────────────

  const isNeverChecked = data?.status === "never_checked";
  const isRunning = data?.is_running === true;
  const pages = data?.pages || [];
  const diag = data?.diagnosis;

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
            <Telescope className="h-6 w-6 text-primary" /> Индексация
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Сколько страниц {currentSite.domain || "сайта"} в индексе Яндекса.
            Если мало — что чинить.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={onCheck} disabled={pending || isRunning} size="sm">
            <RefreshCw
              className={cn(
                "h-4 w-4 mr-2",
                (pending || isRunning) && "animate-spin",
              )}
            />
            {isRunning
              ? "Идёт проверка…"
              : pending
                ? "Запускаю…"
                : "Перепроверить"}
          </Button>
        </div>
      </div>

      {/* Trigger feedback banner */}
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

      {/* Loading skeleton on first load */}
      {isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      )}

      {/* Empty state — never checked (CONCEPT §5: explain WHY) */}
      {!isLoading && isNeverChecked && (
        <Card className="border-dashed">
          <CardContent className="pt-6 space-y-2">
            <div className="font-medium">Проверка ещё не запускалась</div>
            <p className="text-sm text-muted-foreground">
              Жми «Перепроверить» — модуль отправит запрос{" "}
              <code className="text-xs">site:{currentSite.domain}</code> в
              Яндекс Search API, посчитает страницы в индексе и (если мало)
              автоматически разберёт причину: проверит robots.txt, sitemap,
              ответ главной страницы под YandexBot.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Currently running */}
      {!isLoading && isRunning && (
        <Card className="border-blue-200 bg-blue-50">
          <CardContent className="pt-6 flex items-start gap-3">
            <RefreshCw className="h-5 w-5 mt-0.5 text-blue-700 animate-spin" />
            <div className="space-y-1">
              <div className="font-medium text-blue-900">
                Идёт проверка индексации…
              </div>
              <p className="text-sm text-blue-900/80">
                Запрашиваю site:{currentSite.domain} в Яндекс Search API.
                Если страниц мало — следом запущу диагностику robots.txt,
                sitemap и рендеринга главной. Страница обновится сама через
                несколько секунд.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Failed */}
      {!isLoading && data?.status === "failed" && (
        <Card className="border-red-300 bg-red-50">
          <CardContent className="pt-6 flex items-start gap-3">
            <AlertOctagon className="h-5 w-5 mt-0.5 text-red-700" />
            <div className="space-y-1">
              <div className="font-medium text-red-900">
                Search API вернул ошибку
              </div>
              <p className="text-sm text-red-900/80">
                {data.error || "Неизвестная ошибка."}
                {" — "}
                Это инфраструктурная проблема (квота / сеть / ключ). Проверь
                статус подключений на{" "}
                <Link
                  href="/studio/connections"
                  className="underline hover:text-red-950"
                >
                  /studio/connections
                </Link>{" "}
                и попробуй ещё раз.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Successful state — summary header + diagnosis (if any) + pages list */}
      {!isLoading &&
        !isNeverChecked &&
        !isRunning &&
        data?.status !== "failed" && (
          <>
            {/* Big number summary */}
            <Card>
              <CardContent className="pt-6 flex items-baseline gap-4 flex-wrap">
                <div>
                  <div className="text-4xl font-semibold">
                    {data?.pages_found ?? 0}
                  </div>
                  <div className="text-xs text-muted-foreground mt-1">
                    страниц в индексе Яндекса
                  </div>
                </div>
                <div className="ml-auto text-right">
                  <div className="text-xs text-muted-foreground">
                    Последняя проверка
                  </div>
                  <div className="text-sm">
                    {fmtAge(data?.last_check_at ?? null)}
                  </div>
                  {data?.status === "stale_7d+" && (
                    <div className="text-xs text-amber-700 mt-0.5">
                      устарело &gt;7 дней — стоит перепроверить
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            {/* Diagnosis card — only present when pages_found < 3 */}
            {diag && (
              <DiagnosisCard diag={diag} />
            )}

            {/* Pages list */}
            {pages.length > 0 ? (
              <Card>
                <CardContent className="pt-6 space-y-3">
                  <div className="text-sm font-medium">
                    Что Яндекс показывает первым
                    <span className="text-muted-foreground font-normal ml-2">
                      (топ-{pages.length} из {data?.pages_found ?? "?"})
                    </span>
                  </div>
                  <ol className="space-y-2 text-sm">
                    {pages.map((p) => (
                      <li
                        key={p.url}
                        className="flex items-start gap-2 leading-snug"
                      >
                        <span className="text-xs text-muted-foreground tabular-nums w-6 pt-0.5">
                          {p.position}.
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="font-medium truncate">
                            {p.title || p.url}
                          </div>
                          <a
                            href={p.url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1 truncate"
                          >
                            {p.url}
                            <ExternalLink className="h-3 w-3 flex-shrink-0" />
                          </a>
                        </div>
                      </li>
                    ))}
                  </ol>
                </CardContent>
              </Card>
            ) : (
              data?.pages_found === 0 && (
                <Card className="border-dashed">
                  <CardContent className="pt-6">
                    <p className="text-sm text-muted-foreground">
                      Список страниц пуст потому что Яндекс не нашёл ни
                      одной — даже по запросу{" "}
                      <code className="text-xs">
                        site:{currentSite.domain}
                      </code>
                      . Это не баг отображения; это реальный статус сайта в
                      индексе.{" "}
                      {diag
                        ? "Корневая причина — в карточке выше."
                        : "Запусти проверку повторно через несколько дней или жди диагностики."}
                    </p>
                  </CardContent>
                </Card>
              )
            )}
          </>
        )}
    </div>
  );
}

// ── Diagnosis card — single root-cause verdict + action ───────────

function DiagnosisCard({
  diag,
}: {
  diag: {
    verdict: string;
    cause_ru: string;
    action_ru: string;
    severity: "critical" | "high" | "medium" | "low";
  };
}) {
  const style = SEVERITY_STYLE[diag.severity] || SEVERITY_STYLE.medium;
  const Icon = style.icon;
  return (
    <div className={cn("rounded-md border p-4 flex items-start gap-3", style.wrap)}>
      <Icon className="h-5 w-5 mt-0.5 flex-shrink-0" />
      <div className="space-y-2 min-w-0">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="font-medium">Причина: {diag.verdict}</span>
          <span className="text-[10px] uppercase tracking-wide opacity-70">
            {style.label}
          </span>
        </div>
        <p className="text-sm leading-snug">{diag.cause_ru}</p>
        <div className="text-sm pt-1 border-t border-current/20">
          <span className="font-medium">Что делать: </span>
          {diag.action_ru}
        </div>
      </div>
    </div>
  );
}
