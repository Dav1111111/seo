"use client";

import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useCurrentSiteId } from "@/lib/site-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table, TableBody, TableCell, TableHead,
  TableHeader, TableRow,
} from "@/components/ui/table";
import { Play, RefreshCw } from "lucide-react";

const STATUS_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  completed: "default",
  running: "secondary",
  failed: "destructive",
  pending: "outline",
};

export default function PipelinePage() {
  const siteId = useCurrentSiteId();
  const { data, isLoading, mutate } = useSWR(
    `runs-${siteId}`,
    () => api.agentRuns(siteId, 30),
    { refreshInterval: 15_000 }
  );

  const [triggering, setTriggering] = useState<string | null>(null);

  async function trigger(action: string, fn: () => Promise<any>) {
    setTriggering(action);
    try {
      await fn();
      setTimeout(() => mutate(), 2000);
    } catch (e) {
      console.error(e);
    } finally {
      setTriggering(null);
    }
  }

  const runs: any[] = data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Pipeline</h1>
        {data?.total_cost_usd != null && (
          <span className="text-sm text-muted-foreground">
            Всего потрачено: <span className="font-medium">${data.total_cost_usd.toFixed(4)}</span>
          </span>
        )}
      </div>

      {/* Action buttons */}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={triggering !== null}
          onClick={() => trigger("pipeline", () => api.triggerPipeline(siteId))}
        >
          <Play className="mr-2 h-4 w-4" />
          {triggering === "pipeline" ? "Запускаю..." : "Полный пайплайн"}
        </Button>
        <Button
          size="sm" variant="outline"
          disabled={triggering !== null}
          onClick={() => trigger("collect", () => api.triggerCollect(siteId))}
        >
          <RefreshCw className="mr-2 h-4 w-4" />
          Собрать данные
        </Button>
        <Button
          size="sm" variant="outline"
          disabled={triggering !== null}
          onClick={() => trigger("visibility", () => api.triggerAgent(siteId, "search_visibility"))}
        >
          Search Visibility
        </Button>
        <Button
          size="sm" variant="outline"
          disabled={triggering !== null}
          onClick={() => trigger("indexing", () => api.triggerAgent(siteId, "technical_indexing"))}
        >
          Technical Indexing
        </Button>
      </div>

      {/* Runs table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">История запусков</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="p-6 space-y-3">
              {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-10" />)}
            </div>
          ) : runs.length === 0 ? (
            <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">
              Запусков ещё не было. Нажмите «Полный пайплайн».
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Агент</TableHead>
                  <TableHead>Модель</TableHead>
                  <TableHead>Статус</TableHead>
                  <TableHead className="text-right">Токены</TableHead>
                  <TableHead className="text-right">Стоимость</TableHead>
                  <TableHead className="text-right">Время</TableHead>
                  <TableHead>Запущен</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.map((r: any) => (
                  <TableRow key={r.id}>
                    <TableCell className="font-medium text-sm">{r.agent_name}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {r.model_used?.includes("haiku") ? "Haiku" : r.model_used?.includes("sonnet") ? "Sonnet" : r.model_used}
                    </TableCell>
                    <TableCell>
                      <Badge variant={STATUS_VARIANT[r.status] ?? "outline"}>
                        {r.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right text-xs">
                      {r.input_tokens + r.output_tokens}
                    </TableCell>
                    <TableCell className="text-right text-xs">
                      ${(r.cost_usd ?? 0).toFixed(5)}
                    </TableCell>
                    <TableCell className="text-right text-xs">
                      {r.duration_ms ? `${(r.duration_ms / 1000).toFixed(1)}s` : "—"}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {r.started_at ? new Date(r.started_at).toLocaleString("ru") : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
