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
import { RefreshCw, ClipboardList, ChevronRight, ExternalLink } from "lucide-react";

const STATUS_LABEL: Record<string, string> = {
  completed: "готово",
  running: "в работе",
  failed: "ошибка",
  skipped: "пропущено",
  queued: "в очереди",
  pending: "ожидает",
};

const STATUS_TONE: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  completed: "default",
  running: "secondary",
  failed: "destructive",
  skipped: "outline",
  queued: "outline",
  pending: "outline",
};

export default function ReviewsPage() {
  const siteId = useCurrentSiteId();
  const [triggering, setTriggering] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [banner, setBanner] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  const key = siteId ? `reviews-${siteId}-${statusFilter}` : null;
  const { data, isLoading, error, mutate } = useSWR(
    key,
    () => api.reviewsList(siteId, {
      limit: 50,
      ...(statusFilter ? { status: statusFilter } : {}),
    }),
    { refreshInterval: 0 },
  );

  const { data: stats } = useSWR(
    siteId ? `reviews-stats-${siteId}` : null,
    () => api.reviewsStats(siteId),
    { refreshInterval: 0 },
  );

  async function onTrigger() {
    if (!siteId) return;
    setTriggering(true); setBanner(null);
    try {
      await api.triggerSiteReview(siteId, 20);
      setBanner({ kind: "ok", msg: "Запуск ревью в очереди. Обновите через ~1 минуту." });
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
          <h1 className="text-2xl font-bold">Ревью страниц</h1>
          <p className="text-sm text-muted-foreground">
            Глубокий анализ страниц по таргет-интентам — основа для рекомендаций.
          </p>
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => mutate()}>
            <RefreshCw className="mr-2 h-4 w-4" /> Обновить
          </Button>
          <Button size="sm" onClick={onTrigger} disabled={triggering || !siteId}>
            <ClipboardList className={`mr-2 h-4 w-4 ${triggering ? "animate-pulse" : ""}`} />
            {triggering ? "Ставим в очередь…" : "Запустить ревью"}
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

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="Всего ревью" value={stats.total_reviews ?? 0} />
          <StatCard label="Рекомендаций" value={stats.recommendations?.total ?? 0} />
          <StatCard label="Применено" value={stats.recommendations?.by_user_status?.applied ?? 0} tone="emerald" />
          <StatCard label="Расход" value={`$${Number(stats.cost_total_usd ?? 0).toFixed(3)}`} />
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-1 flex-wrap">
        <span className="text-xs text-muted-foreground mr-1">статус:</span>
        {["", "completed", "running", "failed", "skipped"].map((s) => (
          <button
            key={s || "all"}
            onClick={() => setStatusFilter(s)}
            className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
              statusFilter === s
                ? "bg-primary text-primary-foreground border-primary"
                : "bg-background hover:bg-accent"
            }`}
          >
            {s ? STATUS_LABEL[s] || s : "все"}
          </button>
        ))}
      </div>

      {error && (
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">
          {String((error as any)?.message || error)}
        </div>
      )}

      <Card>
        <CardHeader><CardTitle className="text-base">История ревью</CardTitle></CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="p-4 space-y-2">
              {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-20" />)}
            </div>
          ) : items.length === 0 ? (
            <div className="p-10 text-center text-sm text-muted-foreground">
              Ревью ещё не запускались. Нажмите «Запустить ревью».
            </div>
          ) : (
            <ul className="divide-y">
              {items.map((r) => (
                <li key={r.id}>
                  <Link
                    href={`/reviews/${r.id}`}
                    className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-accent transition-colors"
                  >
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge variant={STATUS_TONE[r.status] ?? "outline"} className="text-xs">
                          {STATUS_LABEL[r.status] ?? r.status}
                        </Badge>
                        {r.target_intent_code && (
                          <Badge variant="outline" className="text-xs font-mono">{r.target_intent_code}</Badge>
                        )}
                        {r.skip_reason && (
                          <Badge variant="outline" className="text-xs">{r.skip_reason}</Badge>
                        )}
                      </div>
                      <div className="text-xs text-muted-foreground flex items-center gap-2 flex-wrap">
                        <span className="font-mono truncate max-w-[36ch]">{r.page_id}</span>
                        <span>·</span>
                        <span>{r.reviewed_at ? new Date(r.reviewed_at).toLocaleString("ru") : "—"}</span>
                        {r.cost_usd != null && <><span>·</span><span>${Number(r.cost_usd).toFixed(4)}</span></>}
                        {r.duration_ms != null && <><span>·</span><span>{(r.duration_ms / 1000).toFixed(1)}s</span></>}
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

function StatCard({ label, value, tone }: { label: string; value: number | string; tone?: "emerald" }) {
  return (
    <Card>
      <CardContent className="py-3">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className={`text-2xl font-bold ${tone === "emerald" ? "text-emerald-600" : ""}`}>
          {value}
        </div>
      </CardContent>
    </Card>
  );
}
