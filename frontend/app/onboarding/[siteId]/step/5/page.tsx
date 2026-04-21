"use client";

import { useParams } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StepNav } from "@/components/onboarding/step-nav";
import { cn } from "@/lib/utils";

// Step 5 — a read-only "where are we now" snapshot for the confirmed
// clusters. Full page-intent-fit (green/yellow/red) will be wired by the
// PageReview pipeline after onboarding completes; for now we surface what
// we already know: cluster, relevance, queries_count, and a simple fit
// heuristic based on queries_count > 0 (observed vs. only a seed).

export default function Step5Positions() {
  const { siteId } = useParams<{ siteId: string }>();

  const { data, isLoading } = useSWR(
    siteId ? `onb-map-${siteId}` : null,
    () => api.demandMap(siteId, { limit: 200 }),
  );

  async function persist() {
    await api.patchOnboardingStep(siteId, "confirm_plan");
  }

  const items: any[] = data?.items ?? [];
  const growing = items.filter((c) => c.growth_intent === "grow");

  function fitOf(c: any): { tone: string; label: string; reason: string } {
    if (c.page_intent_fit === "green")
      return { tone: "bg-emerald-100 text-emerald-800 border-emerald-300", label: "OK", reason: c.page_intent_fit_reason_ru || "Страница соответствует запросу" };
    if (c.page_intent_fit === "yellow")
      return { tone: "bg-amber-100 text-amber-800 border-amber-300", label: "Сойдёт", reason: c.page_intent_fit_reason_ru || "Есть страница, но можно улучшить" };
    if (c.page_intent_fit === "red")
      return { tone: "bg-rose-100 text-rose-800 border-rose-300", label: "Не то", reason: c.page_intent_fit_reason_ru || "Страница не про этот запрос" };
    // Fallback heuristic until the PageReview pipeline writes fit/reason.
    if ((c.queries_count || 0) > 0)
      return { tone: "bg-emerald-100 text-emerald-800 border-emerald-300", label: "Видно", reason: "По этому кластеру есть реальные показы" };
    return { tone: "bg-slate-100 text-slate-700 border-slate-300", label: "Нет данных", reason: "Реальных показов пока не собрано" };
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold mb-1">Где ты сейчас стоишь</h2>
        <p className="text-sm text-muted-foreground">
          По каждому кластеру, где ты выбрал «Расту», — что у нас сейчас.
          Точные позиции и оценка страниц подтянутся после запуска pipeline.
        </p>
      </div>

      {isLoading ? (
        <div className="space-y-2">{[...Array(5)].map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
      ) : growing.length === 0 ? (
        <div className="rounded-lg border border-dashed bg-muted/30 p-6 text-center text-sm text-muted-foreground">
          На шаге 4 не выбрано ни одного кластера с «Расту». Вернись и отметь хотя бы один.
        </div>
      ) : (
        <ul className="space-y-2">
          {growing.map((c) => {
            const fit = fitOf(c);
            return (
              <li key={c.id} className="rounded-lg border p-3 flex items-center gap-3">
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">{c.name_ru || c.cluster_key}</div>
                  <div className="text-[11px] text-muted-foreground mt-0.5">
                    релевантность {Number(c.business_relevance ?? 0).toFixed(2)} · запросов {c.queries_count ?? 0}
                  </div>
                </div>
                <div className="shrink-0 flex flex-col items-end gap-1">
                  <Badge variant="outline" className={cn("text-[10px]", fit.tone)}>
                    {fit.label}
                  </Badge>
                  <span className="text-[10px] text-muted-foreground text-right max-w-[16ch] leading-tight">
                    {fit.reason}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <StepNav siteId={siteId} step={5} onNext={persist} />
    </div>
  );
}
