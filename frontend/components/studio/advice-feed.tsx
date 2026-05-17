"use client";

/**
 * Unified advice feed on /studio.
 *
 * Replaces the legacy «one card per module» dashboard with a single
 * ranked stream of `AdviceCard` rows, served by
 * `GET /admin/studio/sites/{id}/advice`. Backend already sorts by
 * `sort_score DESC`; this component only filters by a severity-bucket
 * and renders.
 *
 * Owns:
 *   - SWR fetch of the feed (stable key via `studioKey()`)
 *   - filter-bucket state (5 chips: «Все / Срочно / Важно / Подумать / Инфо»)
 *   - summary line, refresh button, relative «обновлено …» indicator
 *
 * Does NOT own:
 *   - card design — see `advice-card.tsx`
 *   - empty state — see `advice-empty-state.tsx`
 *   - skeleton  — see `advice-skeleton.tsx`
 *
 * The feed is read-only (no per-card mutations) so we skip the
 * optimistic-update pattern; the only mutation is "re-run the full
 * pipeline" which lives behind the empty-state CTA and a footer
 * «Обновить» button.
 */

import { useState, useMemo } from "react";
import useSWR from "swr";
import { RefreshCw, Loader2, AlertCircle, EyeOff } from "lucide-react";

import {
  getAdviceFeed,
  patchAdviceCardState,
  reVerifyAdviceCard,
  type AdviceCard,
  type AdviceFeed,
  type AdviceCardWorkflowStatus,
  type AdviceSeverity,
} from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn, getErrorMessage } from "@/lib/utils";
import { fmtAge, pluralRu } from "@/lib/format";

import { AdviceCardRow } from "./advice-card";
import { AdviceEmptyState } from "./advice-empty-state";
import { AdviceSkeleton } from "./advice-skeleton";

// ── Filter buckets ──────────────────────────────────────────────────
// «Срочно» fuses critical+high (an owner doesn't care which is which —
// both mean "fix this fast"). The rest map 1:1 to severity values.
type FilterKey = "all" | "urgent" | "important" | "consider" | "info";

const FILTER_ORDER: FilterKey[] = [
  "all",
  "urgent",
  "important",
  "consider",
  "info",
];

const FILTER_LABEL: Record<FilterKey, string> = {
  all: "Все",
  urgent: "Срочно",
  important: "Важно",
  consider: "Подумать",
  info: "Инфо",
};

function severityToBucket(s: AdviceSeverity): FilterKey {
  if (s === "critical" || s === "high") return "urgent";
  if (s === "medium") return "important";
  if (s === "low") return "consider";
  return "info";
}

function matchesFilter(severity: AdviceSeverity, filter: FilterKey): boolean {
  if (filter === "all") return true;
  return severityToBucket(severity) === filter;
}

// Used by SWR's `refreshInterval` to decide whether to keep polling.
// We poll only while at least one card is mid-verification — the
// instant every verification has resolved we drop back to 0 and stop
// hammering the backend.
function hasPendingVerification(feed: AdviceFeed | null | undefined): boolean {
  if (!feed) return false;
  for (const card of feed.cards) {
    if (card.state.verification_status === "pending") return true;
  }
  return false;
}

// Optimistic-update helper: returns a *new* AdviceFeed with the named
// card's `verification_status` flipped to "pending". Used when the
// owner clicks «Применил» or «Проверить снова» so the spinner pill
// appears instantly. SWR's 5s refresh reconciles to the real status.
function applyOptimisticVerification(
  feed: AdviceFeed,
  cardId: string,
): AdviceFeed {
  return {
    ...feed,
    cards: feed.cards.map((card) =>
      card.id === cardId
        ? {
            ...card,
            state: {
              ...card.state,
              verification_status: "pending",
            },
          }
        : card,
    ),
  };
}

// ── Component ───────────────────────────────────────────────────────

