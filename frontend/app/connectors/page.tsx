"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Activity, CheckCircle2, XCircle, AlertTriangle,
  ChevronDown, ChevronRight, RefreshCw, Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Connector status board.
 *
 * Lists every external integration we depend on — LLM, Yandex Cloud,
 * Yandex OAuth services, infrastructure, protocols. Each row has an
 * on-demand "test now" button that hits the real endpoint and renders
 * `sample_data` so the owner sees the proof of a working integration,
 * not just a green dot.
 *
 * This page is the single source of truth for "is the platform really
 * connected to what it claims to be connected to?" — the question we
 * otherwise answered only by customer complaints.
 */

type Connector = {
  id: string;
  category: string;
  name: string;
  description_ru: string;
  configured: boolean;
  missing_setting: string | null;
};

type CheckResult = {
  id: string;
  name: string;
  category: string;
  ok: boolean;
  latency_ms: number;
  sample_data: Record<string, unknown> | null;
  error: string | null;
  checked_at: string;
};

const CATEGORY_LABEL: Record<string, string> = {
  infra: "Инфраструктура",
  llm: "LLM-модели",
  yandex_cloud: "Yandex Cloud (AI Studio)",
  yandex_oauth: "Yandex — OAuth-сервисы",
  protocol: "Протоколы",
};

const CATEGORY_ORDER = ["infra", "llm", "yandex_cloud", "yandex_oauth", "protocol"];

function StatusDot({ state }: { state: "ok" | "fail" | "unchecked" | "not_configured" | "running" }) {
  if (state === "running") {
    return <RefreshCw className="h-4 w-4 animate-spin text-muted-foreground" />;
  }
  if (state === "ok") {
    return <CheckCircle2 className="h-4 w-4 text-emerald-600" />;
  }
  if (state === "fail") {
    return <XCircle className="h-4 w-4 text-red-600" />;
  }
  if (state === "not_configured") {
    return <AlertTriangle className="h-4 w-4 text-amber-600" />;
  }
  return <Activity className="h-4 w-4 text-muted-foreground opacity-40" />;
}

function formatSampleData(data: Record<string, unknown> | null): string {
  if (!data) return "—";
  try {
    return JSON.stringify(data, null, 2);
  } catch {
    return String(data);
  }
}

function timeAgo(iso: string | undefined | null): string {
  if (!iso) return "никогда";
  const utcIso = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z";
  const then = new Date(utcIso).getTime();
  const sec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (sec < 5) return "только что";
  if (sec < 60) return `${sec} сек назад`;
  if (sec < 3600) return `${Math.floor(sec / 60)} мин назад`;
  if (sec < 86_400) return `${Math.floor(sec / 3600)} ч назад`;
  return `${Math.floor(sec / 86_400)} д назад`;
}

