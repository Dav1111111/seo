"use client";

import { useState } from "react";
import useSWR from "swr";
import { Microscope, ChevronDown, ChevronRight } from "lucide-react";

import { api, type DeepExtractRow } from "@/lib/api";
import { DeepExtractPanel } from "@/components/studio/deep-extract-panel";

/**
 * Section on /studio/competitors that lets the owner deep-extract any
 * competitor URL with Playwright and see a structured breakdown
 * (CTAs, palette, fonts, performance, screenshots, schema).
 *
 * Two parts:
 *   1. Input + button — paste any competitor URL, hit «Разобрать».
 *   2. List of past extracts grouped by competitor_domain. Each row
 *      expands to show the full DeepExtractPanel (live, polled).
 */

export function CompetitorDeepExtractSection({ siteId }: { siteId: string }) {
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const { data, mutate } = useSWR(
    ["competitor-deep-extracts", siteId],
    () => api.studioListCompetitorDeepExtracts(siteId),
    { refreshInterval: submitting ? 4000 : 0 },
  );

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim() || submitting) return;
    setSubmitting(true);
    setErr(null);
    try {
      await api.studioTriggerDeepExtractCompetitor(siteId, url.trim());
      // Poll for new row appearance — typically 12-25 seconds.
      const before = data?.items.length || 0;
      const start = Date.now();
      const t = setInterval(async () => {
        const next = await mutate();
        const after = next?.items.length || 0;
        if (after > before) {
          clearInterval(t);
          setSubmitting(false);
          setUrl("");
        }
        if (Date.now() - start > 90_000) {
          clearInterval(t);
          setSubmitting(false);
          setErr("Слежу больше 90 секунд — посмотри Activity, возможно Playwright занят.");
        }
      }, 4000);
    } catch (e) {
      setSubmitting(false);
      setErr(e instanceof Error ? e.message : "Не получилось запустить разбор");
    }
  }

  const items: DeepExtractRow[] = data?.items || [];

  // Group by competitor_domain so the owner sees one section per
  // competitor, multiple URLs underneath.
  const byDomain = new Map<string, DeepExtractRow[]>();
  for (const it of items) {
    const key = it.competitor_domain || "(?)";
    if (!byDomain.has(key)) byDomain.set(key, []);
    byDomain.get(key)!.push(it);
  }

  return (
    <div className="rounded-lg border bg-card p-4 space-y-4">
      <div>
        <h3 className="font-medium flex items-center gap-2">
          <Microscope className="h-4 w-4 text-primary" />
          Глубокий разбор конкурента
        </h3>
        <p className="text-xs text-muted-foreground mt-1 max-w-2xl">
          Вставь URL страницы конкурента — открою её как настоящий браузер,
          вытащу все кнопки с цветами, шрифты, скорость загрузки, формы и
          сравним с твоей. Заодно сделаю скриншот для сравнения. Чтобы
          понять <em>почему он впереди</em>.
        </p>
      </div>

      <form onSubmit={onSubmit} className="flex gap-2 flex-wrap">
        <input
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://конкурент.ru/excursion/..."
          required
          className="flex-1 min-w-[280px] rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
        />
        <button
          type="submit"
          disabled={submitting || !url.trim()}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-60"
        >
          <Microscope className="h-4 w-4" />
          {submitting ? "Разбираю…" : "Разобрать"}
        </button>
      </form>

      {err && (
        <p className="text-xs text-red-700 dark:text-red-400">{err}</p>
      )}

      {byDomain.size === 0 && !submitting && (
        <p className="text-xs text-muted-foreground italic">
          Пока нет разборов конкурентов. Вставь URL выше — обычно 12-25 секунд.
        </p>
      )}

      {Array.from(byDomain.entries()).map(([domain, rows]) => (
        <DomainGroup key={domain} domain={domain} rows={rows} siteId={siteId} />
      ))}
    </div>
  );
}

function DomainGroup({
  domain,
  rows,
  siteId,
}: {
  domain: string;
  rows: DeepExtractRow[];
  siteId: string;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="rounded-md border">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 text-sm font-medium hover:bg-muted/50"
      >
        <span className="flex items-center gap-2">
          {open ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
          {domain}
          <span className="text-xs text-muted-foreground font-normal">
            ({rows.length} {rows.length === 1 ? "разбор" : "разбора(-ов)"})
          </span>
        </span>
      </button>
      {open && (
        <div className="border-t divide-y">
          {rows.map((r) => (
            <CompetitorRow key={r.id} row={r} siteId={siteId} />
          ))}
        </div>
      )}
    </div>
  );
}

function CompetitorRow({ row, siteId }: { row: DeepExtractRow; siteId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-start gap-2 px-3 py-2 text-left text-sm hover:bg-muted/30"
      >
        {open ? (
          <ChevronDown className="h-4 w-4 mt-0.5 flex-shrink-0" />
        ) : (
          <ChevronRight className="h-4 w-4 mt-0.5 flex-shrink-0" />
        )}
        <div className="flex-1 min-w-0">
          <div className="truncate text-xs">{row.url}</div>
          <div className="text-[11px] text-muted-foreground">
            {row.title?.slice(0, 90) || "(no title)"} ·{" "}
            {new Date(row.extracted_at).toLocaleString("ru-RU")} ·{" "}
            {row.status === "completed" ? "✓" : `⚠ ${row.status}`}
          </div>
        </div>
      </button>
      {open && (
        <div className="px-3 pb-3 pt-1">
          {/* DeepExtractPanel competitor mode polls list — but here we
              already have the row, so just render its viewer inline.
              We call the panel in competitor mode for the «Обновить»
              button to also work. */}
          <DeepExtractPanel mode="competitor" siteId={siteId} url={row.url} />
        </div>
      )}
    </div>
  );
}