export function AdviceFeed({ siteId }: { siteId: string }) {
  const swrKey = siteId ? studioKey("advice", siteId) : null;
  const { data, error, isLoading, mutate, isValidating } = useSWR<AdviceFeed | null>(
    swrKey,
    () => getAdviceFeed(siteId),
    {
      // Poll every 5s while any card is mid-verification — once the
      // Celery verify task finishes (5-60s typically) the resolved
      // status appears without owner action. Goes silent (0) the
      // moment no card is pending. SWR accepts a function form here,
      // which lets us re-evaluate on every cycle without re-mounting.
      refreshInterval: (latest) => hasPendingVerification(latest) ? 5000 : 0,
    },
  );
  const archiveKey = siteId ? studioKey("advice", siteId, "archive") : null;
  const {
    data: archiveData,
    mutate: mutateArchive,
    isValidating: isArchiveValidating,
  } = useSWR<AdviceFeed | null>(
    archiveKey,
    () => getAdviceFeed(siteId, { includeHidden: true }),
    {
      refreshInterval: (latest) => hasPendingVerification(latest) ? 5000 : 0,
    },
  );

  const [filter, setFilter] = useState<FilterKey>("all");
  const [busyCardId, setBusyCardId] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [showArchive, setShowArchive] = useState(false);

  // Per-bucket counts — used for both chip badges and the summary line.
  // Recomputed only when `data` changes.
  const bucketCounts = useMemo<Record<FilterKey, number>>(() => {
    const counts: Record<FilterKey, number> = {
      all: 0,
      urgent: 0,
      important: 0,
      consider: 0,
      info: 0,
    };
    if (!data) return counts;
    for (const card of data.cards) {
      counts.all += 1;
      counts[severityToBucket(card.severity)] += 1;
    }
    return counts;
  }, [data]);

  const filteredCards = useMemo(() => {
    if (!data) return [];
    if (filter === "all") return data.cards;
    return data.cards.filter((c) => matchesFilter(c.severity, filter));
  }, [data, filter]);

  const archiveCards = useMemo(() => {
    if (!archiveData) return [];
    return archiveData.cards.filter((c) =>
      c.state.status === "applied"
      || c.state.status === "dismissed"
      || c.state.status === "snoozed"
    );
  }, [archiveData]);

  // ── Loading ───────────────────────────────────────────────────────
  if (isLoading) return <AdviceSkeleton />;

  // ── Error ─────────────────────────────────────────────────────────
  if (error) {
    return (
      <Card className="border-red-300 bg-red-50/50 dark:bg-red-950/30 dark:border-red-900/50">
        <CardContent className="space-y-3 pt-2">
          <div className="flex items-start gap-2 text-sm text-red-900 dark:text-red-200">
            <AlertCircle className="h-4 w-4 flex-shrink-0 mt-0.5" aria-hidden />
            <div className="space-y-1">
              <p className="font-medium">Не удалось загрузить советы</p>
              <p className="text-xs text-red-900/80 dark:text-red-200/80">
                {getErrorMessage(error)}
              </p>
            </div>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => mutate()}
            disabled={isValidating}
          >
            {isValidating ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            )}
            Попробовать снова
          </Button>
        </CardContent>
      </Card>
    );
  }

  // ── Empty: never computed (backend 404) OR computed but no cards ──
  if (data && data.cards.length === 0 && !archiveData && isArchiveValidating) {
    return <AdviceSkeleton />;
  }

  if (!data || (data.cards.length === 0 && archiveData && archiveCards.length === 0)) {
    return (
      <AdviceEmptyState
        siteId={siteId}
        onRefresh={async () => {
          await mutate();
        }}
      />
    );
  }

  async function handleCardStateChange(
    card: AdviceCard,
    status: AdviceCardWorkflowStatus,
  ) {
    setBusyCardId(card.id);
    setMutationError(null);
    // When the owner marks a card «Применил», backend kicks off the
    // async verify task immediately. Show «Проверяю…» the same tick —
    // SWR will pick up the resolved status on the next 5s refresh.
    const previous = data;
    if (status === "applied" && data) {
      mutate(applyOptimisticVerification(data, card.id), { revalidate: false });
    }
    try {
      await patchAdviceCardState(siteId, card.id, {
        status,
        snooze_days: status === "snoozed" ? 7 : null,
      });
      await mutate();
      await mutateArchive();
    } catch (e: unknown) {
      // Roll back the optimistic patch on failure so the pill doesn't
      // get stuck pretending we triggered verification we didn't.
      if (status === "applied" && previous) {
        mutate(previous, { revalidate: false });
      }
      setMutationError(getErrorMessage(e));
    } finally {
      setBusyCardId(null);
    }
  }

  async function handleReVerify(card: AdviceCard) {
    setBusyCardId(card.id);
    setMutationError(null);
    const previous = data;
    // Optimistic flip to «pending» so the spinner appears the instant
    // the owner clicks — even before the backend has acknowledged the
    // queued task. SWR refresh (5s while pending) will reconcile.
    if (data) {
      mutate(applyOptimisticVerification(data, card.id), { revalidate: false });
    }
    try {
      await reVerifyAdviceCard(siteId, card.id);
      await mutate();
    } catch (e: unknown) {
      if (previous) mutate(previous, { revalidate: false });
      setMutationError(getErrorMessage(e));
    } finally {
      setBusyCardId(null);
    }
  }

  // ── Default render ────────────────────────────────────────────────
  return (
    <div className="space-y-4">
      <SummaryLine
        total={bucketCounts.all}
        urgent={bucketCounts.urgent}
        important={bucketCounts.important}
        consider={bucketCounts.consider}
      />

      <FilterBar
        filter={filter}
        onChange={setFilter}
        counts={bucketCounts}
      />

      <div className="flex items-center justify-between gap-3 flex-wrap">
        <Button
          type="button"
          variant={showArchive ? "secondary" : "outline"}
          size="sm"
          onClick={() => setShowArchive((v) => !v)}
          disabled={isArchiveValidating && !archiveData}
        >
          {isArchiveValidating && !archiveData ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <EyeOff className="h-3.5 w-3.5" aria-hidden />
          )}
          Скрытые и отложенные ({archiveCards.length})
        </Button>
      </div>

      {mutationError && (
        <Card className="border-red-300 bg-red-50/50 dark:bg-red-950/30 dark:border-red-900/50">
          <CardContent className="pt-2 text-sm text-red-900 dark:text-red-200">
            Не удалось сохранить статус: {mutationError}
          </CardContent>
        </Card>
      )}

      {showArchive ? (
        <ArchivedAdviceList
          cards={archiveCards}
          busyCardId={busyCardId}
          onStateChange={handleCardStateChange}
          onReVerify={handleReVerify}
        />
      ) : filteredCards.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="pt-2 text-sm text-muted-foreground">
            В этой категории сейчас ничего нет.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {filteredCards.map((card) => (
            <AdviceCardRow
              key={card.id}
              card={card}
              busy={busyCardId === card.id}
              onStateChange={handleCardStateChange}
              onReVerify={handleReVerify}
            />
          ))}
        </div>
      )}

      <FeedFooter
        computedAt={data.computed_at}
        onRefresh={async () => {
          await mutate();
          await mutateArchive();
        }}
        refreshing={isValidating || isArchiveValidating}
      />
    </div>
  );
}

