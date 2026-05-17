"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import {
  ArrowRight,
  CheckCircle2,
  Clock3,
  ExternalLink,
  EyeOff,
  Loader2,
  RotateCcw,
  Timer,
} from "lucide-react";

import {
  getGrowthPlan,
  patchAdviceCardState,
  reVerifyAdviceCard,
  type AdviceCardWorkflowStatus,
  type GrowthPlanItem,
  type GrowthPlanResponse,
  type GrowthPlanStage,
} from "@/lib/api";
import { VerificationPill } from "@/components/studio/verification-pill";
import { studioKey } from "@/lib/studio-keys";
import { useSite } from "@/lib/site-context";
import { fmtAge, pluralRu } from "@/lib/format";
import { cn, getErrorMessage } from "@/lib/utils";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const STAGE_ORDER: GrowthPlanStage[] = [
  "found",
  "in_progress",
  "awaiting_followup",
  "measured",
  "snoozed",
  "dismissed",
];

const STAGE_LABEL: Record<GrowthPlanStage, string> = {
  found: "Найдено",
  in_progress: "В работе",
  awaiting_followup: "Ждём проверку",
  measured: "Проверено",
  snoozed: "Отложено",
  dismissed: "Скрыто",
};

const STAGE_HINT: Record<GrowthPlanStage, string> = {
  found: "Новые советы, которые ещё не взяты в работу.",
  in_progress: "То, что уже решили делать руками.",
  awaiting_followup: "Правки применены, baseline зафиксирован, ждём 14 дней.",
  measured: "Правки, по которым уже есть дельта до/после.",
  snoozed: "Временно отложенные советы.",
  dismissed: "Скрытые советы, которые можно вернуть.",
};

const SEVERITY_LABEL: Record<string, string> = {
  critical: "срочно",
  high: "важно",
  medium: "средне",
  low: "низко",
  info: "инфо",
};

// Walk every column and return true if any item is mid-verification.
// SWR uses this to poll every 5s while a Celery verify task is in
// flight, then go silent the moment everything resolves.
function planHasPendingVerification(
  plan: GrowthPlanResponse | null | undefined,
): boolean {
  if (!plan) return false;
  for (const column of Object.values(plan.columns)) {
    for (const item of column) {
      if (item.state?.verification_status === "pending") return true;
    }
  }
  return false;
}

export default function StudioGrowthPlanPage() {
  const { currentSite, loading: siteLoading } = useSite();
  const siteId = currentSite?.id || "";
  const {
    data,
    error,
    isLoading,
    isValidating,
    mutate,
  } = useSWR<GrowthPlanResponse>(
    siteId ? studioKey("growth_plan", siteId) : null,
    () => getGrowthPlan(siteId),
    {
      refreshInterval: (latest) => planHasPendingVerification(latest) ? 5000 : 0,
    },
  );
  const [busyId, setBusyId] = useState<string | null>(null);

  async function setAdviceStatus(
    item: GrowthPlanItem,
    status: AdviceCardWorkflowStatus,
  ) {
    setBusyId(item.id);
    try {
      await patchAdviceCardState(siteId, item.id, {
        status,
        snooze_days: status === "snoozed" ? 7 : null,
      });
      await mutate();
    } finally {
      setBusyId(null);
    }
  }

  async function handleReVerify(item: GrowthPlanItem) {
    setBusyId(item.id);
    try {
      await reVerifyAdviceCard(siteId, item.id);
      await mutate();
    } finally {
      setBusyId(null);
    }
  }

  if (siteLoading) {
    return (
      <div className="p-4 sm:p-6 space-y-3">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-80 w-full" />
      </div>
    );
  }

  if (!currentSite) {
    return (
      <div className="p-4 sm:p-6">
        <Card className="border-dashed max-w-2xl">
          <CardContent className="pt-6 space-y-2">
            <div className="font-medium">Сайт не выбран</div>
            <p className="text-sm text-muted-foreground">
              Выбери сайт слева, чтобы увидеть рабочий SEO-план.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const stats = data?.stats;
  const openTotal = stats?.total_open ?? 0;

  return (
    <div className="p-4 sm:p-6 space-y-5 max-w-7xl">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold">План роста</h1>
        <p className="text-sm text-muted-foreground max-w-3xl">
          Рабочая доска SEO: новые советы, задачи в работе, применённые
          правки и проверка результата через 14 дней.
        </p>
      </header>

      {isLoading && (
        <div className="grid gap-3 sm:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-24 w-full" />
          ))}
        </div>
      )}

      {error && (
        <Card className="border-red-300 bg-red-50/60">
          <CardContent className="pt-4 text-sm text-red-900">
            Не удалось загрузить план: {getErrorMessage(error)}
          </CardContent>
        </Card>
      )}

      {data && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Stat label="открыто" value={openTotal} />
            <Stat label="в работе" value={stats?.in_progress ?? 0} />
            <Stat label="ждут замер" value={stats?.awaiting_followup ?? 0} />
            <Stat label="проверено" value={stats?.measured ?? 0} />
            <Stat label="скрыто/отложено" value={(stats?.snoozed ?? 0) + (stats?.dismissed ?? 0)} />
          </div>

          <div className="text-xs text-muted-foreground rounded-md border border-dashed px-3 py-2">
            Когда нажимаешь «Применил», система фиксирует baseline. Через
            14 дней outcome-модуль сравнит показы, клики и позиции. Это не
            обещание роста, а контроль факта после правки.
          </div>

          <div className="grid gap-4 xl:grid-cols-3">
            {STAGE_ORDER.map((stage) => (
              <PlanColumn
                key={stage}
                stage={stage}
                items={data.columns[stage] ?? []}
                busyId={busyId}
                onSetStatus={setAdviceStatus}
                onReVerify={handleReVerify}
              />
            ))}
          </div>

          <div className="flex items-center justify-between gap-3 border-t pt-3 text-xs text-muted-foreground">
            <span>Обновлено {fmtAge(data.computed_at)}</span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => mutate()}
              disabled={isValidating}
            >
              {isValidating && (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              )}
              Обновить
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <Card>
      <CardContent className="py-3">
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
        <div className="text-xs text-muted-foreground">{label}</div>
      </CardContent>
    </Card>
  );
}

