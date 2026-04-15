"use client";

import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useCurrentSiteId } from "@/lib/site-context";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table, TableBody, TableCell, TableHead,
  TableHeader, TableRow,
} from "@/components/ui/table";
import { QueryDetailDialog } from "@/components/queries/query-detail";
import {
  Search, TrendingUp, TrendingDown, Minus,
  ArrowUpDown, ChevronLeft, ChevronRight,
} from "lucide-react";

const PAGE_SIZE = 50;

function PositionDelta({ delta }: { delta: number | null }) {
  if (delta === null || delta === undefined) return <span className="text-muted-foreground">—</span>;
  if (Math.abs(delta) < 0.3) return <Minus className="h-3 w-3 text-muted-foreground inline" />;
  if (delta > 0) {
    return (
      <span className="text-green-600 flex items-center gap-0.5 text-xs font-medium">
        <TrendingUp className="h-3 w-3" />+{delta.toFixed(1)}
      </span>
    );
  }
  return (
    <span className="text-red-500 flex items-center gap-0.5 text-xs font-medium">
      <TrendingDown className="h-3 w-3" />{delta.toFixed(1)}
    </span>
  );
}

function PctChange({ value }: { value: number | null }) {
  if (value === null || value === undefined) return <span className="text-muted-foreground text-[10px]">—</span>;
  const color = value > 0 ? "text-green-600" : value < 0 ? "text-red-500" : "text-muted-foreground";
  return <span className={`text-[10px] ${color}`}>{value > 0 ? "+" : ""}{value}%</span>;
}

export function QueryTable() {
  const siteId = useCurrentSiteId();
  const [sortBy, setSortBy] = useState("impressions");
  const [sortDir, setSortDir] = useState("desc");
  const [searchText, setSearchText] = useState("");
  const [cluster, setCluster] = useState("");
  const [page, setPage] = useState(0);
  const [selectedQuery, setSelectedQuery] = useState<any | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  const params: Record<string, string | number> = {
    sort_by: sortBy,
    sort_dir: sortDir,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  if (searchText) params.search = searchText;
  if (cluster) params.cluster = cluster;

  const { data, isLoading } = useSWR(
    `queries-${siteId}-${sortBy}-${sortDir}-${searchText}-${cluster}-${page}`,
    () => api.queries(siteId, params),
    { refreshInterval: 60_000 }
  );

  const items: any[] = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  function toggleSort(col: string) {
    if (sortBy === col) {
      setSortDir(sortDir === "desc" ? "asc" : "desc");
    } else {
      setSortBy(col);
      setSortDir("desc");
    }
    setPage(0);
  }

  function SortHeader({ col, children }: { col: string; children: React.ReactNode }) {
    const active = sortBy === col;
    return (
      <TableHead
        className="cursor-pointer select-none hover:text-foreground transition-colors"
        onClick={() => toggleSort(col)}
      >
        <span className="flex items-center gap-1">
          {children}
          {active && <ArrowUpDown className="h-3 w-3" />}
        </span>
      </TableHead>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Запросы</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            {total} запросов найдено
          </p>
        </div>
      </div>

      {/* Search */}
      <div className="flex gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <input
            type="text"
            value={searchText}
            onChange={(e) => { setSearchText(e.target.value); setPage(0); }}
            placeholder="Поиск по запросам..."
            className="w-full rounded-md border bg-background px-8 py-2 text-sm outline-none focus:ring-1 focus:ring-primary placeholder:text-muted-foreground"
          />
        </div>
        {cluster && (
          <Button variant="outline" size="sm" onClick={() => { setCluster(""); setPage(0); }}>
            Кластер: {cluster} ✕
          </Button>
        )}
      </div>

      {/* Table */}
      <Card>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="p-6 space-y-3">
              {[...Array(8)].map((_, i) => <Skeleton key={i} className="h-10" />)}
            </div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 text-muted-foreground text-sm gap-2">
              <Search className="h-8 w-8 opacity-30" />
              <p>Запросов не найдено</p>
              <p className="text-xs">Запустите сбор данных через Pipeline</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <SortHeader col="query">Запрос</SortHeader>
                  <SortHeader col="position">Позиция</SortHeader>
                  <TableHead className="text-center text-xs">Изменение</TableHead>
                  <SortHeader col="impressions">Показы</SortHeader>
                  <SortHeader col="clicks">Клики</SortHeader>
                  <TableHead className="text-right text-xs">CTR</TableHead>
                  <SortHeader col="volume">Объём</SortHeader>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((q: any) => (
                  <TableRow
                    key={q.id}
                    className="cursor-pointer hover:bg-muted/50"
                    onClick={() => { setSelectedQuery(q); setDialogOpen(true); }}
                  >
                    <TableCell className="max-w-xs">
                      <div className="font-medium text-sm truncate">{q.query_text}</div>
                      {q.cluster && (
                        <button
                          className="text-[10px] text-primary hover:underline mt-0.5"
                          onClick={(e) => { e.stopPropagation(); setCluster(q.cluster); setPage(0); }}
                        >
                          {q.cluster}
                        </button>
                      )}
                    </TableCell>
                    <TableCell className="text-sm font-medium tabular-nums">
                      {q.current.avg_position != null
                        ? q.current.avg_position.toFixed(1)
                        : "—"}
                    </TableCell>
                    <TableCell className="text-center">
                      <PositionDelta delta={q.changes.position_delta} />
                    </TableCell>
                    <TableCell className="tabular-nums text-sm">
                      <div>{q.current.impressions}</div>
                      <PctChange value={q.changes.impressions_pct} />
                    </TableCell>
                    <TableCell className="tabular-nums text-sm">
                      <div>{q.current.clicks}</div>
                      <PctChange value={q.changes.clicks_pct} />
                    </TableCell>
                    <TableCell className="text-right text-sm tabular-nums text-muted-foreground">
                      {q.current.impressions > 0
                        ? (q.current.ctr * 100).toFixed(1) + "%"
                        : "—"}
                    </TableCell>
                    <TableCell className="text-sm tabular-nums text-muted-foreground">
                      {q.wordstat_volume != null ? q.wordstat_volume.toLocaleString("ru") : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} из {total}
          </span>
          <div className="flex gap-1">
            <Button
              size="sm" variant="outline"
              disabled={page === 0}
              onClick={() => setPage(page - 1)}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button
              size="sm" variant="outline"
              disabled={page >= totalPages - 1}
              onClick={() => setPage(page + 1)}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}

      {/* Detail Dialog */}
      <QueryDetailDialog
        query={selectedQuery}
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
      />
    </div>
  );
}
