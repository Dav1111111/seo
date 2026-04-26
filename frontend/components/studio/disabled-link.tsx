"use client";

/**
 * <DisabledLink> — renders the same way an enabled cross-module link
 * would, but doesn't navigate and explains WHY (CONCEPT.md §5: empty /
 * disabled UI must explain its state). Used in Studio module pages
 * for cross-links to modules that haven't shipped yet (per
 * IMPLEMENTATION.md §2.1).
 */

import type { ReactNode } from "react";
import { Lock } from "lucide-react";
import { cn } from "@/lib/utils";

export function DisabledLink({
  reason,
  className,
  children,
}: {
  /** Short, owner-friendly reason. Shown as title (hover) and on focus. */
  reason: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-muted-foreground/70 cursor-not-allowed",
        className,
      )}
      title={reason}
      aria-disabled="true"
    >
      <Lock className="h-3 w-3 flex-shrink-0" />
      <span className="line-through decoration-muted-foreground/40">
        {children}
      </span>
    </span>
  );
}
