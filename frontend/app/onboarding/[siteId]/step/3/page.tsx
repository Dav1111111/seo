"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { ChipEditor } from "@/components/onboarding/chip-editor";
import { StepNav } from "@/components/onboarding/step-nav";
import {
  AlertCircle, RefreshCw, Search, ExternalLink, CheckCircle2, X,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface DiscoveredCompetitor {
  domain: string;
  serp_hits: number;
  best_position: number;
  avg_position: number;
  example_url: string;
  example_title: string;
  example_query: string;
}

// While the discovery task is enqueued we poll the /competitors endpoint
// and look at profile.queries_probed — it only becomes >0 once the
// Celery task has written its result.
const POLL_MS = 4000;
const POLL_MAX_ATTEMPTS = 60;  // ~4 минуты макс

export default function Step3Competitors() {
  const { siteId } = useParams<{ siteId: string }>();
  const [saving, setSaving] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Local state: which discovered domains the user has rejected, plus
  // manually-added known competitors (brands or domains).
  const [rejected, setRejected] = useState<Set<string>>(new Set());
  const [manualBrands, setManualBrands] = useState<string[]>([]);
  const [manualDomains, setManualDomains] = useState<string[]>([]);

  const { data, isLoading, mutate } = useSWR(
    siteId ? `onb-competitors-${siteId}` : null,
    () => api.getCompetitors(siteId),
    { refreshInterval: 0 },
  );

  // Seed manual lists once we have a persisted state baseline.
  useEffect(() => {
    if (!data) return;
    // Anything currently in competitor_domains that isn't in the SERP
    // discovery = manually added by a previous session.
    const discovered = new Set(
      (data.profile?.competitors || []).map((c) => c.domain),
    );
    const extra = (data.competitor_domains || []).filter((d) => !discovered.has(d));
    if (manualDomains.length === 0 && extra.length > 0) setManualDomains(extra);
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  const discovered: DiscoveredCompetitor[] = useMemo(
    () => data?.profile?.competitors || [],
    [data],
  );
  const hasDiscovery = (data?.profile?.queries_probed || 0) > 0;

  function toggleReject(domain: string) {
    setRejected((prev) => {
      const next = new Set(prev);
      if (next.has(domain)) next.delete(domain);
      else next.add(domain);
      return next;
    });
  }

  async function runDiscovery() {
    if (!siteId) return;
    setDiscovering(true); setErr(null);
    try {
      await api.triggerCompetitorDiscovery(siteId, 20, 10);
      // Poll — queries_probed increments once task commits.
      const startedAt = data?.profile?.queries_probed ?? 0;
      for (let i = 0; i < POLL_MAX_ATTEMPTS; i++) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        const fresh = await mutate();
        if ((fresh?.profile?.queries_probed || 0) > startedAt) {
          setRejected(new Set());  // fresh run → reset exclusions
          break;
        }
      }
    } catch (e: any) {
      setErr(e?.message ?? String(e));
    } finally {
      setDiscovering(false);
    }
  }

  async function persist() {
    if (!siteId) return;
    setSaving(true); setErr(null);
    try {
      const confirmed_domains = [
        ...discovered
          .filter((c) => !rejected.has(c.domain))
          .map((c) => c.domain),
        ...manualDomains,
      ];
      await api.patchOnboardingCompetitors(siteId, {
        competitor_domains: Array.from(new Set(confirmed_domains)),
        competitor_brands: manualBrands,
      });
      await api.patchOnboardingStep(siteId, "confirm_queries");
      mutate();
    } catch (e: any) {
      setErr(e?.message ?? String(e));
      throw e;
    } finally {
      setSaving(false);
    }
  }

  const activeCount =
    discovered.filter((c) => !rejected.has(c.domain)).length +
    manualDomains.length;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h2 className="text-lg font-semibold mb-1">С кем ты реально конкурируешь</h2>
          <p className="text-sm text-muted-foreground">
            Я схожу в Яндекс по твоим главным запросам и вытащу тех, кто реально
            ранжируется рядом с тобой. Ты просто подтверждаешь или выкидываешь тех,
            кого считаешь мимо.
          </p>
        </div>
        <Button size="sm" onClick={runDiscovery} disabled={discovering}>
          <Search className={cn("mr-2 h-4 w-4", discovering && "animate-pulse")} />
          {hasDiscovery ? "Пересобрать" : "Найти конкурентов"}
        </Button>
      </div>

      {err && (
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900 flex items-start gap-2">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" /> <span>{err}</span>
        </div>
      )}

      {/* Discovery results */}
      {isLoading ? (
        <Skeleton className="h-48" />
      ) : discovering && !hasDiscovery ? (
        <div className="rounded-lg border bg-muted/30 p-6 text-center space-y-2">
          <RefreshCw className="h-5 w-5 mx-auto animate-spin text-primary" />
          <p className="text-sm text-muted-foreground">
            Ищу в Яндексе по твоим запросам… это занимает 1–3 минуты.
          </p>
        </div>
      ) : !hasDiscovery ? (
        <div className="rounded-lg border border-dashed bg-muted/30 p-6 text-center">
          <p className="text-sm text-muted-foreground mb-3">
            Ещё никого не искал. Нажми «Найти конкурентов» — это бесплатно, занимает
            пару минут.
          </p>
        </div>
      ) : discovered.length === 0 ? (
        <div className="rounded-lg border border-dashed bg-muted/30 p-6 text-center text-sm text-muted-foreground">
          В SERP по твоим запросам я не нашёл подходящих доменов. Попробуй добавить
          конкурентов вручную ниже — или пересобрать после того, как уточнишь услуги.
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>
              Проверил {data?.profile?.queries_probed || 0} запросов ·{" "}
              нашёл {discovered.length} доменов ·{" "}
              подтверждено: <b>{activeCount}</b>
            </span>
            <span>
              стоимость разведки: ~${Number(data?.profile?.cost_usd ?? 0).toFixed(3)}
            </span>
          </div>

          <ul className="space-y-2">
            {discovered.map((c) => {
              const isRejected = rejected.has(c.domain);
              return (
                <li
                  key={c.domain}
                  className={cn(
                    "rounded-lg border p-3 flex items-start gap-3 transition-colors",
                    isRejected
                      ? "bg-muted/40 opacity-60"
                      : "bg-emerald-50/30 border-emerald-200/60",
                  )}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <a
                        href={`https://${c.domain}`}
                        target="_blank"
                        rel="noreferrer"
                        className="text-sm font-semibold hover:underline truncate"
                      >
                        {c.domain}
                      </a>
                      <Badge variant="outline" className="text-[10px]">
                        {c.serp_hits}× в выдаче
                      </Badge>
                      <Badge variant="outline" className="text-[10px]">
                        сред. позиция {c.avg_position.toFixed(1)}
                      </Badge>
                      {c.best_position <= 3 && (
                        <Badge className="text-[10px] bg-amber-500/90">
                          топ-3 по запросу
                        </Badge>
                      )}
                    </div>
                    <p className="text-xs mt-1 leading-snug line-clamp-2">
                      {c.example_title || c.example_url}
                    </p>
                    <div className="text-[11px] text-muted-foreground mt-0.5 flex items-center gap-1">
                      <Search className="h-3 w-3" />
                      <span className="font-mono">{c.example_query}</span>
                      {c.example_url && (
                        <a
                          href={c.example_url}
                          target="_blank"
                          rel="noreferrer"
                          className="ml-1 hover:text-foreground inline-flex items-center"
                          title="Открыть страницу"
                        >
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      )}
                    </div>
                  </div>
                  <Button
                    size="sm"
                    variant={isRejected ? "outline" : "ghost"}
                    onClick={() => toggleReject(c.domain)}
                    className="shrink-0"
                  >
                    {isRejected ? (
                      <>
                        <CheckCircle2 className="h-4 w-4 mr-1" /> вернуть
                      </>
                    ) : (
                      <>
                        <X className="h-4 w-4 mr-1" /> не моё
                      </>
                    )}
                  </Button>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* Manual additions */}
      <div className="pt-2 border-t space-y-5">
        <div>
          <div className="text-xs text-muted-foreground mb-2">
            Добавь своих, если кого-то я не нашёл
          </div>
          <ChipEditor
            label="Домены-конкуренты"
            tone="secondary"
            values={manualDomains}
            onChange={setManualDomains}
            placeholder="example.ru"
          />
        </div>
        <ChipEditor
          label="Бренды-конкуренты (опционально)"
          tone="secondary"
          values={manualBrands}
          onChange={setManualBrands}
          placeholder="Название бренда…"
        />
      </div>

      <StepNav siteId={siteId} step={3} onNext={persist} saving={saving} />
    </div>
  );
}
