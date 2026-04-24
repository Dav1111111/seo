"use client";

import { useState } from "react";
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
};

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
              <JsonBlock value={step.response_summary} />
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
