"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { StepNav } from "@/components/onboarding/step-nav";
import { CheckCircle2, AlertCircle } from "lucide-react";

interface KPIs {
  impressions: number | "";
  clicks: number | "";
  avg_position: number | "";
}

const EMPTY: KPIs = { impressions: "", clicks: "", avg_position: "" };

export default function Step7KPI() {
  const { siteId } = useParams<{ siteId: string }>();
  const router = useRouter();
  const [baseline, setBaseline] = useState<KPIs>(EMPTY);
  const [t3, setT3] = useState<KPIs>(EMPTY);
  const [t6, setT6] = useState<KPIs>(EMPTY);
  const [t12, setT12] = useState<KPIs>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Pull current dashboard KPIs to pre-fill baseline.
  const { data: dash } = useSWR(
    siteId ? `dash-${siteId}` : null,
    () => api.dashboard(siteId),
  );
  const { data: state, mutate } = useSWR(
    siteId ? `onb-state-${siteId}` : null,
    () => api.onboardingState(siteId),
  );

  useEffect(() => {
    const kt = state?.kpi_targets || {};
    if (Object.keys(kt).length > 0) {
      setBaseline({ ...EMPTY, ...(kt.baseline || {}) });
      setT3({ ...EMPTY, ...(kt.target_3m || {}) });
      setT6({ ...EMPTY, ...(kt.target_6m || {}) });
      setT12({ ...EMPTY, ...(kt.target_12m || {}) });
    } else if (dash?.kpis) {
      // Pre-fill baseline from the live dashboard, defaults for targets.
      const imp = dash.kpis.impressions ?? 0;
      const clk = dash.kpis.clicks ?? 0;
      const pos = dash.kpis.avg_position ?? 0;
      setBaseline({ impressions: imp, clicks: clk, avg_position: pos });
      setT3({ impressions: Math.round(imp * 1.3), clicks: Math.round(clk * 1.3), avg_position: Math.max(1, pos - 2) });
      setT6({ impressions: Math.round(imp * 1.7), clicks: Math.round(clk * 1.8), avg_position: Math.max(1, pos - 4) });
      setT12({ impressions: Math.round(imp * 2.5), clicks: Math.round(clk * 3),  avg_position: Math.max(1, pos - 6) });
    }
  }, [dash, state]);

  function toNums(k: KPIs) {
    return {
      impressions: k.impressions === "" ? 0 : Number(k.impressions),
      clicks:      k.clicks === ""      ? 0 : Number(k.clicks),
      avg_position: k.avg_position === "" ? 0 : Number(k.avg_position),
    };
  }

  async function saveKpi() {
    setSaving(true); setErr(null);
    try {
      await api.patchOnboardingKpi(siteId, {
        baseline: toNums(baseline),
        target_3m: toNums(t3),
        target_6m: toNums(t6),
        target_12m: toNums(t12),
      });
      mutate();
    } catch (e: any) {
      setErr(e?.message ?? String(e));
      throw e;
    } finally {
      setSaving(false);
    }
  }

  async function finishOnboarding() {
    setFinishing(true); setErr(null);
    try {
      await saveKpi();
      await api.completeOnboarding(siteId);
      router.push("/");
    } catch (e: any) {
      setErr(e?.message ?? String(e));
    } finally {
      setFinishing(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold mb-1">На какие цифры ориентируемся</h2>
        <p className="text-sm text-muted-foreground">
          Зафиксируем текущие значения и цели. Через 3/6/12 месяцев отчёт будет
          показывать прогресс относительно этих чисел.
        </p>
      </div>

      {err && (
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900 flex items-start gap-2">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" /> <span>{err}</span>
        </div>
      )}

      {!dash ? (
        <Skeleton className="h-64" />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <KpiBlock title="Сейчас (baseline)" value={baseline} onChange={setBaseline} />
          <KpiBlock title="Через 3 месяца" value={t3} onChange={setT3} />
          <KpiBlock title="Через 6 месяцев" value={t6} onChange={setT6} />
          <KpiBlock title="Через 12 месяцев" value={t12} onChange={setT12} />
        </div>
      )}

      <div className="rounded-lg border-2 border-dashed border-primary/30 bg-primary/5 p-4 space-y-3">
        <div className="flex items-start gap-3">
          <CheckCircle2 className="h-5 w-5 text-primary shrink-0 mt-0.5" />
          <div className="flex-1">
            <div className="text-sm font-semibold">Готов завершить онбординг?</div>
            <p className="text-xs text-muted-foreground mt-1">
              После этого ночной pipeline начнёт собирать данные, ревьюить страницы
              и готовить еженедельный отчёт. Профиль и цели всегда можно поправить
              позже через «Настройки».
            </p>
          </div>
        </div>
        <Button
          onClick={finishOnboarding}
          disabled={finishing || saving}
          className="w-full"
        >
          {finishing ? "Завершаю…" : "Завершить и запустить"}
        </Button>
      </div>

      <StepNav siteId={siteId} step={7} onNext={saveKpi} saving={saving} nextLabel="Сохранить" />
    </div>
  );
}

function KpiBlock({
  title, value, onChange,
}: { title: string; value: KPIs; onChange: (v: KPIs) => void }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <NumRow label="Показы/нед" value={value.impressions} onChange={(v) => onChange({ ...value, impressions: v })} />
        <NumRow label="Клики/нед"  value={value.clicks}      onChange={(v) => onChange({ ...value, clicks: v })} />
        <NumRow label="Ср. позиция" value={value.avg_position} onChange={(v) => onChange({ ...value, avg_position: v })} step={0.1} />
      </CardContent>
    </Card>
  );
}

function NumRow({
  label, value, onChange, step,
}: { label: string; value: number | ""; onChange: (v: number | "") => void; step?: number }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <label className="flex-1 text-muted-foreground">{label}</label>
      <input
        type="number"
        step={step ?? 1}
        value={value}
        onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
        className="w-24 rounded border bg-background px-2 py-1 text-right text-sm tabular-nums"
      />
    </div>
  );
}
