"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import { useCurrentSiteId } from "@/lib/site-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Activity, Database, Globe, Sparkles, CheckSquare,
  CheckCircle2, AlertCircle, XCircle, Clock,
  TrendingUp, DollarSign,
} from "lucide-react";

const STATUS_ICON: Record<string, any> = {
  ok: CheckCircle2,
  no_data: XCircle,
  idle: Clock,
  not_crawled: AlertCircle,
  no_tasks: AlertCircle,
};

const STATUS_COLOR: Record<string, string> = {
  ok: "text-green-500",
  no_data: "text-red-500",
  idle: "text-amber-500",
  not_crawled: "text-amber-500",
  no_tasks: "text-amber-500",
};

const STATUS_LABEL: Record<string, string> = {
  ok: "Работает",
  no_data: "Нет данных",
  idle: "Простаивает",
  not_crawled: "Не сканирован",
  no_tasks: "Нет задач",
};

const AGENT_LABEL: Record<string, string> = {
  search_visibility: "Видимость в поиске",
  technical_indexing: "Индексация",
  query_clustering: "Кластеризация запросов",
  query_tactical: "Тактические рекомендации",
  query_strategic: "Стратегические рекомендации",
  task_generator: "Генератор SEO-задач",
};

function HealthBlock({
  title,
  icon: Icon,
  status,
  subtitle,
  children,
}: {
  title: string;
  icon: any;
  status: string;
  subtitle?: string;
  children?: React.ReactNode;
}) {
  const StatusIcon = STATUS_ICON[status] || AlertCircle;
  const color = STATUS_COLOR[status] || "text-muted-foreground";

  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <Icon className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium">{title}</span>
          </div>
          <div className={`flex items-center gap-1 text-xs ${color}`}>
            <StatusIcon className="h-3.5 w-3.5" />
            {STATUS_LABEL[status] || status}
          </div>
        </div>
        {subtitle && (
          <div className="text-xs text-muted-foreground mt-1.5">{subtitle}</div>
        )}
        {children && <div className="mt-2">{children}</div>}
      </CardContent>
    </Card>
  );
}

function formatHours(hours: number | null): string {
  if (hours === null || hours === undefined) return "не было";
  if (hours < 1) return "только что";
  if (hours < 24) return `${Math.round(hours)}ч назад`;
  const days = Math.round(hours / 24);
  return `${days}д назад`;
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("ru", { dateStyle: "short", timeStyle: "short" });
}

