"use client";

/**
 * Studio /profile — owner-facing editor for `target_config`.
 *
 * Why this page exists (V2 prerequisite):
 * The query classifier (etap 4 in IMPLEMENTATION-V2.md) anchors on
 * `primary_product` + `services` + `geo_primary`. If the profile says
 * the site rents buggies but actually runs buggy expeditions, the
 * classifier will tag rental queries («прокат сочи») as relevant and
 * we'll keep pulling «джинсы багги» from Wordstat. Owner needs to be
 * able to fix what the LLM hallucinated during onboarding.
 *
 * Scope (v1):
 *   - text input for primary_product (required)
 *   - chip lists for services / secondary_products / geo_primary / geo_secondary
 *   - textarea for narrative_ru (the system's understanding of the business)
 *   - last-edited indicator (onboarding vs owner)
 *   - save → re-read canonical state
 *
 * Out of scope (v2 будут отдельные UI):
 *   - service_weights (computed by tasks)
 *   - business_truth (computed by BusinessTruth agent)
 *   - competitor_*, growth_opportunities (computed)
 */

import { useEffect, useState } from "react";
import useSWR from "swr";
import Link from "next/link";

import { api } from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";
import { fmtAge } from "@/lib/format";
import { getErrorMessage } from "@/lib/utils";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { StrategicFocusEditor } from "@/components/studio/strategic-focus-editor";
import {
  ArrowLeft,
  Sparkles,
  Save,
  Plus,
  X,
  Info,
  CheckCircle2,
  AlertTriangle,
} from "lucide-react";
import { cn } from "@/lib/utils";

type Profile = {
  primary_product: string;
  services: string[];
  secondary_products: string[];
  geo_primary: string[];
  geo_secondary: string[];
  narrative_ru: string;
};

const EMPTY_PROFILE: Profile = {
  primary_product: "",
  services: [],
  secondary_products: [],
  geo_primary: [],
  geo_secondary: [],
  narrative_ru: "",
};

