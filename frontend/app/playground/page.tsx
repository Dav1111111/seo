"use client";

import { Fragment, useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  PlayCircle, ChevronRight, RotateCcw, CheckCircle2,
  XCircle, FlaskConical,
} from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * API Playground — run Yandex integrations one step at a time.
 *
 * Owner picks a scenario, fills in inputs, runs step 1 — sees the
 * exact request we send and the exact response we get. Clicks
 * "Continue" to run step 2. Repeat until scenario ends.
 *
 * Scoped to one scenario at a time (no tabs, no split-views). Back
 * button returns to the picker without preserving state — each run
 * is a fresh observation.
 */

type ScenarioInput = {
  key: string;
  label_ru: string;
  placeholder_ru: string;
  required: boolean;
};

type Scenario = {
  id: string;
  title_ru: string;
  description_ru: string;
  inputs: ScenarioInput[];
  step_count: number;
};

type StepResult = {
  step_index: number;
  step_title_ru: string;
  step_description_ru: string;
  request_shown: {
    endpoint?: string;
    body_preview?: Record<string, unknown>;
  } | null;
  response_summary: Record<string, unknown>;
  ok: boolean;
  error: string | null;
  next_available: boolean;
  next_hint_ru: string | null;
  human_summary_ru: string | null;
  human_summary_level: string;
};

function humanSummaryClasses(level: string): string {
  switch (level) {
    case "good":
      return "border-emerald-300 bg-emerald-50 text-emerald-900";
    case "warning":
      return "border-amber-300 bg-amber-50 text-amber-900";
    case "bad":
      return "border-red-300 bg-red-50 text-red-900";
    default:
      return "border-primary/40 bg-primary/5 text-foreground";
  }
}

function JsonBlock({ value }: { value: unknown }) {
  let text: string;
  try {
    text = JSON.stringify(value, null, 2);
  } catch {
    text = String(value);
  }
  return (
    <pre className="bg-background border rounded p-3 text-[11px] overflow-x-auto max-h-80">
      {text}
    </pre>
  );
}

/** Pretty-render a SERP / page / competitor entry as a clickable row. */
function LinkRow({
  position,
  url,
  title,
  reason,
}: {
  position?: number;
  url: string;
  title?: string;
  reason?: string;
}) {
  return (
    <li className="flex items-start gap-2 py-1.5 border-b last:border-0 text-sm">
      {typeof position === "number" && (
        <span className="text-muted-foreground font-mono text-xs min-w-[1.5rem] text-right">
          {position}.
        </span>
      )}
      <div className="min-w-0 flex-1">
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary hover:underline break-all"
        >
          {title && title !== url ? title : url}
        </a>
        {title && title !== url && (
          <div className="text-[11px] text-muted-foreground truncate mt-0.5">
            {url}
          </div>
        )}
        {reason && (
          <div className="text-[11px] text-amber-700 mt-0.5">
            <span className="text-muted-foreground">причина: </span>
            {reason}
          </div>
        )}
      </div>
    </li>
  );
}

/** Decide what to render based on which fields the response has.
 * Each scenario step returns a different shape, but the cues are
 * obvious — `pages` for indexation results, `raw_serp` for SERP fetch,
 * `kept`/`dropped` for filter step, `competitors` for the final list.
 * Fallback: key-value pairs in a small definition list (still no JSON). */
