"use client";

/**
 * Strategic focus editor — Studio v2 etap 7 Phase E.
 *
 * Lives on /studio/profile. Three states:
 *   - empty: «фокус не задан» + button «задать фокус»
 *   - active: card with read-only summary + кнопка «Изменить» / «Снять»
 *   - editing: form with all 7 fields (label, products, regions,
 *     query_signals, deprioritised, exit_criterion, owner_note,
 *     deadline). Inputs are «chip» fields where pressing Enter or
 *     comma adds an item.
 *
 * Universal: no domain words baked in. Placeholders give a tourism
 * example because the project ships for one tourism owner today,
 * but they're plain text — they don't lock the field semantics.
 */

import { useState } from "react";
import { mutate as swrMutate } from "swr";
import {
  Target,
  Pencil,
  Save,
  X,
  Trash2,
  Plus,
  Loader2,
  Info,
} from "lucide-react";

import { api } from "@/lib/api";
import type {
  StudioStrategicFocus,
  StudioStrategicFocusInput,
} from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn, getErrorMessage } from "@/lib/utils";


type Props = {
  siteId: string;
  focus: StudioStrategicFocus | null | undefined;
  onChanged: () => void;
};


export function StrategicFocusEditor({ siteId, focus, onChanged }: Props) {
  const [editing, setEditing] = useState(false);

  if (editing) {
    return (
      <FocusForm
        siteId={siteId}
        initial={focus || null}
        onCancel={() => setEditing(false)}
        onSaved={() => {
          setEditing(false);
          onChanged();
        }}
      />
    );
  }

  if (!focus) {
    return (
      <Card className="border-dashed">
        <CardContent className="pt-6 space-y-3">
          <div className="font-medium flex items-center gap-2">
            <Target className="h-5 w-5 text-primary" />
            Стратегический фокус
            <span className="text-xs text-muted-foreground font-normal ml-1">
              не задан
            </span>
          </div>
          <p className="text-sm text-muted-foreground leading-snug">
            Это «лазерная указка» для всей системы. Когда задан — мозг,
            помощник в чате и список услуг сначала смотрят на фокус,
            а уже потом на остальное. Без него все направления равны
            и советы получаются общими.
          </p>
          <Button size="sm" onClick={() => setEditing(true)}>
            <Plus className="h-4 w-4 mr-2" />
            Задать фокус
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-primary/30 bg-primary/5">
      <CardContent className="pt-6 space-y-3">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-primary/80 inline-flex items-center gap-1.5">
              <Target className="h-3.5 w-3.5" />
              Стратегический фокус
            </div>
            <h3 className="text-lg font-medium mt-1">{focus.label}</h3>
            {focus.set_by === "owner_via_chat" && (
              <div className="text-[10px] text-muted-foreground mt-0.5">
                задано через чат с помощником
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setEditing(true)}
              className="cursor-pointer"
            >
              <Pencil className="h-3.5 w-3.5 mr-1.5" />
              Изменить
            </Button>
            <ClearFocusButton siteId={siteId} onCleared={onChanged} />
          </div>
        </div>

        <FocusSummary focus={focus} />
      </CardContent>
    </Card>
  );
}


function FocusSummary({ focus }: { focus: StudioStrategicFocus }) {
  return (
    <div className="space-y-2 text-sm">
      <SummaryRow label="Продукты в фокусе" items={focus.products} />
      <SummaryRow label="Регионы в фокусе" items={focus.regions} />
      <SummaryRow label="Ключевые запросы" items={focus.query_signals} />
      {focus.deprioritised.length > 0 && (
        <SummaryRow
          label="Отложено"
          items={focus.deprioritised}
          tone="muted"
        />
      )}
      {focus.exit_criterion && (
        <div>
          <span className="text-xs text-muted-foreground">Условие выхода:</span>{" "}
          {focus.exit_criterion}
        </div>
      )}
      {focus.deadline && (
        <div>
          <span className="text-xs text-muted-foreground">Дедлайн:</span>{" "}
          {focus.deadline}
        </div>
      )}
      {focus.owner_note && (
        <div className="rounded-md border bg-background/60 px-3 py-2 mt-2">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">
            Заметка
          </div>
          <p className="leading-snug">{focus.owner_note}</p>
        </div>
      )}
    </div>
  );
}


function SummaryRow({
  label,
  items,
  tone = "default",
}: {
  label: string;
  items: string[];
  tone?: "default" | "muted";
}) {
  if (items.length === 0) return null;
  return (
    <div className="flex items-baseline gap-2 flex-wrap">
      <span className="text-xs text-muted-foreground">{label}:</span>
      {items.map((it, i) => (
        <Badge
          key={i}
          variant="outline"
          className={cn(
            "text-xs",
            tone === "muted" &&
              "border-muted-foreground/20 text-muted-foreground line-through",
          )}
        >
          {it}
        </Badge>
      ))}
    </div>
  );
}


function ClearFocusButton({
  siteId,
  onCleared,
}: {
  siteId: string;
  onCleared: () => void;
}) {
  const [busy, setBusy] = useState(false);
  async function clearIt() {
    if (!confirm("Снять фокус? Все рекомендации снова станут общими.")) return;
    setBusy(true);
    try {
      await api.studioClearStrategicFocus(siteId);
      // Invalidate brain plan cache too — actions might re-rank.
      await swrMutate(
        (key: unknown) =>
          Array.isArray(key) &&
          (key as string[])[0]?.startsWith("studio:"),
        undefined,
        { revalidate: true },
      );
      onCleared();
    } catch (e: unknown) {
      alert("Не получилось: " + getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Button
      size="sm"
      variant="ghost"
      onClick={clearIt}
      disabled={busy}
      className="cursor-pointer text-muted-foreground hover:text-red-700"
      title="Снять фокус"
    >
      <Trash2 className="h-3.5 w-3.5 mr-1.5" />
      Снять
    </Button>
  );
}


// ── Form ─────────────────────────────────────────────────────────────


function FocusForm({
  siteId,
  initial,
  onCancel,
  onSaved,
}: {
  siteId: string;
  initial: StudioStrategicFocus | null;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [label, setLabel] = useState(initial?.label || "");
  const [products, setProducts] = useState<string[]>(initial?.products || []);
  const [regions, setRegions] = useState<string[]>(initial?.regions || []);
  const [querySignals, setQuerySignals] = useState<string[]>(
    initial?.query_signals || [],
  );
  const [deprioritised, setDeprioritised] = useState<string[]>(
    initial?.deprioritised || [],
  );
  const [exitCriterion, setExitCriterion] = useState(
    initial?.exit_criterion || "",
  );
  const [ownerNote, setOwnerNote] = useState(initial?.owner_note || "");
  const [deadline, setDeadline] = useState(initial?.deadline || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    setErr(null);
    if (!label.trim()) {
      setErr("Опиши фокус одной фразой — например, «Багги-экспедиции в Абхазию».");
      return;
    }
    if (
      products.length === 0
      && regions.length === 0
      && querySignals.length === 0
    ) {
      setErr(
        "Укажи хотя бы одно из трёх: продукт, регион или ключевой запрос. " +
          "Без этого мозгу не на что опираться.",
      );
      return;
    }
    const payload: StudioStrategicFocusInput = {
      label: label.trim(),
      products,
      regions,
      query_signals: querySignals,
      deprioritised,
      exit_criterion: exitCriterion.trim() || null,
      owner_note: ownerNote.trim() || null,
      deadline: deadline.trim() || null,
    };
    setBusy(true);
    try {
      await api.studioSetStrategicFocus(siteId, payload);
      // Bust both focus and brain plan caches so banner + plan refresh.
      await swrMutate(
        (key: unknown) =>
          Array.isArray(key) &&
          (key as string[])[0]?.startsWith("studio:"),
        undefined,
        { revalidate: true },
      );
      onSaved();
    } catch (e: unknown) {
      setErr(getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="border-primary/40">
      <CardContent className="pt-6 space-y-4">
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-primary/80 inline-flex items-center gap-1.5">
            <Target className="h-3.5 w-3.5" />
            {initial ? "Изменить фокус" : "Задать фокус"}
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            Чем точнее опишешь — тем точнее будут советы. Поля «отложено»
            и «условие выхода» делают фокус живым: мозг ставит «отложенное»
            ниже, а условие выхода — это сигнал «когда переключаться».
          </p>
        </div>

        <Field
          label="Главная фраза"
          hint="Одной строкой, своими словами — что для тебя сейчас главное."
          required
        >
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Например: Багги-экспедиции в Абхазию"
            className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
        </Field>

        <ChipField
          label="Продукты в фокусе"
          hint="Что именно продвигаем. Пиши через Enter или запятую."
          values={products}
          onChange={setProducts}
          placeholder="багги-экспедиции"
        />

        <ChipField
          label="Регионы в фокусе"
          hint="Где это происходит. Один или несколько."
          values={regions}
          onChange={setRegions}
          placeholder="абхазия"
        />

        <ChipField
          label="Ключевые запросы / сигналы"
          hint="По каким запросам мы хотим выйти в топ. По ним помощник будет считать «в фокусе ли это»."
          values={querySignals}
          onChange={setQuerySignals}
          placeholder="экскурсии абхазия"
        />

        <ChipField
          label="Отложено (не предлагать сейчас)"
          hint="Что важно, но не сейчас. Помощник перестанет тебе про них напоминать."
          values={deprioritised}
          onChange={setDeprioritised}
          placeholder="яхты, вертолёты, гастрономия"
        />

        <Field
          label="Условие выхода из фокуса"
          hint="Когда переключаемся на следующее. Своими словами — это для тебя и для помощника."
        >
          <input
            type="text"
            value={exitCriterion}
            onChange={(e) => setExitCriterion(e.target.value)}
            placeholder="Когда выйдем в топ-10 по «экскурсии абхазия»"
            className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
        </Field>

        <Field
          label="Дедлайн (необязательно)"
          hint="Любая дата или фраза — «к началу сезона», «до 1 июня»."
        >
          <input
            type="text"
            value={deadline}
            onChange={(e) => setDeadline(e.target.value)}
            placeholder="2026-06-01 или «к началу июня»"
            className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
        </Field>

        <Field
          label="Заметка для себя"
          hint="Свободный текст — мысли, причины, контекст."
        >
          <textarea
            value={ownerNote}
            onChange={(e) => setOwnerNote(e.target.value)}
            rows={2}
            placeholder="Сначала разбираемся с этим. Остальное потом."
            className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 resize-none"
          />
        </Field>

        {err && (
          <div className="rounded-md border border-red-300 bg-red-50 text-red-900 px-3 py-2 text-sm flex items-start gap-2">
            <Info className="h-4 w-4 mt-0.5 flex-shrink-0" />
            {err}
          </div>
        )}

        <div className="flex items-center gap-2 pt-2">
          <Button onClick={save} disabled={busy}>
            {busy ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-2" />
            )}
            {busy ? "Сохраняю…" : "Сохранить"}
          </Button>
          <Button variant="outline" onClick={onCancel} disabled={busy}>
            <X className="h-4 w-4 mr-2" />
            Отмена
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}


function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-sm font-medium block">
        {label}
        {required && <span className="text-red-700 ml-1">*</span>}
      </label>
      {hint && (
        <p className="text-xs text-muted-foreground leading-snug">{hint}</p>
      )}
      {children}
    </div>
  );
}


function ChipField({
  label,
  hint,
  values,
  onChange,
  placeholder,
}: {
  label: string;
  hint?: string;
  values: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState("");

  function addCurrent() {
    const cleaned = draft
      .split(/[,;\n]/)
      .map((s) => s.trim().toLowerCase())
      .filter((s) => s.length > 0)
      .filter((s) => !values.includes(s));
    if (cleaned.length === 0) return;
    onChange([...values, ...cleaned]);
    setDraft("");
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addCurrent();
    } else if (e.key === "Backspace" && draft === "" && values.length > 0) {
      onChange(values.slice(0, -1));
    }
  }

  function remove(idx: number) {
    onChange(values.filter((_, i) => i !== idx));
  }

  return (
    <Field label={label} hint={hint}>
      <div className="rounded-md border bg-background px-2 py-1.5 flex items-center gap-1.5 flex-wrap focus-within:ring-2 focus-within:ring-primary/40">
        {values.map((v, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary/5 text-foreground text-xs px-2 py-0.5"
          >
            {v}
            <button
              type="button"
              onClick={() => remove(i)}
              aria-label={`Удалить «${v}»`}
              className="text-muted-foreground hover:text-red-700 cursor-pointer"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          onBlur={addCurrent}
          placeholder={values.length === 0 ? placeholder : ""}
          className="flex-1 min-w-[120px] bg-transparent text-sm outline-none px-1 py-0.5"
        />
      </div>
    </Field>
  );
}