export default function StudioProfilePage() {
  const { currentSite, loading: siteLoading } = useSite();
  const siteId = currentSite?.id || "";

  const { data, error, isLoading, mutate } = useSWR(
    siteId ? studioKey("profile", siteId) : null,
    () => api.studioGetProfile(siteId),
  );

  // Local edit state — initialized from server, dirty-flag tracked
  // explicitly so we can show «несохранённые изменения» banner.
  const [draft, setDraft] = useState<Profile>(EMPTY_PROFILE);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState<{
    kind: "ok" | "err";
    text: string;
  } | null>(null);

  // Re-seed local draft from server only when it would not clobber
  // unsaved work. Background SWR revalidations (focus / reconnect)
  // would otherwise wipe what the user is typing.
  useEffect(() => {
    if (!data?.profile) return;
    if (dirty) return;  // user has uncommitted edits — don't touch
    setDraft(data.profile);
  }, [data, dirty]);

  function patch<K extends keyof Profile>(k: K, v: Profile[K]) {
    setDraft((d) => ({ ...d, [k]: v }));
    setDirty(true);
    setBanner(null);
  }

  async function onSave() {
    if (!siteId || saving) return;
    setSaving(true);
    setBanner(null);
    try {
      const res = await api.studioPutProfile(siteId, draft);
      setBanner({
        kind: "ok",
        text: "Профиль сохранён. Классификатор запросов теперь будет использовать эти данные.",
      });
      setDirty(false);
      // Replace SWR cache with the canonical post-write shape.
      await mutate(res, { revalidate: false });
    } catch (e: unknown) {
      setBanner({ kind: "err", text: getErrorMessage(e) });
    } finally {
      setSaving(false);
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
              Выбери сайт в свитчере слева — профиль редактируется в
              контексте конкретного сайта.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="p-4 sm:p-6 space-y-3 max-w-3xl">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-4 sm:p-6 max-w-3xl">
        <Card className="border-red-300 bg-red-50">
          <CardContent className="pt-6 text-sm text-red-900">
            Не удалось загрузить профиль:{" "}
            {error ? getErrorMessage(error) : "нет данных"}
          </CardContent>
        </Card>
      </div>
    );
  }

  const editedByOwner = data.last_edited_by === "owner";

  return (
    <div className="p-4 sm:p-6 space-y-5 max-w-3xl">
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
            <Sparkles className="h-6 w-6 text-primary" /> Профиль бизнеса
          </h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            Что система считает твоим бизнесом. От этого зависит как
            классифицируются запросы (наш / смежный / спорный / мусор) и
            какие фразы Wordstat считает релевантными.
          </p>
        </div>
        <Button onClick={onSave} disabled={!dirty || saving} size="sm">
          <Save
            className={cn(
              "h-4 w-4 mr-2",
              saving && "animate-pulse",
            )}
          />
          {saving ? "Сохраняю…" : dirty ? "Сохранить" : "Сохранено"}
        </Button>
      </div>

      {/* Provenance indicator */}
      <div
        className={cn(
          "text-xs rounded-md border px-3 py-2 flex items-start gap-2",
          editedByOwner
            ? "border-emerald-300 bg-emerald-50 text-emerald-900"
            : "border-dashed text-muted-foreground",
        )}
      >
        {editedByOwner ? (
          <CheckCircle2 className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
        ) : (
          <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
        )}
        <span>
          {editedByOwner ? (
            <>
              Профиль был отредактирован вручную{" "}
              <strong>{fmtAge(data.last_edited_at)}</strong>.
            </>
          ) : (
            <>
              Профиль сгенерирован LLM при онбординге и не редактировался
              вручную. Часто LLM ошибается (дописывает несуществующие
              услуги, путает регионы) — проверь и поправь.
            </>
          )}
        </span>
      </div>

      {/* Save feedback */}
      {banner && (
        <div
          className={cn(
            "rounded-md border px-3 py-2 text-sm flex items-start gap-2",
            banner.kind === "ok" &&
              "border-emerald-300 bg-emerald-50 text-emerald-900",
            banner.kind === "err" && "border-red-300 bg-red-50 text-red-900",
          )}
        >
          {banner.kind === "ok" ? (
            <CheckCircle2 className="h-4 w-4 mt-0.5 flex-shrink-0" />
          ) : (
            <AlertTriangle className="h-4 w-4 mt-0.5 flex-shrink-0" />
          )}
          <span>{banner.text}</span>
        </div>
      )}

      {/* Dirty banner */}
      {dirty && !banner && (
        <div className="rounded-md border border-amber-300 bg-amber-50 text-amber-900 px-3 py-2 text-sm flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 mt-0.5 flex-shrink-0" />
          <span>
            Несохранённые изменения. Не забудь нажать «Сохранить».
          </span>
        </div>
      )}

      {/* V2 etap 7 Phase E — strategic focus. Lives above the
          profile fields because it's the lens that recolors everything
          downstream. Independent SWR cache; doesn't share dirty state
          with the profile form. */}
      <FocusBlock siteId={siteId} />

      {/* Primary product */}
      <Card>
        <CardContent className="pt-5 space-y-2">
          <label className="block">
            <span className="text-sm font-medium">
              Основной продукт <span className="text-red-600">*</span>
            </span>
            <p className="text-xs text-muted-foreground mt-0.5 mb-2">
              Одно слово / короткая фраза. На него опирается классификатор:
              запросы где это слово прямо упоминается → «наш».
            </p>
            <input
              type="text"
              value={draft.primary_product}
              onChange={(e) => patch("primary_product", e.target.value)}
              placeholder="например: багги"
              maxLength={80}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </label>
        </CardContent>
      </Card>

      {/* Services */}
      <ChipField
        label="Услуги"
        hint="Все услуги что вы реально предлагаете. Не дописывай несуществующие — классификатор поверит и потянет с Wordstat нерелевантное."
        items={draft.services}
        onChange={(items) => patch("services", items)}
        placeholder="например: экспедиции, маршруты, прокат"
      />

      {/* Secondary products */}
      <ChipField
        label="Дополнительные продукты"
        hint="Связанные с основным, но не главные. Используются для смежных запросов."
        items={draft.secondary_products}
        onChange={(items) => patch("secondary_products", items)}
        placeholder="например: маршруты, экспедиции"
      />

      {/* Geo primary */}
      <ChipField
        label="Основная география *"
        hint="Регионы где работаете. Без них классификатор не сможет отличить «багги Сочи» (наш) от «багги Москва» (не наш)."
        items={draft.geo_primary}
        onChange={(items) => patch("geo_primary", items)}
        placeholder="например: сочи, абхазия, красная поляна"
        required
      />

      {/* Geo secondary */}
      <ChipField
        label="Дополнительная география"
        hint="Куда ездите редко или соседние регионы куда смежные клиенты могут отнести вас."
        items={draft.geo_secondary}
        onChange={(items) => patch("geo_secondary", items)}
        placeholder="например: адлер"
      />

      {/* Narrative */}
      <Card>
        <CardContent className="pt-5 space-y-2">
          <label className="block">
            <span className="text-sm font-medium">
              Описание бизнеса (narrative)
            </span>
            <p className="text-xs text-muted-foreground mt-0.5 mb-2">
              Один абзац, как бы ты объяснил бизнес другу. Это
              самая важная часть для смежных запросов — LLM решает по
              этому тексту, насколько «экскурсии Сочи» подходят твоему
              «багги Абхазия».
            </p>
            <textarea
              value={draft.narrative_ru}
              onChange={(e) => patch("narrative_ru", e.target.value)}
              placeholder="Например: премиальный клуб активного отдыха в Сочи. Багги-экспедиции по Абхазии, яхт-туры, вертолётные прогулки. Гости — обеспеченные туристы 30-50 лет."
              maxLength={4000}
              rows={6}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <div className="text-xs text-muted-foreground text-right mt-1">
              {draft.narrative_ru.length} / 4000
            </div>
          </label>
        </CardContent>
      </Card>

      {/* Bottom save */}
      <div className="flex justify-end pt-2 border-t">
        <Button onClick={onSave} disabled={!dirty || saving} size="sm">
          <Save
            className={cn(
              "h-4 w-4 mr-2",
              saving && "animate-pulse",
            )}
          />
          {saving ? "Сохраняю…" : dirty ? "Сохранить" : "Сохранено"}
        </Button>
      </div>
    </div>
  );
}

