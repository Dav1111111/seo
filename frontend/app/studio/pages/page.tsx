"use client";

/**
 * Studio /pages — list of pages (PR-S4).
 *
 * Backend contract: backend/app/api/v1/studio.py · list_pages.
 *
 * What the owner sees: every page the crawler knows about, with a
 * compact summary — is it indexed, when was it last reviewed, how many
 * recommendations are open. Click → /studio/pages/[id] workspace.
 *
 * Sort modes (mirroring the backend):
 *   recent_review — pages with freshest review on top (default — owner
 *                   usually wants to act on what was just analysed)
 *   crawl         — most recently crawled
 *   alpha         — alphabetical by URL (auditable scan)
 *   recs          — most recommendations first (where the work is)
 */

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";

import { api } from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  FileText,
  ArrowLeft,
  ChevronRight,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";

type SortMode = "recent_review" | "crawl" | "alpha" | "recs";

const SORT_OPTIONS: Array<{ value: SortMode; label: string }> = [
  { value: "recent_review", label: "По дате ревью" },
  { value: "crawl", label: "По дате crawl" },
  { value: "alpha", label: "По алфавиту" },
  { value: "recs", label: "По кол-ву рекомендаций" },
];

function fmtAge(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.floor(ms / 60000);
  if (min < 1) return "только что";
  if (min < 60) return `${min} мин`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} ч`;
  const day = Math.floor(hr / 24);
  return `${day} дн`;
}

function shortPath(p: string): string {
  if (!p || p === "/") return "/";
  return p.length > 60 ? "…" + p.slice(-57) : p;
}

export default function StudioPagesIndex() {
  const { currentSite, loading: siteLoading } = useSite();
  const siteId = currentSite?.id || "";
  const [sort, setSort] = useState<SortMode>("recent_review");

  const { data, isLoading, error } = useSWR(
    siteId ? studioKey("pages_list", siteId, sort) : null,
    () => api.studioListPages(siteId, sort, 200),
  );

  if (siteLoading) {
    return (
      <div className="p-6 space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!currentSite) {
    return (
      <div className="p-6">
        <Card className="border-dashed max-w-2xl">
          <CardContent className="pt-6 space-y-2">
            <div className="font-medium">Сайт не выбран</div>
            <p className="text-sm text-muted-foreground">
              Выбери сайт в свитчере слева — модуль «Страницы» работает
              в контексте конкретного сайта.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-5 max-w-6xl">
      {/* Header */}
      <div>
        <Link
          href="/studio"
          className="inline-flex items-center text-xs text-muted-foreground hover:text-foreground mb-1"
        >
          <ArrowLeft className="h-3 w-3 mr-1" /> К Студии
        </Link>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <FileText className="h-6 w-6 text-primary" /> Страницы
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          {data
            ? `${data.total} страниц на сайте · ` +
              `${data.items.filter((p) => p.has_review).length} с ревью · ` +
              `${data.items.reduce((s, p) => s + p.n_pending, 0)} рекомендаций ждут действия`
            : "загружаю…"}
        </p>
      </div>

      {/* Sort */}
      <div className="flex items-center gap-1 text-sm flex-wrap">
        <span className="text-muted-foreground mr-1">Сортировка:</span>
        {SORT_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            onClick={() => setSort(opt.value)}
            className={cn(
              "rounded-md px-3 py-1 text-xs transition-colors",
              sort === opt.value
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-accent-foreground border",
            )}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {/* Loading / error */}
      {isLoading && (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      )}

      {error && (
        <Card className="border-red-300 bg-red-50">
          <CardContent className="pt-6 text-sm text-red-900">
            Не удалось загрузить страницы:{" "}
            {error instanceof Error ? error.message : String(error)}
          </CardContent>
        </Card>
      )}

      {/* Empty state */}
      {data && data.total === 0 && (
        <Card className="border-dashed">
          <CardContent className="pt-6 space-y-2">
            <div className="font-medium">Страниц пока нет</div>
            <p className="text-sm text-muted-foreground">
              Crawler ещё не прошёлся по сайту. Запусти полный конвейер
              на дашборде Студии — он соберёт страницы, после чего они
              появятся здесь со статусом «без ревью», и ты сможешь
              запустить ревью отдельной страницы из её workspace.
            </p>
          </CardContent>
        </Card>
      )}

      {/* List */}
      {data && data.total > 0 && (
        <div className="space-y-2">
          {data.items.map((p) => (
            <Link
              key={p.page_id}
              href={`/studio/pages/${p.page_id}`}
              className="block"
            >
              <Card className="hover:border-primary/50 transition-colors">
                <CardContent className="py-3 flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium truncate">
                        {p.title || shortPath(p.path)}
                      </span>
                      {!p.in_index && (
                        <Badge
                          variant="outline"
                          className="text-[10px] border-amber-300 text-amber-700 bg-amber-50"
                        >
                          не в индексе
                        </Badge>
                      )}
                      {p.http_status && p.http_status >= 400 && (
                        <Badge
                          variant="outline"
                          className="text-[10px] border-red-300 text-red-700 bg-red-50"
                        >
                          HTTP {p.http_status}
                        </Badge>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground truncate mt-0.5">
                      {shortPath(p.path)}
                    </div>
                  </div>

                  <div className="hidden sm:flex flex-col items-end text-right text-xs text-muted-foreground tabular-nums w-20">
                    <span>crawl</span>
                    <span>{fmtAge(p.last_crawled_at)}</span>
                  </div>

                  <div className="flex items-center gap-1 w-32 justify-end">
                    {p.has_review ? (
                      <>
                        <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                        <span className="text-xs text-muted-foreground">
                          {p.n_pending > 0
                            ? `${p.n_pending} ждут`
                            : `${p.n_applied} применено`}
                        </span>
                      </>
                    ) : (
                      <>
                        <AlertCircle className="h-4 w-4 text-muted-foreground/50" />
                        <span className="text-xs text-muted-foreground">
                          без ревью
                        </span>
                      </>
                    )}
                  </div>

                  <ChevronRight className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
