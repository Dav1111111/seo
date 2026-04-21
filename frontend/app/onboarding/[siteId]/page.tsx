"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";

const STEP_OF_STATE: Record<string, number> = {
  pending_analyze:     1,
  confirm_business:    1,
  confirm_products:    2,
  confirm_competitors: 3,
  confirm_queries:     4,
  confirm_positions:   5,
  confirm_plan:        6,
  confirm_kpi:         7,
  active:              7,
};

export default function OnboardingIndex() {
  const { siteId } = useParams<{ siteId: string }>();
  const router = useRouter();

  const { data, error } = useSWR(
    siteId ? `onb-index-${siteId}` : null,
    () => api.onboardingState(siteId),
  );

  useEffect(() => {
    if (!data) return;
    const step = STEP_OF_STATE[data.onboarding_step] ?? 1;
    router.replace(`/onboarding/${siteId}/step/${step}`);
  }, [data, siteId, router]);

  if (error) {
    return (
      <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900">
        {String((error as any)?.message || error)}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <Skeleton className="h-4 w-48" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}