function ArchivedAdviceList({
  cards,
  busyCardId,
  onStateChange,
  onReVerify,
}: {
  cards: AdviceCard[];
  busyCardId: string | null;
  onStateChange: (
    card: AdviceCard,
    status: AdviceCardWorkflowStatus,
  ) => Promise<void> | void;
  onReVerify: (card: AdviceCard) => Promise<void> | void;
}) {
  if (cards.length === 0) {
    return (
      <Card className="border-dashed">
        <CardContent className="pt-2 text-sm text-muted-foreground">
          Скрытых и отложенных советов сейчас нет.
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="space-y-2">
      {cards.map((card) => (
        <AdviceCardRow
          key={card.id}
          card={card}
          busy={busyCardId === card.id}
          workflowMode="archive"
          onStateChange={onStateChange}
          onReVerify={onReVerify}
        />
      ))}
    </div>
  );
}

// ── Sub-components ──────────────────────────────────────────────────

function SummaryLine({
  total,
  urgent,
  important,
  consider,
}: {
  total: number;
  urgent: number;
  important: number;
  consider: number;
}) {
  // Russian-plural «совет/совета/советов» for the headline number.
  const adviceWord = pluralRu(total, ["совет", "совета", "советов"]);

  // Build parts only for non-zero buckets so the line stays clean.
  const parts: string[] = [];
  if (urgent > 0) {
    parts.push(`${urgent} ${pluralRu(urgent, ["срочный", "срочных", "срочных"])}`);
  }
  if (important > 0) {
    parts.push(`${important} ${pluralRu(important, ["важный", "важных", "важных"])}`);
  }
  if (consider > 0) {
    parts.push(`${consider} ${pluralRu(consider, ["на подумать", "на подумать", "на подумать"])}`);
  }

  return (
    <p className="text-sm text-muted-foreground">
      Найдено {total} {adviceWord}
      {parts.length > 0 && (
        <>
          {": "}
          <span className="text-foreground/85">{parts.join(", ")}</span>
        </>
      )}
      .
    </p>
  );
}

function FilterBar({
  filter,
  onChange,
  counts,
}: {
  filter: FilterKey;
  onChange: (next: FilterKey) => void;
  counts: Record<FilterKey, number>;
}) {
  return (
    <div className="flex items-center gap-2 flex-wrap" role="tablist" aria-label="Фильтр советов">
      {FILTER_ORDER.map((key) => {
        const isActive = filter === key;
        const n = counts[key];
        const isDisabled = key !== "all" && n === 0;
        return (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={isActive}
            disabled={isDisabled}
            onClick={() => onChange(key)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors cursor-pointer",
              isActive
                ? "border-primary/50 bg-primary text-primary-foreground"
                : "border-border bg-card text-foreground hover:bg-muted",
              isDisabled && "opacity-50 cursor-not-allowed hover:bg-card",
            )}
          >
            <span>{FILTER_LABEL[key]}</span>
            <span
              className={cn(
                "tabular-nums",
                isActive ? "text-primary-foreground/85" : "text-muted-foreground",
              )}
            >
              ({n})
            </span>
          </button>
        );
      })}
    </div>
  );
}

function FeedFooter({
  computedAt,
  onRefresh,
  refreshing,
}: {
  computedAt: string;
  onRefresh: () => Promise<unknown> | void;
  refreshing: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3 flex-wrap pt-2 border-t">
      <span className="text-xs text-muted-foreground">
        Обновлено {fmtAge(computedAt)}
      </span>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => onRefresh()}
        disabled={refreshing}
      >
        {refreshing ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : (
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />
        )}
        {refreshing ? "Обновляю…" : "Обновить"}
      </Button>
    </div>
  );
}
