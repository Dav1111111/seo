/**
 * Loading skeleton for the advice feed.
 *
 * Five placeholder rows that mirror the structural rhythm of an
 * AdviceCardRow (header pills, title, body, action) so the layout
 * doesn't visibly jump when real data arrives.
 *
 * Server-component-safe — no client hooks.
 */

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const CARD_COUNT = 5;

export function AdviceSkeleton() {
  return (
    <div className="space-y-3">
      {/* Summary line + filter chips */}
      <Skeleton className="h-5 w-72" />
      <div className="flex items-center gap-2 flex-wrap">
        <Skeleton className="h-7 w-16" />
        <Skeleton className="h-7 w-20" />
        <Skeleton className="h-7 w-20" />
        <Skeleton className="h-7 w-24" />
        <Skeleton className="h-7 w-16" />
      </div>

      <div className="space-y-2 pt-1">
        {Array.from({ length: CARD_COUNT }).map((_, i) => (
          <Card key={i} size="sm">
            <CardContent className="space-y-2">
              <div className="flex items-center gap-2">
                <Skeleton className="h-4 w-4 rounded-full" />
                <Skeleton className="h-5 w-20" />
                <Skeleton className="h-3 w-14" />
              </div>
              <Skeleton className="h-5 w-4/5" />
              <Skeleton className="h-3.5 w-full" />
              <Skeleton className="h-3.5 w-3/4" />
              <Skeleton className="h-4 w-1/2" />
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
