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
  Select, SelectContent, SelectItem,
  SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { IssueDetailDialog } from "@/components/issues/issue-detail";
import {
  AlertTriangle, AlertCircle, Info, ChevronRight,
  Brain, Calendar,
} from "lucide-react";

const SEVERITY_COLOR: Record<string, string> = {
  critical: "destructive",
  high: "destructive",
  medium: "secondary",
  low: "outline",
  info: "outline",
};

const SEVERITY_LABEL: Record<string, string> = {
  critical: "Критическая",
  high: "Высокая",
  medium: "Средняя",
  low: "Низкая",
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

const SEVERITY_ICON: Record<string, typeof AlertTriangle> = {
  critical: AlertTriangle,
  high: AlertTriangle,
  medium: AlertCircle,
  low: Info,
};

export default function IssuesPage() {
  const siteId = useCurrentSiteId();
  const [status, setStatus] = useState<string>("all");
  const [severity, setSeverity] = useState<string>("all");
  const [selectedIssue, setSelectedIssue] = useState<any | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

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
      // Update selected issue in dialog too
      if (selectedIssue?.id === issueId) {
        setSelectedIssue((prev: any) => prev ? { ...prev, status: newStatus } : null);
      }
    } finally {
      setUpdating(null);
    }
  }

  function openIssue(issue: any) {
    setSelectedIssue(issue);
    setDialogOpen(true);
  }

  const items: any[] = data?.items ?? [];

  // Group by status for summary
  const openCount = items.filter((i) => i.status === "open").length;
  const reviewCount = items.filter((i) => i.status === "review").length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Проблемы</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            {data?.total ?? 0} найдено
            {openCount > 0 && <span className="text-destructive"> &middot; {openCount} открытых</span>}
            {reviewCount > 0 && <span> &middot; {reviewCount} на проверке</span>}
          </p>
        </div>
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

      {/* Issue Cards */}
      {isLoading ? (
        <div className="space-y-3">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-28 rounded-xl" />)}
        </div>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="flex items-center justify-center h-40 text-muted-foreground text-sm">
            Проблем не найдено
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {items.map((issue: any) => {
            const Icon = SEVERITY_ICON[issue.severity] ?? Info;
            const isHighSeverity = issue.severity === "critical" || issue.severity === "high";

            return (
              <Card
                key={issue.id}
                className={`cursor-pointer transition-all hover:shadow-md hover:border-primary/20 ${
                  isHighSeverity && issue.status === "open" ? "border-l-2 border-l-destructive" : ""
                }`}
                onClick={() => openIssue(issue)}
              >
                <CardContent className="p-4">
                  <div className="flex items-start gap-3">
                    {/* Icon */}
                    <div className={`mt-0.5 shrink-0 ${isHighSeverity ? "text-destructive" : "text-muted-foreground"}`}>
                      <Icon className="h-4 w-4" />
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      {/* Title row */}
                      <div className="flex items-start justify-between gap-2">
                        <h3 className="font-medium text-sm leading-snug">{issue.title}</h3>
                        <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0 mt-0.5" />
                      </div>

                      {/* Description preview */}
                      {issue.description && (
                        <p className="text-xs text-muted-foreground mt-1 line-clamp-2 leading-relaxed">
                          {issue.description}
                        </p>
                      )}

                      {/* Recommendation preview */}
                      {issue.recommendation && (
                        <p className="text-xs text-muted-foreground/70 mt-1 line-clamp-1 italic">
                          Рекомендация: {issue.recommendation}
                        </p>
                      )}

                      {/* Meta row */}
                      <div className="flex items-center gap-2 mt-2 flex-wrap">
                        <Badge variant={SEVERITY_COLOR[issue.severity] as any} className="text-[10px]">
                          {SEVERITY_LABEL[issue.severity] ?? issue.severity}
                        </Badge>
                        <Badge variant="outline" className="text-[10px]">
                          {STATUS_LABEL[issue.status] ?? issue.status}
                        </Badge>
                        <span className="text-[10px] text-muted-foreground flex items-center gap-0.5">
                          <Brain className="h-2.5 w-2.5" />
                          {Math.round(issue.confidence * 100)}%
                        </span>
                        <span className="text-[10px] text-muted-foreground">
                          {issue.agent_name}
                        </span>
                        {issue.created_at && (
                          <span className="text-[10px] text-muted-foreground flex items-center gap-0.5">
                            <Calendar className="h-2.5 w-2.5" />
                            {new Date(issue.created_at).toLocaleDateString("ru")}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Quick actions (stop propagation to not open dialog) */}
                  <div className="flex gap-1 mt-2 ml-7" onClick={(e) => e.stopPropagation()}>
                    {issue.status === "open" && (
                      <Button size="xs" variant="outline" className="text-[10px] h-6"
                        disabled={updating === issue.id}
                        onClick={() => changeStatus(issue.id, "acknowledged")}>
                        Принять
                      </Button>
                    )}
                    {["open", "acknowledged", "in_progress"].includes(issue.status) && (
                      <Button size="xs" variant="outline" className="text-[10px] h-6"
                        disabled={updating === issue.id}
                        onClick={() => changeStatus(issue.id, "resolved")}>
                        Решено
                      </Button>
                    )}
                    {issue.status !== "false_positive" && (
                      <Button size="xs" variant="ghost" className="text-[10px] h-6 text-muted-foreground"
                        disabled={updating === issue.id}
                        onClick={() => changeStatus(issue.id, "false_positive")}>
                        Ложная
                      </Button>
                    )}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {/* Detail Dialog */}
      <IssueDetailDialog
        issue={selectedIssue}
        open={dialogOpen}
        onClose={() => {
          setDialogOpen(false);
          mutate(); // refresh list after potential changes
        }}
        onStatusChange={changeStatus}
      />
    </div>
  );
}
