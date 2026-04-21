"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StepNav } from "@/components/onboarding/step-nav";
import { Star, X, Plus, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";

interface ProductRow {
  name: string;
  weight: number;     // 0..1
  isPrimary: boolean;
}

export default function Step2Products() {
  const { siteId } = useParams<{ siteId: string }>();
  const [rows, setRows] = useState<ProductRow[]>([]);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [adding, setAdding] = useState("");

  const { data, mutate, isLoading } = useSWR(
    siteId ? `onb-state-${siteId}` : null,
    () => api.onboardingState(siteId),
    { refreshInterval: 0 },
  );

  // Seed rows from target_config (services + weights + primary marker).
  useEffect(() => {
    if (!data) return;
    const cfg = data.target_config || {};
    const draft = (data.target_config_draft?.draft_config) || {};
    const services: string[] = cfg.services?.length ? cfg.services : (draft.services || []);
    const weights: Record<string, number> = cfg.service_weights || {};
    const primary: string | null = cfg.primary_product || null;

    if (!services.length) { setRows([]); return; }
    const seeded = services.map((name) => ({
      name,
      weight: weights[name] ?? (name === primary ? 1.0 : 0.5),
      isPrimary: primary ? name === primary : false,
    }));
    // If no primary set, default to first row.
    if (!seeded.some((r) => r.isPrimary) && seeded.length > 0) {
      seeded[0].isPrimary = true;
      seeded[0].weight = 1.0;
    }
    setRows(seeded);
  }, [data]);

  function setPrimary(idx: number) {
    setRows((prev) =>
      prev.map((r, i) => ({
        ...r,
        isPrimary: i === idx,
        weight: i === idx ? 1.0 : Math.min(r.weight, 0.7),
      })),
    );
  }

  function setWeight(idx: number, w: number) {
    setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, weight: w } : r)));
  }

  function remove(idx: number) {
    setRows((prev) => {
      const next = prev.filter((_, i) => i !== idx);
      if (!next.some((r) => r.isPrimary) && next.length > 0) {
        next[0] = { ...next[0], isPrimary: true, weight: 1.0 };
      }
      return next;
    });
  }

  function addProduct() {
    const v = adding.trim();
    if (!v) return;
    if (rows.some((r) => r.name.toLowerCase() === v.toLowerCase())) {
      setAdding(""); return;
    }
    setRows((prev) => [
      ...prev,
      { name: v, weight: 0.5, isPrimary: prev.length === 0 },
    ]);
    setAdding("");
  }

  const primaryName = useMemo(() => rows.find((r) => r.isPrimary)?.name ?? null, [rows]);

  async function persist() {
    setSaving(true); setErr(null);
    try {
      await api.patchOnboardingProducts(siteId, {
        primary_product: primaryName,
        service_weights: Object.fromEntries(rows.map((r) => [r.name, r.weight])),
        secondary_products: rows.filter((r) => !r.isPrimary).map((r) => r.name),
      });
      await api.patchOnboardingStep(siteId, "confirm_competitors");
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
        <h2 className="text-lg font-semibold mb-1">Что у тебя главное, а что — второстепенное</h2>
        <p className="text-sm text-muted-foreground">
          Отметь один главный продукт ⭐ — это то, чем ты в первую очередь зарабатываешь.
          Остальным поставь вес 0.2–0.7 в зависимости от важности. Те, что с весом 0, не будут
          учитываться в рекомендациях.
        </p>
      </div>

      {err && (
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900 flex items-start gap-2">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
          <span>{err}</span>
        </div>
      )}

      {isLoading ? (
        <Skeleton className="h-48" />
      ) : rows.length === 0 ? (
        <div className="rounded-lg border border-dashed bg-muted/30 p-6 text-center text-sm text-muted-foreground">
          Нет услуг в профиле. Добавь руками ниже или вернись в «Профиль спроса» и собери draft.
        </div>
      ) : (
        <ul className="space-y-2">
          {rows.map((row, idx) => (
            <li
              key={row.name}
              className={cn(
                "rounded-lg border p-3 flex items-center gap-3 transition-colors",
                row.isPrimary && "border-primary bg-primary/5",
              )}
            >
              <button
                onClick={() => setPrimary(idx)}
                title={row.isPrimary ? "Главный продукт" : "Сделать главным"}
                className={cn(
                  "shrink-0 p-1 rounded transition-colors",
                  row.isPrimary ? "text-yellow-500" : "text-muted-foreground hover:text-yellow-500",
                )}
              >
                <Star className={cn("h-5 w-5", row.isPrimary && "fill-current")} />
              </button>

              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium truncate">{row.name}</div>
                <div className="flex items-center gap-2 mt-1">
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.1}
                    value={row.weight}
                    onChange={(e) => setWeight(idx, parseFloat(e.target.value))}
                    disabled={row.isPrimary}
                    className="flex-1 accent-primary disabled:opacity-40"
                  />
                  <span className="text-xs font-mono w-10 text-right tabular-nums">
                    {row.weight.toFixed(1)}
                  </span>
                </div>
              </div>

              <button
                onClick={() => remove(idx)}
                className="shrink-0 p-1 text-muted-foreground hover:text-destructive"
                title="Убрать из списка"
              >
                <X className="h-4 w-4" />
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center gap-2 pt-2">
        <input
          value={adding}
          onChange={(e) => setAdding(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addProduct(); } }}
          placeholder="Добавить продукт…"
          className="flex-1 rounded-md border bg-background px-3 py-2 text-sm"
        />
        <Button size="sm" variant="outline" onClick={addProduct} disabled={!adding.trim()}>
          <Plus className="h-4 w-4 mr-1" /> Добавить
        </Button>
      </div>

      <StepNav
        siteId={siteId}
        step={2}
        onNext={persist}
        nextDisabled={!primaryName}
        saving={saving}
      />
    </div>
  );
}
