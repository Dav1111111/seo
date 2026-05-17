"use client";

/**
 * Single «совет» card in the unified Studio feed.
 *
 * Mostly presentational — receives an already-shaped AdviceCard and
 * renders it with severity-driven left-border colour, category pill,
 * action line, optional impact note, CTA, and workflow buttons. State
 * changes are delegated to the parent feed.
 *
 * Backend contract: `AdviceCard` in frontend/lib/api.ts. Backend
 * already sorted the feed by `sort_score`; this component does no
 * sorting/filtering of its own.
 */

import Link from "next/link";
import { useState } from "react";
import {
  AlertCircle,
  AlertTriangle,
  Info,
  Lightbulb,
  CheckCircle2,
  ArrowRight,
  ExternalLink,
  Clock3,
  EyeOff,
  Loader2,
  RotateCcw,
  ChevronDown,
  ChevronRight,
  ListChecks,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type {
  AdviceCard as AdviceCardType,
  AdviceSeverity,
  AdviceCategory,
  AdviceCardWorkflowStatus,
} from "@/lib/api";
import { VerificationPill } from "./verification-pill";

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

function workflowStateLabel(card: AdviceCardType): string | null {
  const state = card.state;
  if (state.status === "in_progress") return "в работе";
  if (state.status === "applied") return "применено";
  if (state.status === "dismissed") return "скрыто";
  if (state.status === "snoozed") {
    if (!state.snoozed_until) return "отложено";
    return `отложено до ${new Date(state.snoozed_until).toLocaleDateString("ru-RU")}`;
  }
  return null;
}

function hasProof(card: AdviceCardType): boolean {
  return Boolean(
    card.why_ru
    || card.source_ru
    || card.target_ru
    || card.verification_ru
    || card.evidence_ru.length > 0,
  );
}

export function AdviceCardRow({
  card,
  onStateChange,
  onReVerify,
  busy,
  workflowMode = "active",
}: {
  card: AdviceCardType;
  onStateChange?: (
    card: AdviceCardType,
    status: AdviceCardWorkflowStatus,
  ) => Promise<void> | void;
  // Manual «проверить снова» trigger for the verification pill. The
  // parent feed owns the actual mutation + SWR refresh; this component
  // only wires the click.
  onReVerify?: (card: AdviceCardType) => Promise<void> | void;
  busy?: boolean;
  workflowMode?: "active" | "archive";
}) {
  const sev = SEVERITY_STYLE[card.severity] ?? SEVERITY_STYLE.medium;
  const Icon = sev.icon;
  const categoryLabel = CATEGORY_LABEL[card.category] ?? card.category;
  const pillLabel = SEVERITY_PILL_TEXT[card.severity] ?? card.severity;
  const hasCta = card.cta_ru !== null && card.link !== null;
  const stateLabel = workflowStateLabel(card);
  const [proofOpen, setProofOpen] = useState(false);
  const canShowProof = hasProof(card);

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
          {workflowMode === "archive" && stateLabel && (
            <Badge variant="secondary" className="text-[10px]">
              {stateLabel}
            </Badge>
          )}
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

        {canShowProof && (
          <div className="pt-0.5">
            <Button
              type="button"
              variant="ghost"
              size="xs"
              onClick={() => setProofOpen((v) => !v)}
              aria-expanded={proofOpen}
              className="px-0 text-muted-foreground hover:text-foreground"
              title="Показать источник, факты и способ проверки"
            >
              {proofOpen ? (
                <ChevronDown className="h-3.5 w-3.5" aria-hidden />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" aria-hidden />
              )}
              Доказательства
            </Button>
            {proofOpen && (
              <ProofBlock card={card} />
            )}
          </div>
        )}

        {/* CTA + workflow buttons + source debug line */}
        <div className="flex items-end justify-between gap-3 pt-1.5">
          <div className="flex-1 min-w-0 space-y-2">
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
            {onStateChange && workflowMode === "active" && (
              <div className="flex items-center gap-1.5 flex-wrap">
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  onClick={() => onStateChange(card, "in_progress")}
                  disabled={busy}
                  title="Перенести совет в рабочий план"
                >
                  <ListChecks className="h-3 w-3" aria-hidden />
                  В работу
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="xs"
                  onClick={() => onStateChange(card, "applied")}
                  disabled={busy}
                  title="Отметить, что правка сделана. Система зафиксирует baseline и проверит результат через 14 дней."
                >
                  {busy ? (
                    <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                  ) : (
                    <CheckCircle2 className="h-3 w-3" aria-hidden />
                  )}
                  Применил
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={() => onStateChange(card, "snoozed")}
                  disabled={busy}
                  title="Скрыть совет на 7 дней"
                >
                  <Clock3 className="h-3 w-3" aria-hidden />
                  Отложить
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={() => onStateChange(card, "dismissed")}
                  disabled={busy}
                  title="Скрыть совет, если он не нужен"
                >
                  <EyeOff className="h-3 w-3" aria-hidden />
                  Скрыть
                </Button>
              </div>
            )}
            {onStateChange && workflowMode === "archive" && (
              <div className="flex items-center gap-1.5 flex-wrap">
                <Button
                  type="button"
                  variant="secondary"
                  size="xs"
                  onClick={() => onStateChange(card, "pending")}
                  disabled={busy}
                  title="Вернуть совет в рабочую очередь"
                >
                  {busy ? (
                    <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                  ) : (
                    <RotateCcw className="h-3 w-3" aria-hidden />
                  )}
                  Вернуть
                </Button>
              </div>
            )}
            {/*
              Verification pill: rendered only when the card is in
              `applied` state AND backend has a verification record.
              Tells the owner whether the technical change is actually
              live on the page — separate from the 14-day SEO outcome.
            */}
            <VerificationPill
              state={card.state}
              busy={busy}
              onReVerify={onReVerify ? () => onReVerify(card) : undefined}
            />
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

function ProofBlock({ card }: { card: AdviceCardType }) {
  return (
    <div className="mt-2 rounded-md border bg-muted/30 px-3 py-2 text-xs leading-snug text-muted-foreground">
      <div className="grid gap-2 sm:grid-cols-2">
        {card.why_ru && (
          <ProofRow label="Почему найдено" value={card.why_ru} />
        )}
        {card.source_ru && (
          <ProofRow label="Источник" value={card.source_ru} />
        )}
        {card.target_ru && (
          <ProofRow label="Страница / запрос" value={card.target_ru} />
        )}
        {card.verification_ru && (
          <ProofRow label="Проверка" value={card.verification_ru} />
        )}
      </div>
      {card.evidence_ru.length > 0 && (
        <div className="mt-2 border-t pt-2">
          <div className="mb-1 flex items-center gap-1.5 font-medium text-foreground/80">
            <ListChecks className="h-3.5 w-3.5" aria-hidden />
            Факты
          </div>
          <ul className="space-y-1">
            {card.evidence_ru.map((item, index) => (
              <li key={`${card.id}:evidence:${index}`} className="break-words">
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ProofRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="font-medium text-foreground/80">{label}</div>
      <div className="mt-0.5 break-words">{value}</div>
    </div>
  );
}
