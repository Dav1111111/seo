"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import useSWR from "swr";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Check } from "lucide-react";

const STEPS = [
  { n: 1, label: "Бизнес",       slug: "1" },
  { n: 2, label: "Продукты",     slug: "2" },
  { n: 3, label: "Конкуренты",   slug: "3" },
  { n: 4, label: "Запросы",      slug: "4" },
  { n: 5, label: "Позиции",      slug: "5" },
  { n: 6, label: "План",         slug: "6" },
  { n: 7, label: "Цели",         slug: "7" },
];

// Map backend state-machine values to the step number you're allowed on.
// "pending_analyze" → 1 (forced), "confirm_business" → 1 (reviewing), etc.
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

export default function OnboardingLayout({ children }: { children: React.ReactNode }) {
  const { siteId } = useParams<{ siteId: string }>();
  const pathname = usePathname();

  const { data } = useSWR(
    siteId ? `onb-state-${siteId}` : null,
    () => api.onboardingState(siteId),
    { refreshInterval: 0 },
  );

  const currentStep = STEP_OF_STATE[data?.onboarding_step ?? "pending_analyze"] ?? 1;
  const activeSlug = pathname?.split("/").pop();

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      {/* Header */}
      <div className="mb-8">
        <div className="flex items-baseline justify-between gap-4 mb-2">
          <h1 className="text-2xl font-bold">Давай разберёмся в твоём сайте</h1>
          {data?.domain && (
            <span className="text-sm text-muted-foreground font-mono">{data.domain}</span>
          )}
        </div>
        <p className="text-sm text-muted-foreground">
          Семь шагов. Я показываю что понял — ты подтверждаешь или правишь. Это база,
          на которой дальше всё строится.
        </p>
      </div>

      {/* Progress — 7 dots with labels */}
      <nav className="mb-8" aria-label="Шаги онбординга">
        <ol className="flex items-center justify-between gap-1 flex-wrap sm:flex-nowrap">
          {STEPS.map((step) => {
            const isPast = step.n < currentStep;
            const isCurrent = `${step.n}` === activeSlug;
            const reachable = step.n <= currentStep;
            return (
              <li key={step.n} className="flex-1 min-w-0">
                <Link
                  href={reachable ? `/onboarding/${siteId}/step/${step.slug}` : "#"}
                  aria-disabled={!reachable}
                  className={cn(
                    "flex flex-col items-center gap-1.5 py-2 px-1 rounded transition-colors",
                    reachable ? "hover:bg-accent cursor-pointer" : "cursor-not-allowed opacity-50",
                  )}
                >
                  <div
                    className={cn(
                      "h-8 w-8 rounded-full border-2 flex items-center justify-center text-xs font-semibold shrink-0",
                      isCurrent && "border-primary bg-primary text-primary-foreground",
                      !isCurrent && isPast && "border-emerald-500 bg-emerald-500 text-white",
                      !isCurrent && !isPast && "border-muted-foreground/30 text-muted-foreground",
                    )}
                  >
                    {isPast ? <Check className="h-4 w-4" /> : step.n}
                  </div>
                  <span
                    className={cn(
                      "text-[11px] text-center leading-tight",
                      isCurrent ? "font-semibold text-foreground" : "text-muted-foreground",
                    )}
                  >
                    {step.label}
                  </span>
                </Link>
              </li>
            );
          })}
        </ol>
      </nav>

      {/* Step content */}
      <div className="rounded-xl border bg-card p-6">
        {children}
      </div>

      {/* Footer — exit + debug */}
      <div className="mt-4 flex items-center justify-between text-xs text-muted-foreground">
        <Link href="/" className="hover:text-foreground">← Выйти на дашборд</Link>
        <span>
          Состояние: <span className="font-mono">{data?.onboarding_step ?? "…"}</span>
        </span>
      </div>
    </div>
  );
}
