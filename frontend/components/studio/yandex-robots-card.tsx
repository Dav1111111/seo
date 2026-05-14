"use client";

/**
 * Yandex robots.txt audit card.
 *
 * Backend contract:
 *   POST /admin/sites/{site_id}/robots-audit   → run + return result
 *   GET  /admin/sites/{site_id}/robots-audit   → last cached result or 404
 *
 * Owner question this card answers: «правильно ли мой robots.txt
 * подсказывает YandexBot что можно индексировать?». A broken
 * robots.txt sits upstream of every other indexation problem, so
 * the card is mounted at the top of /studio/indexation.
 */

import { useState } from "react";
import useSWR from "swr";

import {
  getRobotsAudit,
  runRobotsAudit,
  type RobotsAuditResult,
  type RobotsIssue,
} from "@/lib/api";
import { studioKey } from "@/lib/studio-keys";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

type Props = {
  siteId: string | number;
};

const SEVERITY_LABEL: Record<RobotsIssue["severity"], string> = {
  critical: "критично",
  warning: "предупреждение",
  info: "к сведению",
};

const SEVERITY_BADGE: Record<
  RobotsIssue["severity"],
  "destructive" | "secondary" | "outline"
> = {
  critical: "destructive",
  warning: "secondary",
  info: "outline",
};

function countBySeverity(issues: RobotsIssue[]) {
  let critical = 0;
  let warning = 0;
  let info = 0;
  for (const it of issues) {
    if (it.severity === "critical") critical += 1;
    else if (it.severity === "warning") warning += 1;
    else if (it.severity === "info") info += 1;
  }
  return { critical, warning, info };
}

function statusOf(
  data: RobotsAuditResult,
): { tone: "ok" | "warn" | "bad"; icon: string; text: string } {
  const { critical, warning } = countBySeverity(data.issues);
  if (!data.valid_for_yandex || critical > 0) {
    return {
      tone: "bad",
      icon: "❌",
      text:
        critical > 0
          ? `Критические проблемы: ${critical}`
          : "Критические проблемы",
    };
  }
  if (warning > 0) {
    return {
      tone: "warn",
      icon: "⚠️",
      text: `Есть предупреждения: ${warning}`,
    };
  }
  return { tone: "ok", icon: "✅", text: "robots.txt в порядке" };
}

const TONE_WRAP: Record<"ok" | "warn" | "bad", string> = {
  ok: "border-emerald-300 bg-emerald-50 text-emerald-900",
  warn: "border-amber-300 bg-amber-50 text-amber-900",
  bad: "border-red-300 bg-red-50 text-red-900",
};

function IssueRow({ issue }: { issue: RobotsIssue }) {
  return (
    <li className="rounded-md border bg-card px-3 py-2 space-y-1.5">
      <div className="flex items-baseline gap-2 flex-wrap">
        <Badge variant={SEVERITY_BADGE[issue.severity]}>
          {SEVERITY_LABEL[issue.severity]}
        </Badge>
        <span className="font-medium text-sm">{issue.message_ru}</span>
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground ml-auto">
          {issue.code}
        </span>
      </div>
      {issue.evidence && (
        <pre className="text-xs font-mono bg-muted/50 rounded px-2 py-1 overflow-x-auto whitespace-pre-wrap break-all">
          {issue.evidence}
        </pre>
      )}
      {issue.fix_ru && (
        <p className="text-xs leading-snug text-muted-foreground">
          <span className="font-medium text-foreground">Что сделать: </span>
          {issue.fix_ru}
        </p>
      )}
    </li>
  );
}

