"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useCurrentSiteId } from "@/lib/site-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  Table, TableBody, TableCell, TableHead,
  TableHeader, TableRow,
} from "@/components/ui/table";
import { ConfidenceChip } from "@/components/demand-profile/confidence-chip";
import { ChipEditor } from "@/components/demand-profile/chip-editor";
import { RefreshCw, Check, Eye } from "lucide-react";

interface FieldConfidence {
  field: string;
  confidence: number;
  evidence_count: number;
  reasoning_ru: string;
}

interface DraftConfig {
  services?: string[];
  excluded_services?: string[];
  geo_primary?: string[];
  geo_secondary?: string[];
  excluded_geo?: string[];
  competitor_brands?: string[];
  [k: string]: any;
}

interface DraftBlob {
  site_id?: string;
  draft_config?: DraftConfig;
  confidences?: FieldConfidence[];
  overall_confidence?: number;
  generated_at?: string;
  generator_version?: string;
  signals?: Record<string, any>;
}

function confMap(list: FieldConfidence[] | undefined): Record<string, FieldConfidence> {
  const m: Record<string, FieldConfidence> = {};
  (list || []).forEach((c) => { m[c.field] = c; });
  return m;
}

export default function DemandProfilePage() {
  const siteId = useCurrentSiteId();
  const [rebuilding, setRebuilding] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [banner, setBanner] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);
  const [edits, setEdits] = useState<DraftConfig>({});

  const { data, isLoading, error, mutate } = useSWR(
    siteId ? `draft-${siteId}` : null,
    () => api.draftProfile(siteId),
    { refreshInterval: 0 },
  );

  const { data: mapData } = useSWR(
    siteId ? `map-${siteId}` : null,
    () => api.demandMap(siteId, { limit: 50 }),
  );

  const draft: DraftBlob = data?.draft || {};
  const cfg: DraftConfig = draft.draft_config || {};
  const confs = useMemo(() => confMap(draft.confidences), [draft.confidences]);

  // Seed local edits whenever a fresh draft arrives.
  useEffect(() => {
    setEdits({
      services: cfg.services || [],
      excluded_services: cfg.excluded_services || [],
      geo_primary: cfg.geo_primary || [],
      geo_secondary: cfg.geo_secondary || [],
      excluded_geo: cfg.excluded_geo || [],
      competitor_brands: cfg.competitor_brands || [],
    });
  }, [draft.generated_at]); // eslint-disable-line react-hooks/exhaustive-deps

  async function onRebuild() {
    if (!siteId) return;
    setRebuilding(true); setBanner(null);
    try {
      await api.triggerDraftRebuild(siteId);
      setBanner({ kind: "ok", msg: "Пересборка черновика поставлена в очередь. Обновите через ~30 сек." });
    } catch (e: any) {
      setBanner({ kind: "err", msg: e?.message ?? String(e) });
    } finally {
      setRebuilding(false);
    }
  }

  async function onCommit(confirm: boolean) {
    if (!siteId) return;
    setCommitting(true); setBanner(null);
    try {
      const res = await api.commitDraft(siteId, {
        confirm,
        field_overrides: edits,
      });
      setBanner({
        kind: "ok",
        msg: confirm
          ? "Профиль применён к target_config."
          : "Предпросмотр готов — записи не было.",
      });
      if (confirm) mutate();
      console.info("commit-draft result", res);
    } catch (e: any) {
      setBanner({ kind: "err", msg: e?.message ?? String(e) });
    } finally {
      setCommitting(false);
    }
  }

  const diffFields = (["services", "excluded_services", "geo_primary", "geo_secondary", "excluded_geo", "competitor_brands"] as const)
    .filter((f) => JSON.stringify(edits[f] || []) !== JSON.stringify(cfg[f] || []));

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Профиль спроса</h1>
          <p className="text-sm text-muted-foreground">
            Черновик target_config, собранный из страниц, запросов и LLM-анализа. Проверьте и применяйте.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={onRebuild} disabled={rebuilding || !siteId}>
            <RefreshCw className={`mr-2 h-4 w-4 ${rebuilding ? "animate-spin" : ""}`} />
            {rebuilding ? "Ставим в очередь…" : "Пересобрать"}
          </Button>
          <Button size="sm" variant="outline" onClick={() => onCommit(false)} disabled={committing || !data?.has_draft}>
            <Eye className="mr-2 h-4 w-4" /> Предпросмотр
          </Button>
          <Button size="sm" onClick={() => onCommit(true)} disabled={committing || !data?.has_draft}>
            <Check className="mr-2 h-4 w-4" /> {committing ? "Применяю…" : "Применить к target_config"}
          </Button>
        </div>
      </div>

      {banner && (
        <div className={`rounded border px-3 py-2 text-sm ${banner.kind === "ok"
          ? "border-emerald-300 bg-emerald-50 text-emerald-900"
          : "border-red-300 bg-red-50 text-red-900"}`}>
          {banner.msg}
        </div>
      )}

      {error && (
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">
          {String((error as any)?.message || error)}
        </div>
      )}

      {isLoading ? (
        <div className="space-y-3">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-24" />)}
        </div>
      ) : !data?.has_draft ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Черновик ещё не собран. Нажмите «Пересобрать» и вернитесь через минуту.
          </CardContent>
        </Card>
      ) : (
        <>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="text-base">Сводка</CardTitle>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Общая уверенность</span>
                <ConfidenceChip value={draft.overall_confidence ?? 0} />
                {diffFields.length > 0 && (
                  <Badge variant="outline" className="text-xs">
                    изменено полей: {diffFields.length}
                  </Badge>
                )}
              </div>
            </CardHeader>
            <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <FieldBlock
                label="Услуги"
                tone="primary"
                values={edits.services || []}
                onChange={(v) => setEdits((s) => ({ ...s, services: v }))}
                confidence={confs.services}
              />
              <FieldBlock
                label="Исключённые услуги"
                tone="danger"
                values={edits.excluded_services || []}
                onChange={(v) => setEdits((s) => ({ ...s, excluded_services: v }))}
                confidence={confs.excluded_services}
              />
              <FieldBlock
                label="География (основная)"
                tone="primary"
                values={edits.geo_primary || []}
                onChange={(v) => setEdits((s) => ({ ...s, geo_primary: v }))}
                confidence={confs.geo_primary}
              />
              <FieldBlock
                label="География (вторичная)"
                tone="secondary"
                values={edits.geo_secondary || []}
                onChange={(v) => setEdits((s) => ({ ...s, geo_secondary: v }))}
                confidence={confs.geo_secondary}
              />
              <FieldBlock
                label="Исключённая география"
                tone="danger"
                values={edits.excluded_geo || []}
                onChange={(v) => setEdits((s) => ({ ...s, excluded_geo: v }))}
                confidence={confs.excluded_geo}
              />
              <FieldBlock
                label="Бренды-конкуренты"
                tone="secondary"
                values={edits.competitor_brands || []}
                onChange={(v) => setEdits((s) => ({ ...s, competitor_brands: v }))}
                confidence={confs.competitor_brands}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Предпросмотр карты спроса</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              {!mapData || mapData.items.length === 0 ? (
                <div className="p-6 text-sm text-muted-foreground text-center">
                  Карта пока пустая. Сначала примените профиль и дождитесь пересборки.
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Кластер</TableHead>
                      <TableHead>Тип</TableHead>
                      <TableHead>Tier</TableHead>
                      <TableHead className="text-right">Релевантность</TableHead>
                      <TableHead className="text-right">Запросов</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {mapData.items.slice(0, 20).map((c: any) => (
                      <TableRow key={c.id}>
                        <TableCell className="font-medium text-sm">{c.name_ru || c.cluster_key}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">{c.cluster_type}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className="text-xs">{c.quality_tier}</Badge>
                        </TableCell>
                        <TableCell className="text-right text-xs">{(c.business_relevance ?? 0).toFixed(2)}</TableCell>
                        <TableCell className="text-right text-xs">{c.queries_count}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          <div className="text-xs text-muted-foreground">
            Черновик от {draft.generated_at ? new Date(draft.generated_at).toLocaleString("ru") : "—"}
            {draft.generator_version ? ` · v${draft.generator_version}` : ""}
            {draft.signals?.llm_cost_usd != null ? ` · $${Number(draft.signals.llm_cost_usd).toFixed(4)}` : ""}
          </div>
        </>
      )}
    </div>
  );
}

function FieldBlock({
  label, tone, values, onChange, confidence,
}: {
  label: string;
  tone: "primary" | "secondary" | "danger" | "neutral";
  values: string[];
  onChange: (v: string[]) => void;
  confidence?: FieldConfidence;
}) {
  return (
    <div className="rounded-lg border p-3 space-y-2">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold">{label}</div>
        {confidence && <ConfidenceChip value={confidence.confidence} />}
      </div>
      <ChipEditor label="" values={values} onChange={onChange} tone={tone} />
      {confidence?.reasoning_ru && (
        <div className="text-xs text-muted-foreground leading-snug">
          {confidence.reasoning_ru}
          {confidence.evidence_count > 0 && (
            <span className="ml-1 text-muted-foreground/70">· источников: {confidence.evidence_count}</span>
          )}
        </div>
      )}
    </div>
  );
}
