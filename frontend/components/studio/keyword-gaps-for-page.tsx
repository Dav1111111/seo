"use client";

/**
 * Per-page keyword gap section — rendered inside the deep-extract
 * panel BELOW the AI advisor block.
 *
 * Shows top-3 missing-keyword opportunities for THIS page with
 * editable «Новый title» / «Новый H1» fields and an «Применить»
 * button. Apply creates a `PageReviewRecommendation` on the
 * backend — the owner then sees it on /studio/pages/[page_id].
 *
 * Inputs default to EMPTY. The LLM's suggested title arrives via the
 * AI advisor's `ai_summary_md` (rendered above this section); the
 * owner copy-pastes/edits into the inputs here.
 */

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import {
  Target,
  Loader2,
  CheckCircle2,
  Snowflake,
  Sparkles,
  AlertCircle,
} from "lucide-react";

import {
  getPageKeywordGaps,
  applyKeywordPlacement,
  type KeywordGapDetail,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { cn, getErrorMessage } from "@/lib/utils";

export function KeywordGapsForPage({ pageId }: { pageId: string | null }) {
  // Competitor extracts have no `pageId` — nothing to show.
  if (!pageId) return null;
  return <KeywordGapsForPageInner pageId={pageId} />;
}

function KeywordGapsForPageInner({ pageId }: { pageId: string }) {
  const { data, error, isLoading } = useSWR(
    ["page-kw-gaps", pageId],
    () => getPageKeywordGaps(pageId),
    { refreshInterval: 0 },
  );

  const [showAll, setShowAll] = useState(false);

  if (isLoading) {
    return (
      <div className="rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground flex items-center gap-2">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        Считаем пробелы в title/H1 этой страницы…
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-md border border-red-300/40 bg-red-50 px-3 py-2 text-xs text-red-900">
        Не удалось загрузить keyword-gaps: {getErrorMessage(error)}
      </div>
    );
  }

  // 404 — analysis has never run for the whole site. (Also handles
  // the brief SWR `undefined` state during transitions — same CTA.)
  if (data == null) {
    return (
      <div className="rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">
        Анализ ключевых слов ещё не запускался для этого сайта.{" "}
        <Link href="/studio" className="text-primary hover:underline cursor-pointer">
          Запустить на дашборде
        </Link>
        .
      </div>
    );
  }

  if (data.gaps.length === 0) {
    return (
      <div className="rounded-md border border-emerald-200 bg-emerald-50/50 px-3 py-2 text-xs text-emerald-900 flex items-center gap-2">
        <CheckCircle2 className="h-3.5 w-3.5" />
        По этой странице все ключевые слова в title и H1 на месте.
      </div>
    );
  }

  const visible = showAll ? data.gaps : data.gaps.slice(0, 3);
  const hiddenCount = data.gaps.length - visible.length;

  return (
    <div className="rounded-md border bg-card p-3 space-y-3">
      <div className="flex items-start gap-2">
        <Target className="h-4 w-4 text-primary mt-0.5" />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium">
            Ключевые слова для добавления ({data.gaps.length})
          </div>
          <p className="text-xs text-muted-foreground mt-0.5 max-w-xl">
            Запросы из Wordstat, у которых не хватает ключевых лемм
            в title/H1 этой страницы. Добавишь их — выше шанс выйти
            в топ-5 и собрать клики.
          </p>
        </div>
      </div>

      <div className="space-y-3">
        {visible.map((gap) => (
          <GapCard key={gap.query_id} gap={gap} pageId={pageId} />
        ))}
      </div>

      {hiddenCount > 0 && !showAll && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="text-xs text-primary hover:underline cursor-pointer"
        >
          Показать ещё {hiddenCount}
        </button>
      )}
      {showAll && data.gaps.length > 3 && (
        <button
          type="button"
          onClick={() => setShowAll(false)}
          className="text-xs text-muted-foreground hover:text-foreground cursor-pointer"
        >
          Свернуть
        </button>
      )}
    </div>
  );
}