function ResponseRenderer({ data }: { data: Record<string, unknown> }) {
  const pages = Array.isArray(data.pages) ? (data.pages as Array<Record<string, unknown>>) : null;
  const raw = Array.isArray(data.raw_serp) ? (data.raw_serp as Array<Record<string, unknown>>) : null;
  const kept = Array.isArray(data.kept) ? (data.kept as Array<Record<string, unknown>>) : null;
  const dropped = Array.isArray(data.dropped) ? (data.dropped as Array<Record<string, unknown>>) : null;
  const competitors = Array.isArray(data.competitors)
    ? (data.competitors as Array<Record<string, unknown>>)
    : null;

  // ── Final competitors list / SERP list / pages list ────────────────
  if (competitors && competitors.length > 0) {
    return (
      <ul className="border rounded divide-y">
        {competitors.map((c, i) => (
          <LinkRow
            key={i}
            position={typeof c.position === "number" ? c.position : i + 1}
            url={typeof c.url === "string" ? c.url : ""}
            title={typeof c.title === "string" ? c.title : undefined}
          />
        ))}
      </ul>
    );
  }
  if (raw && raw.length > 0) {
    return (
      <ul className="border rounded divide-y">
        {raw.map((d, i) => (
          <LinkRow
            key={i}
            position={typeof d.position === "number" ? d.position : i + 1}
            url={typeof d.url === "string" ? d.url : ""}
            title={typeof d.title === "string" ? d.title : undefined}
          />
        ))}
      </ul>
    );
  }
  if (pages && pages.length > 0) {
    return (
      <ul className="border rounded divide-y">
        {pages.map((p, i) => (
          <LinkRow
            key={i}
            position={typeof p.position === "number" ? p.position : i + 1}
            url={typeof p.url === "string" ? p.url : ""}
            title={typeof p.title === "string" ? p.title : undefined}
          />
        ))}
      </ul>
    );
  }

  // ── Filter step: kept + dropped two-column ─────────────────────────
  if (kept || dropped) {
    return (
      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <div className="text-xs font-medium mb-1 text-emerald-800">
            Оставлено: {kept?.length ?? 0}
          </div>
          {kept && kept.length > 0 ? (
            <ul className="border rounded divide-y">
              {kept.map((c, i) => (
                <LinkRow
                  key={i}
                  position={typeof c.position === "number" ? c.position : undefined}
                  url={typeof c.url === "string" ? c.url : ""}
                  title={typeof c.title === "string" ? c.title : undefined}
                />
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground italic">пусто</p>
          )}
        </div>
        <div>
          <div className="text-xs font-medium mb-1 text-amber-800">
            Отброшено: {dropped?.length ?? 0}
          </div>
          {dropped && dropped.length > 0 ? (
            <ul className="border rounded divide-y">
              {dropped.map((c, i) => (
                <LinkRow
                  key={i}
                  position={typeof c.position === "number" ? c.position : undefined}
                  url={typeof c.url === "string" ? c.url : ""}
                  title={typeof c.title === "string" ? c.title : undefined}
                  reason={typeof c.reason === "string" ? c.reason : undefined}
                />
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground italic">пусто</p>
          )}
        </div>
      </div>
    );
  }

  // ── Generic fact list — for sitemap / robots / rendering steps ─────
  // No URL list to draw, just dump the meaningful key-value pairs as
  // a small definition list. Skip noisy keys (status code, raw bodies).
  const SKIP = new Set(["body", "raw", "raw_body", "hint_ru"]);
  const facts = Object.entries(data).filter(
    ([k, v]) =>
      !SKIP.has(k) &&
      v !== null &&
      v !== undefined &&
      typeof v !== "object",
  );
  if (facts.length === 0) {
    return (
      <p className="text-xs text-muted-foreground italic">
        Без структурных данных — см. «Что это значит» выше.
      </p>
    );
  }
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
      {facts.map(([k, v]) => (
        <Fragment key={k}>
          <dt className="text-muted-foreground">{prettyKey(k)}</dt>
          <dd className="font-mono break-all">{String(v)}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

function prettyKey(k: string): string {
  const dict: Record<string, string> = {
    pages_found: "Страниц в индексе",
    docs_returned: "Результатов",
    kept_count: "Оставлено",
    dropped_count: "Отброшено",
    competitors_count: "Конкурентов",
    valid_xml: "XML валидный",
    urls_declared: "URL в sitemap",
    status: "HTTP статус",
    sitemap_referenced: "Ссылка на sitemap",
    sitemap_url: "Sitemap URL",
    disallow_count: "Disallow-правил",
    title: "Title",
    text_length: "Символов текста",
    spa_root_only: "Только пустой <div id=\"root\">",
    problem: "Проблема",
    error: "Ошибка",
    url: "URL",
    domain: "Домен",
    query: "Запрос",
    verdict: "Вердикт",
    severity: "Уровень",
  };
  return dict[k] ?? k;
}

export default function PlaygroundPage() {
  const { data, isLoading } = useSWR("playground-list", () =>
    api.listPlaygroundScenarios(),
  );

  const [selected, setSelected] = useState<Scenario | null>(null);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [results, setResults] = useState<StepResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [fatal, setFatal] = useState<string | null>(null);

  function pickScenario(s: Scenario) {
    setSelected(s);
    // Pre-fill inputs with empty strings so typed-union TS matches
    const initial: Record<string, string> = {};
    for (const i of s.inputs) initial[i.key] = "";
    setInputs(initial);
    setResults([]);
    setFatal(null);
  }

  function backToList() {
    setSelected(null);
    setInputs({});
    setResults([]);
    setFatal(null);
  }

  async function runStep(step_index: number) {
    if (!selected) return;
    setBusy(true);
    setFatal(null);
    try {
      const r = await api.runPlaygroundStep({
        scenario_id: selected.id,
        step_index,
        inputs,
        prior: results.map((x) => ({
          response_summary: x.response_summary,
        })),
      });
      // Replace if re-running an existing step, otherwise append
      setResults((prev) => {
        const next = [...prev];
        next[step_index] = r;
        return next.slice(0, step_index + 1);
      });
    } catch (e: unknown) {
      setFatal(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (isLoading || !data) {
    return (
      <div className="p-6 space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  // Scenario list view
  if (!selected) {
    return (
      <div className="p-6 max-w-4xl space-y-5">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <FlaskConical className="h-6 w-6" />
            Playground
          </h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            Пошагово смотрим, что платформа делает с Яндекс-API.
            Каждый сценарий — один рабочий процесс, разбитый на шаги.
            Ты жмёшь «Продолжить» между шагами и видишь реальные запросы
            и ответы.
          </p>
        </div>

        <div className="space-y-3">
          {data.scenarios.map((s) => (
            <Card
              key={s.id}
              className="cursor-pointer hover:border-primary/50 transition-colors"
              onClick={() => pickScenario(s)}
            >
              <CardContent className="p-4 flex items-start gap-3">
                <PlayCircle className="h-5 w-5 text-primary mt-0.5 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium">{s.title_ru}</span>
                    <Badge variant="secondary" className="text-[10px]">
                      {s.step_count} {s.step_count === 1 ? "шаг" : "шагов"}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">
                    {s.description_ru}
                  </p>
                </div>
                <ChevronRight className="h-5 w-5 text-muted-foreground" />
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    );
  }

  // Running view
  const nextStepIndex = results.length;
  const canRunFirst = results.length === 0;
  const canContinue =
    results.length > 0 && results[results.length - 1]?.next_available;

  return (
    <div className="p-6 max-w-4xl space-y-5">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <Button
            size="sm"
            variant="ghost"
            onClick={backToList}
            className="mb-2 -ml-2"
          >
            ← К списку сценариев
          </Button>
          <h1 className="text-xl font-bold">{selected.title_ru}</h1>
          <p className="text-sm text-muted-foreground mt-0.5 max-w-2xl">
            {selected.description_ru}
          </p>
        </div>
        {results.length > 0 && (
          <Button size="sm" variant="outline" onClick={() => setResults([])}>
            <RotateCcw className="h-3 w-3 mr-1" />
            Начать заново
          </Button>
        )}
      </div>

      {/* Inputs */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Параметры</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {selected.inputs.map((input) => (
            <div key={input.key} className="space-y-1">
              <label
                htmlFor={`pg-${input.key}`}
                className="text-xs text-muted-foreground block"
              >
                {input.label_ru}
                {input.required && (
                  <span className="text-red-500 ml-1">*</span>
                )}
              </label>
              <input
                id={`pg-${input.key}`}
                type="text"
                value={inputs[input.key] ?? ""}
                placeholder={input.placeholder_ru}
                onChange={(e) =>
                  setInputs((prev) => ({ ...prev, [input.key]: e.target.value }))
                }
                disabled={results.length > 0}
                className="w-full h-9 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
              />
            </div>
          ))}

          {canRunFirst && (
            <Button
              onClick={() => runStep(0)}
              disabled={busy || !selected.inputs.every((i) => !i.required || inputs[i.key]?.trim())}
            >
              {busy ? "Запускаю…" : "Начать"}
            </Button>
          )}
        </CardContent>
      </Card>

      {fatal && (
        <div className="rounded border border-red-300 bg-red-50 text-red-900 px-3 py-2 text-sm">
          Ошибка вызова: {fatal}
        </div>
      )}

      {/* Step results */}
      {results.map((step, idx) => (
        <Card
          key={idx}
          className={cn(
            step.ok ? "" : "border-red-300",
          )}
        >
          <CardHeader className="pb-3">
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1">
                <CardTitle className="text-base flex items-center gap-2">
                  {step.ok ? (
                    <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                  ) : (
                    <XCircle className="h-4 w-4 text-red-600" />
                  )}
                  {step.step_title_ru}
                </CardTitle>
                <p className="text-xs text-muted-foreground mt-1">
                  {step.step_description_ru}
                </p>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {step.error && (
              <div className="rounded border border-red-300 bg-red-50 text-red-900 px-3 py-2 text-xs">
                {step.error}
              </div>
            )}

            {step.human_summary_ru && (
              <div
                className={cn(
                  "rounded border-l-4 px-4 py-3 text-sm whitespace-pre-line",
                  humanSummaryClasses(step.human_summary_level),
                )}
              >
                <div className="text-[11px] uppercase tracking-wide opacity-70 mb-1">
                  Что это значит
                </div>
                {step.human_summary_ru}
              </div>
            )}

            {step.request_shown && (
              <div>
                <div className="text-xs font-medium text-muted-foreground mb-1">
                  Что мы отправляем
                </div>
                {step.request_shown.endpoint && (
                  <code className="block text-[11px] bg-background border rounded px-2 py-1 mb-2">
                    {step.request_shown.endpoint}
                  </code>
                )}
                {step.request_shown.body_preview && (
                  <JsonBlock value={step.request_shown.body_preview} />
                )}
              </div>
            )}

            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">
                Что получили
              </div>
              <ResponseRenderer data={step.response_summary} />
              <details className="mt-2">
                <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground select-none">
                  Показать сырой ответ (JSON)
                </summary>
                <div className="mt-2">
                  <JsonBlock value={step.response_summary} />
                </div>
              </details>
            </div>

            {step.next_hint_ru && (
              <div className="text-xs text-muted-foreground border-l-2 border-primary/50 pl-3 py-1">
                {step.next_hint_ru}
              </div>
            )}
          </CardContent>
        </Card>
      ))}

      {/* Continue / done */}
      {results.length > 0 && (
        <div className="flex items-center gap-3">
          {canContinue ? (
            <Button
              onClick={() => runStep(nextStepIndex)}
              disabled={busy}
            >
              {busy ? "Запускаю шаг…" : `Продолжить к шагу ${nextStepIndex + 1}`}
            </Button>
          ) : (
            <Badge variant="outline" className="bg-muted">
              Сценарий завершён
            </Badge>
          )}
        </div>
      )}
    </div>
  );
}
