"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StepNav } from "@/components/onboarding/step-nav";
import { cn } from "@/lib/utils";
import { AlertCircle } from "lucide-react";

type Intent = "grow" | "ignore" | "not_mine";

const INTENT_LABEL: Record<Intent, string> = {
  grow: "Расту",
  ignore: "Пока нет",
  not_mine: "Не моё",
};

const INTENT_TONE: Record<Intent, string> = {
  grow: "bg-emerald-100 text-emerald-800 border-emerald-300",
  ignore: "bg-amber-100 text-amber-800 border-amber-300",
  not_mine: "bg-slate-100 text-slate-700 border-slate-300",
};

export default function Step4Queries() {
  const { siteId } = useParams<{ siteId: string }>();
  const [saving, setSaving] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const { data, isLoading, mutate } = useSWR(
    siteId ? `onb-map-${siteId}` : null,
    () => api.demandMap(siteId, { limit: 100 }),
  );

  async function setIntent(clusterId: string, intent: Intent) {
    setSaving(clusterId); setErr(null);
    try {
      await api.patchClusterReview(siteId, clusterId, {
        growth_intent: intent,
        user_confirmed: intent !== "not_mine",
      });
      mutate();
    } catch (e: any) {
      setErr(e?.message ?? String(e));
    } finally {
      setSaving(null);
    }
  }

  async function persistStep() {
    await api.patchOnboardingStep(siteId, "confirm_positions");
  }

  const items: any[] = data?.items ?? [];
  const grown = items.filter((c) => c.growth_intent === "grow").length;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold mb-1">По каким запросам хочешь расти</h2>
        <p className="text-sm text-muted-foreground">
          Пройдись по карте. «Расту» — хочу больше трафика по этой теме.
          «Пока нет» — в курсе, но не сейчас. «Не моё» — совсем не про мой бизнес.
        </p>
        <div className="mt-2 text-xs text-muted-foreground">
          Отмечено: <b>{items.length}</b> кластеров · «Расту»: <b>{grown}</b>
        </div>
      </div>

      {err && (
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900 flex items-start gap-2">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" /> <span>{err}</span>
        </div>
      )}

      {isLoading ? (
        <div className="space-y-2">
          {[...Array(6)].map((_, i) => <Skeleton key={i} className="h-14" />)}
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed bg-muted/30 p-6 text-center text-sm text-muted-foreground">
          Карта спроса пустая. Вернись на шаг 2 и примени профиль — карта перестроится.
        </div>
      ) : (
        <ul className="space-y-2 max-h-[60vh] overflow-y-auto pr-1">
          {items.map((c) => {
            const current = (c.growth_intent || null) as Intent | null;
            return (
              <li key={c.id} className="rounded-lg border p-3 flex items-center gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium text-sm truncate">{c.name_ru || c.cluster_key}</span>
                    <Badge variant="outline" className="text-[10px]">{c.cluster_type}</Badge>
                    <Badge variant="outline" className="text-[10px]">tier: {c.quality_tier}</Badge>
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-0.5">
                    релевантность {Number(c.business_relevance ?? 0).toFixed(2)} · запросов {c.queries_count ?? 0}
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  {(["grow", "ignore", "not_mine"] as Intent[]).map((it) => (
                    <button
                      key={it}
                      disabled={saving === c.id}
                      onClick={() => setIntent(c.id, it)}
                      className={cn(
                        "rounded-full border px-2.5 py-1 text-[11px] transition-colors",
                        current === it ? INTENT_TONE[it] + " border" : "hover:bg-accent",
                      )}
                    >
                      {INTENT_LABEL[it]}
                    </button>
                  ))}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <StepNav siteId={siteId} step={4} onNext={persistStep} />
    </div>
  );
}