function GapCard({
  gap,
  pageId,
}: {
  gap: KeywordGapDetail;
  pageId: string;
}) {
  const [newTitle, setNewTitle] = useState("");
  const [newH1, setNewH1] = useState("");
  const [applying, setApplying] = useState(false);
  const [applyErr, setApplyErr] = useState<string | null>(null);
  const [applied, setApplied] = useState<{
    rec_id: string;
    priority: "high" | "medium";
  } | null>(null);

  const positionLabel =
    gap.current_position == null
      ? "нет в выдаче"
      : `поз. ${gap.current_position}`;

  async function onApply() {
    setApplyErr(null);
    const titleTrimmed = newTitle.trim();
    const h1Trimmed = newH1.trim();
    if (!titleTrimmed && !h1Trimmed) {
      setApplyErr("Введи хотя бы один из вариантов — title или H1.");
      return;
    }
    setApplying(true);
    try {
      const res = await applyKeywordPlacement({
        page_id: pageId,
        query_id: gap.query_id,
        new_title: titleTrimmed || undefined,
        new_h1: h1Trimmed || undefined,
      });
      setApplied({ rec_id: res.recommendation_id, priority: res.priority });
    } catch (e) {
      setApplyErr(getErrorMessage(e));
    } finally {
      setApplying(false);
    }
  }

  return (
    <div className="rounded-md border bg-background p-3 space-y-2">
      {/* Heading row */}
      <div className="flex items-start gap-2 flex-wrap">
        <div className="flex-1 min-w-0">
          <div className="text-sm leading-snug">
            <span className="font-medium">«{gap.query}»</span>
          </div>
          <div className="text-xs text-muted-foreground mt-0.5 tabular-nums">
            Wordstat: {gap.wordstat_volume.toLocaleString("ru-RU")}/мес
            {gap.wordstat_volume_peak_3mo != null
              && gap.wordstat_volume_peak_3mo > gap.wordstat_volume && (
              <>
                {" "}
                · пик за 3 мес: {gap.wordstat_volume_peak_3mo.toLocaleString("ru-RU")}
              </>
            )}{" "}
            · {positionLabel}
          </div>
          <div className="text-xs text-foreground/80 mt-1">
            <span className="font-medium">Прогноз:</span> +
            {Math.round(gap.expected_clicks_uplift)} кликов/мес если выйти в
            топ-5.
          </div>
        </div>
        <div className="flex items-center gap-1.5 flex-wrap">
          {gap.is_off_season && (
            <span
              className="inline-flex items-center gap-1 rounded-full bg-muted text-muted-foreground border px-2 py-0.5 text-[10px]"
              title="Сейчас межсезонье — спрос ниже годового пика"
            >
              <Snowflake className="h-3 w-3" />
              межсезонье
            </span>
          )}
        </div>
      </div>

      {/* Missing lemmas — by slot */}
      <MissingLemmasRow label="В title нет" lemmas={gap.missing_in_title_lemmas} />
      <MissingLemmasRow label="В H1 нет" lemmas={gap.missing_in_h1_lemmas} />
      {gap.missing_in_h2_lemmas.length > 0 && (
        <MissingLemmasRow label="В H2 нет" lemmas={gap.missing_in_h2_lemmas} muted />
      )}
      {gap.missing_in_first_para_lemmas.length > 0 && (
        <MissingLemmasRow
          label="В первом абзаце нет"
          lemmas={gap.missing_in_first_para_lemmas}
          muted
        />
      )}

      {/* Anti-stuffing nudge — backend says synonym is already present */}
      {gap.has_synonym_in_title && (
        <div className="flex items-start gap-2 rounded-md border bg-muted/40 px-2.5 py-1.5 text-[11px] text-muted-foreground">
          <Sparkles className="h-3 w-3 mt-0.5 flex-shrink-0" />
          <span>
            У тебя в title уже есть похожие по смыслу слова — Яндекс может
            понять. Эта рекомендация необязательна.
          </span>
        </div>
      )}

      {applied ? (
        <div className="rounded-md border border-emerald-200 bg-emerald-50/60 px-3 py-2 text-xs text-emerald-900 space-y-1">
          <div className="flex items-center gap-1.5">
            <CheckCircle2 className="h-3.5 w-3.5" />
            <span>
              Рекомендация создана (приоритет: {applied.priority === "high" ? "высокий" : "средний"}).
            </span>
          </div>
          <div>
            Открой{" "}
            <Link
              href={`/studio/pages/${pageId}`}
              className="font-medium text-emerald-900 underline hover:no-underline cursor-pointer"
            >
              workspace страницы
            </Link>{" "}
            — найдёшь её в списке правок.
          </div>
        </div>
      ) : (
        <>
          {/* Editable inputs */}
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="block">
              <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
                Новый title
              </span>
              <input
                type="text"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder="Скопируй из AI-резюме выше и поправь под бренд"
                disabled={applying}
                className="mt-1 w-full rounded-md border bg-background px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 disabled:opacity-60"
              />
            </label>
            <label className="block">
              <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
                Новый H1
              </span>
              <input
                type="text"
                value={newH1}
                onChange={(e) => setNewH1(e.target.value)}
                placeholder="Можно оставить пустым, если правишь только title"
                disabled={applying}
                className="mt-1 w-full rounded-md border bg-background px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 disabled:opacity-60"
              />
            </label>
          </div>

          {applyErr && (
            <div className="flex items-start gap-1.5 rounded-md border border-red-300/40 bg-red-50 px-2.5 py-1.5 text-xs text-red-900">
              <AlertCircle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
              <span>{applyErr}</span>
            </div>
          )}

          <div className="flex items-center justify-end">
            <button
              type="button"
              onClick={onApply}
              disabled={applying}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-60 cursor-pointer"
            >
              {applying ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Target className="h-3.5 w-3.5" />
              )}
              {applying ? "Применяю…" : "Применить"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function MissingLemmasRow({
  label,
  lemmas,
  muted,
}: {
  label: string;
  lemmas: string[];
  muted?: boolean;
}) {
  if (lemmas.length === 0) return null;
  return (
    <div
      className={cn(
        "text-xs flex items-baseline gap-2 flex-wrap",
        muted && "text-muted-foreground",
      )}
    >
      <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}:
      </span>
      <div className="flex flex-wrap gap-1">
        {lemmas.map((lemma) => (
          <Badge key={lemma} variant="outline" className="font-mono text-[10px]">
            {lemma}
          </Badge>
        ))}
      </div>
    </div>
  );
}
