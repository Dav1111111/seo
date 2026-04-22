"use client";

import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useCurrentSiteId } from "@/lib/site-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ActivityFeed } from "@/components/dashboard/activity-feed";
import { LastRunSummary } from "@/components/dashboard/last-run-summary";
import {
  RefreshCw, Search, ExternalLink, Swords,
  TrendingDown, Layers, Check, X, Target, CheckCircle2,
} from "lucide-react";
import { cn } from "@/lib/utils";

export default function CompetitorsPage() {
  const siteId = useCurrentSiteId();
  const [discovering, setDiscovering] = useState(false);
  const [diving, setDiving] = useState(false);
  const [tab, setTab] = useState<"opps" | "list" | "gaps" | "dive">("opps");
  const [banner, setBanner] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  const activitySWR = useSWR(
    siteId ? `cp-activity-${siteId}` : null,
    () => api.getActivity(siteId, 15),
    { refreshInterval: 5_000 },
  );
  // Running = per-stage the newest event is not terminal. Prevents old
  // "progress" rows from pinning the page in a spinning state forever.
  const hasRunning = (() => {
    const events = activitySWR.data?.events ?? [];
    const TERMINAL = new Set(["done", "failed", "skipped"]);
    const latest = new Map<string, string>();
    for (const e of events) {
      if (!latest.has(e.stage)) latest.set(e.stage, e.status);
    }
    return [...latest.values()].some((s) => !TERMINAL.has(s));
  })();

  // While a task is active, poll data every 8s so the user sees fresh
  // results as soon as Celery finishes. When idle, no polling.
  const refreshInterval = hasRunning ? 8_000 : 0;

  const listSWR = useSWR(
    siteId ? `competitors-${siteId}` : null,
    () => api.getCompetitors(siteId),
    { refreshInterval },
  );
  const gapsSWR = useSWR(
    siteId ? `gaps-${siteId}` : null,
    () => api.getContentGaps(siteId, 25),
    { refreshInterval },
  );
  const diveSWR = useSWR(
    siteId ? `dive-${siteId}` : null,
    () => api.getCompetitorDeepDive(siteId),
    { refreshInterval },
  );
  const oppsSWR = useSWR(
    siteId ? `opps-${siteId}` : null,
    () => api.getGrowthOpportunities(siteId),
    { refreshInterval },
  );
  const outcomesSWR = useSWR(
    siteId ? `outcomes-${siteId}` : null,
    () => api.getOutcomes(siteId),
    { refreshInterval: 0 },
  );

  async function runDiscovery() {
    if (!siteId) return;
    setDiscovering(true); setBanner(null);
    try {
      await api.triggerCompetitorDiscovery(siteId, 25, 10);
      activitySWR.mutate();
      setBanner({
        kind: "ok",
        msg: "Разведка запущена — следи за прогрессом в ленте ниже. Занимает 1–3 минуты, после неё автоматически пойдёт глубокий анализ и пересчёт точек роста.",
      });
    } catch (e: any) {
      setBanner({ kind: "err", msg: e?.message ?? String(e) });
    } finally {
      setDiscovering(false);
    }
  }

  async function runDeepDive() {
    if (!siteId) return;
    setDiving(true); setBanner(null);
    try {
      await api.triggerCompetitorDeepDive(siteId);
      activitySWR.mutate();
      setBanner({
        kind: "ok",
        msg: "Глубокий анализ запущен — следи за прогрессом в ленте ниже.",
      });
    } catch (e: any) {
      setBanner({ kind: "err", msg: e?.message ?? String(e) });
    } finally {
      setDiving(false);
    }
  }

  function refresh() {
    listSWR.mutate();
    gapsSWR.mutate();
    diveSWR.mutate();
    oppsSWR.mutate();
  }

  const list = listSWR.data;
  const competitors = list?.profile?.competitors ?? [];
  const gaps = gapsSWR.data?.gaps ?? [];
  const dive = diveSWR.data;
  const opps = oppsSWR.data?.opportunities ?? [];
  const outcomes = outcomesSWR.data?.outcomes ?? [];
  const outcomeByRecId = new Map(outcomes.map((o) => [o.recommendation_id, o]));

  async function markOppApplied(opp: any) {
    if (!siteId) return;
    try {
      await api.markApplied(
        siteId,
        opp.id,
        "opportunity",
        opp.evidence?.matched_page?.url,
      );
      outcomesSWR.mutate();
      setBanner({ kind: "ok", msg: "Отмечено. Через 14 дней платформа посчитает эффект." });
    } catch (e: any) {
      setBanner({ kind: "err", msg: e?.message ?? String(e) });
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Swords className="h-6 w-6" /> Конкуренты
          </h1>
          <p className="text-sm text-muted-foreground">
            Кто в Яндексе стоит рядом по твоим запросам, что у них есть, чего нет у тебя.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={refresh}>
            <RefreshCw className="mr-2 h-4 w-4" /> Обновить
          </Button>
          <Button size="sm" variant="outline" onClick={runDiscovery} disabled={discovering}>
            <Search className={cn("mr-2 h-4 w-4", discovering && "animate-pulse")} />
            Пересобрать список
          </Button>
          <Button size="sm" onClick={runDeepDive} disabled={diving || competitors.length === 0}>
            <Layers className={cn("mr-2 h-4 w-4", diving && "animate-pulse")} />
            Глубокий анализ
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

      {/* Last-run summary card — always on top so the user sees results
          at a glance without hunting through tabs. */}
      <LastRunSummary siteId={siteId} />

      {/* Live progress — visible whenever discovery/deep-dive is running */}
      {hasRunning && <ActivityFeed siteId={siteId} />}

      <Tabs value={tab} onValueChange={(v) => setTab(v as any)}>
        <TabsList>
          <TabsTrigger value="opps">
            <Target className="h-4 w-4 mr-2" />Что делать
            {opps.length > 0 && <Badge variant="secondary" className="ml-2">{opps.length}</Badge>}
          </TabsTrigger>
          <TabsTrigger value="list">
            Список
            {competitors.length > 0 && <Badge variant="secondary" className="ml-2">{competitors.length}</Badge>}
          </TabsTrigger>
          <TabsTrigger value="gaps">
            Где я теряю
            {gaps.length > 0 && <Badge variant="secondary" className="ml-2">{gaps.length}</Badge>}
          </TabsTrigger>
          <TabsTrigger value="dive">
            Глубокий анализ
            {dive && dive.competitors?.length > 0 && (
              <Badge variant="secondary" className="ml-2">{dive.competitors.length}</Badge>
            )}
          </TabsTrigger>
        </TabsList>

        {/* OPPORTUNITIES TAB */}
        <TabsContent value="opps" className="mt-4">
          {oppsSWR.isLoading ? (
            <div className="space-y-2">{[...Array(4)].map((_, i) => <Skeleton key={i} className="h-24" />)}</div>
          ) : opps.length === 0 ? (
            <Card>
              <CardContent className="py-10 text-center text-sm text-muted-foreground">
                План действий появится после запуска разведки + глубокого анализа.
                Нажми «Пересобрать список» — он автоматически потянет глубокий анализ
                и сгенерирует план.
              </CardContent>
            </Card>
          ) : (
            <ul className="space-y-3">
              {opps.map((o) => (
                <li key={o.id} className="rounded-lg border bg-card p-4 space-y-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge
                      variant="outline"
                      className={cn(
                        "text-[10px]",
                        o.priority === "high"
                          ? "bg-rose-100 text-rose-800 border-rose-300"
                          : o.priority === "medium"
                          ? "bg-amber-100 text-amber-800 border-amber-300"
                          : "bg-slate-100 text-slate-700 border-slate-300",
                      )}
                    >
                      {o.priority}
                    </Badge>
                    <Badge variant="secondary" className="text-[10px]">
                      {o.category === "new_page"
                        ? "новая страница"
                        : o.category === "strengthen_existing_page"
                        ? "усилить страницу"
                        : o.category === "crossover_page"
                        ? "стрейтч-страница"
                        : o.category === "schema"
                        ? "schema.org"
                        : o.category === "contact"
                        ? "контакты"
                        : "элемент сайта"}
                    </Badge>
                  </div>
                  <h3 className="font-semibold leading-snug">{o.title_ru}</h3>
                  <p className="text-sm text-muted-foreground leading-snug">{o.reasoning_ru}</p>
                  <p className="text-sm leading-snug">{o.suggested_action_ru}</p>

                  {o.evidence?.matched_page && (
                    <a
                      href={o.evidence.matched_page.url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                    >
                      <ExternalLink className="h-3 w-3" />
                      Твоя страница: {o.evidence.matched_page.path || o.evidence.matched_page.url}
                      {" "}· совпадение {Math.round((o.evidence.matched_page.score ?? 0) * 100)}%
                    </a>
                  )}

                  {o.source === "content_gap" && o.evidence?.queries && (
                    <details className="text-xs">
                      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                        Запросы в кластере ({o.evidence.queries.length})
                      </summary>
                      <ul className="mt-2 space-y-0.5 pl-4 text-muted-foreground">
                        {o.evidence.queries.map((q: string, i: number) => (
                          <li key={i} className="font-mono">· {q}</li>
                        ))}
                      </ul>
                      {o.evidence.competitor_url && (
                        <a
                          href={o.evidence.competitor_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 mt-2 text-primary hover:underline"
                        >
                          <ExternalLink className="h-3 w-3" /> пример страницы
                          у {o.evidence.competitor_domain}
                        </a>
                      )}
                    </details>
                  )}

                  {(o.source === "feature_diff" || o.source === "schema_diff") &&
                    Array.isArray(o.evidence?.competitors_with) && (
                      <div className="text-xs text-muted-foreground">
                        Есть у: {o.evidence.competitors_with.join(", ")}
                      </div>
                    )}

                  {(() => {
                    const out = outcomeByRecId.get(o.id);
                    if (!out) {
                      return (
                        <div className="pt-1">
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => markOppApplied(o)}
                          >
                            <CheckCircle2 className="mr-2 h-4 w-4" /> Отметить «применил»
                          </Button>
                        </div>
                      );
                    }
                    // Backend ts is naive UTC — force UTC parse.
                    const _ap = out.applied_at;
                    const applied = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(_ap) ? _ap : _ap + "Z");
                    const daysAgo = Math.floor((Date.now() - applied.getTime()) / 86400000);
                    if (!out.followup_at) {
                      const daysLeft = Math.max(0, 14 - daysAgo);
                      return (
                        <div className="rounded bg-emerald-50 border border-emerald-200 px-3 py-2 text-xs text-emerald-900">
                          <CheckCircle2 className="inline h-3.5 w-3.5 mr-1" />
                          Применено {daysAgo} д назад ·
                          {" "}{daysLeft > 0 ? `через ${daysLeft} д посчитаем эффект` : "эффект замерим сегодня"}
                        </div>
                      );
                    }
                    const d = out.delta || {};
                    const impPct = d.impressions_pct;
                    const clkPct = d.clicks_pct;
                    const fmt = (v: number | null | undefined) =>
                      v == null ? "н/д" : `${v >= 0 ? "+" : ""}${v}%`;
                    const tone =
                      (impPct ?? 0) > 5
                        ? "bg-emerald-50 border-emerald-200 text-emerald-900"
                        : (impPct ?? 0) < -5
                        ? "bg-rose-50 border-rose-200 text-rose-900"
                        : "bg-slate-50 border-slate-200 text-slate-800";
                    return (
                      <div className={cn("rounded border px-3 py-2 text-xs", tone)}>
                        <b>Итог 14 дней:</b> показы {fmt(impPct)}, клики {fmt(clkPct)}
                        {d.position_delta != null && (
                          <> · позиция {d.position_delta > 0 ? "улучшилась" : "ухудшилась"} на {Math.abs(d.position_delta)}</>
                        )}
                      </div>
                    );
                  })()}
                </li>
              ))}
            </ul>
          )}
        </TabsContent>

        {/* LIST TAB */}
        <TabsContent value="list" className="mt-4">
          {listSWR.isLoading ? (
            <div className="space-y-2">{[...Array(5)].map((_, i) => <Skeleton key={i} className="h-20" />)}</div>
          ) : competitors.length === 0 ? (
            <Card>
              <CardContent className="py-10 text-center text-sm text-muted-foreground">
                Конкурентов ещё не искал. Нажми «Пересобрать список».
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-2">
              <div className="text-xs text-muted-foreground">
                Проверил {list?.profile?.queries_probed} запросов · нашёл {competitors.length} доменов ·
                стоимость: ${Number(list?.profile?.cost_usd ?? 0).toFixed(3)}
              </div>
              <ul className="space-y-2">
                {competitors.map((c) => (
                  <li key={c.domain} className="rounded-lg border p-3 space-y-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <a href={`https://${c.domain}`} target="_blank" rel="noreferrer" className="font-semibold hover:underline">
                        {c.domain}
                      </a>
                      <Badge variant="outline" className="text-[10px]">
                        {c.serp_hits}× в выдаче
                      </Badge>
                      <Badge variant="outline" className="text-[10px]">
                        ср. {c.avg_position.toFixed(1)} · лучшая {c.best_position}
                      </Badge>
                      {c.best_position <= 3 && (
                        <Badge className="text-[10px] bg-amber-500/90">топ-3</Badge>
                      )}
                    </div>
                    <p className="text-sm leading-snug">{c.example_title}</p>
                    <div className="text-[11px] text-muted-foreground flex items-center gap-1">
                      <Search className="h-3 w-3" />
                      <span className="font-mono">{c.example_query}</span>
                      <a href={c.example_url} target="_blank" rel="noreferrer" className="ml-1">
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </TabsContent>

        {/* GAPS TAB */}
        <TabsContent value="gaps" className="mt-4">
          {gapsSWR.isLoading ? (
            <div className="space-y-2">{[...Array(5)].map((_, i) => <Skeleton key={i} className="h-16" />)}</div>
          ) : gaps.length === 0 ? (
            <Card>
              <CardContent className="py-10 text-center text-sm text-muted-foreground">
                {gapsSWR.data?.note || "Пробелов нет по текущим данным. Пересобери список или проверь подтверждённых конкурентов."}
              </CardContent>
            </Card>
          ) : (
            <>
              <p className="text-sm text-muted-foreground mb-3">
                Запросы, по которым конкуренты в топ-5, а ты не в топ-30 или отсутствуешь.
                Это темы для новых страниц или жёсткого усиления.
              </p>
              <ul className="space-y-2">
                {gaps.map((g, i) => (
                  <li key={i} className="rounded-lg border p-3 space-y-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <TrendingDown className="h-4 w-4 text-rose-600" />
                      <span className="font-mono text-sm font-medium">{g.query}</span>
                      <Badge variant="outline" className="text-[10px]">
                        ты: {g.site_position ?? "нет в топ-100"}
                      </Badge>
                    </div>
                    <div className="text-xs leading-snug pl-6">
                      <span className="text-muted-foreground">конкурент: </span>
                      <a href={g.competitor_url} target="_blank" rel="noreferrer" className="font-semibold hover:underline">
                        {g.competitor_domain}
                      </a>
                      {" "}на позиции <b>{g.competitor_position}</b>
                      {g.other_competitors.length > 1 && (
                        <span className="text-muted-foreground">
                          {" "}+ ещё {g.other_competitors.length - 1} в топ-10
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground pl-6 line-clamp-2">
                      «{g.competitor_title}»
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </TabsContent>

        {/* DEEP DIVE TAB */}
        <TabsContent value="dive" className="mt-4">
          {diveSWR.isLoading ? (
            <Skeleton className="h-48" />
          ) : !dive || dive.competitors.length === 0 ? (
            <Card>
              <CardContent className="py-10 text-center text-sm text-muted-foreground">
                Глубокого анализа ещё не было. Нажми «Глубокий анализ» выше —
                я обойду топ-5 конкурентов и сравню ключевые признаки с твоим сайтом.
              </CardContent>
            </Card>
          ) : (
            <DeepDiveComparison dive={dive} />
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

function DeepDiveComparison({ dive }: { dive: any }) {
  const self = dive.self || {};
  const rows = [
    { key: "has_price",       label: "Цены на сайте" },
    { key: "has_booking_cta", label: "Кнопка брони/заявки" },
    { key: "has_reviews",     label: "Отзывы / рейтинг" },
    { key: "has_phone",       label: "Телефон" },
    { key: "has_telegram",    label: "Telegram" },
    { key: "has_whatsapp",    label: "WhatsApp" },
  ] as const;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Ты vs конкуренты — структурный чек</CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b">
              <th className="text-left py-2 pr-4 font-medium">Признак</th>
              <th className="text-center py-2 px-2 font-medium">Ты</th>
              {dive.competitors.map((c: any) => (
                <th key={c.domain} className="text-center py-2 px-2 font-medium">
                  <a href={`https://${c.domain}`} target="_blank" rel="noreferrer" className="hover:underline">
                    {c.domain}
                  </a>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-b last:border-0">
                <td className="py-2 pr-4">{row.label}</td>
                <td className="text-center py-2 px-2">
                  <YesNo ok={!!self[row.key]} />
                </td>
                {dive.competitors.map((c: any) => (
                  <td key={c.domain} className="text-center py-2 px-2">
                    <YesNo ok={!!c[row.key]} />
                  </td>
                ))}
              </tr>
            ))}
            <tr>
              <td className="pt-3 pr-4 text-xs text-muted-foreground">Schema.org</td>
              <td className="text-center pt-3 px-2 text-xs text-muted-foreground">
                {(self.schema_types || []).join(", ") || "—"}
              </td>
              {dive.competitors.map((c: any) => (
                <td key={c.domain} className="text-center pt-3 px-2 text-xs text-muted-foreground">
                  {(c.schema_types || []).join(", ") || "—"}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
        <p className="mt-4 text-xs text-muted-foreground">
          Красные клетки — у конкурента есть, у тебя нет. Это прямые точки роста.
        </p>
      </CardContent>
    </Card>
  );
}

function YesNo({ ok }: { ok: boolean }) {
  return ok ? (
    <Check className="h-4 w-4 text-emerald-600 inline" />
  ) : (
    <X className="h-4 w-4 text-rose-500 inline" />
  );
}
