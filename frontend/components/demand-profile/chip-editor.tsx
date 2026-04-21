"use client";

import { useState } from "react";
import { X, Plus } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  tone?: "neutral" | "primary" | "secondary" | "danger";
}

const TONES: Record<NonNullable<Props["tone"]>, string> = {
  neutral: "bg-muted text-foreground",
  primary: "bg-primary/10 text-primary border-primary/30",
  secondary: "bg-sky-50 text-sky-800 border-sky-200",
  danger: "bg-rose-50 text-rose-800 border-rose-200",
};

export function ChipEditor({ label, values, onChange, placeholder, tone = "neutral" }: Props) {
  const [draft, setDraft] = useState("");

  function add() {
    const v = draft.trim();
    if (!v) return;
    if (values.includes(v)) { setDraft(""); return; }
    onChange([...values, v]);
    setDraft("");
  }
  function remove(v: string) {
    onChange(values.filter((x) => x !== v));
  }

  return (
    <div className="space-y-2">
      <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</div>
      <div className="flex flex-wrap gap-1.5">
        {values.length === 0 && (
          <span className="text-xs text-muted-foreground italic">пусто</span>
        )}
        {values.map((v) => (
          <span
            key={v}
            className={cn(
              "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs",
              TONES[tone],
            )}
          >
            {v}
            <button
              type="button"
              onClick={() => remove(v)}
              className="hover:text-destructive"
              aria-label={`Удалить ${v}`}
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }}
          placeholder={placeholder || "добавить…"}
          className="flex-1 rounded-md border bg-background px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
        />
        <button
          type="button"
          onClick={add}
          className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-accent"
        >
          <Plus className="h-3 w-3" /> добавить
        </button>
      </div>
    </div>
  );
}