function CollapsibleList({
  label,
  items,
}: {
  label: string;
  items: string[];
}) {
  const [open, setOpen] = useState(items.length <= 5);
  if (items.length === 0) return null;
  const visible = open ? items : items.slice(0, 3);
  return (
    <div className="space-y-1">
      <div className="flex items-baseline gap-2">
        <span className="text-xs font-medium">{label}</span>
        <span className="text-[10px] text-muted-foreground">
          ({items.length})
        </span>
        {items.length > 3 && (
          <button
            type="button"
            className="text-[10px] underline text-muted-foreground hover:text-foreground ml-auto"
            onClick={() => setOpen((v) => !v)}
          >
            {open ? "свернуть" : "показать все"}
          </button>
        )}
      </div>
      <ul className="text-xs font-mono space-y-0.5">
        {visible.map((it, i) => (
          <li key={`${it}-${i}`} className="truncate text-muted-foreground">
            {it}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function YandexRobotsCard({ siteId }: Props) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data, isLoading, mutate } = useSWR<RobotsAuditResult | null>(
    siteId ? studioKey(String(siteId), "robots-audit") : null,
    () => getRobotsAudit(siteId),
    {
      // The audit is owner-triggered; refresh on focus only.
      revalidateOnFocus: true,
    },
  );

  async function onRun() {
    if (!siteId || pending) return;
    setPending(true);
    setError(null);

    // Optimistic rollback pattern (CLAUDE.md frontend rule 4):
    // keep the previous result in memory so we can put it back if
    // the network call fails.
    const previous = data;

    try {
      const fresh = await runRobotsAudit(siteId);
      await mutate(fresh, { revalidate: false });
    } catch (e: unknown) {
      // Revert SWR cache to the previous value so the UI doesn't
      // get stuck on a half-applied state.
      await mutate(previous, { revalidate: false });
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  }

  // ── Loading ──────────────────────────────────────────────────────
  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Проверка robots.txt для Яндекса</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-24 w-full" />
        </CardContent>
      </Card>
    );
  }

  // ── Never ran ────────────────────────────────────────────────────
  if (!data) {
    return (
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3">
          <div>
            <CardTitle>Проверка robots.txt для Яндекса</CardTitle>
            <p className="text-xs text-muted-foreground mt-1">
              Аудит ещё не запускался. Проверяем синтаксис, директивы
              для YandexBot, Sitemap, Clean-param и Host.
            </p>
          </div>
          <Button onClick={onRun} disabled={pending} size="sm">
            {pending ? "Проверяем…" : "Проверить сейчас"}
          </Button>
        </CardHeader>
        {error && (
          <CardContent>
            <div className="text-sm text-red-700 border border-red-300 bg-red-50 rounded px-3 py-2">
              {error}
            </div>
          </CardContent>
        )}
      </Card>
    );
  }

  // ── Has data ─────────────────────────────────────────────────────
  const status = statusOf(data);
  const counts = countBySeverity(data.issues);

  // Group issues by severity, critical first (UI must surface the
  // worst findings at the top of the list).
  const ordered: RobotsIssue[] = [
    ...data.issues.filter((i) => i.severity === "critical"),
    ...data.issues.filter((i) => i.severity === "warning"),
    ...data.issues.filter((i) => i.severity === "info"),
  ];

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="min-w-0">
          <CardTitle>Проверка robots.txt для Яндекса</CardTitle>
          <div
            className={`rounded-md border px-3 py-2 mt-2 inline-flex items-center gap-2 text-sm ${TONE_WRAP[status.tone]}`}
          >
            <span aria-hidden>{status.icon}</span>
            <span className="font-medium">{status.text}</span>
          </div>
          {data.summary_ru && (
            <p className="text-sm text-muted-foreground mt-2 leading-snug">
              {data.summary_ru}
            </p>
          )}
        </div>
        <Button
          onClick={onRun}
          disabled={pending}
          variant="outline"
          size="sm"
        >
          {pending ? "Проверяем…" : "Обновить"}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && (
          <div className="text-sm text-red-700 border border-red-300 bg-red-50 rounded px-3 py-2">
            {error}
          </div>
        )}

        {/* Counts strip */}
        <div className="flex items-center gap-3 text-xs text-muted-foreground flex-wrap">
          <span>
            URL: <span className="font-mono">{data.robots_url || "—"}</span>
          </span>
          {data.http_status !== null && (
            <span>HTTP {data.http_status}</span>
          )}
          <span>{data.size_bytes} байт</span>
          <span>
            проблем: {counts.critical} крит, {counts.warning} пред,{" "}
            {counts.info} инфо
          </span>
        </div>

        {/* Issues list — critical first */}
        {ordered.length > 0 && (
          <ul className="space-y-2">
            {ordered.map((it, i) => (
              <IssueRow key={`${it.code}-${i}`} issue={it} />
            ))}
          </ul>
        )}

        {/* Recommendations (free-form Russian strings from audit) */}
        {data.recommendations_ru.length > 0 && (
          <div className="space-y-1">
            <div className="text-xs font-medium">Рекомендации</div>
            <ul className="text-sm space-y-1 list-disc pl-5">
              {data.recommendations_ru.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Sitemaps + Clean-param — short lists collapsed when long */}
        <div className="grid sm:grid-cols-2 gap-4">
          <CollapsibleList label="Sitemap" items={data.sitemaps} />
          <CollapsibleList label="Clean-param" items={data.clean_params} />
        </div>
      </CardContent>
    </Card>
  );
}