// ── Chip-style array editor ──────────────────────────────────────────

function ChipField({
  label,
  hint,
  items,
  onChange,
  placeholder,
  required = false,
}: {
  label: string;
  hint: string;
  items: string[];
  onChange: (items: string[]) => void;
  placeholder: string;
  required?: boolean;
}) {
  const [pending, setPending] = useState("");

  function commit() {
    const v = pending.trim();
    if (!v) return;
    if (items.some((x) => x.toLowerCase() === v.toLowerCase())) {
      setPending("");
      return;
    }
    onChange([...items, v]);
    setPending("");
  }

  function remove(i: number) {
    onChange(items.filter((_, idx) => idx !== i));
  }

  return (
    <Card>
      <CardContent className="pt-5 space-y-2">
        <div>
          <span className="text-sm font-medium">{label}</span>
          <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>
        </div>

        <div className="flex flex-wrap gap-1.5 min-h-[2rem]">
          {items.length === 0 && !required && (
            <span className="text-xs text-muted-foreground italic self-center">
              пусто (необязательно)
            </span>
          )}
          {items.length === 0 && required && (
            <span className="text-xs text-red-700 italic self-center">
              обязательно — добавь хотя бы один
            </span>
          )}
          {items.map((it, i) => (
            // Key by value (items are case-insensitive deduped on
            // commit), not by index — removing position 0 with index
            // keys causes React to reuse DOM and the aria-label
            // briefly references the wrong chip.
            <Badge
              key={it}
              variant="outline"
              className="gap-1 pr-1 text-xs"
            >
              <span>{it}</span>
              <button
                type="button"
                onClick={() => remove(i)}
                className="hover:text-red-700 rounded-sm"
                aria-label={`Удалить ${it}`}
              >
                <X className="h-3 w-3" />
              </button>
            </Badge>
          ))}
        </div>

        <div className="flex gap-2">
          <input
            type="text"
            value={pending}
            onChange={(e) => setPending(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commit();
              }
              if (e.key === "," || e.key === ";") {
                e.preventDefault();
                commit();
              }
            }}
            placeholder={placeholder}
            maxLength={80}
            className="flex-1 rounded-md border bg-background px-3 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={commit}
            disabled={!pending.trim()}
          >
            <Plus className="h-4 w-4 mr-1" />
            Добавить
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}


// ── Strategic focus block (V2 etap 7 Phase E) ────────────────────────


function FocusBlock({ siteId }: { siteId: string }) {
  const { data: focus, mutate: refetch } = useSWR(
    siteId ? studioKey("strategic_focus", siteId) : null,
    () => api.studioGetStrategicFocus(siteId),
  );

  return (
    <StrategicFocusEditor
      siteId={siteId}
      focus={focus}
      onChanged={() => {
        refetch();
      }}
    />
  );
}
