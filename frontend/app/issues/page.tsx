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
import {
  Select, SelectContent, SelectItem,
  SelectTrigger, SelectValue,
} from "@/components/ui/select";

const SEVERITY_COLOR: Record<string, string> = {
  critical: "destructive",
  high: "destructive",
  medium: "secondary",
  low: "outline",
  info: "outline",
};

const STATUS_LABEL: Record<string, string> = {
  open: "Открыта",
  review: "На проверке",
  acknowledged: "Принято",
  in_progress: "В работе",
  resolved: "Решена",
  false_positive: "Ложная",
  suppressed: "Скрыта",
};

export default function IssuesPage() {
  const siteId = useCurrentSiteId();
  const [status, setStatus] = useState<string>("all");
  const [severity, setSeverity] = useState<string>("all");

  const params: Record<string, string> = {};
  if (status !== "all") params.status = status;
  if (severity !== "all") params.severity = severity;

  const { data, isLoading, mutate } = useSWR(
    `issues-${siteId}-${status}-${severity}`,
    () => api.issues(siteId, params),
    { refreshInterval: 30_000 }
  );

  const [updating, setUpdating] = useState<string | null>(null);

  async function changeStatus(issueId: string, newStatus: string) {
    setUpdating(issueId);
    try {
      await api.updateIssue(siteId, issueId, { status: newStatus });
      mutate();
    } finally {
      setUpdating(null);
    }
  }

  const items: any[] = data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Проблемы</h1>
        <span className="text-sm text-muted-foreground">
          {data?.total ?? 0} найдено
        </span>
      </div>

      {/* Filters */}
      <div className="flex gap-3">
        <Select value={status} onValueChange={(v) => setStatus(v ?? "all")}>
          <SelectTrigger className="w-44">
            <SelectValue placeholder="Статус" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все статусы</SelectItem>
            <SelectItem value="open">Открытые</SelectItem>
            <SelectItem value="review">На проверке</SelectItem>
            <SelectItem value="resolved">Решённые</SelectItem>
            <SelectItem value="false_positive">Ложные</SelectItem>
            <SelectItem value="suppressed">Скрытые</SelectItem>
          </SelectContent>
        </Select>

        <Select value={severity} onValueChange={(v) => setSeverity(v ?? "all")}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="Критичность" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все</SelectItem>
            <SelectItem value="critical">Критические</SelectItem>
            <SelectItem value="high">Высокие</SelectItem>
            <SelectItem value="medium">Средние</SelectItem>
            <SelectItem value="low">Низкие</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Table */}
      <Card>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="p-6 space-y-3">
              {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-12" />)}
            </div>
          ) : items.length === 0 ? (
            <div className="flex items-center justify-center h-40 text-muted-foreground text-sm">
              Проблем не найдено
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Агент</TableHead>
                  <TableHead>Проблема</TableHead>
                  <TableHead>Критичность</TableHead>
                  <TableHead className="text-right">Уверенность</TableHead>
                  <TableHead>Статус</TableHead>
                  <TableHead>Действия</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((issue: any) => (
                  <TableRow key={issue.id}>
                    <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                      {issue.agent_name}
                    </TableCell>
                    <TableCell>
                      <div className="font-medium text-sm">{issue.title}</div>
                      {issue.recommendation && (
                        <div className="text-xs text-muted-foreground mt-0.5 line-clamp-1">
                          {issue.recommendation}
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant={SEVERITY_COLOR[issue.severity] as any}>
                        {issue.severity}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right text-sm">
                      {Math.round(issue.confidence * 100)}%
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{STATUS_LABEL[issue.status] ?? issue.status}</Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        {issue.status === "open" && (
                          <Button
                            size="sm" variant="outline"
                            className="text-xs h-7"
                            disabled={updating === issue.id}
                            onClick={() => changeStatus(issue.id, "acknowledged")}
                          >
                            Принять
                          </Button>
                        )}
                        {["open", "acknowledged", "in_progress"].includes(issue.status) && (
                          <Button
                            size="sm" variant="outline"
                            className="text-xs h-7"
                            disabled={updating === issue.id}
                            onClick={() => changeStatus(issue.id, "resolved")}
                          >
                            Решено
                          </Button>
                        )}
                        {issue.status !== "false_positive" && (
                          <Button
                            size="sm" variant="ghost"
                            className="text-xs h-7 text-muted-foreground"
                            disabled={updating === issue.id}
                            onClick={() => changeStatus(issue.id, "false_positive")}
                          >
                            Ложная
                          </Button>
                        )}
                      </div>
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