export function AgentStatusView() {
  const siteId = useCurrentSiteId();

  const { data, isLoading } = useSWR(
    siteId ? `agent-status-${siteId}` : null,
    () => api.agentStatus(siteId),
    { refreshInterval: 30_000 }
  );

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-24" />)}
        </div>
      </div>
    );
  }

  if (!data) return null;

  const h = data.health;
  const coverage = data.data_coverage;
  const timeline = data.timeline ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Activity className="h-6 w-6" />
          Статус SEO-агента
        </h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Работает ли агент, собирает ли данные, создаёт ли задачи
        </p>
      </div>

      {/* Health Pipeline */}
      <div>
        <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wide mb-2">
          Пайплайн агента
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <HealthBlock
            title="1. Сбор данных"
            icon={Database}
            status={h.data_collection.status}
            subtitle={`Webmaster: ${formatHours(h.data_collection.hours_since_webmaster)}`}
          >
            <div className="text-xs text-muted-foreground">
              Metrica: {formatHours(h.data_collection.hours_since_metrica)}
            </div>
          </HealthBlock>

          <HealthBlock
            title="2. Сканирование"
            icon={Globe}
            status={h.site_crawled.status}
            subtitle={`${h.site_crawled.pages} страниц`}
          >
            <div className="text-xs text-muted-foreground">
              {formatHours(h.site_crawled.hours_since_crawl)}
            </div>
          </HealthBlock>

          <HealthBlock
            title="3. AI-агенты"
            icon={Sparkles}
            status={h.ai_active.status}
            subtitle={`${h.ai_active.runs_last_7d} запусков за неделю`}
          >
            <div className="text-xs text-muted-foreground flex items-center gap-1">
              <DollarSign className="h-3 w-3" />
              потрачено ${h.ai_active.total_cost_usd}
            </div>
          </HealthBlock>

          <HealthBlock
            title="4. SEO-задачи"
            icon={CheckSquare}
            status={h.has_tasks.status}
            subtitle={`${h.has_tasks.total} всего задач`}
          >
            <div className="text-xs text-muted-foreground">
              В работе: {h.has_tasks.in_progress} · Готово: {h.has_tasks.done}
            </div>
          </HealthBlock>
        </div>
      </div>

      {/* Data Coverage */}
      <div>
        <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wide mb-2">
          Что собрано
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Card>
            <CardContent className="p-4">
              <div className="text-2xl font-bold tabular-nums">{coverage.queries_total}</div>
              <div className="text-xs text-muted-foreground mt-0.5">Запросов</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <div className="text-2xl font-bold tabular-nums">{coverage.queries_clustered}</div>
              <div className="text-xs text-muted-foreground mt-0.5">Кластеризовано</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <div className="text-2xl font-bold tabular-nums">{coverage.pages_total}</div>
              <div className="text-xs text-muted-foreground mt-0.5">Страниц сайта</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <div className="text-2xl font-bold tabular-nums">{coverage.pages_with_content}</div>
              <div className="text-xs text-muted-foreground mt-0.5">С контентом</div>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Agent Runs */}
      {data.agent_runs && data.agent_runs.length > 0 && (
        <div>
          <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wide mb-2">
            Агенты (последние 7 дней)
          </h2>
          <Card>
            <CardContent className="p-0">
              <div className="divide-y">
                {data.agent_runs.map((r: any) => (
                  <div key={r.agent_name} className="p-3 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Sparkles className="h-4 w-4 text-primary/60" />
                      <span className="text-sm font-medium">
                        {AGENT_LABEL[r.agent_name] ?? r.agent_name}
                      </span>
                      <Badge variant="outline" className="text-[10px]">
                        {r.successful}/{r.runs_last_7d} успешно
                      </Badge>
                    </div>
                    <div className="flex items-center gap-3 text-xs text-muted-foreground">
                      <span>${r.total_cost_usd.toFixed(4)}</span>
                      <span>{formatDateTime(r.last_run)}</span>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Timeline */}
      {timeline.length > 0 && (
        <div>
          <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wide mb-2">
            Лента активности
          </h2>
          <Card>
            <CardContent className="p-0">
              <div className="divide-y">
                {timeline.slice(0, 8).map((t: any, i: number) => {
                  const isOk = t.status === "completed";
                  return (
                    <div key={i} className="p-3 flex items-start justify-between gap-3">
                      <div className="flex items-start gap-2 min-w-0">
                        {isOk ?
                          <CheckCircle2 className="h-4 w-4 text-green-500 shrink-0 mt-0.5" /> :
                          <XCircle className="h-4 w-4 text-red-500 shrink-0 mt-0.5" />
                        }
                        <div className="min-w-0">
                          <div className="text-sm font-medium">
                            {AGENT_LABEL[t.agent_name] ?? t.agent_name}
                          </div>
                          {t.summary && (
                            <div className="text-xs text-muted-foreground mt-0.5 truncate">
                              {t.summary.summary ??
                                (t.summary.tasks_created !== undefined
                                  ? `Создано задач: ${t.summary.tasks_created}`
                                  : t.summary.issues_saved !== undefined
                                    ? `Найдено проблем: ${t.summary.issues_saved}`
                                    : JSON.stringify(t.summary).slice(0, 80))}
                            </div>
                          )}
                        </div>
                      </div>
                      <div className="text-xs text-muted-foreground shrink-0 text-right">
                        <div>{formatDateTime(t.started_at)}</div>
                        {t.cost_usd > 0 && (
                          <div className="text-[10px]">${t.cost_usd.toFixed(4)}</div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Impact */}
      <div>
        <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wide mb-2">
          Эффект от задач
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Card>
            <CardContent className="p-4">
              <div className="text-lg font-semibold">{h.has_tasks.done}</div>
              <div className="text-xs text-muted-foreground">Сделано (ждут замер)</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <div className="text-lg font-semibold">{data.tasks_measuring}</div>
              <div className="text-xs text-muted-foreground">Замеряем эффект</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <div className="text-lg font-semibold">{data.tasks_with_effect}</div>
              <div className="text-xs text-muted-foreground">Эффект зафиксирован</div>
            </CardContent>
          </Card>
        </div>
        {h.has_tasks.total === 0 && (
          <div className="mt-3 text-sm text-muted-foreground bg-muted/30 rounded-lg p-3">
            Нет данных об эффекте. Чтобы увидеть работу агента:
            1) Сканируйте сайт → 2) Сгенерируйте задачи → 3) Выполните какие-то →
            4) Через 2-4 недели система сама замерит эффект.
          </div>
        )}
      </div>
    </div>
  );
}
