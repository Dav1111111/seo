"use client";

/**
 * Strategic-focus banner — Studio v2 etap 7 Phase E.
 *
 * Compact strip at the top of /studio (and reusable elsewhere) that
 * declares «вот сейчас работаем над этим». Hidden when no focus is
 * set; otherwise sticky / prominent so the owner can't miss it.
 *
 * Reads via SWR so it auto-updates after the editor on /studio/profile
 * saves, after the chat «Применить» dialog applies, or after «Снять».
 */

import Link from "next/link";
import useSWR from "swr";
import { Target, ChevronRight } from "lucide-react";

import { api } from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";
import { cn } from "@/lib/utils";


export function StrategicFocusBanner() {
  const { currentSite } = useSite();
  const siteId = currentSite?.id || "";

  const { data: focus } = useSWR(
    siteId ? studioKey("strategic_focus", siteId) : null,
    () => api.studioGetStrategicFocus(siteId),
  );

  if (!siteId || !focus) return null;

  const exit = focus.exit_criterion;
  const deadline = focus.deadline;

  return (
    <Link
      href="/studio/profile"
      className={cn(
        "block rounded-lg border border-primary/40 bg-primary/5",
        "hover:bg-primary/10 transition-colors cursor-pointer",
        "px-4 py-3",
      )}
      title="Открыть редактор стратегического фокуса"
    >
      <div className="flex items-start gap-3">
        <Target className="h-5 w-5 text-primary mt-0.5 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-[10px] uppercase tracking-wider text-primary/80 font-medium">
            Сейчас работаем над
          </div>
          <div className="font-medium text-base mt-0.5 truncate">
            {focus.label}
          </div>
          {(exit || deadline) && (
            <div className="text-xs text-muted-foreground mt-1 leading-snug">
              {exit && <span>Выходим из фокуса: {exit}</span>}
              {exit && deadline && <span> · </span>}
              {deadline && <span>Дедлайн: {deadline}</span>}
            </div>
          )}
        </div>
        <ChevronRight className="h-4 w-4 text-muted-foreground flex-shrink-0 mt-1" />
      </div>
    </Link>
  );
}
