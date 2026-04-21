"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useCurrentSiteId } from "@/lib/site-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { PriorityCard, PriorityItem } from "@/components/priorities/priority-card";
import { RefreshCw, ListChecks, Inbox } from "lucide-react";

const CATEGORIES = ["title", "description", "h1", "content", "eeat", "commercial", "internal_links", "structured_data", "ux"];
const PRIORITIES = ["critical", "high", "medium", "low"];

export default function PrioritiesPage() {
  const siteId = useCurrentSiteId();
  const [tab, setTab] = useState<"plan" | "backlog">("plan");
  const [filterPriority, setFilterPriority] = useState<string>("");
  const [filterCategory, setFilterCategory] = useState<string>("");
  const [includeDismissed, setIncludeDismissed] = useState(false);
  const [rescoring, setRescoring] = useState(false);
  const [banner, setBanner] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  const plan = useSWR(
    siteId ? `plan-${siteId}` : null,
    () => api.weeklyPlan(siteId, 10, 2),
    { refreshInterval: 0 },
  );

  const backlogKey = siteId
    ? `backlog-${siteId}-${filterPriority}-${filterCategory}-${includeDismissed}`
    : null;
  const backlog = useSWR(
    backlogKey,
    () => api.priorities(siteId, {
      top_n: 100,
      ...(filterPriority ? { priority: filterPriority } : {}),
      ...(filterCategory ? { category: filterCategory } : {}),
      include_dismissed: includeDismissed,
    }),
    { refreshInterval: 0 },
  );

  async function onRescore() {
    if (!siteId) return;
    setRescoring(true); setBanner(null);
    try {
      await api.triggerRescore(siteId);
      setBanner({ kind: "ok", msg: "Пересчёт приоритетов поставлен в очередь. Обновите через ~30 сек." });
    } catch (e: any) {
      setBanner({ kind: "err", msg: e?.message ?? String(e) });
    } finally {
      setRescoring(false);
    }
  }

  function refresh() {
    plan.mutate();
    backlog.mutate();
  }

  const planItems: PriorityItem[] = plan.data?.items ?? [];
  const backlogItems: PriorityItem[] = backlog.data?.items ?? [];

  const backlogStats = useMemo(() => {
    const byPriority: Record<string, number> = {};
    const byCategory: Record<string, number> = {};
    for (const it of backlogItems) {
      byPriority[it.priority] = (byPriority[it.priority] || 0) + 1;
      byCategory[it.category] = (byCategory[it.category] || 0) + 1;
    }
    return { byPriority, byCategory };
  }, [backlogItems]);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold">Приоритеты</h1>
          <p className="text-sm text-muted-foreground">
            План на неделю — топ-10 с диверсификацией по страницам. Бэклог — весь список с фильтрами.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={refresh}>
            <RefreshCw className="mr-2 h-4 w-4" /> Обновить
          </Button>
          <Button size="sm" onClick={onRescore} disabled={rescoring || !siteId}>
            <RefreshCw className={`mr-2 h-4 w-4 ${rescoring ? "animate-spin" : ""}`} />
            {rescoring ? "Ставим в очередь…" : "Пересчитать"}
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

      <Tabs value={tab} onValueChange={(v) => setTab(v as any)}>
        <TabsList>
          <TabsTrigger value="plan">
            <ListChecks className="h-4 w-4 mr-2" /> План на неделю
            {plan.data && <Badge variant="secondary" className="ml-2">{plan.data.items.length}</Badge>}
          </TabsTrigger>
          <TabsTrigger value="backlog">
            <Inbox className="h-4 w-4 mr-2" /> Бэклог
            {backlog.data && <Badge variant="secondary" className="ml-2">{backlog.data.total}</Badge>}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="plan" className="space-y-4 mt-4">
          {plan.isLoading ? (
            <div className="space-y-3">{[...Array(5)].map((_, i) => <Skeleton key={i} className="h-32" />)}</div>
          ) : plan.error ? (
            <ErrorBox err={plan.error} />
          ) : planItems.length === 0 ? (
            <EmptyState label="План пустой. Запустите «Пересчитать» после ревью страниц." />
          ) : (
            <>
              <div className="text-xs text-muted-foreground">
                Бэклог: <b>{plan.data?.total_in_backlog ?? 0}</b> · страниц покрыто: {plan.data?.pages_represented ?? 0}
                {" "}· max/страница: {plan.data?.max_per_page ?? 0}
              </div>
              <div className="space-y-3">
                {planItems.map((it) => (
                  <PriorityCard key={it.recommendation_id} item={it} onMutated={refresh} />
                ))}
              </div>
            </>
          )}
        </TabsContent>

        <TabsContent value="backlog" className="space-y-4 mt-4">
          <Card>
            <CardHeader><CardTitle className="text-sm">Фильтры</CardTitle></CardHeader>
            <CardContent className="flex flex-wrap gap-4 items-center">
              <div className="space-y-1">
                <div className="text-xs text-muted-foreground">Приоритет</div>
                <div className="flex gap-1">
                  <FilterChip active={!filterPriority} onClick={() => setFilterPriority("")}>все</FilterChip>
                  {PRIORITIES.map((p) => (
                    <FilterChip
                      key={p}
                      active={filterPriority === p}
                      onClick={() => setFilterPriority(filterPriority === p ? "" : p)}
                    >
                      {p}
                      {backlogStats.byPriority[p] != null && (
                        <span className="ml-1 text-muted-foreground">({backlogStats.byPriority[p]})</span>
                      )}
                    </FilterChip>
                  ))}
                </div>
              </div>
              <div className="space-y-1">
                <div className="text-xs text-muted-foreground">Категория</div>
                <div className="flex flex-wrap gap-1">
                  <FilterChip active={!filterCategory} onClick={() => setFilterCategory("")}>все</FilterChip>
                  {CATEGORIES.map((c) => (
                    <FilterChip
                      key={c}
                      active={filterCategory === c}
                      onClick={() => setFilterCategory(filterCategory === c ? "" : c)}
                    >
                      {c}
                      {backlogStats.byCategory[c] != null && (
                        <span className="ml-1 text-muted-foreground">({backlogStats.byCategory[c]})</span>
                      )}
                    </FilterChip>
                  ))}
                </div>
              </div>
              <label className="flex items-center gap-2 text-xs cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={includeDismissed}
                  onChange={(e) => setIncludeDismissed(e.target.checked)}
                />
                показывать отклонённые
              </label>
            </CardContent>
          </Card>

          {backlog.isLoading ? (
            <div className="space-y-3">{[...Array(5)].map((_, i) => <Skeleton key={i} className="h-32" />)}</div>
          ) : backlog.error ? (
            <ErrorBox err={backlog.error} />
          ) : backlogItems.length === 0 ? (
            <EmptyState label="Нет рекомендаций с такими фильтрами." />
          ) : (
            <div className="space-y-3">
              {backlogItems.map((it) => (
                <PriorityCard key={it.recommendation_id} item={it} onMutated={refresh} />
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

function FilterChip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
        active
          ? "bg-primary text-primary-foreground border-primary"
          : "bg-background text-foreground hover:bg-accent"
      }`}
    >
      {children}
    </button>
  );
}

function EmptyState({ label }: { label: string }) {
  return (
    <Card>
      <CardContent className="py-10 text-center text-sm text-muted-foreground">
        {label}
      </CardContent>
    </Card>
  );
}

function ErrorBox({ err }: { err: unknown }) {
  return (
    <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">
      {String((err as any)?.message || err)}
    </div>
  );
}
