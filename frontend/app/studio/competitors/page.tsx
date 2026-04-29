"use client";

/**
 * Studio /competitors — module page (PR-S5).
 *
 * Owner-facing question: «кто реально соревнуется со мной за мои
 * запросы, что у них есть, чего нет у меня, что мне делать первым».
 *
 * Replaces the old `/competitors` (kept alive until PR-S9 final
 * cleanup). Uses the same backend endpoints (admin_demand_map.py
 * exposes them at /api/v1/admin/sites/{id}/competitors/*) — Studio
 * is a UI on top of stable data, not a duplicate pipeline (CONCEPT
 * §2.2).
 *
 * Composition (top-down, by descending owner-action value):
 *   1. Header + last-run badge + 2 trigger buttons.
 *   2. «Что делать» — growth opportunities. Highest action density.
 *   3. «Кто конкуренты» — top-N competitor list by SERP hits.
 *   4. «Где я теряю» — content gaps (query × position table).
 *   5. «Что есть у них и чего нет у меня» — deep-dive signal matrix.
 *
 * Empty states explain WHY they are empty (CONCEPT §5) — never raw
 * "no data" without context.
 */

import { useEffect, useState } from "react";
import useSWR from "swr";
import Link from "next/link";

import { api } from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";
import { pluralRu } from "@/lib/format";
import { useTimeoutSetter } from "@/lib/hooks/use-timeout";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ArrowLeft,
  Swords,
  RefreshCw,
  Search as SearchIcon,
  Telescope,
  CheckCircle2,
  Info,
  ExternalLink,
  TrendingDown,
  Check,
  X,
  MapPin,
  Compass,
  Quote,
} from "lucide-react";
import { cn, getErrorMessage } from "@/lib/utils";

// ── Helpers ──────────────────────────────────────────────────────────

// getErrorMessage now in lib/utils.

const PRIORITY_STYLE: Record<string, string> = {
  high: "border-red-300 bg-red-50 text-red-900",
  medium: "border-amber-300 bg-amber-50 text-amber-900",
  low: "border-emerald-300 bg-emerald-50 text-emerald-900",
};

const PRIORITY_LABEL: Record<string, string> = {
  high: "важно",
  medium: "средне",
  low: "несрочно",
};

const CATEGORY_LABEL: Record<string, string> = {
  new_page: "новая страница",
  strengthen_existing_page: "усилить существующую",
  crossover_page: "объединить под один url",
  on_page_feature: "элемент на странице",
  schema: "schema.org",
  contact: "контакты",
};

// ── Page component ───────────────────────────────────────────────────

