"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface ReportPayload {
  diagnostic?: any;
  meta: any;
  executive: any;
  action_plan: any;
  coverage: any;
  query_trends: any;
  page_findings: any;
  technical: any;
}

const PRIORITY_TONE: Record<string, string> = {
  critical: "bg-rose-100 text-rose-800 border-rose-300",
  high:     "bg-orange-100 text-orange-800 border-orange-300",
  medium:   "bg-amber-100 text-amber-800 border-amber-300",
  low:      "bg-slate-100 text-slate-700 border-slate-300",
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

            {d.supporting_symptoms_ru?.length > 0 && (
              <div>
                <div className="text-xs font-semibold text-muted-foreground uppercase mb-1">Сопутствующие симптомы</div>
                <ul className="list-disc pl-5 text-sm space-y-1">
                  {d.supporting_symptoms_ru.map((s: string, i: number) => <li key={i}>{s}</li>)}
                </ul>
              </div>
            )}

            {d.recommended_first_actions_ru?.length > 0 && (
              <div>
                <div className="text-xs font-semibold text-muted-foreground uppercase mb-1">Что делать в первую очередь</div>
                <ol className="list-decimal pl-5 text-sm space-y-1">
                  {d.recommended_first_actions_ru.map((s: string, i: number) => <li key={i}>{s}</li>)}
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

            {d.missing_target_clusters?.length > 0 && (
              <div>
                <div className="text-xs font-semibold text-muted-foreground uppercase mb-1">Приоритетные пробелы (топ-10)</div>
                <ul className="space-y-1 text-sm">
                  {d.missing_target_clusters.slice(0, 10).map((c: any) => (
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
            {e.top_wins?.length > 0 && (
              <div>
                <div className="text-xs font-semibold uppercase text-emerald-700 mb-1">Победы недели</div>
                <ul className="space-y-1 text-sm">
                  {e.top_wins.map((w: string, i: number) => <li key={i}>✅ {w}</li>)}
                </ul>
              </div>
            )}
            {e.top_losses?.length > 0 && (
              <div>
                <div className="text-xs font-semibold uppercase text-rose-700 mb-1">Потери недели</div>
                <ul className="space-y-1 text-sm">
                  {e.top_losses.map((l: string, i: number) => <li key={i}>⚠️ {l}</li>)}
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
              {ap.items.map((it: any, i: number) => (
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
          {cov.intent_gaps?.length > 0 && (
            <div>
              <div className="text-xs font-semibold uppercase text-muted-foreground mb-1">Пробелы</div>
              <ul className="list-disc pl-5 space-y-1">
                {cov.intent_gaps.map((g: string, i: number) => <li key={i}>{g}</li>)}
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
                {tr.top_movers_up?.length > 0 && (
                  <div>
                    <div className="text-xs font-semibold uppercase text-emerald-700 mb-1">Растут</div>
                    <ul className="space-y-1">
                      {tr.top_movers_up.slice(0, 5).map((q: any, i: number) => (
                        <li key={i} className="font-mono text-xs">
                          {q.query_text}: +{q.impressions_diff}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {tr.top_movers_down?.length > 0 && (
                  <div>
                    <div className="text-xs font-semibold uppercase text-rose-700 mb-1">Падают</div>
                    <ul className="space-y-1">
                      {tr.top_movers_down.slice(0, 5).map((q: any, i: number) => (
                        <li key={i} className="font-mono text-xs">
                          {q.query_text}: {q.impressions_diff}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
              {tr.new_queries?.length > 0 && (
                <div><b>Новые в топ-50:</b> <span className="font-mono text-xs">{tr.new_queries.join(", ")}</span></div>
              )}
              {tr.lost_queries?.length > 0 && (
                <div><b>Потеряны из топ-50:</b> <span className="font-mono text-xs">{tr.lost_queries.join(", ")}</span></div>
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
                    <span key={p} className="mr-3">{p}: <b>{pf.by_priority_count[p] ?? 0}</b></span>
                  ))}
                </div>
              )}
              <div className="space-y-2">
                {pf.pages?.slice(0, 10).map((p: any) => (
                  <div key={p.page_id} className="rounded border p-2 break-inside-avoid">
                    <div className="text-sm font-medium truncate">{p.page_url || p.page_id}</div>
                    <div className="text-xs text-muted-foreground mt-0.5">
                      интент: {p.target_intent_code} · крит {p.critical_count} / важно {p.high_count} / средне {p.medium_count}
                    </div>
                    {p.top_issues?.length > 0 && (
                      <div className="text-xs mt-1">Проблемы: {p.top_issues.join(", ")}</div>
                    )}
                    {p.missing_eeat_signals?.length > 0 && (
                      <div className="text-xs mt-0.5 text-muted-foreground">
                        Не найдено E-E-A-T: {p.missing_eeat_signals.join(", ")}
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
          <CardTitle className="text-lg">
            6. Техническое SEO · индексация: {(tech.indexation_rate * 100).toFixed(1)}% ({tech.pages_indexed}/{tech.pages_total})
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-1 text-sm">
          <div>Non-200 страниц: <b>{tech.pages_non_200}</b></div>
          <div>Подозрение на дубли: <b>{tech.duplicates_suspected}</b></div>
          <div>Устаревшие fingerprints (&gt;30 дн.): <b>{tech.fingerprint_stale_count}</b></div>
          {tech.warning_ru && <div className="text-amber-700 mt-2">⚠ {tech.warning_ru}</div>}
        </CardContent>
      </Card>
    </div>
  );
}
