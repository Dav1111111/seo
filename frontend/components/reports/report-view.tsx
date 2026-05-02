"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface DemandSummary {
  clusters?: number;
  observed_impressions?: number;
  covered?: number;
}

interface ClusterRef {
  cluster_key: string;
  name_ru: string;
  business_relevance: number;
  coverage_score?: number | null;
}

interface DiagnosticPayload {
  available?: boolean;
  root_problem_classification?: string;
  root_problem_ru?: string;
  supporting_symptoms_ru?: string[];
  recommended_first_actions_ru?: string[];
  brand_demand?: DemandSummary;
  non_brand_demand?: DemandSummary;
  missing_target_clusters?: ClusterRef[];
}

interface ReportMetaPayload {
  status: string;
  site_host: string;
  week_start: string;
  week_end: string;
  generated_at: string;
  builder_version: string;
  llm_cost_usd: number;
  generation_ms: number;
}

interface ExecutivePayload {
  health_score: number;
  wow_impressions_pct?: number | null;
  wow_clicks_pct?: number | null;
  health_score_delta?: number | null;
  prose_ru?: string;
  top_wins?: string[];
  top_losses?: string[];
}

interface ActionPlanItemPayload {
  recommendation_id: string;
  page_url?: string | null;
  priority: string;
  priority_score: number;
  category: string;
  suggested_owner: string;
  eta_ru: string;
  reasoning_ru: string;
  expected_lift_impressions?: number | null;
}

interface ActionPlanPayload {
  items?: ActionPlanItemPayload[];
  narrative_ru?: string;
  total_in_backlog?: number;
}

interface CoveragePayload {
  strong_count: number;
  weak_count: number;
  missing_count: number;
  open_decisions_count?: number;
  intent_gaps?: string[];
}

interface QueryMovePayload {
  query_text: string;
  impressions_diff: number;
}

interface QueryTrendsPayload {
  data_available?: boolean;
  note_ru?: string;
  totals_this_week?: { impressions?: number };
  totals_prev_week?: { impressions?: number };
  wow_diff?: { impressions_pct?: number | null };
  top_movers_up?: QueryMovePayload[];
  top_movers_down?: QueryMovePayload[];
  new_queries?: string[];
  lost_queries?: string[];
}

interface PageFindingPayload {
  page_id: string;
  page_url?: string | null;
  target_intent_code: string;
  critical_count: number;
  high_count: number;
  medium_count: number;
  top_issues?: string[];
  missing_eeat_signals?: string[];
}

interface PageFindingsPayload {
  reviews_run_count: number;
  pages_reviewed: number;
  warning_ru?: string | null;
  by_priority_count?: Record<string, number>;
  pages?: PageFindingPayload[];
}

interface TechnicalIssuePayload {
  code: string;
  severity: string;
  title_ru: string;
  detail_ru: string;
  count: number;
  examples?: string[];
}

interface TechnicalPayload {
  pages_total: number;
  pages_indexed: number;
  pages_non_200: number;
  indexation_rate: number;
  duplicates_suspected: number;
  fingerprint_stale_count: number;
  technical_score?: number;
  robots?: { ok?: boolean };
  sitemap?: { valid_xml?: boolean; urls_declared?: number };
  checks?: Record<string, number | undefined>;
  schema_types?: Record<string, number>;
  issues?: TechnicalIssuePayload[];
  warning_ru?: string | null;
}

interface ReportPayload {
  diagnostic?: DiagnosticPayload | null;
  meta: ReportMetaPayload;
  executive: ExecutivePayload;
  action_plan: ActionPlanPayload;
  coverage: CoveragePayload;
  query_trends: QueryTrendsPayload;
  page_findings: PageFindingsPayload;
  technical: TechnicalPayload;
}

const PRIORITY_TONE: Record<string, string> = {
  critical: "bg-rose-100 text-rose-800 border-rose-300",
  high:     "bg-orange-100 text-orange-800 border-orange-300",
  medium:   "bg-amber-100 text-amber-800 border-amber-300",
  low:      "bg-slate-100 text-slate-700 border-slate-300",
};