export default function StudioCompetitorsPage() {
  const { currentSite, loading: siteLoading } = useSite();
  const siteId = currentSite?.id || "";

  const [busy, setBusy] = useState<
    "discover" | "deep-dive" | "missing-landings" | null
  >(null);
  const [banner, setBanner] = useState<{
    kind: "ok" | "err";
    text: string;
  } | null>(null);
  const [appliedSet, setAppliedSet] = useState<Set<string>>(new Set());
  const setSafeTimeout = useTimeoutSetter();

  const { data: comp, error: compErr, isLoading: compLoading } = useSWR(
    siteId ? studioKey("competitors", siteId) : null,
    () => api.getCompetitors(siteId),
    {
      // While a trigger is running we have no `is_running` flag from
      // the backend (this endpoint is fire-and-forget) — `busy` is the
      // cleanest local signal. Poll every 4 s while a trigger is live;
      // stop once `busy` clears (after the 3 s cooldown).
      refreshInterval: () => (busy !== null ? 4000 : 0),
    },
  );
  const { data: gaps } = useSWR(
    siteId ? studioKey("comp_gaps", siteId) : null,
    () => api.getContentGaps(siteId, 30),
  );
  const { data: dive } = useSWR(
    siteId ? studioKey("comp_dive", siteId) : null,
    () => api.getCompetitorDeepDive(siteId),
  );
  const { data: opps } = useSWR(
    siteId ? studioKey("comp_opps", siteId) : null,
    () => api.getGrowthOpportunities(siteId),
    {
      refreshInterval: () => (busy !== null ? 4000 : 0),
    },
  );

  // V2 etap 6 — missing landing pages. Independent of competitor data:
  // it derives from `sites.understanding` + Page table, not SERPs. Same
  // pollwhile-busy pattern as the rest.
  const { data: missingLandings } = useSWR(
    siteId ? studioKey("missing_landings", siteId) : null,
    () => api.studioGetMissingLandings(siteId),
    {
      refreshInterval: () =>
        busy === "missing-landings" ? 4000 : 0,
    },
  );

  // Hydrate `appliedSet` from already-persisted outcomes so navigating
  // away + back doesn't lose the "applied" flag (PR-S8 will own a real
  // /studio/outcomes view; until then, this is the ground truth).
  // Outcomes carry `recommendation_id` which for opportunities is
  // exactly the `opp.id` we pass to markApplied.
  const { data: outcomesData } = useSWR(
    siteId ? studioKey("outcomes", siteId) : null,
    () => api.getOutcomes(siteId),
  );
  useEffect(() => {
    const ids = outcomesData?.outcomes
      ?.filter((o) => o.source === "opportunity")
      .map((o) => o.recommendation_id);
    if (!ids || ids.length === 0) return;
    setAppliedSet((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const id of ids) {
        if (!next.has(id)) {
          next.add(id);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [outcomesData]);

  async function onDiscover() {
    if (!siteId || busy) return;
    setBusy("discover");
    setBanner(null);
    try {
      const res = await api.triggerCompetitorDiscovery(siteId, 20, 10);
      setBanner({
        kind: "ok",
        text: `Разведка запущена · task ${res.task_id.slice(0, 8)}…. Discovery → автоматом запустит deep-dive. Результат через 1–2 минуты, страница обновится сама.`,
      });
      // SWR `refreshInterval` (above) handles polling while busy — no
      // manual setTimeout-mutate needed.
    } catch (e: unknown) {
      setBanner({ kind: "err", text: getErrorMessage(e) });
    } finally {
      setSafeTimeout(() => {
        setBusy((cur) => (cur === "discover" ? null : cur));
      }, 3000);
    }
  }

  async function onDeepDive() {
    if (!siteId || busy) return;
    setBusy("deep-dive");
    setBanner(null);
    try {
      const res = await api.triggerCompetitorDeepDive(siteId);
      setBanner({
        kind: "ok",
        text: `Глубокий анализ запущен · task ${res.task_id.slice(0, 8)}…. Это пересоберёт opportunities. Результат через ~1 минуту.`,
      });
    } catch (e: unknown) {
      setBanner({ kind: "err", text: getErrorMessage(e) });
    } finally {
      setSafeTimeout(() => {
        setBusy((cur) => (cur === "deep-dive" ? null : cur));
      }, 3000);
    }
  }

  async function onScanMissingLandings() {
    if (!siteId || busy) return;
    setBusy("missing-landings");
    setBanner(null);
    try {
      const res = await api.studioTriggerMissingLandingsScan(siteId);
      if (res.deduped) {
        setBanner({
          kind: "ok",
          text: `Сканирование уже идёт (run ${res.run_id.slice(0, 8)}…). Карточки обновятся сами.`,
        });
      } else {
        setBanner({
          kind: "ok",
          text: `Ищу услуги без посадочных… task ${res.task_id?.slice(0, 8)}…. Один LLM-вызов, ~30 секунд, ~10 центов.`,
        });
      }
    } catch (e: unknown) {
      setBanner({ kind: "err", text: getErrorMessage(e) });
    } finally {
      // Hold busy a bit longer than other actions — single LLM call
      // is ~30 s, 60 s safety net keeps polling alive end-to-end.
      // Functional `setBusy(prev => …)` so a trailing timer from this
      // click can never stomp another in-flight trigger (e.g. user
      // started «Глубокий анализ» before our safety fired).
      setSafeTimeout(() => {
        setBusy((cur) => (cur === "missing-landings" ? null : cur));
      }, 60_000);
    }
  }

  async function onApplyOpp(oppId: string, evidence: Record<string, unknown>) {
    if (!siteId || appliedSet.has(oppId)) return;
    const pageUrl =
      (evidence?.matched_page_url as string) ||
      (evidence?.our_url as string) ||
      undefined;
    try {
      await api.markApplied(siteId, oppId, "opportunity", pageUrl);
      setAppliedSet((s) => new Set(s).add(oppId));
    } catch (e: unknown) {
      setBanner({ kind: "err", text: getErrorMessage(e) });
    }
  }

  // ── Render guards ──────────────────────────────────────────────

  if (siteLoading) {
    return (
      <div className="p-4 sm:p-6 space-y-3">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (!currentSite) {
    return (
      <div className="p-4 sm:p-6">
        <Card className="border-dashed max-w-2xl">
          <CardContent className="pt-6 space-y-2">
            <div className="font-medium">Сайт не выбран</div>
            <p className="text-sm text-muted-foreground">
              Выбери сайт в свитчере слева.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const profile = comp?.profile;
  const competitors = profile?.competitors || [];
  const queriesProbed = profile?.queries_probed ?? null;
  const oppsList = opps?.opportunities || [];
  const gapsList = gaps?.gaps || [];

  return (
    <div className="p-4 sm:p-6 space-y-5 max-w-6xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <Link
            href="/studio"
            className="inline-flex items-center text-xs text-muted-foreground hover:text-foreground mb-1"
          >
            <ArrowLeft className="h-3 w-3 mr-1" /> К Студии
          </Link>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Swords className="h-6 w-6 text-primary" /> Конкуренты
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            {compLoading
              ? "загружаю…"
              : queriesProbed
                ? `Разведано по ${queriesProbed} ${pluralRu(queriesProbed, ["запросу", "запросам", "запросам"])} · найдено ${competitors.length} ${pluralRu(competitors.length, ["конкурент", "конкурента", "конкурентов"])} · ${oppsList.length} ${pluralRu(oppsList.length, ["opportunity", "opportunities", "opportunities"])}`
                : "разведка ещё не запускалась"}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <Button
            variant="outline"
            size="sm"
            onClick={onScanMissingLandings}
            disabled={busy !== null}
            title="Найти услуги в narrative бизнеса, под которые нет отдельной страницы. Защита от выдумок: каждое предложение требует точную цитату из narrative."
          >
            <Compass
              className={cn(
                "h-4 w-4 mr-2",
                busy === "missing-landings" && "animate-spin",
              )}
            />
            {busy === "missing-landings"
              ? "Ищу…"
              : "Услуги без страниц"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={onDeepDive}
            disabled={busy !== null || competitors.length === 0}
            title={
              competitors.length === 0
                ? "Сначала запусти разведку — без списка конкурентов нечего анализировать"
                : "Перезапустить crawl 5 топ-конкурентов и пересобрать opportunities"
            }
          >
            <Telescope
              className={cn(
                "h-4 w-4 mr-2",
                busy === "deep-dive" && "animate-pulse",
              )}
            />
            {busy === "deep-dive" ? "Запускаю…" : "Глубокий анализ"}
          </Button>
          <Button
            size="sm"
            onClick={onDiscover}
            disabled={busy !== null}
          >
            <RefreshCw
              className={cn(
                "h-4 w-4 mr-2",
                busy === "discover" && "animate-spin",
              )}
            />
            {busy === "discover" ? "Запускаю…" : "Пересобрать список"}
          </Button>
        </div>
      </div>

      {/* Banner */}
      {banner && (
        <div
          className={cn(
            "rounded-md border px-3 py-2 text-sm flex items-start gap-2",
            banner.kind === "ok" &&
              "border-emerald-300 bg-emerald-50 text-emerald-900",
            banner.kind === "err" && "border-red-300 bg-red-50 text-red-900",
          )}
        >
          <Info className="h-4 w-4 mt-0.5 flex-shrink-0" />
          <span>{banner.text}</span>
        </div>
      )}

      {/* Empty bootstrap state */}
      {!compLoading &&
        !compErr &&
        competitors.length === 0 &&
        oppsList.length === 0 && (
          <Card className="border-dashed">
            <CardContent className="pt-6 space-y-2">
              <div className="font-medium">Разведка ещё не запускалась</div>
              <p className="text-sm text-muted-foreground">
                Жми «Пересобрать список» — модуль возьмёт топ-20 запросов
                из Webmaster, отправит каждый в Яндекс Search API, соберёт
                кто стоит в топе и кеширует SERP. Сразу после этого
                автоматом запустится «Глубокий анализ»: crawl 5 лучших
                конкурентов + расчёт opportunities. Всего 1–2 минуты.
              </p>
            </CardContent>
          </Card>
        )}

      {/* 1. Opportunities — what to do (most actionable) */}
      {oppsList.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="font-medium text-lg">
              Что делать
              <span className="text-muted-foreground font-normal ml-2 text-base">
                ({oppsList.length})
              </span>
            </h2>
            <span className="text-xs text-muted-foreground">
              отсортировано по приоритету
            </span>
          </div>
          <div className="space-y-2">
            {oppsList.map((o) => (
              <OpportunityCard
                key={o.id}
                opp={o}
                applied={appliedSet.has(o.id)}
                onApply={() => onApplyOpp(o.id, o.evidence)}
              />
            ))}
          </div>
        </section>
      )}

      {/* 1.5 Missing landings — services in narrative without a page.
          We always render the section once we have a `missingLandings`
          response: it explains its own state (never run / clean / has
          gaps) per CONCEPT §5. */}
      {missingLandings && (
        <MissingLandingsSection
          data={missingLandings}
          onScan={onScanMissingLandings}
          busy={busy === "missing-landings"}
        />
      )}

      {/* 2. Competitor list */}
      {competitors.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="font-medium text-lg">Кто конкуренты</h2>
            <span className="text-xs text-muted-foreground">
              отсортировано по числу SERP-попаданий
            </span>
          </div>
          <div className="space-y-2">
            {competitors.map((c) => (
              <CompetitorRow key={c.domain} comp={c} />
            ))}
          </div>
        </section>
      )}

      {/* 3. Gaps */}
      {!compLoading && (
        <section className="space-y-3">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="font-medium text-lg">
              Где я теряю
              {gapsList.length > 0 && (
                <span className="text-muted-foreground font-normal ml-2 text-base">
                  ({gapsList.length})
                </span>
              )}
            </h2>
          </div>
          {gapsList.length === 0 ? (
            <Card className="border-dashed">
              <CardContent className="pt-6 text-sm text-muted-foreground">
                {competitors.length === 0
                  ? "Чтобы найти gap'ы, сначала нужна разведка конкурентов."
                  : "Не нашли запросов, где конкуренты в топ-5, а вы вне топ-30. Это значит либо выборка маленькая, либо у вас плотные позиции — проверьте /studio/queries."}
                {gaps?.note && <span className="block mt-1">{gaps.note}</span>}
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardContent className="pt-4 px-0 overflow-x-auto">
                <table className="w-full text-sm min-w-[560px]">
                  <thead className="text-xs text-muted-foreground border-b">
                    <tr>
                      <th className="text-left px-4 py-2 font-normal">Запрос</th>
                      <th className="text-right px-4 py-2 font-normal w-20">
                        Моя
                      </th>
                      <th className="text-right px-4 py-2 font-normal w-20">
                        У них
                      </th>
                      <th className="text-left px-4 py-2 font-normal">
                        Главный конкурент
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {gapsList.slice(0, 30).map((g, idx) => (
                      <tr key={idx} className="border-b last:border-b-0">
                        <td className="px-4 py-2 truncate max-w-[300px]">
                          {g.query}
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums">
                          {g.site_position == null ? (
                            <span className="text-red-700 font-medium">
                              30+
                            </span>
                          ) : (
                            <span>{g.site_position}</span>
                          )}
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums">
                          <span className="text-emerald-700 font-medium">
                            {g.competitor_position}
                          </span>
                        </td>
                        <td className="px-4 py-2">
                          <a
                            href={g.competitor_url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-xs text-foreground hover:text-primary inline-flex items-center gap-1 truncate max-w-[260px]"
                          >
                            {g.competitor_domain}
                            <ExternalLink className="h-3 w-3 flex-shrink-0" />
                          </a>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          )}
        </section>
      )}

      {/* 4. Deep-dive comparison */}
      {dive?.competitors && dive.competitors.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="font-medium text-lg">
              Что есть у них и чего нет у меня
            </h2>
          </div>
          <DeepDiveTable own={dive.self} competitors={dive.competitors} />
        </section>
      )}
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────────────

function OpportunityCard({
  opp,
  applied,
  onApply,
}: {
  opp: {
    id: string;
    source: string;
    category: string;
    priority: string;
    title_ru: string;
    reasoning_ru: string;
    suggested_action_ru: string;
    evidence: Record<string, unknown>;
  };
  applied: boolean;
  onApply: () => void;
}) {
  const ps = PRIORITY_STYLE[opp.priority] || PRIORITY_STYLE.medium;
  const pageUrl = (opp.evidence?.matched_page_url as string) || null;
  const exampleQueries = (opp.evidence?.example_queries as string[]) || [];

  return (
    <Card className={cn(applied && "opacity-70")}>
      <CardContent className="pt-5 space-y-2">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span
            className={cn(
              "text-[10px] uppercase tracking-wide rounded-full border px-2 py-0.5",
              ps,
            )}
          >
            {PRIORITY_LABEL[opp.priority] || opp.priority}
          </span>
          <span className="text-xs text-muted-foreground">
            {CATEGORY_LABEL[opp.category] || opp.category}
          </span>
          <span className="font-medium">{opp.title_ru}</span>
          {applied && (
            <Badge
              variant="outline"
              className="ml-auto border-emerald-300 bg-emerald-50 text-emerald-800"
            >
              отмечено
            </Badge>
          )}
        </div>
        <p className="text-sm leading-snug">{opp.reasoning_ru}</p>
        <div className="text-sm">
          <span className="font-medium">Что делать: </span>
          {opp.suggested_action_ru}
        </div>
        {(pageUrl || exampleQueries.length > 0) && (
          <div className="text-xs text-muted-foreground space-y-0.5 pt-1 border-t">
            {pageUrl && (
              <div>
                Связано со страницей:{" "}
                <a
                  href={pageUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="text-foreground hover:text-primary inline-flex items-center gap-1"
                >
                  {pageUrl}
                  <ExternalLink className="h-3 w-3" />
                </a>
              </div>
            )}
            {exampleQueries.length > 0 && (
              <div>
                Запросы: {exampleQueries.slice(0, 5).join(" · ")}
              </div>
            )}
          </div>
        )}
        {!applied && (
          <div className="pt-2 border-t">
            <Button size="sm" onClick={onApply}>
              <CheckCircle2 className="h-4 w-4 mr-1.5" />
              Применил & замерить эффект
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function CompetitorRow({
  comp,
}: {
  comp: {
    domain: string;
    serp_hits: number;
    best_position: number;
    avg_position: number;
    example_url: string;
    example_title: string;
    example_query: string;
  };
}) {
  return (
    <Card>
      <CardContent className="py-3 flex items-center gap-3 flex-wrap">
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="font-medium">{comp.domain}</span>
            {comp.best_position <= 3 && (
              <Badge
                variant="outline"
                className="text-[10px] border-emerald-300 bg-emerald-50 text-emerald-800"
              >
                в топ-3
              </Badge>
            )}
          </div>
          <a
            href={comp.example_url}
            target="_blank"
            rel="noreferrer"
            className="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1 truncate"
            title={`пример страницы по запросу «${comp.example_query}»`}
          >
            {comp.example_title || comp.example_url}
            <ExternalLink className="h-3 w-3 flex-shrink-0" />
          </a>
        </div>
        <div className="grid grid-cols-3 gap-3 text-right text-xs tabular-nums">
          <div>
            <div className="text-muted-foreground">в SERP</div>
            <div className="font-medium">{comp.serp_hits}</div>
          </div>
          <div>
            <div className="text-muted-foreground">лучшая</div>
            <div className="font-medium">{comp.best_position}</div>
          </div>
          <div>
            <div className="text-muted-foreground">средняя</div>
            <div className="font-medium">{comp.avg_position.toFixed(1)}</div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function DeepDiveTable({
  own,
  competitors,
}: {
  own: Record<string, unknown> | null;
  competitors: Array<Record<string, unknown>>;
}) {
  if (!own) return null;
  const rows: Array<{ key: string; label: string }> = [
    { key: "has_price", label: "Цены на странице" },
    { key: "has_booking_cta", label: "Кнопка «Забронировать»" },
    { key: "has_reviews", label: "Отзывы" },
    { key: "has_phone", label: "Телефон" },
    { key: "has_telegram", label: "Telegram" },
    { key: "has_whatsapp", label: "WhatsApp" },
  ];
  const allDomains = [own, ...competitors];

  return (
    <Card>
      <CardContent className="pt-4 px-0 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-muted-foreground border-b">
            <tr>
              <th className="text-left px-4 py-2 font-normal">Сигнал</th>
              {allDomains.map((d, i) => (
                <th
                  key={(d.domain as string) + i}
                  className={cn(
                    "px-3 py-2 font-medium text-center min-w-[110px]",
                    i === 0 && "bg-emerald-50 text-emerald-900",
                  )}
                >
                  {i === 0 ? "Я" : <span className="font-normal">{d.domain as string}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-b last:border-b-0">
                <td className="px-4 py-2 text-muted-foreground">{r.label}</td>
                {allDomains.map((d, i) => (
                  <td
                    key={i}
                    className={cn(
                      "px-3 py-2 text-center",
                      i === 0 && "bg-emerald-50/30",
                    )}
                  >
                    {d[r.key] ? (
                      <Check className="h-4 w-4 text-emerald-600 inline" />
                    ) : (
                      <X className="h-4 w-4 text-muted-foreground/40 inline" />
                    )}
                  </td>
                ))}
              </tr>
            ))}
            {/* Schema row */}
            <tr>
              <td className="px-4 py-2 text-muted-foreground">Schema.org</td>
              {allDomains.map((d, i) => {
                const types = (d.schema_types as string[]) || [];
                return (
                  <td
                    key={i}
                    className={cn(
                      "px-3 py-2 text-center text-xs",
                      i === 0 && "bg-emerald-50/30",
                    )}
                  >
                    {types.length === 0 ? (
                      <span className="text-muted-foreground/60">—</span>
                    ) : (
                      <span title={types.join(", ")}>{types.length} типов</span>
                    )}
                  </td>
                );
              })}
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

// ── Missing landings section ─────────────────────────────────────────

const MISSING_PRIORITY_STYLE: Record<string, string> = {
  high: "border-red-300 bg-red-50 text-red-900",
  medium: "border-amber-300 bg-amber-50 text-amber-900",
  low: "border-emerald-300 bg-emerald-50 text-emerald-900",
};

const MISSING_PRIORITY_LABEL: Record<string, string> = {
  high: "важно",
  medium: "средне",
  low: "несрочно",
};

type MissingLandingsData = Awaited<
  ReturnType<typeof api.studioGetMissingLandings>
>;

function MissingLandingsSection({
  data,
  onScan,
  busy,
}: {
  data: MissingLandingsData;
  onScan: () => void;
  busy: boolean;
}) {
  const items = data.items || [];
  const neverRun = !data.computed_at;
  const computedAge = data.computed_at
    ? formatDateRu(data.computed_at)
    : null;

  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <h2 className="font-medium text-lg flex items-center gap-2">
          <MapPin className="h-5 w-5 text-primary" />
          Услуги без посадочных страниц
          {items.length > 0 && (
            <span className="text-muted-foreground font-normal text-base">
              ({items.length})
            </span>
          )}
        </h2>
        {!neverRun && (
          <span className="text-xs text-muted-foreground">
            проверено {computedAge}
            {data.input_pages != null
              ? ` · ${data.input_pages} ${pluralRu(data.input_pages, ["страница", "страницы", "страниц"])}`
              : ""}
            {data.cost_usd != null
              ? ` · стоимость $${data.cost_usd.toFixed(4)}`
              : ""}
          </span>
        )}
      </div>

      {neverRun ? (
        <Card className="border-dashed">
          <CardContent className="pt-6 space-y-3">
            <p className="text-sm text-muted-foreground">
              Модуль ищет услуги, которые упомянуты в описании бизнеса
              (narrative + observed_facts), но под них нет отдельной
              страницы. Пример: «экспедиции в Крым» в описании, но
              страницы <code>/experiences/exp-crimea</code> на сайте нет.
            </p>
            <p className="text-xs text-muted-foreground">
              Защита от выдумок: каждое предложение требует точную
              цитату из narrative — если её нет, элемент отбрасывается.
            </p>
            <Button onClick={onScan} disabled={busy} size="sm">
              <Compass
                className={cn("h-4 w-4 mr-2", busy && "animate-spin")}
              />
              {busy ? "Ищу…" : "Найти услуги без страниц"}
            </Button>
          </CardContent>
        </Card>
      ) : items.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="pt-6 text-sm text-muted-foreground">
            {data.summary_ru ||
              "Все упомянутые услуги покрыты страницами."}
            {data.rejected_no_evidence
              ? ` LLM предложил ${data.rejected_no_evidence}, но evidence-фильтр отбросил всё (модель не сослалась на конкретный текст).`
              : ""}
          </CardContent>
        </Card>
      ) : (
        <>
          {data.summary_ru && (
            <p className="text-sm text-muted-foreground">
              {data.summary_ru}
            </p>
          )}
          <div className="space-y-2">
            {items.map((item, idx) => (
              <MissingLandingCard key={idx} item={item} />
            ))}
          </div>
          {data.rejected_no_evidence ? (
            <p className="text-xs text-muted-foreground">
              Дополнительно {data.rejected_no_evidence} предложений LLM
              было отброшено: модель не сослалась на конкретный
              фрагмент описания бизнеса.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

function MissingLandingCard({
  item,
}: {
  item: MissingLandingsData["items"][number];
}) {
  const ps =
    MISSING_PRIORITY_STYLE[item.priority] ||
    MISSING_PRIORITY_STYLE.medium;
  return (
    <Card>
      <CardContent className="pt-5 space-y-3">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span
            className={cn(
              "text-[10px] uppercase tracking-wide rounded-full border px-2 py-0.5",
              ps,
            )}
          >
            {MISSING_PRIORITY_LABEL[item.priority] || item.priority}
          </span>
          <span className="font-medium text-base">
            {item.service_name}
          </span>
          {item.suggested_url_path && (
            <code className="text-xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
              {item.suggested_url_path}
            </code>
          )}
        </div>

        {item.why_it_matters_ru && (
          <p className="text-sm leading-snug">{item.why_it_matters_ru}</p>
        )}

        <div className="rounded-md border border-amber-200 bg-amber-50/50 px-3 py-2 text-xs flex items-start gap-2">
          <Quote className="h-3.5 w-3.5 mt-0.5 flex-shrink-0 text-amber-700" />
          <div>
            <div className="text-[10px] uppercase tracking-wide text-amber-800/80 mb-0.5">
              Цитата из описания бизнеса
            </div>
            <span className="italic">«{item.evidence_quote}»</span>
          </div>
        </div>

        {item.closest_existing_url && (
          <div className="text-xs text-muted-foreground flex items-center gap-1.5">
            Ближайшая существующая страница:{" "}
            <a
              href={item.closest_existing_url}
              target="_blank"
              rel="noreferrer"
              className="text-foreground hover:text-primary inline-flex items-center gap-1"
            >
              {item.closest_existing_url}
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function formatDateRu(iso: string): string {
  try {
    const d = new Date(iso);
    const diffMs = Date.now() - d.getTime();
    const minutes = Math.round(diffMs / 60_000);
    if (minutes < 1) return "только что";
    if (minutes < 60)
      return `${minutes} ${pluralRu(minutes, ["минуту", "минуты", "минут"])} назад`;
    const hours = Math.round(minutes / 60);
    if (hours < 24)
      return `${hours} ${pluralRu(hours, ["час", "часа", "часов"])} назад`;
    const days = Math.round(hours / 24);
    return `${days} ${pluralRu(days, ["день", "дня", "дней"])} назад`;
  } catch {
    return iso;
  }
}
