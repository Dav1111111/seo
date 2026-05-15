"use client";

/**
 * Single «совет» card in the unified Studio feed.
 *
 * Pure presentational — receives an already-shaped AdviceCard and
 * renders it with severity-driven left-border colour, category pill,
 * action line, optional impact note, and an optional CTA button that
 * routes either to an internal Studio path (Next.js Link) or, when
 * the link starts with http(s)://, opens an external URL in a new
 * tab.
 *
 * Backend contract: `AdviceCard` in frontend/lib/api.ts. Backend
 * already sorted the feed by `sort_score`; this component does no
 * sorting/filtering of its own.
 */

import Link from "next/link";
import {
  AlertCircle,
  AlertTriangle,
  Info,
  Lightbulb,
  CheckCircle2,
  ArrowRight,
  ExternalLink,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { AdviceCard as AdviceCardType, AdviceSeverity, AdviceCategory } from "@/lib/api";

// Severity → presentation tokens. Left-border colour is the dominant
// visual signal — owners scan the feed top-to-bottom and the left
// edge tells them at a glance how urgent each row is.
const SEVERITY_STYLE: Record<AdviceSeverity, {
  border: string;        // left border colour class
  icon: LucideIcon;
  iconClass: string;     // colour for the leading icon
}> = {
  critical: {
    border: "border-l-4 border-l-red-500",
    icon: AlertCircle,
    iconClass: "text-red-600 dark:text-red-400",
  },
  high: {
    border: "border-l-4 border-l-amber-500",
    icon: AlertTriangle,
    iconClass: "text-amber-600 dark:text-amber-400",
  },
  medium: {
    border: "border-l-4 border-l-yellow-500",
    icon: Info,
    iconClass: "text-yellow-700 dark:text-yellow-400",
  },
  low: {
    border: "border-l-4 border-l-slate-300",
    icon: Info,
    iconClass: "text-slate-500 dark:text-slate-400",
  },
  // Info cards intentionally omit the left border — they're not
  // a problem, just a note. They also get a softer icon and muted
  // background.
  info: {
    border: "",
    icon: Lightbulb,
    iconClass: "text-muted-foreground",
  },
};

const CATEGORY_LABEL: Record<AdviceCategory, string> = {
  technical: "Техника",
  health: "Здоровье",
  funnel: "Воронка",
  schema: "Разметка",
  keywords: "Ключи",
  seo_content: "Контент",
};

const SEVERITY_PILL_TEXT: Record<AdviceSeverity, string> = {
  critical: "сейчас",
  high: "важно",
  medium: "стоит сделать",
  low: "когда-нибудь",
  info: "к сведению",
};

function isExternalLink(href: string): boolean {
  return /^https?:\/\//i.test(href);
}

export function AdviceCardRow({ card }: { card: AdviceCardType }) {
  const sev = SEVERITY_STYLE[card.severity] ?? SEVERITY_STYLE.medium;
  const Icon = sev.icon;
  const categoryLabel = CATEGORY_LABEL[card.category] ?? card.category;
  const pillLabel = SEVERITY_PILL_TEXT[card.severity] ?? card.severity;
  const hasCta = card.cta_ru !== null && card.link !== null;

  return (
    <Card
      size="sm"
      className={cn(
        sev.border,
        card.severity === "info" && "bg-muted/30",
      )}
    >
      <CardContent className="space-y-2.5">
        {/* Header row: severity icon + category pill + severity pill */}
        <div className="flex items-center gap-2 flex-wrap">
          <Icon className={cn("h-4 w-4 flex-shrink-0", sev.iconClass)} aria-hidden />
          <Badge variant="outline" className="uppercase tracking-wide">
            {categoryLabel}
          </Badge>
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            {pillLabel}
          </span>
        </div>

        {/* Title */}
        <h3 className="text-base font-semibold leading-snug">
          {card.title_ru}
        </h3>

        {/* Body — clamp at 3 lines to keep the feed scannable */}
        <p className="text-sm text-muted-foreground leading-snug line-clamp-3">
          {card.body_ru}
        </p>

        {/* Action row */}
        <div className="flex items-start gap-2 pt-0.5">
          <CheckCircle2
            className="h-4 w-4 text-emerald-600 dark:text-emerald-400 flex-shrink-0 mt-0.5"
            aria-hidden
          />
          <p className="text-sm font-medium leading-snug">
            {card.action_ru}
          </p>
        </div>

        {/* Optional expected impact */}
        {card.expected_impact_ru && (
          <p className="text-xs text-emerald-700 dark:text-emerald-400 leading-snug">
            Ожидаемый эффект: {card.expected_impact_ru}
          </p>
        )}

        {/* CTA + source debug line */}
        <div className="flex items-end justify-between gap-3 pt-1.5">
          <div className="flex-1 min-w-0">
            {hasCta && card.link && card.cta_ru && (
              isExternalLink(card.link) ? (
                <Button
                  variant="outline"
                  size="sm"
                  nativeButton={false}
                  render={
                    <a
                      href={card.link}
                      target="_blank"
                      rel="noopener noreferrer"
                    />
                  }
                >
                  {card.cta_ru}
                  <ExternalLink className="ml-1 h-3.5 w-3.5" aria-hidden />
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  nativeButton={false}
                  render={<Link href={card.link} />}
                >
                  {card.cta_ru}
                  <ArrowRight className="ml-1 h-3.5 w-3.5" aria-hidden />
                </Button>
              )
            )}
          </div>
          <span
            className="text-[10px] text-muted-foreground/70 tabular-nums flex-shrink-0"
            title="Модуль-источник этого совета"
          >
            {card.source_module}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
