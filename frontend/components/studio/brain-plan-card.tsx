"use client";

/**
 * Studio v2 etap 7 — brain plan card on /studio.
 *
 * Renders the synthesised «do this first» plan from the backend. The
 * plan is built from pure SQL counts + Russian rule templates — no
 * LLM in the pipeline, every line of body text is a static template
 * with real numbers substituted in. Show evidence dict in plain
 * Russian as a receipt so the owner sees on what basis the system
 * pushed each item.
 *
 * Site context comes from `useSite()` like every other Studio page.
 */

import Link from "next/link";
import useSWR from "swr";
import {
  AlertTriangle,
  Brain,
  ChevronRight,
  Clock,
  Info,
} from "lucide-react";

import { api } from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

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

function formatAge(iso: string): string {
  try {
    const diff = Date.now() - new Date(iso).getTime();
    const sec = Math.round(diff / 1000);
    if (sec < 60) return "только что";
    const min = Math.round(sec / 60);
    if (min < 60) return `${min} мин назад`;
    const hr = Math.round(min / 60);
    if (hr < 24) return `${hr} ч назад`;
    const days = Math.round(hr / 24);
    return `${days} дн назад`;
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
          Не удалось собрать план: {String(error)}
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
              План на эту неделю
            </h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Без LLM. Каждая строка — счёт из БД, шаблон, ссылка на
              модуль.
            </p>
          </div>
          <span className="text-xs text-muted-foreground inline-flex items-center gap-1">
            <Clock className="h-3 w-3" />
            обновлено {formatAge(data.computed_at)}
          </span>
        </div>

        {noActions ? (
          <div className="rounded-md border border-emerald-200 bg-emerald-50/50 px-3 py-2 text-sm text-emerald-900">
            Срочных действий не вижу. Если у тебя есть свежие правки —
            запусти ревью + классификацию запросов, мозг их подхватит.
          </div>
        ) : (
          <ul className="space-y-2">
            {data.actions.map((a) => (
              <li key={a.id}>
                <Link
                  href={a.link_to}
                  className={cn(
                    "block rounded-lg border p-3 transition-colors",
                    SEV_STYLE[a.severity] || SEV_STYLE.medium,
                    "hover:border-primary/60",
                  )}
                >
                  <div className="flex items-start gap-3">
                    <span
                      className={cn(
                        "h-2.5 w-2.5 rounded-full mt-1.5 flex-shrink-0",
                        SEV_DOT[a.severity] || SEV_DOT.medium,
                      )}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-baseline gap-2 flex-wrap">
                        <span className="font-medium">{a.title}</span>
                        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                          {SEV_LABEL[a.severity] || a.severity}
                        </span>
                      </div>
                      <p className="text-sm leading-snug mt-1">
                        {a.body_ru}
                      </p>
                      <Receipt evidence={a.evidence} />
                    </div>
                    <ChevronRight className="h-4 w-4 text-muted-foreground flex-shrink-0 mt-1" />
                  </div>
                </Link>
              </li>
            ))}
          </ul>
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

function Receipt({ evidence }: { evidence: Record<string, unknown> }) {
  const entries = Object.entries(evidence).filter(
    ([, v]) => v !== null && v !== undefined,
  );
  if (entries.length === 0) return null;
  return (
    <div className="flex items-center gap-3 flex-wrap mt-2 text-[11px] text-muted-foreground">
      <AlertTriangle className="h-3 w-3 flex-shrink-0" />
      <span className="font-medium">основание:</span>
      {entries.map(([k, v]) => (
        <span key={k} className="tabular-nums">
          <span className="text-muted-foreground/70">{k}</span>={" "}
          <span className="text-foreground/80">{String(v)}</span>
        </span>
      ))}
    </div>
  );
}
