"use client";

import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useCurrentSiteId } from "@/lib/site-context";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { TaskDetailDialog } from "@/components/tasks/task-detail";
import {
  CheckSquare, Sparkles, Loader2, ChevronRight,
  Zap, Target, AlertCircle,
} from "lucide-react";

const STATUS_LABEL: Record<string, string> = {
  backlog: "В очереди",
  planned: "Запланирована",
  in_progress: "В работе",
  done: "Сделано",
  measuring: "Замеряем эффект",
  completed: "Завершена",
  failed: "Провал",
  cancelled: "Отменена",
};

const STATUS_ORDER = ["backlog", "planned", "in_progress", "done", "completed"];

const IMPACT_COLOR: Record<string, string> = {
  high: "text-red-500",
  medium: "text-amber-500",
  low: "text-muted-foreground",
};

const TASK_TYPE_LABEL: Record<string, string> = {
  meta_rewrite: "Переписать meta-теги",
  new_page: "Создать страницу",
  new_article: "Написать статью",
  content_expansion: "Расширить контент",
  schema_add: "Добавить Schema",
  faq_add: "Добавить FAQ",
  internal_linking: "Внутренние ссылки",
  h1_rewrite: "Переписать H1",
};

const EFFORT_LABEL: Record<string, string> = {
  XS: "~15 мин",
  S: "~1 час",
  M: "полдня",
  L: "день",
  XL: "неделя",
};

export function TaskBoard() {
  const siteId = useCurrentSiteId();
  const [selectedStatus, setSelectedStatus] = useState<string>("backlog");
  const [selectedTask, setSelectedTask] = useState<any | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [crawling, setCrawling] = useState(false);

  const { data, isLoading, mutate } = useSWR(
    `tasks-${siteId}-${selectedStatus}`,
    () => api.tasks(siteId, selectedStatus === "all" ? {} : { status: selectedStatus }),
    { refreshInterval: 30_000 }
  );

  async function generateTasks() {
    setGenerating(true);
    try {
      await api.triggerGenerateTasks(siteId);
      setTimeout(() => mutate(), 8000);
    } finally {
      setTimeout(() => setGenerating(false), 8000);
    }
  }

  async function crawlSite() {
    setCrawling(true);
    try {
      await api.triggerCrawl(siteId);
      setTimeout(() => setCrawling(false), 6000);
    } catch {
      setCrawling(false);
    }
  }

  async function changeStatus(taskId: string, newStatus: string) {
    await api.updateTask(siteId, taskId, { status: newStatus });
    mutate();
    if (selectedTask?.id === taskId) {
      setSelectedTask({ ...selectedTask, status: newStatus });
    }
  }

  const tasks: any[] = data?.items ?? [];
  const statusCounts = data?.status_counts ?? {};

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <CheckSquare className="h-6 w-6" />
            SEO-задачи
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Готовые задачи с контентом, который можно скопировать и применить
          </p>
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" disabled={crawling} onClick={crawlSite}>
            {crawling ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Target className="h-4 w-4 mr-1" />}
            {crawling ? "Сканирую..." : "Сканировать сайт"}
          </Button>
          <Button size="sm" disabled={generating} onClick={generateTasks}>
            {generating ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Sparkles className="h-4 w-4 mr-1" />}
            {generating ? "Генерирую..." : "Сгенерировать задачи"}
          </Button>
        </div>
      </div>

      {/* Status filter tabs */}
      <div className="flex gap-1 overflow-x-auto border-b">
        {["all", ...STATUS_ORDER].map((s) => {
          const count = s === "all"
            ? Object.values(statusCounts).reduce((a: any, b: any) => a + b, 0)
            : (statusCounts[s] ?? 0);
          const label = s === "all" ? "Все" : STATUS_LABEL[s];
          return (
            <button
              key={s}
              onClick={() => setSelectedStatus(s)}
              className={`px-3 py-2 text-sm border-b-2 transition-colors whitespace-nowrap ${
                selectedStatus === s
                  ? "border-primary text-foreground font-medium"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {label} <span className="text-xs opacity-60">({count})</span>
            </button>
          );
        })}
      </div>

      {/* Task list */}
      {isLoading ? (
        <div className="space-y-2">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-24 rounded-lg" />)}
        </div>
      ) : tasks.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center h-48 text-muted-foreground gap-3">
            <Sparkles className="h-8 w-8 opacity-30" />
            <div className="text-sm">Задач пока нет</div>
            <div className="text-xs max-w-md text-center">
              Нажмите "Сканировать сайт" чтобы собрать контент страниц, затем "Сгенерировать задачи" —
              AI проанализирует данные и создаст конкретные задачи с готовым контентом.
            </div>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {tasks.map((task) => (
            <Card
              key={task.id}
              className="cursor-pointer transition-all hover:shadow-md hover:border-primary/20"
              onClick={() => { setSelectedTask(task); setDialogOpen(true); }}
            >
              <CardContent className="p-3">
                <div className="flex items-start gap-3">
                  {/* Priority badge */}
                  <div className="shrink-0 flex flex-col items-center gap-1 pt-0.5">
                    <div className={`text-xl font-bold tabular-nums ${
                      task.priority >= 80 ? "text-red-500" :
                      task.priority >= 60 ? "text-amber-500" :
                      "text-muted-foreground"
                    }`}>
                      {task.priority}
                    </div>
                    <div className="text-[9px] text-muted-foreground uppercase">приоритет</div>
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-2">
                      <h3 className="font-medium text-sm leading-snug">{task.title}</h3>
                      <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0 mt-0.5" />
                    </div>

                    {task.description && (
                      <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                        {task.description}
                      </p>
                    )}

                    <div className="flex items-center gap-2 mt-2 flex-wrap">
                      <Badge variant="outline" className="text-[10px]">
                        {TASK_TYPE_LABEL[task.task_type] ?? task.task_type}
                      </Badge>
                      {task.estimated_impact && (
                        <span className={`text-[10px] flex items-center gap-0.5 ${IMPACT_COLOR[task.estimated_impact]}`}>
                          <Zap className="h-2.5 w-2.5" />
                          {task.estimated_impact === "high" ? "высокий эффект" :
                           task.estimated_impact === "medium" ? "средний эффект" :
                           "низкий эффект"}
                        </span>
                      )}
                      {task.estimated_effort && (
                        <span className="text-[10px] text-muted-foreground">
                          {EFFORT_LABEL[task.estimated_effort] ?? task.estimated_effort}
                        </span>
                      )}
                      {task.target_query && (
                        <span className="text-[10px] text-primary/80">
                          "{task.target_query.slice(0, 40)}"
                        </span>
                      )}
                    </div>

                    <div className="flex gap-1 mt-2" onClick={(e) => e.stopPropagation()}>
                      {task.status === "backlog" && (
                        <Button size="xs" variant="outline"
                          onClick={() => changeStatus(task.id, "in_progress")}>
                          В работу
                        </Button>
                      )}
                      {task.status === "in_progress" && (
                        <Button size="xs"
                          onClick={() => changeStatus(task.id, "done")}>
                          Готово
                        </Button>
                      )}
                      {task.status === "done" && (
                        <Button size="xs" variant="outline"
                          onClick={() => changeStatus(task.id, "measuring")}>
                          Замерять эффект
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <TaskDetailDialog
        task={selectedTask}
        open={dialogOpen}
        onClose={() => { setDialogOpen(false); mutate(); }}
        onStatusChange={changeStatus}
      />
    </div>
  );
}
