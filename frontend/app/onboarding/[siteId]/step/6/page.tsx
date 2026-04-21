"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StepNav } from "@/components/onboarding/step-nav";
import { cn } from "@/lib/utils";
import { Check, X, RefreshCw } from "lucide-react";

const PRIORITY_TONE: Record<string, string> = {
  critical: "bg-rose-100 text-rose-800 border-rose-300",
  high:     "bg-orange-100 text-orange-800 border-orange-300",
  medium:   "bg-amber-100 text-amber-800 border-amber-300",
  low:      "bg-slate-100 text-slate-700 border-slate-300",
};

export default function Step6Plan() {
  const { siteId } = useParams<{ siteId: string }>();
  const [rescoring, setRescoring] = useState(false);
  const [busyRec, setBusyRec] = useState<string | null>(null);

  const { data, isLoading, mutate } = useSWR(
    siteId ? `onb-plan-${siteId}` : null,
    () => api.weeklyPlan(siteId, 10, 2),
  );

  async function rescore() {
    setRescoring(true);
    try {
      await api.triggerRescore(siteId);
      // give scorer a moment, then refresh
      await new Promise((r) => setTimeout(r, 2000));
      mutate();
    } finally {
      setRescoring(false);
    }
  }

  async function act(recId: string, next: "applied" | "dismissed" | "deferred") {
    setBusyRec(recId);
    try {
      await api.patchRecommendation(recId, { user_status: next });
      mutate();
    } finally {
      setBusyRec(null);
    }
  }

  async function persist() {
    await api.patchOnboardingStep(siteId, "confirm_kpi");
  }

  const items: any[] = data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-2 flex-wrap">
        <div>
          <h2 className="text-lg font-semibold mb-1">План на эту неделю</h2>
          <p className="text-sm text-muted-foreground">
            Топ-10 по подтверждённым кластерам. Отметь, что берёшь в работу, а что — мимо.
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={rescore} disabled={rescoring}>
          <RefreshCw className={cn("mr-2 h-4 w-4", rescoring && "animate-spin")} />
          Пересчитать
        </Button>
      </div>

      {isLoading ? (
        <div className="space-y-2">{[...Array(5)].map((_, i) => <Skeleton key={i} className="h-24" />)}</div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed bg-muted/30 p-6 text-center text-sm text-muted-foreground">
          Плана ещё нет. Нажми «Пересчитать» — скорер соберёт задачи из подтверждённых кластеров.
        </div>
      ) : (
        <ul className="space-y-2">
          {items.map((it: any, i: number) => {
            const isDone = it.user_status === "applied";
            const isDismissed = it.user_status === "dismissed";
            const isDeferred = it.user_status === "deferred";
            return (
              <li
                key={it.recommendation_id}
                className={cn(
                  "rounded-lg border p-3 space-y-2 transition-opacity",
                  (isDone || isDismissed) && "opacity-60",
                )}
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs font-bold text-muted-foreground">#{i + 1}</span>
                  <Badge variant="outline" className={cn("text-[10px]", PRIORITY_TONE[it.priority])}>
                    {it.priority} · {it.priority_score.toFixed(1)}
                  </Badge>
                  <Badge variant="secondary" className="text-[10px]">{it.category}</Badge>
                  {isDone && <Badge className="text-[10px] bg-emerald-600">✓ беру</Badge>}
                  {isDismissed && <Badge variant="outline" className="text-[10px]">мимо</Badge>}
                  {isDeferred && <Badge variant="outline" className="text-[10px]">отложено</Badge>}
                </div>
                {it.page_url && (
                  <div className="text-[11px] text-muted-foreground truncate">{it.page_url}</div>
                )}
                <p className="text-sm leading-snug">{it.reasoning_ru}</p>

                <div className="flex items-center gap-2 pt-1">
                  <Button
                    size="sm"
                    variant={isDone ? "default" : "outline"}
                    disabled={busyRec === it.recommendation_id}
                    onClick={() => act(it.recommendation_id, "applied")}
                  >
                    <Check className="h-4 w-4 mr-1" /> Беру
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busyRec === it.recommendation_id}
                    onClick={() => act(it.recommendation_id, "dismissed")}
                  >
                    <X className="h-4 w-4 mr-1" /> Мимо
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <StepNav siteId={siteId} step={6} onNext={persist} />
    </div>
  );
}
