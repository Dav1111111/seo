"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StepNav } from "@/components/onboarding/step-nav";
import { Sparkles, RefreshCw, AlertCircle } from "lucide-react";

export default function Step1Business() {
  const { siteId } = useParams<{ siteId: string }>();
  const [analyzing, setAnalyzing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // local editable copy — seeded from backend understanding
  const [narrative, setNarrative] = useState("");
  const [niche, setNiche] = useState("");
  const [positioning, setPositioning] = useState("");
  const [usp, setUsp] = useState("");

  const { data, mutate, isLoading } = useSWR(
    siteId ? `onb-state-${siteId}` : null,
    () => api.onboardingState(siteId),
    { refreshInterval: 0 },
  );

  const u = data?.understanding || {};
  const hasContent = !!u.narrative_ru;
  const status = u.status ?? "pending";

  // Seed form when fresh data arrives.
  useEffect(() => {
    setNarrative(u.narrative_ru ?? "");
    setNiche(u.detected_niche ?? "");
    setPositioning(u.detected_positioning ?? "");
    setUsp(u.detected_usp ?? "");
  }, [u.narrative_ru, u.detected_niche, u.detected_positioning, u.detected_usp]);

  async function runAnalyze() {
    setAnalyzing(true); setErr(null);
    try {
      await api.triggerUnderstandingAnalyze(siteId);
      // Haiku ~5–12s; poll every 3s for up to 45s.
      for (let i = 0; i < 15; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const fresh = await mutate();
        if (fresh?.understanding?.status === "ok") break;
        if (fresh?.understanding?.status === "llm_failed" ||
            fresh?.understanding?.status === "malformed") {
          setErr(fresh.understanding.error || "Модель не справилась. Попробуй ещё раз.");
          break;
        }
      }
    } catch (e: any) {
      setErr(e?.message ?? String(e));
    } finally {
      setAnalyzing(false);
    }
  }

  async function persist() {
    if (!hasContent) return;
    setSaving(true); setErr(null);
    try {
      await api.patchUnderstanding(siteId, {
        narrative_ru: narrative,
        detected_niche: niche,
        detected_positioning: positioning,
        detected_usp: usp || null,
      });
      await api.patchOnboardingStep(siteId, "confirm_products");
      mutate();
    } catch (e: any) {
      setErr(e?.message ?? String(e));
      throw e;
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-2 mb-1">
          <Sparkles className="h-4 w-4 text-primary" />
          <h2 className="text-lg font-semibold">Что я понял про твой бизнес</h2>
        </div>
        <p className="text-sm text-muted-foreground">
          Я прочитал страницы сайта и пересказываю что увидел. Прочитай, поправь
          что не так — это фундамент для всего дальнейшего.
        </p>
      </div>

      {err && (
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900 flex items-start gap-2">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
          <span>{err}</span>
        </div>
      )}

      {isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : !hasContent ? (
        <div className="rounded-lg border border-dashed bg-muted/30 p-8 text-center space-y-3">
          <p className="text-sm text-muted-foreground">
            Анализа ещё нет. Запусти — займёт 5–15 секунд.
          </p>
          <Button onClick={runAnalyze} disabled={analyzing}>
            <RefreshCw className={`mr-2 h-4 w-4 ${analyzing ? "animate-spin" : ""}`} />
            {analyzing ? "Читаю сайт…" : "Проанализировать сайт"}
          </Button>
        </div>
      ) : (
        <div className="space-y-5">
          <div>
            <label className="text-sm font-medium block mb-1.5">
              Коротко о бизнесе
              <span className="text-xs text-muted-foreground font-normal ml-2">
                (перепиши если звучит не так)
              </span>
            </label>
            <textarea
              value={narrative}
              onChange={(e) => setNarrative(e.target.value)}
              rows={6}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm leading-relaxed focus:outline-none focus:ring-1 focus:ring-primary resize-y"
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="Ниша" value={niche} onChange={setNiche} hint="2–5 слов" />
            <Field label="Позиционирование" value={positioning} onChange={setPositioning} hint="одно предложение" />
          </div>

          <Field
            label="УТП (если есть)"
            value={usp}
            onChange={setUsp}
            hint="оставь пустым если нет явного"
          />

          {/* Observed facts + uncertainties — read-only context */}
          {(u.observed_facts?.length > 0 || u.uncertainties?.length > 0) && (
            <details className="rounded border bg-muted/30 p-3 text-sm">
              <summary className="cursor-pointer font-medium text-xs uppercase text-muted-foreground">
                Что я увидел на страницах (детали)
              </summary>
              <div className="mt-3 space-y-3">
                {u.observed_facts?.length > 0 && (
                  <div>
                    <div className="text-xs font-semibold text-muted-foreground mb-1">
                      Наблюдения
                    </div>
                    <ul className="space-y-1">
                      {u.observed_facts.map((f: any, i: number) => (
                        <li key={i} className="text-xs leading-snug">
                          <span>{f.fact}</span>
                          {f.page_ref && (
                            <span className="text-muted-foreground"> — {f.page_ref}</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {u.uncertainties?.length > 0 && (
                  <div>
                    <div className="text-xs font-semibold text-amber-700 mb-1">
                      Неуверен
                    </div>
                    <ul className="list-disc pl-5 space-y-0.5 text-xs">
                      {u.uncertainties.map((x: string, i: number) => (<li key={i}>{x}</li>))}
                    </ul>
                  </div>
                )}
              </div>
            </details>
          )}

          <div className="flex items-center justify-between pt-2">
            <div className="text-xs text-muted-foreground flex items-center gap-2">
              {status === "ok" && <Badge variant="outline" className="text-[10px]">Проанализировано</Badge>}
              {u.pages_analyzed != null && <span>{u.pages_analyzed} страниц · ${Number(u.cost_usd ?? 0).toFixed(4)}</span>}
            </div>
            <Button size="sm" variant="ghost" onClick={runAnalyze} disabled={analyzing}>
              <RefreshCw className={`mr-2 h-3 w-3 ${analyzing ? "animate-spin" : ""}`} />
              Перечитать сайт
            </Button>
          </div>
        </div>
      )}

      <StepNav
        siteId={siteId}
        step={1}
        onNext={persist}
        nextDisabled={!hasContent || !narrative.trim()}
        saving={saving}
      />
    </div>
  );
}

function Field({
  label, value, onChange, hint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  hint?: string;
}) {
  return (
    <div>
      <label className="text-sm font-medium block mb-1.5">
        {label}
        {hint && <span className="text-xs text-muted-foreground font-normal ml-2">({hint})</span>}
      </label>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
      />
    </div>
  );
}
