"use client";

/**
 * Focus proposal dialog — Studio v2 etap 7 Phase E step 2.
 *
 * When the LLM picks the propose_strategic_focus tool in /studio/chat,
 * the API response carries a `proposal` payload. This component
 * renders that payload as a modal with three buttons:
 *   - «Применить» → POST to /strategic-focus/from-proposal, save.
 *   - «Изменить» → close modal, deep-link to /studio/profile editor.
 *     Fields preselected via URL hash so owner can tweak before save.
 *   - «Отмена» → close, write nothing.
 *
 * Hard guarantee: nothing reaches DB until the owner clicks
 * «Применить» here or saves manually on /studio/profile. The LLM
 * never writes — even after the tool call, this dialog is the gate.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";
import { mutate as swrMutate } from "swr";
import { Target, Check, Pencil, X, Loader2, Quote } from "lucide-react";

import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn, getErrorMessage } from "@/lib/utils";


export type FocusProposal = {
  label: string;
  products: string[];
  regions: string[];
  query_signals: string[];
  deprioritised: string[];
  exit_criterion: string | null;
  owner_note: string | null;
  deadline: string | null;
  rationale: string;
};


export function FocusProposalDialog({
  siteId,
  proposal,
  onClose,
  onApplied,
}: {
  siteId: string;
  proposal: FocusProposal;
  onClose: () => void;
  onApplied: () => void;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function apply() {
    setBusy(true);
    setErr(null);
    try {
      await api.studioApplyStrategicFocusProposal(siteId, {
        label: proposal.label,
        products: proposal.products,
        regions: proposal.regions,
        query_signals: proposal.query_signals,
        deprioritised: proposal.deprioritised,
        exit_criterion: proposal.exit_criterion,
        owner_note: proposal.owner_note,
        deadline: proposal.deadline,
      });
      // Bust caches: focus banner, plan (for in_focus re-rank), profile.
      await swrMutate(
        (key: unknown) =>
          Array.isArray(key) &&
          (key as string[])[0]?.startsWith("studio:"),
        undefined,
        { revalidate: true },
      );
      onApplied();
      onClose();
    } catch (e: unknown) {
      setErr(getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  function tweak() {
    // Owner wants to edit before applying — go to the profile editor.
    // The proposal is dropped; if owner needs the suggested values,
    // they're still visible in the chat history.
    router.push("/studio/profile");
    onClose();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-full max-w-lg rounded-lg bg-card border shadow-lg overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="border-b px-5 py-3 flex items-center gap-2 bg-primary/5">
          <Target className="h-5 w-5 text-primary" />
          <div className="text-sm font-medium">
            Помощник предлагает установить фокус
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Закрыть"
            className="ml-auto text-muted-foreground hover:text-foreground cursor-pointer"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-3 max-h-[70vh] overflow-y-auto">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
              Главное
            </div>
            <div className="text-lg font-medium">{proposal.label}</div>
          </div>

          {proposal.rationale && (
            <div className="rounded-md border bg-muted/30 px-3 py-2 text-sm flex items-start gap-2">
              <Quote className="h-3.5 w-3.5 mt-0.5 flex-shrink-0 text-muted-foreground" />
              <div className="flex-1">
                <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">
                  Помощник объясняет
                </div>
                <p className="leading-snug italic">{proposal.rationale}</p>
              </div>
            </div>
          )}

          <ChipRow label="Продукты" items={proposal.products} />
          <ChipRow label="Регионы" items={proposal.regions} />
          <ChipRow label="Ключевые запросы" items={proposal.query_signals} />
          {proposal.deprioritised.length > 0 && (
            <ChipRow
              label="Отложено"
              items={proposal.deprioritised}
              tone="muted"
            />
          )}

          {proposal.exit_criterion && (
            <div className="text-sm">
              <span className="text-xs text-muted-foreground">
                Условие выхода:
              </span>{" "}
              {proposal.exit_criterion}
            </div>
          )}
          {proposal.deadline && (
            <div className="text-sm">
              <span className="text-xs text-muted-foreground">Дедлайн:</span>{" "}
              {proposal.deadline}
            </div>
          )}
          {proposal.owner_note && (
            <div className="rounded-md border bg-background px-3 py-2 text-sm">
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">
                Заметка
              </div>
              {proposal.owner_note}
            </div>
          )}

          {err && (
            <div className="rounded-md border border-red-300 bg-red-50 text-red-900 px-3 py-2 text-sm">
              Не получилось применить: {err}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="border-t px-5 py-3 flex items-center justify-end gap-2 bg-muted/20">
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            disabled={busy}
            className="cursor-pointer"
          >
            Отмена
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={tweak}
            disabled={busy}
            className="cursor-pointer"
            title="Перейти в редактор профиля и поправить вручную"
          >
            <Pencil className="h-3.5 w-3.5 mr-1.5" />
            Изменить руками
          </Button>
          <Button
            size="sm"
            onClick={apply}
            disabled={busy || !proposal.label.trim()}
            className="cursor-pointer"
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
            ) : (
              <Check className="h-3.5 w-3.5 mr-1.5" />
            )}
            {busy ? "Применяю…" : "Применить"}
          </Button>
        </div>
      </div>
    </div>
  );
}


function ChipRow({
  label,
  items,
  tone = "default",
}: {
  label: string;
  items: string[];
  tone?: "default" | "muted";
}) {
  if (items.length === 0) return null;
  return (
    <div className="flex items-baseline gap-2 flex-wrap">
      <span className="text-xs text-muted-foreground">{label}:</span>
      {items.map((it, i) => (
        <Badge
          key={i}
          variant="outline"
          className={cn(
            "text-xs",
            tone === "muted" &&
              "border-muted-foreground/20 text-muted-foreground line-through",
          )}
        >
          {it}
        </Badge>
      ))}
    </div>
  );
}
