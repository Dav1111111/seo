"use client";

/**
 * «Сейчас всё в порядке» empty state for the advice feed.
 *
 * Shown when the backend returns an `AdviceFeed` with `cards.length === 0`.
 * Includes a quick «Запустить полную проверку» button that re-runs the
 * pipeline so the owner can refresh after fixing something — without
 * leaving the feed page.
 *
 * Re-uses the existing pipeline trigger (`api.triggerFullAnalysis`)
 * rather than a fresh endpoint — backend behaviour is identical.
 */

import { useState } from "react";
import { Sparkles, Loader2, Play, RefreshCw } from "lucide-react";

import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { getErrorMessage } from "@/lib/utils";

interface AdviceEmptyStateProps {
  siteId: string;
  onRefresh: () => Promise<unknown>;
}

export function AdviceEmptyState({ siteId, onRefresh }: AdviceEmptyStateProps) {
  const [running, setRunning] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  async function handleRun() {
    if (running) return;
    setRunning(true);
    setErrMsg(null);
    try {
      await api.triggerFullAnalysis(siteId);
      // Re-fetch the feed so the owner sees the spinner / fresh state.
      await onRefresh();
    } catch (e) {
      setErrMsg(getErrorMessage(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <Card className="border-emerald-200 bg-emerald-50/40 dark:bg-emerald-950/20 dark:border-emerald-900/40">
      <CardContent className="space-y-4 pt-2">
        <div className="flex items-start gap-3">
          <Sparkles
            className="h-6 w-6 text-emerald-600 dark:text-emerald-400 flex-shrink-0 mt-0.5"
            aria-hidden
          />
          <div className="flex-1 space-y-2">
            <h2 className="text-lg font-semibold text-emerald-900 dark:text-emerald-100">
              Сейчас всё в порядке.
            </h2>
            <p className="text-sm text-emerald-900/85 dark:text-emerald-200/85 leading-relaxed">
              Ни одного срочного действия — система не находит проблем. Это означает что:
            </p>
            <ul className="text-sm text-emerald-900/85 dark:text-emerald-200/85 leading-relaxed list-disc pl-5 space-y-0.5">
              <li>все коллекторы данных работают,</li>
              <li>нет критичных дыр по разметке или контенту,</li>
              <li>все запросы воронки имеют соответствующие страницы.</li>
            </ul>
            <p className="text-sm text-emerald-900/70 dark:text-emerald-200/70 leading-relaxed">
              Если только что закончили исправлять что-то, дайте 15 минут — система пересоберёт картинку.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 flex-wrap pt-1">
          <Button
            type="button"
            variant="default"
            size="default"
            onClick={handleRun}
            disabled={running}
          >
            {running ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <Play className="h-4 w-4" aria-hidden />
            )}
            {running ? "Запускаю…" : "Запустить полную проверку"}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onRefresh()}
            disabled={running}
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            Обновить
          </Button>
        </div>

        {errMsg && (
          <p className="text-xs text-red-700 dark:text-red-400">{errMsg}</p>
        )}
      </CardContent>
    </Card>
  );
}
