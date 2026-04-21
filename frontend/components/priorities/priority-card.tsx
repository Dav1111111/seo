"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Check, X, Clock, ExternalLink, ChevronDown, ChevronUp } from "lucide-react";
import { cn } from "@/lib/utils";

interface PriorityItem {
  recommendation_id: string;
  review_id: string;
  page_id: string;
  page_url: string | null;
  target_intent_code: string | null;
  category: string;
  priority: string;
  user_status: string;
  reasoning_ru: string;
  before_text: string | null;
  after_text: string | null;
  priority_score: number;
  impact: number;
  confidence: number;
  ease: number;
}

const PRIORITY_TONE: Record<string, string> = {
  critical: "bg-rose-100 text-rose-800 border-rose-300",
  high:     "bg-orange-100 text-orange-800 border-orange-300",
  medium:   "bg-amber-100 text-amber-800 border-amber-300",
  low:      "bg-slate-100 text-slate-700 border-slate-300",
};

const CATEGORY_LABEL_RU: Record<string, string> = {
  title: "Title",
  description: "Description",
  h1: "H1",
  content: "Контент",
  eeat: "E-E-A-T",
  commercial: "Комм. факторы",
  internal_links: "Внутр. ссылки",
  structured_data: "Schema.org",
  ux: "UX",
};

export function PriorityCard({
  item, onMutated,
}: {
  item: PriorityItem;
  onMutated: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function updateStatus(next: string) {
    setBusy(next); setError(null);
    try {
      await api.patchRecommendation(item.recommendation_id, { user_status: next });
      onMutated();
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setBusy(null);
    }
  }

  const isDone = item.user_status === "applied";
  const isDismissed = item.user_status === "dismissed";
  const isDeferred = item.user_status === "deferred";

  return (
    <div className={cn(
      "rounded-lg border bg-card p-4 space-y-3 transition-opacity",
      (isDone || isDismissed) && "opacity-60",
    )}>
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1.5 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge variant="outline" className={cn("text-xs", PRIORITY_TONE[item.priority])}>
              {item.priority} · {item.priority_score.toFixed(1)}
            </Badge>
            <Badge variant="secondary" className="text-xs">
              {CATEGORY_LABEL_RU[item.category] || item.category}
            </Badge>
            {item.target_intent_code && (
              <Badge variant="outline" className="text-xs font-mono">
                {item.target_intent_code}
              </Badge>
            )}
            {isDone && <Badge className="text-xs bg-emerald-600">✓ применено</Badge>}
            {isDismissed && <Badge variant="outline" className="text-xs">отклонено</Badge>}
            {isDeferred && <Badge variant="outline" className="text-xs">отложено</Badge>}
          </div>
          {item.page_url && (
            <a
              href={item.page_url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1 truncate max-w-full"
            >
              <ExternalLink className="h-3 w-3 shrink-0" />
              <span className="truncate">{item.page_url}</span>
            </a>
          )}
          <div className="text-sm leading-snug">{item.reasoning_ru}</div>
        </div>

        <div className="shrink-0 flex items-center gap-1">
          <Button
            size="sm"
            variant={isDone ? "default" : "outline"}
            disabled={busy !== null}
            onClick={() => updateStatus(isDone ? "pending" : "applied")}
            title="Применено"
          >
            <Check className="h-4 w-4" />
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy !== null}
            onClick={() => updateStatus(isDeferred ? "pending" : "deferred")}
            title="Отложить"
          >
            <Clock className="h-4 w-4" />
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy !== null}
            onClick={() => updateStatus(isDismissed ? "pending" : "dismissed")}
            title="Отклонить"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {error && (
        <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1">
          {error}
        </div>
      )}

      {(item.before_text || item.after_text) && (
        <button
          type="button"
          onClick={() => setExpanded((x) => !x)}
          className="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
        >
          {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          {expanded ? "Скрыть diff" : "Показать before/after"}
        </button>
      )}

      {expanded && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
          <div className="rounded border bg-rose-50/50 p-2">
            <div className="text-rose-800 font-semibold mb-1">Сейчас</div>
            <div className="whitespace-pre-wrap font-mono text-[11px] leading-snug">
              {item.before_text || "—"}
            </div>
          </div>
          <div className="rounded border bg-emerald-50/50 p-2">
            <div className="text-emerald-800 font-semibold mb-1">Предлагается</div>
            <div className="whitespace-pre-wrap font-mono text-[11px] leading-snug">
              {item.after_text || "—"}
            </div>
          </div>
        </div>
      )}

      <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
        <span>impact {(item.impact * 100).toFixed(0)}</span>
        <span>·</span>
        <span>confidence {(item.confidence * 100).toFixed(0)}</span>
        <span>·</span>
        <span>ease {(item.ease * 100).toFixed(0)}</span>
      </div>
    </div>
  );
}

export type { PriorityItem };
