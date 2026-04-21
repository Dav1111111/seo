"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useCurrentSiteId } from "@/lib/site-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { RefreshCw, FileText, ChevronRight } from "lucide-react";

export default function ReportsPage() {
  const siteId = useCurrentSiteId();
  const [triggering, setTriggering] = useState(false);
  const [banner, setBanner] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  const { data, isLoading, error, mutate } = useSWR(
    siteId ? `reports-${siteId}` : null,
    () => api.reportsList(siteId, 20),
    { refreshInterval: 0 },
  );

  async function onTrigger() {
    if (!siteId) return;
    setTriggering(true); setBanner(null);
    try {
      await api.triggerReport(siteId);
      setBanner({ kind: "ok", msg: "Сборка отчёта поставлена в очередь. Готов будет через 1–2 минуты." });
    } catch (e: any) {
      setBanner({ kind: "err", msg: e?.message ?? String(e) });
    } finally {
      setTriggering(false);
    }
  }

  const items: any[] = data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">Отчёты</h1>
          <p className="text-sm text-muted-foreground">
            Еженедельные отчёты: корневая проблема, план, покрытие, тренды, ревью, техника.
          </p>
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => mutate()}>
            <RefreshCw className="mr-2 h-4 w-4" /> Обновить
          </Button>
          <Button size="sm" onClick={onTrigger} disabled={triggering || !siteId}>
            <FileText className={`mr-2 h-4 w-4 ${triggering ? "animate-pulse" : ""}`} />
            {triggering ? "Ставим в очередь…" : "Сформировать отчёт"}
          </Button>
        </div>
      </div>

      {banner && (
        <div className={`rounded border px-3 py-2 text-sm ${banner.kind === "ok"
          ? "border-emerald-300 bg-emerald-50 text-emerald-900"
          : "border-red-300 bg-red-50 text-red-900"}`}>
          {banner.msg}
        </div>
      )}

      {error && (
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">
          {String((error as any)?.message || error)}
        </div>
      )}

      <Card>
        <CardHeader><CardTitle className="text-base">История</CardTitle></CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="p-4 space-y-2">
              {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-16" />)}
            </div>
          ) : items.length === 0 ? (
            <div className="p-10 text-center text-sm text-muted-foreground">
              Отчётов ещё не было. Нажмите «Сформировать отчёт».
            </div>
          ) : (
            <ul className="divide-y">
              {items.map((r) => (
                <li key={r.id}>
                  <Link
                    href={`/reports/${r.id}`}
                    className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-accent transition-colors"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium text-sm">
                          {r.week_start} — {r.week_end}
                        </span>
                        <Badge variant={r.status === "completed" ? "default" : "outline"} className="text-xs">
                          {r.status}
                        </Badge>
                        <HealthBadge score={r.health_score} />
                      </div>
                      <div className="text-xs text-muted-foreground mt-0.5">
                        {r.generated_at ? new Date(r.generated_at).toLocaleString("ru") : "—"}
                        {" · "}
                        ${Number(r.llm_cost_usd ?? 0).toFixed(4)}
                      </div>
                    </div>
                    <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function HealthBadge({ score }: { score: number | null | undefined }) {
  if (score == null) return null;
  const tone =
    score >= 80 ? "bg-emerald-100 text-emerald-800 border-emerald-300"
    : score >= 50 ? "bg-amber-100 text-amber-800 border-amber-300"
    : "bg-rose-100 text-rose-800 border-rose-300";
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${tone}`}>
      Health {score}
    </span>
  );
}
