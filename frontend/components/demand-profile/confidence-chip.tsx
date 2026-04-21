"use client";

import { cn } from "@/lib/utils";

export function ConfidenceChip({ value, className }: { value: number; className?: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const tone =
    value >= 0.8 ? "bg-emerald-100 text-emerald-800 border-emerald-300"
    : value >= 0.5 ? "bg-amber-100 text-amber-800 border-amber-300"
    : "bg-rose-100 text-rose-800 border-rose-300";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium",
        tone,
        className,
      )}
      title={`Уверенность ${pct}%`}
    >
      {pct}%
    </span>
  );
}
