/**
 * Strategic-focus pill — small inline badge marking list items that
 * match the site's current strategic_focus tokens (products / regions
 * / query_signals). Canonical style mirrors the pill rendered by
 * brain-plan-card.tsx so a focus-tagged action card and a focus-tagged
 * row in /studio/queries look like the same visual concept.
 *
 * Render conservatively — sits next to an item's title, takes no
 * vertical space, hidden when `in_focus` is false so non-focused
 * items don't grow a placeholder.
 */

import { cn } from "@/lib/utils";

export function FocusPill({
  in_focus,
  className,
}: {
  in_focus: boolean | null | undefined;
  className?: string;
}) {
  if (!in_focus) return null;
  return (
    <span
      className={cn(
        "text-[10px] uppercase tracking-wide rounded-full",
        "border border-primary/40 bg-primary/10 text-primary",
        "px-2 py-0.5 whitespace-nowrap",
        className,
      )}
      title="В зоне твоего стратегического фокуса"
    >
      в фокусе
    </span>
  );
}