export default function ConnectorsPage() {
  const { data: listing, error: listErr, isLoading: listLoading } = useSWR(
    "connectors-list",
    () => api.listConnectors(),
  );

  const [results, setResults] = useState<Record<string, CheckResult>>({});
  const [running, setRunning] = useState<Record<string, boolean>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [testingAll, setTestingAll] = useState(false);
  const [globalError, setGlobalError] = useState<string | null>(null);

  // Run a single check on demand
  async function testOne(id: string) {
    setRunning((r) => ({ ...r, [id]: true }));
    setGlobalError(null);
    try {
      const r = await api.testConnector(id);
      setResults((prev) => ({ ...prev, [id]: r }));
      // Auto-expand failed rows so the error is visible
      if (!r.ok) setExpanded((e) => ({ ...e, [id]: true }));
    } catch (e: unknown) {
      setGlobalError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning((r) => ({ ...r, [id]: false }));
    }
  }

  async function testAll() {
    setTestingAll(true);
    setGlobalError(null);
    try {
      const all = await api.testAllConnectors();
      const byId: Record<string, CheckResult> = {};
      for (const r of all.results) byId[r.id] = r;
      setResults(byId);
      // Expand any failures so owner sees them immediately
      const fails: Record<string, boolean> = {};
      for (const r of all.results) if (!r.ok) fails[r.id] = true;
      setExpanded(fails);
    } catch (e: unknown) {
      setGlobalError(e instanceof Error ? e.message : String(e));
    } finally {
      setTestingAll(false);
    }
  }

  // Auto-run a full check on first page view so user sees real state
  // immediately, not a blank "unchecked" table.
  useEffect(() => {
    if (!listing || testingAll || Object.keys(results).length > 0) return;
    void testAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listing]);

  const grouped = useMemo(() => {
    const map: Record<string, Connector[]> = {};
    for (const c of listing?.connectors ?? []) {
      (map[c.category] ??= []).push(c);
    }
    return map;
  }, [listing]);

  const totals = useMemo(() => {
    const list = Object.values(results);
    return {
      ok: list.filter((r) => r.ok).length,
      fail: list.filter((r) => !r.ok).length,
      total: listing?.count ?? 0,
    };
  }, [results, listing]);

  if (listLoading) {
    return (
      <div className="p-6 space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (listErr) {
    return (
      <div className="p-6">
        <Card>
          <CardContent className="pt-6 text-red-800">
            Не удалось получить список коннекторов: {String(listErr)}
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-5 max-w-6xl">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">Коннекторы</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Реальный статус всех внешних сервисов. Каждая проверка — живой
            запрос к endpoint, не симуляция.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {totals.total > 0 && (
            <div className="text-sm">
              <Badge variant="outline" className="bg-emerald-50 text-emerald-900 border-emerald-300">
                {totals.ok} работает
              </Badge>{" "}
              {totals.fail > 0 && (
                <Badge variant="outline" className="bg-red-50 text-red-900 border-red-300 ml-1">
                  {totals.fail} с ошибкой
                </Badge>
              )}
              <span className="ml-2 text-muted-foreground">
                из {totals.total}
              </span>
            </div>
          )}
          <Button onClick={testAll} disabled={testingAll} size="sm">
            {testingAll ? (
              <>
                <RefreshCw className="h-4 w-4 mr-2 animate-spin" />
                Проверяю все…
              </>
            ) : (
              <>
                <Zap className="h-4 w-4 mr-2" />
                Проверить все
              </>
            )}
          </Button>
        </div>
      </div>

      {globalError && (
        <div className="rounded border border-red-300 bg-red-50 text-red-900 px-3 py-2 text-sm">
          {globalError}
        </div>
      )}

      {CATEGORY_ORDER.filter((cat) => grouped[cat]?.length).map((cat) => (
        <Card key={cat}>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">
              {CATEGORY_LABEL[cat] ?? cat}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <div className="divide-y">
              {grouped[cat].map((c) => {
                const result = results[c.id];
                const isRunning = running[c.id];
                const isExpanded = expanded[c.id];
                const state: "ok" | "fail" | "unchecked" | "not_configured" | "running" =
                  isRunning ? "running"
                  : !c.configured ? "not_configured"
                  : result ? (result.ok ? "ok" : "fail")
                  : "unchecked";

                return (
                  <div key={c.id}>
                    <div
                      className="flex items-center gap-3 px-4 py-3 hover:bg-muted/50 cursor-pointer"
                      onClick={() =>
                        setExpanded((e) => ({ ...e, [c.id]: !isExpanded }))
                      }
                    >
                      <StatusDot state={state} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-medium text-sm">{c.name}</span>
                          <code className="text-[10px] text-muted-foreground">
                            {c.id}
                          </code>
                          {!c.configured && (
                            <Badge variant="outline" className="text-[10px] bg-amber-50 border-amber-300 text-amber-900">
                              не настроено
                            </Badge>
                          )}
                          {result && result.ok && (
                            <Badge variant="outline" className="text-[10px] bg-emerald-50 border-emerald-300 text-emerald-800">
                              {result.latency_ms} мс
                            </Badge>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5">
                          {c.description_ru}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        {result && (
                          <span className="text-[11px] text-muted-foreground whitespace-nowrap">
                            {timeAgo(result.checked_at)}
                          </span>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={(e) => {
                            e.stopPropagation();
                            void testOne(c.id);
                          }}
                          disabled={isRunning}
                        >
                          {isRunning ? "…" : "Проверить"}
                        </Button>
                        {isExpanded ? (
                          <ChevronDown className="h-4 w-4 text-muted-foreground" />
                        ) : (
                          <ChevronRight className="h-4 w-4 text-muted-foreground" />
                        )}
                      </div>
                    </div>

                    {isExpanded && (
                      <div className="bg-muted/30 px-4 py-3 border-t text-xs space-y-2">
                        {!c.configured && (
                          <div className="text-amber-900">
                            Требуется заполнить: <code>{c.missing_setting}</code>
                          </div>
                        )}
                        {result ? (
                          <>
                            {result.error && (
                              <div className="text-red-900">
                                <span className="font-semibold">Ошибка: </span>
                                <code className="break-all">{result.error}</code>
                              </div>
                            )}
                            {result.sample_data && (
                              <div>
                                <div className="text-muted-foreground mb-1">
                                  Ответ сервиса (подтверждение реального соединения):
                                </div>
                                <pre className="bg-background rounded border p-2 overflow-x-auto text-[11px]">
                                  {formatSampleData(result.sample_data)}
                                </pre>
                              </div>
                            )}
                            <div className="text-muted-foreground">
                              Проверено {timeAgo(result.checked_at)} · задержка {result.latency_ms} мс
                            </div>
                          </>
                        ) : (
                          <div className="text-muted-foreground italic">
                            Ещё не проверяли. Нажми «Проверить».
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
