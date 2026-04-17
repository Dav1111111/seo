"use client";

import { useState } from "react";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  Copy, Check, ExternalLink, Zap, Clock, Target, FileText,
  Lightbulb, CheckCircle,
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

function CopyButton({ text, label = "Копировать" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  }

  return (
    <Button size="xs" variant="ghost" onClick={copy} className="text-[10px]">
      {copied ? <Check className="h-3 w-3 mr-1" /> : <Copy className="h-3 w-3 mr-1" />}
      {copied ? "Скопировано" : label}
    </Button>
  );
}

function ContentBlock({
  title,
  value,
  showCopy = true,
}: {
  title: string;
  value: string;
  showCopy?: boolean;
}) {
  return (
    <div className="border rounded-lg overflow-hidden">
      <div className="bg-muted/50 px-3 py-1.5 flex items-center justify-between border-b">
        <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">{title}</span>
        {showCopy && <CopyButton text={value} />}
      </div>
      <div className="p-3 text-sm whitespace-pre-wrap">{value}</div>
    </div>
  );
}

export function TaskDetailDialog({
  task,
  open,
  onClose,
  onStatusChange,
}: {
  task: any | null;
  open: boolean;
  onClose: () => void;
  onStatusChange: (id: string, status: string) => void;
}) {
  if (!task) return null;

  const content = task.generated_content ?? {};

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <div className="flex items-start gap-3">
            <div className={`text-2xl font-bold tabular-nums shrink-0 ${
              task.priority >= 80 ? "text-red-500" :
              task.priority >= 60 ? "text-amber-500" :
              "text-muted-foreground"
            }`}>
              {task.priority}
            </div>
            <div className="flex-1 min-w-0">
              <DialogTitle className="text-base leading-snug">{task.title}</DialogTitle>
              <div className="flex items-center gap-2 mt-2 flex-wrap">
                <Badge variant="outline">
                  {TASK_TYPE_LABEL[task.task_type] ?? task.task_type}
                </Badge>
                <Badge variant="secondary">{STATUS_LABEL[task.status] ?? task.status}</Badge>
                {task.estimated_impact && (
                  <span className="text-xs flex items-center gap-1 text-muted-foreground">
                    <Zap className="h-3 w-3" />
                    {task.estimated_impact === "high" ? "высокий эффект" :
                     task.estimated_impact === "medium" ? "средний" : "низкий"}
                  </span>
                )}
                {task.estimated_effort && (
                  <span className="text-xs flex items-center gap-1 text-muted-foreground">
                    <Clock className="h-3 w-3" />
                    {task.estimated_effort === "XS" ? "~15 мин" :
                     task.estimated_effort === "S" ? "~1 час" :
                     task.estimated_effort === "M" ? "полдня" :
                     task.estimated_effort === "L" ? "день" : "неделя"}
                  </span>
                )}
              </div>
            </div>
          </div>
        </DialogHeader>

        {/* Description */}
        {task.description && (
          <div>
            <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">
              Что делать
            </h4>
            <p className="text-sm leading-relaxed">{task.description}</p>
          </div>
        )}

        {/* Target */}
        <div className="grid grid-cols-1 gap-2 text-xs">
          {task.target_query && (
            <div className="flex items-center gap-2">
              <Target className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="text-muted-foreground">Запрос:</span>
              <span className="font-medium">"{task.target_query}"</span>
            </div>
          )}
          {task.target_cluster && (
            <div className="flex items-center gap-2">
              <Lightbulb className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="text-muted-foreground">Кластер:</span>
              <Badge variant="secondary" className="text-[10px]">{task.target_cluster}</Badge>
            </div>
          )}
          {task.target_page_url && (
            <div className="flex items-center gap-2">
              <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="text-muted-foreground">Страница:</span>
              <a href={task.target_page_url} target="_blank" rel="noreferrer"
                 className="text-primary hover:underline truncate">
                {task.target_page_url}
              </a>
            </div>
          )}
        </div>

        <Separator />

        {/* Ready-to-paste content */}
        {(content.new_title || content.new_description || content.new_h1) && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-xs font-medium text-primary">
              <FileText className="h-3.5 w-3.5" />
              ГОТОВЫЙ КОНТЕНТ — ПРОСТО СКОПИРУЙТЕ
            </div>
            {content.new_title && <ContentBlock title="Новый Title" value={content.new_title} />}
            {content.new_description && <ContentBlock title="Новый Meta Description" value={content.new_description} />}
            {content.new_h1 && <ContentBlock title="Новый H1" value={content.new_h1} />}
          </div>
        )}

        {/* Article content */}
        {(content.article_intro || content.article_outline) && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-xs font-medium text-primary">
              <FileText className="h-3.5 w-3.5" />
              СТРУКТУРА СТАТЬИ
            </div>
            {content.article_intro && (
              <ContentBlock title="Вступление" value={content.article_intro} />
            )}
            {content.article_outline && content.article_outline.length > 0 && (
              <div className="border rounded-lg overflow-hidden">
                <div className="bg-muted/50 px-3 py-1.5 flex items-center justify-between border-b">
                  <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                    План статьи (H2-заголовки)
                  </span>
                  <CopyButton text={content.article_outline.map((h: string, i: number) => `${i + 1}. ${h}`).join("\n")} />
                </div>
                <ol className="p-3 space-y-1.5 text-sm list-decimal list-inside">
                  {content.article_outline.map((heading: string, i: number) => (
                    <li key={i}>{heading}</li>
                  ))}
                </ol>
              </div>
            )}
          </div>
        )}

        {/* FAQ */}
        {content.faq_items && content.faq_items.length > 0 && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-xs font-medium text-primary">
              <FileText className="h-3.5 w-3.5" />
              FAQ-БЛОК
            </div>
            <div className="border rounded-lg overflow-hidden">
              <div className="bg-muted/50 px-3 py-1.5 flex items-center justify-between border-b">
                <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  {content.faq_items.length} вопросов
                </span>
                <CopyButton text={content.faq_items.map((f: any) => `Q: ${f.question}\nA: ${f.answer}`).join("\n\n")} />
              </div>
              <div className="p-3 space-y-3">
                {content.faq_items.map((f: any, i: number) => (
                  <div key={i} className="text-sm">
                    <div className="font-medium">{i + 1}. {f.question}</div>
                    <div className="text-muted-foreground mt-0.5 text-xs leading-relaxed">{f.answer}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Schema.org */}
        {content.schema_jsonld && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-xs font-medium text-primary">
              <FileText className="h-3.5 w-3.5" />
              SCHEMA.ORG ({content.schema_type})
            </div>
            <div className="border rounded-lg overflow-hidden">
              <div className="bg-muted/50 px-3 py-1.5 flex items-center justify-between border-b">
                <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  JSON-LD для {'<head>'}
                </span>
                <CopyButton text={content.schema_jsonld} />
              </div>
              <pre className="p-3 text-[10px] font-mono overflow-x-auto max-h-64 overflow-y-auto bg-slate-50 dark:bg-slate-900">
{content.schema_jsonld}
              </pre>
              {content.install_notes && (
                <div className="bg-muted/30 px-3 py-2 text-xs text-muted-foreground border-t">
                  💡 {content.install_notes}
                </div>
              )}
            </div>
          </div>
        )}

        <Separator />

        {/* Actions */}
        <div className="flex items-center gap-2 flex-wrap">
          {task.status === "backlog" && (
            <Button size="sm" onClick={() => onStatusChange(task.id, "in_progress")}>
              Взять в работу
            </Button>
          )}
          {task.status === "in_progress" && (
            <Button size="sm" onClick={() => onStatusChange(task.id, "done")}>
              <CheckCircle className="h-4 w-4 mr-1" />
              Сделано
            </Button>
          )}
          {task.status === "done" && (
            <Button size="sm" variant="outline" onClick={() => onStatusChange(task.id, "measuring")}>
              Замерять эффект (2-4 недели)
            </Button>
          )}
          {task.status === "measuring" && (
            <Button size="sm" variant="outline" onClick={() => onStatusChange(task.id, "completed")}>
              Закрыть задачу
            </Button>
          )}
          {!["cancelled", "failed", "completed"].includes(task.status) && (
            <Button size="sm" variant="ghost" className="text-muted-foreground"
              onClick={() => onStatusChange(task.id, "cancelled")}>
              Отменить
            </Button>
          )}
        </div>

        {/* Effect measurement */}
        {task.effect_result && (
          <div className="border-l-2 border-green-500 pl-3 bg-green-50/50 dark:bg-green-900/10 py-2 rounded">
            <div className="text-xs font-medium text-green-700 dark:text-green-400">Замеренный эффект</div>
            <div className="text-xs text-muted-foreground mt-1">
              {JSON.stringify(task.effect_result, null, 2)}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