const SEVERITY_TONE: Record<string, string> = {
  critical: "border-rose-300 bg-rose-50 text-rose-900",
  high: "border-orange-300 bg-orange-50 text-orange-900",
  medium: "border-amber-300 bg-amber-50 text-amber-900",
  low: "border-slate-200 bg-slate-50 text-slate-800",
};

function HealthRing({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(100, score));
  const tone = pct >= 80 ? "text-emerald-600" : pct >= 50 ? "text-amber-600" : "text-rose-600";
  return (
    <div className={cn("inline-flex flex-col items-center", tone)}>
      <span className="text-5xl font-bold tabular-nums leading-none">{pct}</span>
      <span className="text-[11px] uppercase tracking-wide text-muted-foreground mt-1">Health</span>
    </div>
  );
}

function pct(n: number | null | undefined) {
  if (n == null) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
}

export function ReportView({ payload, present = false }: { payload: ReportPayload; present?: boolean }) {
  const m = payload.meta;
  const e = payload.executive;
  const ap = payload.action_plan;
  const cov = payload.coverage;
  const tr = payload.query_trends;
  const pf = payload.page_findings;
  const tech = payload.technical;
  const d = payload.diagnostic;
  const diagnosticSymptoms = d?.supporting_symptoms_ru ?? [];
  const diagnosticActions = d?.recommended_first_actions_ru ?? [];
  const missingClusters = d?.missing_target_clusters ?? [];
  const topWins = e.top_wins ?? [];
  const topLosses = e.top_losses ?? [];
  const intentGaps = cov.intent_gaps ?? [];
  const moversUp = tr.top_movers_up ?? [];
  const moversDown = tr.top_movers_down ?? [];
  const newQueries = tr.new_queries ?? [];
  const lostQueries = tr.lost_queries ?? [];
  const priorityCounts = pf.by_priority_count ?? {};
  const techSchemaTypes = tech.schema_types ?? {};
  const techIssues = tech.issues ?? [];

  return (
    <div className={cn(
      "space-y-8",
      present && "max-w-4xl mx-auto text-[15px] leading-relaxed",
    )}>
      {/* Header */}
      <section className="space-y-1">
        <div className="flex items-baseline justify-between gap-4 flex-wrap">
          <h1 className={cn("font-bold", present ? "text-4xl" : "text-3xl")}>
            Еженедельный SEO-отчёт — {m.site_host}
          </h1>
          <Badge variant={m.status === "completed" ? "default" : "outline"}>{m.status}</Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          {m.week_start} — {m.week_end} (UTC) · сформирован {new Date(m.generated_at).toLocaleString("ru")}
          {" "}· v{m.builder_version} · LLM ${Number(m.llm_cost_usd).toFixed(4)} · {m.generation_ms}ms
        </p>
      </section>

      {/* 0. Diagnostic */}
      {d?.available && (
        <Card className="border-primary/40 bg-primary/5 break-inside-avoid">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              🧭 Корневая проблема
              <Badge variant="outline" className="text-xs font-normal">
                {d.root_problem_classification}
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-base">{d.root_problem_ru}</p>

            {diagnosticSymptoms.length > 0 && (
              <div>
                <div className="text-xs font-semibold text-muted-foreground uppercase mb-1">Сопутствующие симптомы</div>
                <ul className="list-disc pl-5 text-sm space-y-1">
                  {diagnosticSymptoms.map((s, i) => <li key={i}>{s}</li>)}
                </ul>
              </div>
            )}

            {diagnosticActions.length > 0 && (
              <div>
                <div className="text-xs font-semibold text-muted-foreground uppercase mb-1">Что делать в первую очередь</div>
                <ol className="list-decimal pl-5 text-sm space-y-1">
                  {diagnosticActions.map((s, i) => <li key={i}>{s}</li>)}
                </ol>
              </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
              {d.brand_demand && (
                <div className="rounded border bg-background/80 p-3">
                  <div className="text-xs font-semibold uppercase text-muted-foreground mb-1">Брендовый спрос</div>
                  <div>{d.brand_demand.clusters ?? 0} кластеров · {d.brand_demand.observed_impressions ?? 0} показов</div>
                </div>
              )}
              {d.non_brand_demand && (
                <div className="rounded border bg-background/80 p-3">
                  <div className="text-xs font-semibold uppercase text-muted-foreground mb-1">Небрендовый спрос</div>
                  <div>
                    {d.non_brand_demand.clusters ?? 0} кластеров · {d.non_brand_demand.observed_impressions ?? 0} показов
                    {" "}· покрыто {d.non_brand_demand.covered ?? 0}/{d.non_brand_demand.clusters ?? 0}
                  </div>
                </div>
              )}
            </div>

            {missingClusters.length > 0 && (
              <div>
                <div className="text-xs font-semibold text-muted-foreground uppercase mb-1">Приоритетные пробелы (топ-10)</div>
                <ul className="space-y-1 text-sm">
                  {missingClusters.slice(0, 10).map((c) => (
                    <li key={c.cluster_key} className="flex items-baseline justify-between gap-2">
                      <span>{c.name_ru}</span>
                      <span className="text-xs text-muted-foreground tabular-nums">
                        релевантность {c.business_relevance.toFixed(2)} · покрытие {c.coverage_score != null ? c.coverage_score.toFixed(2) : "—"}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* 1. Executive */}
      <Card className="break-inside-avoid">
        <CardHeader>
          <CardTitle className="text-lg">1. Резюме</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-6 flex-wrap">
            <HealthRing score={e.health_score} />
            <div className="space-y-1">
              <div className="text-sm"><b>Показы WoW:</b> {pct(e.wow_impressions_pct)}</div>
              <div className="text-sm"><b>Клики WoW:</b> {pct(e.wow_clicks_pct)}</div>
              {e.health_score_delta != null && (
                <div className="text-sm"><b>Δ Health:</b> {e.health_score_delta > 0 ? "+" : ""}{e.health_score_delta}</div>
              )}
            </div>
          </div>
          {e.prose_ru && <p className="text-sm leading-relaxed">{e.prose_ru}</p>}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {topWins.length > 0 && (
              <div>
                <div className="text-xs font-semibold uppercase text-emerald-700 mb-1">Победы недели</div>
                <ul className="space-y-1 text-sm">
                  {topWins.map((w, i) => <li key={i}>✅ {w}</li>)}
                </ul>
              </div>
            )}
            {topLosses.length > 0 && (
              <div>
                <div className="text-xs font-semibold uppercase text-rose-700 mb-1">Потери недели</div>
                <ul className="space-y-1 text-sm">
                  {topLosses.map((l, i) => <li key={i}>⚠️ {l}</li>)}
                </ul>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* 2. Action Plan */}
      <Card className="break-inside-avoid">
        <CardHeader>
          <CardTitle className="text-lg">
            2. План на эту неделю · топ-{ap.items?.length ?? 0} из {ap.total_in_backlog ?? 0}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {ap.narrative_ru && <p className="text-sm">{ap.narrative_ru}</p>}
          {!ap.items?.length ? (
            <p className="text-sm text-muted-foreground italic">Нет приоритетных задач.</p>
          ) : (
            <ol className="space-y-2">
              {ap.items.map((it, i) => (
                <li key={it.recommendation_id} className="rounded border p-3 text-sm break-inside-avoid">
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <span className="font-bold">#{i + 1}</span>
                    <Badge variant="outline" className={cn("text-xs", PRIORITY_TONE[it.priority])}>
                      {it.priority} · {it.priority_score.toFixed(1)}
                    </Badge>
                    <Badge variant="secondary" className="text-xs">{it.category}</Badge>
                    <Badge variant="outline" className="text-xs">{it.suggested_owner}</Badge>
                    <Badge variant="outline" className="text-xs">{it.eta_ru}</Badge>
                  </div>
                  {it.page_url && (
                    <div className="text-xs text-muted-foreground truncate mb-1">{it.page_url}</div>
                  )}
                  <div className="leading-snug">{it.reasoning_ru}</div>
                  {it.expected_lift_impressions != null && (
                    <div className="text-xs text-muted-foreground mt-1">
                      Ожидаемый рост показов: ~{it.expected_lift_impressions}
                    </div>
                  )}
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>

      {/* 3. Coverage */}
      <Card className="break-inside-avoid">
        <CardHeader>
          <CardTitle className="text-lg">
            3. Покрытие интентов · сильных: {cov.strong_count} · слабых: {cov.weak_count} · пропусков: {cov.missing_count}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div>Открытых решений в очереди: <b>{cov.open_decisions_count ?? 0}</b></div>
          {intentGaps.length > 0 && (
            <div>
              <div className="text-xs font-semibold uppercase text-muted-foreground mb-1">Пробелы</div>
              <ul className="list-disc pl-5 space-y-1">
                {intentGaps.map((g, i) => <li key={i}>{g}</li>)}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 4. Query Trends */}
      <Card className="break-inside-avoid">
        <CardHeader>
          <CardTitle className="text-lg">4. Тренды запросов</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {!tr.data_available ? (
            <p className="text-muted-foreground italic">{tr.note_ru || "Нет данных"}</p>
          ) : (
            <>
              <div>
                Показы: <b>{(tr.totals_this_week?.impressions ?? 0).toLocaleString("ru")}</b>
                {" "}({pct(tr.wow_diff?.impressions_pct)} от {(tr.totals_prev_week?.impressions ?? 0).toLocaleString("ru")})
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {moversUp.length > 0 && (
                  <div>
                    <div className="text-xs font-semibold uppercase text-emerald-700 mb-1">Растут</div>
                    <ul className="space-y-1">
                      {moversUp.slice(0, 5).map((q, i) => (
                        <li key={i} className="font-mono text-xs">
                          {q.query_text}: +{q.impressions_diff}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {moversDown.length > 0 && (
                  <div>
                    <div className="text-xs font-semibold uppercase text-rose-700 mb-1">Падают</div>
                    <ul className="space-y-1">
                      {moversDown.slice(0, 5).map((q, i) => (
                        <li key={i} className="font-mono text-xs">
                          {q.query_text}: {q.impressions_diff}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
              {newQueries.length > 0 && (
                <div><b>Новые в топ-50:</b> <span className="font-mono text-xs">{newQueries.join(", ")}</span></div>
              )}
              {lostQueries.length > 0 && (
                <div><b>Потеряны из топ-50:</b> <span className="font-mono text-xs">{lostQueries.join(", ")}</span></div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* 5. Page Findings */}
      <Card className="break-inside-avoid">
        <CardHeader>
          <CardTitle className="text-lg">
            5. Ревью страниц · страниц: {pf.pages_reviewed} · ревью: {pf.reviews_run_count}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {pf.warning_ru ? (
            <p className="text-muted-foreground italic">{pf.warning_ru}</p>
          ) : (
            <>
              {pf.by_priority_count && (
                <div className="text-xs text-muted-foreground">
                  {["critical", "high", "medium", "low"].map((p) => (
                    <span key={p} className="mr-3">{p}: <b>{priorityCounts[p] ?? 0}</b></span>
                  ))}
                </div>
              )}
              <div className="space-y-2">
                {pf.pages?.slice(0, 10).map((p) => (
                  <div key={p.page_id} className="rounded border p-2 break-inside-avoid">
                    <div className="text-sm font-medium truncate">{p.page_url || p.page_id}</div>
                    <div className="text-xs text-muted-foreground mt-0.5">
                      интент: {p.target_intent_code} · крит {p.critical_count} / важно {p.high_count} / средне {p.medium_count}
                    </div>
                    {(p.top_issues ?? []).length > 0 && (
                      <div className="text-xs mt-1">Проблемы: {(p.top_issues ?? []).join(", ")}</div>
                    )}
                    {(p.missing_eeat_signals ?? []).length > 0 && (
                      <div className="text-xs mt-0.5 text-muted-foreground">
                        Не найдено E-E-A-T: {(p.missing_eeat_signals ?? []).join(", ")}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* 6. Technical */}
      <Card className="break-inside-avoid">
        <CardHeader>
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <CardTitle className="text-lg">6. Техническое SEO</CardTitle>
            <Badge variant="outline" className={cn(
              "text-xs",
              (tech.technical_score ?? 100) >= 80
                ? "bg-emerald-50 text-emerald-800 border-emerald-300"
                : (tech.technical_score ?? 100) >= 50
                  ? "bg-amber-50 text-amber-800 border-amber-300"
                  : "bg-rose-50 text-rose-800 border-rose-300",
            )}>
              техscore {tech.technical_score ?? 100}/100
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="rounded border p-3">
              <div className="text-xs text-muted-foreground">Индексация</div>
              <div className="font-semibold">
                {(tech.indexation_rate * 100).toFixed(1)}% ({tech.pages_indexed}/{tech.pages_total})
              </div>
            </div>
            <div className="rounded border p-3">
              <div className="text-xs text-muted-foreground">robots.txt</div>
              <div className="font-semibold">{tech.robots?.ok ? "в порядке" : "есть проблема"}</div>
            </div>
            <div className="rounded border p-3">
              <div className="text-xs text-muted-foreground">sitemap.xml</div>
              <div className="font-semibold">
                {tech.sitemap?.valid_xml ? `${tech.sitemap.urls_declared ?? 0} URL` : "не валиден"}
              </div>
            </div>
            <div className="rounded border p-3">
              <div className="text-xs text-muted-foreground">HTTP ошибки</div>
              <div className="font-semibold">{tech.pages_non_200}</div>
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <div>Дубли title: <b>{tech.checks?.duplicate_titles ?? 0}</b></div>
            <div>noindex: <b>{tech.checks?.noindex_pages ?? 0}</b></div>
            <div>битые ссылки: <b>{tech.checks?.broken_internal_links ?? 0}</b></div>
            <div>canonical ошибки: <b>{(tech.checks?.canonical_external ?? 0) + (tech.checks?.canonical_mismatch ?? 0)}</b></div>
          </div>

          {Object.keys(techSchemaTypes).length > 0 && (
            <div>
              <div className="text-xs font-semibold uppercase text-muted-foreground mb-1">Schema types</div>
              <div className="flex flex-wrap gap-1">
                {Object.entries(techSchemaTypes).map(([name, count]) => (
                  <Badge key={name} variant="secondary" className="text-xs">
                    {name}: {String(count)}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {techIssues.length > 0 && (
            <div className="space-y-2">
              <div className="text-xs font-semibold uppercase text-muted-foreground">Что исправить</div>
              {techIssues.slice(0, 8).map((issue) => (
                <div
                  key={issue.code}
                  className={cn(
                    "rounded border p-3",
                    SEVERITY_TONE[issue.severity] ?? SEVERITY_TONE.low,
                  )}
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge variant="outline" className="text-xs">{issue.severity}</Badge>
                    <b>{issue.title_ru}</b>
                    <span className="text-xs opacity-75">({issue.count})</span>
                  </div>
                  <p className="mt-1 leading-snug">{issue.detail_ru}</p>
                  {(issue.examples ?? []).length > 0 && (
                    <div className="mt-2 space-y-1 text-xs opacity-80">
                      {(issue.examples ?? []).slice(0, 3).map((ex) => (
                        <div key={ex} className="truncate">пример: {ex}</div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="text-xs text-muted-foreground">
            Подозрение на дубли: <b>{tech.duplicates_suspected}</b> · Устаревшие fingerprints (&gt;30 дн.): <b>{tech.fingerprint_stale_count}</b>
          </div>
          {tech.warning_ru && <div className="text-amber-700 mt-2">⚠ {tech.warning_ru}</div>}
        </CardContent>
      </Card>
    </div>
  );
}
