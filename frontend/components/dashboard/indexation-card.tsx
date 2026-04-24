"use client";

import useSWR from "swr";
import { useState } from "react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Search, ExternalLink, AlertCircle, CheckCircle2 } from "lucide-react";
import { IndexNowSetup } from "@/components/dashboard/indexnow-setup";

/**
 * Honest indexation status — asks Yandex Search API `site:domain` directly
 * (independent of Webmaster). When Webmaster is stuck at HOST_NOT_LOADED,
 * this card is the *only* way the owner gets a truthful answer to
 * "is my site visible in Yandex search at all?".
 *
 * Reads the most recent `indexation` stage event. If nothing on record,
 * prompts the owner to run the first check.
 */
type IndexationExtra = {
  pages_found?: number;
  pages?: Array<{ url: string; title?: string; position?: number }>;
  query?: string;
  error?: string;
};

type ActivityEvent = {
  id: number;
  stage: string;
  status: string;
  message: string;
  ts: string;
  extra: Record<string, unknown>;
  run_id: string | null;
};

function asIndexationExtra(extra: Record<string, unknown>): IndexationExtra {
  const pages = Array.isArray(extra.pages)
    ? (extra.pages as Array<Record<string, unknown>>)
        .filter((p): p is Record<string, unknown> => typeof p === "object" && p !== null)
        .map((p) => ({
          url: typeof p.url === "string" ? p.url : "",
          title: typeof p.title === "string" ? p.title : undefined,
          position:
            typeof p.position === "number" ? p.position : undefined,
        }))
        .filter((p) => p.url)
    : [];
  const pagesFound =
    typeof extra.pages_found === "number" ? extra.pages_found : pages.length;
  return {
    pages_found: pagesFound,
    pages,
    query: typeof extra.query === "string" ? extra.query : undefined,
    error: typeof extra.error === "string" ? extra.error : undefined,
  };
}

export function IndexationCard({ siteId }: { siteId: string }) {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data, mutate } = useSWR(
    siteId ? ["indexation", siteId] : null,
    () => api.activity(siteId, 50),
    { refreshInterval: 5_000 },
  );

  // Pick the most recent terminal indexation event.
  const event: ActivityEvent | undefined = data?.events.find(
    (e) => e.stage === "indexation" && ["done", "failed", "skipped"].includes(e.status),
  );
  const extra = event ? asIndexationExtra(event.extra) : null;

  async function runCheck() {
    setRunning(true);
    setError(null);
    try {
      await api.triggerIndexationCheck(siteId);
      // Poll activity until a fresh indexation terminal lands
      setTimeout(() => mutate(), 2_000);
      setTimeout(() => mutate(), 6_000);
      setTimeout(() => mutate(), 12_000);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "запуск не удался");
    } finally {
      setTimeout(() => setRunning(false), 2_000);
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4 pb-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <Search className="h-4 w-4" /> Индексация в Яндексе
          </CardTitle>
          <p className="text-xs text-muted-foreground mt-1">
            Прямой запрос <code>site:домен</code> — не зависит от Вебмастера.
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={runCheck} disabled={running}>
          {running ? "Проверяю…" : "Проверить сейчас"}
        </Button>
      </CardHeader>
      <CardContent>
        {error && (
          <div className="rounded border border-red-300 bg-red-50 p-2 text-sm text-red-900 mb-3">
            {error}
          </div>
        )}

        {!data ? (
          <Skeleton className="h-16 w-full" />
        ) : !event ? (
          <p className="text-sm text-muted-foreground italic">
            Ещё не проверяли. Нажми «Проверить сейчас» — ответ придёт за 5–10 секунд.
          </p>
        ) : event.status === "failed" ? (
          <div className="flex items-start gap-2 text-sm text-red-800">
            <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
            <span>{event.message}</span>
          </div>
        ) : event.status === "skipped" || extra?.pages_found === 0 ? (
          <div className="space-y-2">
            <div className="flex items-start gap-2 text-sm">
              <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0 text-amber-600" />
              <span>{event.message}</span>
            </div>
            <div className="text-xs text-muted-foreground pl-6">
              Это ограничение Яндекса, не нашей платформы. Открой Вебмастер →
              проверь, загружен ли хост, нет ли запретов в robots.txt.
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm">
              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
              <span>
                В индексе найдено{" "}
                <Badge variant="secondary">{extra?.pages_found ?? 0}</Badge>{" "}
                страниц
              </span>
            </div>
            {extra?.pages && extra.pages.length > 0 && (
              <div className="rounded border p-2 max-h-48 overflow-y-auto space-y-1">
                {extra.pages.slice(0, 10).map((p) => (
                  <a
                    key={p.url}
                    href={p.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-start gap-2 text-xs hover:bg-accent rounded px-1 py-0.5"
                  >
                    <ExternalLink className="h-3 w-3 mt-0.5 flex-shrink-0 text-muted-foreground" />
                    <span className="truncate flex-1">
                      {p.title && p.title !== p.url ? p.title : p.url}
                    </span>
                  </a>
                ))}
                {extra.pages.length > 10 && (
                  <p className="text-[11px] text-muted-foreground pt-1">
                    …и ещё {extra.pages.length - 10}
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        <IndexNowSetup siteId={siteId} />
      </CardContent>
    </Card>
  );
}
