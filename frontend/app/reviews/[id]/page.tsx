"use client";

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { ArrowLeft, Check, X, Clock } from "lucide-react";
import { cn } from "@/lib/utils";

const PRIORITY_TONE: Record<string, string> = {
  critical: "bg-rose-100 text-rose-800 border-rose-300",
  high:     "bg-orange-100 text-orange-800 border-orange-300",
  medium:   "bg-amber-100 text-amber-800 border-amber-300",
  low:      "bg-slate-100 text-slate-700 border-slate-300",
};

export default function ReviewDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  const { data, isLoading, error, mutate } = useSWR(
    id ? `review-${id}` : null,
    () => api.review(id),
  );

  return (
    <div className="space-y-4">
      <Button size="sm" variant="ghost" onClick={() => router.push("/reviews")}>
        <ArrowLeft className="h-4 w-4 mr-2" /> К списку
      </Button>

      {isLoading ? (
        <div className="space-y-3">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-24" />)}
        </div>
      ) : error ? (
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">
          {String((error as any)?.message || error)}
        </div>
      ) : data ? (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-lg">
                Ревью
                <Badge variant={data.status === "completed" ? "default" : "outline"}>
                  {data.status}
                </Badge>
                {data.target_intent_code && (
                  <Badge variant="outline" className="font-mono">{data.target_intent_code}</Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="text-sm space-y-2">
              <KV k="Страница" v={<span className="font-mono text-xs">{data.page_id}</span>} />
              <KV k="Reviewer" v={`${data.reviewer_model ?? "—"} · v${data.reviewer_version ?? "—"}`} />
              <KV k="Когда" v={data.reviewed_at ? new Date(data.reviewed_at).toLocaleString("ru") : "—"} />
              <KV k="Стоимость" v={`$${Number(data.cost_usd ?? 0).toFixed(5)}`} />
              <KV k="Время" v={data.duration_ms ? `${(data.duration_ms / 1000).toFixed(1)}s` : "—"} />
              {data.skip_reason && <KV k="Причина пропуска" v={data.skip_reason} />}
              {data.error && <KV k="Ошибка" v={<span className="text-rose-700">{data.error}</span>} />}
            </CardContent>
          </Card>

          {data.page_level_summary && (
            <Card>
              <CardHeader><CardTitle className="text-base">Summary страницы</CardTitle></CardHeader>
              <CardContent>
                <pre className="text-xs whitespace-pre-wrap font-mono text-muted-foreground">
                  {typeof data.page_level_summary === "string"
                    ? data.page_level_summary
                    : JSON.stringify(data.page_level_summary, null, 2)}
                </pre>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Рекомендации · {data.recommendations_total ?? 0}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              {!data.recommendations_by_category ||
               Object.keys(data.recommendations_by_category).length === 0 ? (
                <p className="text-sm text-muted-foreground italic">Нет рекомендаций.</p>
              ) : (
                Object.entries(data.recommendations_by_category).map(([cat, list]) => (
                  <div key={cat} className="space-y-2">
                    <h3 className="text-sm font-semibold uppercase text-muted-foreground tracking-wide">
                      {cat} <span className="text-muted-foreground/70">({(list as any[]).length})</span>
                    </h3>
                    <div className="space-y-2">
                      {(list as any[]).map((r) => (
                        <RecommendationRow key={r.id} rec={r} onMutated={mutate} />
                      ))}
                    </div>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>
      ) : (
        <div className="text-sm text-muted-foreground">Ревью не найдено.</div>
      )}
    </div>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4">
      <span className="text-muted-foreground">{k}</span>
      <span className="font-medium text-right">{v}</span>
    </div>
  );
}

function RecommendationRow({ rec, onMutated }: { rec: any; onMutated: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isDone = rec.user_status === "applied";
  const isDismissed = rec.user_status === "dismissed";
  const isDeferred = rec.user_status === "deferred";

  async function update(next: string) {
    setBusy(next); setError(null);
    try {
      await api.patchRecommendation(rec.id, { user_status: next });
      onMutated();
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className={cn(
      "rounded border p-3 text-sm space-y-2 transition-opacity",
      (isDone || isDismissed) && "opacity-60",
    )}>
      <div className="flex items-center gap-2 flex-wrap">
        <Badge variant="outline" className={cn("text-xs", PRIORITY_TONE[rec.priority])}>
          {rec.priority}
        </Badge>
        {isDone && <Badge className="text-xs bg-emerald-600">✓ применено</Badge>}
        {isDismissed && <Badge variant="outline" className="text-xs">отклонено</Badge>}
        {isDeferred && <Badge variant="outline" className="text-xs">отложено</Badge>}
        <div className="ml-auto flex items-center gap-1">
          <Button size="sm" variant={isDone ? "default" : "outline"} disabled={busy !== null}
            onClick={() => update(isDone ? "pending" : "applied")} title="Применено">
            <Check className="h-4 w-4" />
          </Button>
          <Button size="sm" variant="outline" disabled={busy !== null}
            onClick={() => update(isDeferred ? "pending" : "deferred")} title="Отложить">
            <Clock className="h-4 w-4" />
          </Button>
          <Button size="sm" variant="outline" disabled={busy !== null}
            onClick={() => update(isDismissed ? "pending" : "dismissed")} title="Отклонить">
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {rec.reasoning_ru && <p className="leading-snug">{rec.reasoning_ru}</p>}

      {(rec.before_text || rec.after_text) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
          <div className="rounded border bg-rose-50/50 p-2">
            <div className="text-rose-800 font-semibold mb-1">Сейчас</div>
            <div className="whitespace-pre-wrap font-mono text-[11px] leading-snug">
              {rec.before_text || "—"}
            </div>
          </div>
          <div className="rounded border bg-emerald-50/50 p-2">
            <div className="text-emerald-800 font-semibold mb-1">Предлагается</div>
            <div className="whitespace-pre-wrap font-mono text-[11px] leading-snug">
              {rec.after_text || "—"}
            </div>
          </div>
        </div>
      )}

      {error && (
        <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1">
          {error}
        </div>
      )}
    </div>
  );
}
