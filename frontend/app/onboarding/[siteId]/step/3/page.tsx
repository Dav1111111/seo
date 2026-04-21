"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { ChipEditor } from "@/components/onboarding/chip-editor";
import { StepNav } from "@/components/onboarding/step-nav";
import { Skeleton } from "@/components/ui/skeleton";
import { AlertCircle } from "lucide-react";

export default function Step3Competitors() {
  const { siteId } = useParams<{ siteId: string }>();
  const [domains, setDomains] = useState<string[]>([]);
  const [brands, setBrands] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const { data, mutate, isLoading } = useSWR(
    siteId ? `onb-state-${siteId}` : null,
    () => api.onboardingState(siteId),
  );

  useEffect(() => {
    if (!data) return;
    setDomains(data.competitor_domains || []);
    const draft = data.target_config_draft?.draft_config || {};
    setBrands((data.target_config?.competitor_brands?.length
      ? data.target_config.competitor_brands
      : (draft.competitor_brands || [])));
  }, [data]);

  async function persist() {
    setSaving(true); setErr(null);
    try {
      await api.patchOnboardingCompetitors(siteId, {
        competitor_domains: domains,
        competitor_brands: brands,
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

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold mb-1">С кем ты реально конкурируешь</h2>
        <p className="text-sm text-muted-foreground">
          Бренды, которые бьются за тех же клиентов, и их сайты. Можно пустыми
          оставить — тогда мы обойдёмся без них.
        </p>
      </div>

      {err && (
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900 flex items-start gap-2">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" /> <span>{err}</span>
        </div>
      )}

      {isLoading ? (
        <Skeleton className="h-48" />
      ) : (
        <div className="space-y-6">
          <ChipEditor
            label="Бренды-конкуренты"
            tone="secondary"
            values={brands}
            onChange={setBrands}
            placeholder="Название бренда…"
          />
          <ChipEditor
            label="Домены-конкуренты"
            tone="neutral"
            values={domains}
            onChange={setDomains}
            placeholder="example.ru"
          />
        </div>
      )}

      <StepNav siteId={siteId} step={3} onNext={persist} saving={saving} />
    </div>
  );
}
