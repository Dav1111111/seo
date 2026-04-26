"use client";

/**
 * <DisabledLink> — renders the same way an enabled cross-module link
 * would, but doesn't navigate and explains WHY (CONCEPT.md §5: empty /
 * disabled UI must explain its state). Used in Studio module pages
 * for cross-links to modules that haven't shipped yet (per
 * IMPLEMENTATION.md §2.1).
 *
 * Renders as a real <button disabled>, not a <span>, so keyboard users
 * can tab to it and screen readers announce the reason via
 * aria-describedby. We don't have a Tooltip primitive in
 * components/ui/, so the reason is also the button's `title` for
 * mouse hover (lesser-but-fine UX) — adding a shadcn Tooltip primitive
 * is out of scope for this PR.
 */

import { useId, type ReactNode } from "react";
import { Lock } from "lucide-react";
import { cn } from "@/lib/utils";

export function DisabledLink({
  reason,
  className,
  children,
}: {
  /** Short, owner-friendly reason. Shown as title (hover) and via aria-describedby (focus / SR). */
  reason: string;
  className?: string;
  children: ReactNode;
}) {
  const reasonId = useId();
  return (
    <>
      <button
        type="button"
        disabled
        aria-describedby={reasonId}
        title={reason}
        className={cn(
          "inline-flex items-center gap-1 text-muted-foreground/70 cursor-not-allowed bg-transparent border-0 p-0 font-inherit text-inherit",
          // Keep the focus ring visible — it's the whole point of making
          // this focusable. Match the rest of the app's focus-visible pattern.
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:rounded-sm",
          // Override the default <button disabled> opacity so the link
          // still looks like a link, just locked.
          "disabled:opacity-100",
          className,
        )}
      >
        <Lock className="h-3 w-3 flex-shrink-0" />
        <span className="line-through decoration-muted-foreground/40">
          {children}
        </span>
      </button>
      <span id={reasonId} className="sr-only">
        {reason}
      </span>
    </>
  );
}