function PlanColumn({
  stage,
  items,
  busyId,
  onSetStatus,
  onReVerify,
}: {
  stage: GrowthPlanStage;
  items: GrowthPlanItem[];
  busyId: string | null;
  onSetStatus: (
    item: GrowthPlanItem,
    status: AdviceCardWorkflowStatus,
  ) => Promise<void>;
  onReVerify: (item: GrowthPlanItem) => Promise<void>;
}) {
  return (
    <section className="min-w-0 space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold">
            {STAGE_LABEL[stage]}{" "}
            <span className="text-muted-foreground font-normal">
              ({items.length})
            </span>
          </h2>
          <p className="text-xs text-muted-foreground">
            {STAGE_HINT[stage]}
          </p>
        </div>
      </div>
      {items.length === 0 ? (
        <div className="rounded-md border border-dashed px-3 py-6 text-sm text-muted-foreground">
          Пусто
        </div>
      ) : (
        <div className="space-y-2">
          {items.map((item) => (
            <PlanItemCard
              key={`${item.kind}:${item.id}`}
              item={item}
              busy={busyId === item.id}
              onSetStatus={onSetStatus}
              onReVerify={onReVerify}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function PlanItemCard({
  item,
  busy,
  onSetStatus,
  onReVerify,
}: {
  item: GrowthPlanItem;
  busy: boolean;
  onSetStatus: (
    item: GrowthPlanItem,
    status: AdviceCardWorkflowStatus,
  ) => Promise<void>;
  onReVerify: (item: GrowthPlanItem) => Promise<void>;
}) {
  const outcome = item.outcome;
  const measured = outcome?.followup_at !== null && outcome?.followup_at !== undefined;
  return (
    <Card className={cn(item.stage === "awaiting_followup" && "border-dashed")}>
      <CardContent className="space-y-3 py-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant="outline" className="text-[10px]">
            {SEVERITY_LABEL[item.severity] ?? item.severity}
          </Badge>
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            {item.source_module}
          </span>
        </div>

        <div className="space-y-1">
          <h3 className="text-sm font-semibold leading-snug">
            {item.title_ru}
          </h3>
          <p className="text-xs text-muted-foreground leading-snug line-clamp-3">
            {item.body_ru}
          </p>
        </div>

        {item.target_ru && (
          <div className="text-xs text-muted-foreground break-words">
            <span className="font-medium text-foreground/80">Где:</span>{" "}
            {item.target_ru}
          </div>
        )}

        {item.evidence_ru.length > 0 && (
          <ul className="space-y-1 text-xs text-muted-foreground">
            {item.evidence_ru.slice(0, 3).map((line, index) => (
              <li key={`${item.id}:ev:${index}`} className="break-words">
                {line}
              </li>
            ))}
          </ul>
        )}

        {outcome && (
          <OutcomeStrip outcome={outcome} measured={Boolean(measured)} />
        )}

        {/*
          Technical verification pill — shows whether the change is
          actually live on the page (separate from the 14-day SEO
          outcome above). Only renders for advice items with an
          applied state + verification record.
        */}
        {item.kind === "advice" && item.state && (
          <VerificationPill
            state={item.state}
            busy={busy}
            onReVerify={() => onReVerify(item)}
          />
        )}

        {item.expected_impact_ru && (
          <div className="text-xs text-emerald-700">
            Ожидаемый эффект: {item.expected_impact_ru}
          </div>
        )}

        <div className="flex items-center gap-1.5 flex-wrap">
          {item.kind === "advice" && item.stage === "found" && (
            <WorkflowButton
              busy={busy}
              icon="work"
              label="В работу"
              onClick={() => onSetStatus(item, "in_progress")}
            />
          )}
          {item.kind === "advice" && item.stage === "in_progress" && (
            <>
              <WorkflowButton
                busy={busy}
                icon="done"
                label="Применил"
                onClick={() => onSetStatus(item, "applied")}
              />
              <WorkflowButton
                busy={busy}
                icon="back"
                label="Вернуть"
                onClick={() => onSetStatus(item, "pending")}
              />
            </>
          )}
          {item.kind === "advice" && item.stage === "found" && (
            <>
              <WorkflowButton
                busy={busy}
                icon="later"
                label="Отложить"
                onClick={() => onSetStatus(item, "snoozed")}
              />
              <WorkflowButton
                busy={busy}
                icon="hide"
                label="Скрыть"
                onClick={() => onSetStatus(item, "dismissed")}
              />
            </>
          )}
          {item.kind === "advice" && ["snoozed", "dismissed"].includes(item.stage) && (
            <WorkflowButton
              busy={busy}
              icon="back"
              label="Вернуть"
              onClick={() => onSetStatus(item, "pending")}
            />
          )}
          {item.link && item.cta_ru && (
            <OpenLink href={item.link} label={item.cta_ru} />
          )}
          {item.kind === "outcome" && (
            <Button
              variant="outline"
              size="xs"
              nativeButton={false}
              render={<Link href="/studio/outcomes" />}
            >
              До / После
              <ArrowRight className="h-3 w-3" aria-hidden />
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function WorkflowButton({
  busy,
  icon,
  label,
  onClick,
}: {
  busy: boolean;
  icon: "work" | "done" | "later" | "hide" | "back";
  label: string;
  onClick: () => void;
}) {
  const Icon =
    icon === "done"
      ? CheckCircle2
      : icon === "later"
        ? Clock3
        : icon === "hide"
          ? EyeOff
          : icon === "back"
            ? RotateCcw
            : Timer;
  return (
    <Button
      type="button"
      variant={icon === "done" || icon === "work" ? "secondary" : "ghost"}
      size="xs"
      onClick={onClick}
      disabled={busy}
    >
      {busy ? (
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
      ) : (
        <Icon className="h-3 w-3" aria-hidden />
      )}
      {label}
    </Button>
  );
}

function OpenLink({ href, label }: { href: string; label: string }) {
  const external = /^https?:\/\//i.test(href);
  if (external) {
    return (
      <Button
        variant="outline"
        size="xs"
        nativeButton={false}
        render={<a href={href} target="_blank" rel="noreferrer" />}
      >
        {label}
        <ExternalLink className="h-3 w-3" aria-hidden />
      </Button>
    );
  }
  return (
    <Button
      variant="outline"
      size="xs"
      nativeButton={false}
      render={<Link href={href} />}
    >
      {label}
      <ArrowRight className="h-3 w-3" aria-hidden />
    </Button>
  );
}

function OutcomeStrip({
  outcome,
  measured,
}: {
  outcome: NonNullable<GrowthPlanItem["outcome"]>;
  measured: boolean;
}) {
  if (!measured) {
    const days = outcome.days_until_followup;
    return (
      <div className="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground">
        <Clock3 className="mr-1 inline h-3.5 w-3.5" aria-hidden />
        Замер через {days} {pluralRu(days, ["день", "дня", "дней"])}
      </div>
    );
  }

  const delta = outcome.delta || {};
  return (
    <div className="grid grid-cols-3 gap-2 text-xs">
      <Delta label="показы" value={asNumber(delta.impressions_pct)} suffix="%" />
      <Delta label="клики" value={asNumber(delta.clicks_pct)} suffix="%" />
      <Delta label="позиция" value={asNumber(delta.position_delta)} />
    </div>
  );
}

function Delta({
  label,
  value,
  suffix = "",
}: {
  label: string;
  value: number | null;
  suffix?: string;
}) {
  const positive = value != null && value > 0;
  const negative = value != null && value < 0;
  return (
    <div className="rounded-md border px-2 py-1.5">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div
        className={cn(
          "font-semibold tabular-nums",
          positive && "text-emerald-700",
          negative && "text-red-700",
        )}
      >
        {value == null
          ? "нет"
          : `${value > 0 ? "+" : ""}${value.toFixed(1)}${suffix}`}
      </div>
    </div>
  );
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
