"use client";

/**
 * VerificationPill — shows the result of the immediate technical
 * re-verification that runs after the owner clicks «Применил».
 *
 * Distinct from the 14-day SEO outcome (which lives on the outcome
 * snapshot): this pill answers «did the change actually land on the
 * page?», not «did rankings improve?».
 *
 * Renders ONLY when:
 *   - state.status === "applied"  (verification has no meaning for
 *                                  pending/dismissed/snoozed cards)
 *   - state.verification_status   (a verification record exists)
 *
 * Five visual branches:
 *   pending          spinner + «Проверяю изменения…»  (muted blue)
 *   verified         check    + «✅ Факт подтверждён»  (emerald)
 *   not_yet_visible  warn     + «⚠ Применил, но не видно» (amber, re-check)
 *   user_attested    user     + «🤝 Принято на слово»  (slate)
 *   failed           x        + «❌ Проверка сломалась»  (red, re-check)
 *
 * Pure presentational; re-verify trigger is delegated to the parent
 * via `onReVerify`. Parent owns busy-state across PATCH and re-verify.
 */

import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  RotateCcw,
  UserCheck,
  XCircle,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { fmtAge } from "@/lib/format";
import type {
  AdviceCardState,
  AdviceCardVerificationStatus,
} from "@/lib/api";

type Branch = {
  icon: LucideIcon;
  label: string;
  tone: string; // text + bg + border classes
  spinIcon?: boolean;
  showReVerify?: boolean;
};

const BRANCHES: Record<AdviceCardVerificationStatus, Branch> = {
  pending: {
    icon: Loader2,
    label: "Проверяю изменения…",
    tone: "text-sky-700 dark:text-sky-300 bg-sky-50 dark:bg-sky-950/40 border-sky-200 dark:border-sky-900/60",
    spinIcon: true,
  },
  verified: {
    icon: CheckCircle2,
    label: "Факт подтверждён на странице",
    tone: "text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-950/40 border-emerald-200 dark:border-emerald-900/60",
  },
  not_yet_visible: {
    icon: AlertCircle,
    label: "Применил, но изменений ещё не видно",
    tone: "text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/40 border-amber-200 dark:border-amber-900/60",
    showReVerify: true,
  },
  user_attested: {
    icon: UserCheck,
    label: "Принято на слово (нельзя проверить автоматически)",
    tone: "text-slate-600 dark:text-slate-300 bg-slate-50 dark:bg-slate-900/40 border-slate-200 dark:border-slate-800",
  },
  failed: {
    icon: XCircle,
    label: "Проверка сломалась — попробуй ещё раз",
    tone: "text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/40 border-red-200 dark:border-red-900/60",
    showReVerify: true,
  },
};

// Best-effort tooltip from `verification_evidence`. Backend shape varies
// per check-type (title-change vs. schema vs. meta) so we walk the
// top-level keys and format «key: value» pairs. Tries to surface the
// common «actual vs. expected» wording first when those keys exist.
function evidenceTooltip(
  evidence: Record<string, unknown> | null | undefined,
): string | undefined {
  if (!evidence || typeof evidence !== "object") return undefined;
  const lines: string[] = [];

  const actual = pickOne(evidence, ["actual", "fact_now", "current", "found"]);
  const expected = pickOne(evidence, ["expected", "wanted", "target"]);
  if (actual !== undefined) {
    lines.push(`Факт сейчас: ${stringifyShort(actual)}`);
  }
  if (expected !== undefined) {
    lines.push(`Ожидалось: ${stringifyShort(expected)}`);
  }

  if (lines.length === 0) {
    // Fallback — plain key: value list, capped at 4 entries to fit
    // the native title tooltip.
    for (const [key, value] of Object.entries(evidence).slice(0, 4)) {
      lines.push(`${key}: ${stringifyShort(value)}`);
    }
  }
  return lines.length > 0 ? lines.join("\n") : undefined;
}

function pickOne(
  obj: Record<string, unknown>,
  keys: string[],
): unknown | undefined {
  for (const k of keys) {
    if (k in obj) return obj[k];
  }
  return undefined;
}

function stringifyShort(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") {
    return value.length > 120 ? `${value.slice(0, 117)}…` : value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    const s = JSON.stringify(value);
    return s.length > 120 ? `${s.slice(0, 117)}…` : s;
  } catch {
    return String(value);
  }
}

export function VerificationPill({
  state,
  onReVerify,
  busy,
}: {
  state: AdviceCardState;
  onReVerify?: () => void | Promise<void>;
  busy?: boolean;
}) {
  // Render gate: only for applied cards that have a verification record.
  if (state.status !== "applied") return null;
  const status = state.verification_status;
  if (!status) return null;

  const branch = BRANCHES[status];
  if (!branch) return null;
  const Icon = branch.icon;
  const tooltip = evidenceTooltip(state.verification_evidence);

  const verifiedAtAge =
    status === "verified" && state.verified_at
      ? `Проверено ${fmtAge(state.verified_at)}`
      : undefined;

  return (
    <div className="flex items-center gap-2 flex-wrap pt-1">
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium",
          branch.tone,
        )}
        title={tooltip ?? verifiedAtAge}
      >
        <Icon
          className={cn(
            "h-3.5 w-3.5",
            branch.spinIcon && "animate-spin",
          )}
          aria-hidden
        />
        <span>{branch.label}</span>
      </span>
      {branch.showReVerify && onReVerify && (
        <Button
          type="button"
          variant="ghost"
          size="xs"
          onClick={() => onReVerify()}
          disabled={busy || status === "pending"}
          title="Запустить проверку ещё раз"
        >
          {busy ? (
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          ) : (
            <RotateCcw className="h-3 w-3" aria-hidden />
          )}
          Проверить снова
        </Button>
      )}
    </div>
  );
}
