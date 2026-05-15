"use client";

/**
 * Client shell that resolves the active siteId from `SiteContext`
 * (localStorage-backed switcher) and hands it to `<AdviceFeed />`.
 *
 * Studio in this codebase has no server-side session helper —
 * site selection is owned by `lib/site-context.tsx` and persisted
 * in localStorage. The same indirection is used by `BrainPlanCard`
 * and `KeywordGapsCard` on the old dashboard. Keeping the bridge
 * here means `app/studio/page.tsx` can stay a clean server component
 * with no `'use client'` boundary.
 */

import { useSite } from "@/lib/site-context";

import { AdviceFeed } from "./advice-feed";
import { AdviceSkeleton } from "./advice-skeleton";

export function AdviceFeedShell() {
  const { currentSite, loading } = useSite();

  // While we don't yet know which site is active, show the same
  // skeleton the feed will use — avoids a layout jump.
  if (loading) return <AdviceSkeleton />;

  if (!currentSite) {
    return (
      <div className="rounded-lg border border-dashed bg-muted/30 p-4 text-sm text-muted-foreground">
        Выбери сайт в свитчере слева — лента советов собирается под
        конкретный сайт.
      </div>
    );
  }

  return <AdviceFeed siteId={currentSite.id} />;
}
