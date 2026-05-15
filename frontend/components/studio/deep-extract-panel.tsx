"use client";

import { useState } from "react";
import useSWR from "swr";
import {
  Microscope,
  Loader2,
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Info,
} from "lucide-react";

import {
  api,
  type DeepExtractRow,
  type SchemaAudit,
  type SchemaAuditIssue,
} from "@/lib/api";
import { KeywordGapsForPage } from "@/components/studio/keyword-gaps-for-page";

/**
 * Deep Extract panel — Playwright-rendered snapshot for a URL.
 *
 * Two modes (auto-detected by props):
 *   - mode="own"          → siteId + pageId, button triggers own-page extract
 *   - mode="competitor"   → siteId + url prop, button triggers competitor extract
 *
 * Renders the latest extraction below the trigger: title/H1 from real
 * render, performance metrics, screenshot, CTA inventory, palette,
 * forms, JS errors, Schema. Re-extract by clicking «Обновить».
 */

interface OwnPageProps {
  mode: "own";
  siteId: string;
  pageId: string;
}

interface CompetitorProps {
  mode: "competitor";
  siteId: string;
  url: string;
}

type Props = OwnPageProps | CompetitorProps;

type FormSnapshot = {
  field_count?: number;
  above_fold?: boolean;
  fields?: Array<{ type?: string }>;
};

type JsErrorSnapshot = {
  kind?: string;
  message?: string;
};

export function DeepExtractPanel(props: Props) {
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const swrKey =
    props.mode === "own"
      ? ["deep-extract-own", props.siteId, props.pageId]
      : ["deep-extract-competitor-list", props.siteId];

  const { data, mutate } = useSWR(
    swrKey,
    async () => {
      if (props.mode === "own") {
        return api.studioGetDeepExtractForPage(props.siteId, props.pageId);
      }
      const list = await api.studioListCompetitorDeepExtracts(props.siteId);
      return list.items.find((it) => it.url === props.url) || null;
    },
    { refreshInterval: running ? 4000 : 0 },
  );

  async function onRun() {
    setRunning(true);
    setErr(null);
    try {
      if (props.mode === "own") {
        await api.studioTriggerDeepExtractOwnPage(props.siteId, props.pageId);
      } else {
        await api.studioTriggerDeepExtractCompetitor(props.siteId, props.url);
      }
      // Polling kicks in via refreshInterval — wait until status flips
      // from old extract to a fresh one. We give up running state when
      // the extracted_at advances or after 90s safety timeout.
      const before = data?.extracted_at;
      const start = Date.now();
      const poll = setInterval(async () => {
        const next = await mutate();
        const fresh = next?.extracted_at;
        if (fresh && fresh !== before) {
          clearInterval(poll);
          setRunning(false);
        }
        if (Date.now() - start > 90_000) {
          clearInterval(poll);
          setRunning(false);
          setErr("Слежу больше 90 секунд — посмотри Activity, возможно упало.");
        }
      }, 4000);
    } catch (e) {
      setRunning(false);
      setErr(e instanceof Error ? e.message : "не получилось запустить");
    }
  }

  const extract: DeepExtractRow | null = (data as DeepExtractRow | null) || null;

  return (
    <div className="rounded-lg border bg-card p-4 space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h3 className="font-medium flex items-center gap-2">
            <Microscope className="h-4 w-4 text-primary" />
            Глубокий разбор страницы
          </h3>
          <p className="text-xs text-muted-foreground mt-1 max-w-xl">
            Открываем страницу в браузере с JS и сохраняем снимок: title/H1,
            CTA, формы, Schema, цвета, шрифты, JS-ошибки, лабораторные
            LCP/FCP/CLS и скриншоты. Можно запустить заново — кнопкой «Обновить».
          </p>
        </div>
        <button
          type="button"
          onClick={onRun}
          disabled={running}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-60"
        >
          {running ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Microscope className="h-4 w-4" />
          )}
          {running ? "Идёт…" : extract ? "Обновить" : "Запустить"}
        </button>
      </div>

      {err && (
        <div className="flex items-start gap-2 rounded-md border border-red-300/40 bg-red-50 px-3 py-2 text-xs text-red-900 dark:bg-red-950/40 dark:text-red-200">
          <AlertCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
          <span>{err}</span>
        </div>
      )}

      {!extract && !running && (
        <p className="text-xs text-muted-foreground italic">
          Пока не запускали. Нажми «Запустить» чтобы получить браузерный снимок.
        </p>
      )}

      {extract && (
        <DeepExtractView
          extract={extract}
          siteId={props.siteId}
          pageId={props.mode === "own" ? props.pageId : null}
        />
      )}
    </div>
  );
}

function DeepExtractView({
  extract,
  siteId,
  pageId,
}: {
  extract: DeepExtractRow;
  siteId: string;
  // null for competitor extracts — keyword-gaps section skips render.
  pageId: string | null;
}) {
  const failed = extract.status !== "completed";
  const [analyzing, setAnalyzing] = useState(false);
  const [summary, setSummary] = useState<string | null>(
    extract.ai_summary_md || null,
  );
  // Track when this summary was generated so we can flag stale AI
  // resumes vs the underlying snapshot. Updated on successful
  // /analyze. Stays in sync with `extract.ai_summary_at` from the
  // server for cached/idle renders.
  const [summaryAt, setSummaryAt] = useState<string | null>(
    extract.ai_summary_at || null,
  );
  const [analyzeErr, setAnalyzeErr] = useState<string | null>(null);

  async function onAnalyze() {
    if (analyzing) return;
    setAnalyzing(true);
    setAnalyzeErr(null);
    try {
      const res = await api.studioAnalyzeDeepExtract(siteId, extract.id, Boolean(summary));
      setSummary(res.summary_md);
      if (res.ai_summary_at) {
        setSummaryAt(res.ai_summary_at);
      } else if (res.model && res.model !== "cached") {
        setSummaryAt(new Date().toISOString());
      }
    } catch (e) {
      setAnalyzeErr(e instanceof Error ? e.message : "не удалось разобрать");
    } finally {
      setAnalyzing(false);
    }
  }

  // ── AI summary freshness vs snapshot ─────────────────────────────
  // Compare the summary's generation moment with `extracted_at`. If
  // the snapshot was refreshed after the summary, the summary is
  // describing a stale page — nudge the owner to re-generate.
  const summaryFreshness = (():
    | { kind: "synced"; label: string }
    | { kind: "stale"; label: string }
    | { kind: "unknown"; label: string }
    | null => {
    if (!summary) return null;
    if (!summaryAt) {
      return {
        kind: "unknown",
        label:
          "AI-резюме сгенерировано давно — точное время неизвестно. Если снимок недавно обновлялся, кликни «Перезапросить».",
      };
    }
    const summaryTs = new Date(summaryAt).getTime();
    const snapTs = new Date(extract.extracted_at).getTime();
    if (!Number.isFinite(summaryTs) || !Number.isFinite(snapTs)) {
      return { kind: "unknown", label: "Свежесть AI-резюме не определена." };
    }
    // ≤60s gap counts as "same moment" — accounts for the gap
    // between snapshot insert and the summary LLM call.
    if (Math.abs(snapTs - summaryTs) <= 60_000) {
      return { kind: "synced", label: "AI-резюме совпадает со снимком" };
    }
    if (snapTs > summaryTs) {
      const ageDays = Math.max(
        1,
        Math.round((snapTs - summaryTs) / (1000 * 60 * 60 * 24)),
      );
      // Russian plural forms for "день/дня/дней" — keeps the label
      // grammatical at 1, 2-4, 5+ days.
      const mod10 = ageDays % 10;
      const mod100 = ageDays % 100;
      let dayWord: string;
      if (mod10 === 1 && mod100 !== 11) dayWord = "день";
      else if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20))
        dayWord = "дня";
      else dayWord = "дней";
      return {
        kind: "stale",
        label: `AI-резюме сделано ${ageDays} ${dayWord} назад, снимок обновлён позже — кликни «Перезапросить».`,
      };
    }
    // Summary newer than snapshot — odd but harmless; just say synced.
    return { kind: "synced", label: "AI-резюме совпадает со снимком" };
  })();
  const ctas = extract.cta_inventory || [];
  const aboveFoldCtas = ctas.filter((c) => c.above_fold);
  const palette = extract.css_palette || [];
  const fonts = extract.fonts || [];
  const perf = extract.performance || {};
  const layout = extract.layout_meta || {};
  const forms = (extract.forms_inventory || []) as FormSnapshot[];
  const errors = (extract.js_errors || []) as JsErrorSnapshot[];
  const schemaAudit: SchemaAudit | null = extract.schema_audit ?? null;

  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
        {failed ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-red-100 dark:bg-red-950/40 text-red-900 dark:text-red-200 px-2 py-0.5">
            <AlertCircle className="h-3 w-3" />
            {extract.status}: {extract.error || "?"}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 dark:bg-emerald-950/40 text-emerald-900 dark:text-emerald-200 px-2 py-0.5">
            <CheckCircle2 className="h-3 w-3" />
            готово, {extract.duration_ms ?? "?"} мс
          </span>
        )}
        <span>{new Date(extract.extracted_at).toLocaleString("ru-RU")}</span>
        {extract.is_competitor && extract.competitor_domain && (
          <span className="font-medium text-foreground">
            {extract.competitor_domain}
          </span>
        )}
      </div>

      {failed ? null : (
        <>
          {/* Hero: title + H1 + meta */}
          <div className="space-y-1">
            <div className="text-xs uppercase tracking-wide text-muted-foreground">
              Что показывает Яндексу
            </div>
            <div>
              <span className="text-xs text-muted-foreground">title:</span>{" "}
              <span className="font-medium">{extract.title || "—"}</span>{" "}
              <span className="text-xs text-muted-foreground">
                ({(extract.title || "").length})
              </span>
            </div>
            <div>
              <span className="text-xs text-muted-foreground">H1:</span>{" "}
              <span className="font-medium">{extract.h1 || "—"}</span>
            </div>
            <div>
              <span className="text-xs text-muted-foreground">meta:</span>{" "}
              <span>{extract.meta_description || "— нет"}</span>
            </div>
          </div>

          {/* Screenshots */}
          <div className="grid gap-3 sm:grid-cols-2">
            {extract.has_screenshot_desktop && (
              <ScreenshotBox
                title="Десктоп 1280×800"
                src={api.studioDeepExtractScreenshotUrl(siteId, extract.id, "desktop")}
              />
            )}
            {extract.has_screenshot_mobile && (
              <ScreenshotBox
                title="Мобильный 375×800"
                src={api.studioDeepExtractScreenshotUrl(siteId, extract.id, "mobile")}
              />
            )}
          </div>

          {/* Performance */}
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground mb-1">
              Скорость (лабораторный рендер)
            </div>
            <div className="flex flex-wrap gap-3 text-xs">
              <PerfChip label="LCP" value={perf.lcp} unit="ms" target={2500} />
              <PerfChip label="FCP" value={perf.fcp} unit="ms" target={1800} />
              <PerfChip label="CLS" value={perf.cls} unit="" target={0.1} />
              <PerfChip
                label="высота страницы"
                value={layout.doc_height}
                unit="px"
                target={null}
              />
              <PerfChip
                label="JS-ошибок"
                value={errors.length}
                unit=""
                target={0}
              />
            </div>
          </div>

          {/* CTA inventory */}
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground mb-1">
              Кнопки на странице ({ctas.length}, выше fold: {aboveFoldCtas.length})
            </div>
            {ctas.length === 0 ? (
              <p className="text-xs text-red-700 dark:text-red-400">
                CTA-кнопок не найдено — проверь, не сделаны ли они обычными
                ссылками или виджетами.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-muted-foreground">
                      <th className="pr-2">текст</th>
                      <th className="pr-2">цвет</th>
                      <th className="pr-2">фон</th>
                      <th className="pr-2">размер</th>
                      <th className="pr-2">позиция</th>
                      <th>fold</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ctas.slice(0, 12).map((c, i) => (
                      <tr key={i} className="border-t">
                        <td className="pr-2 py-1">{c.text}</td>
                        <td className="pr-2">
                          <ColorChip value={c.color} />
                        </td>
                        <td className="pr-2">
                          <ColorChip value={c.bg_color} />
                        </td>
                        <td className="pr-2">
                          {c.width}×{c.height}
                        </td>
                        <td className="pr-2">y={c.top}</td>
                        <td>{c.above_fold ? "✓" : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {ctas.length > 12 && (
                  <p className="text-xs text-muted-foreground mt-1">
                    …ещё {ctas.length - 12}
                  </p>
                )}
              </div>
            )}
          </div>

          {/* Palette + fonts */}
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground mb-1">
                Цветовая палитра
              </div>
              <div className="flex flex-wrap gap-1">
                {palette.slice(0, 10).map((p, i) => (
                  <ColorChip key={i} value={p.color} count={p.count} />
                ))}
              </div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground mb-1">
                Шрифты
              </div>
              <ul className="text-xs space-y-0.5">
                {fonts.slice(0, 6).map((f, i) => (
                  <li key={i}>
                    {f.family} <span className="text-muted-foreground">×{f.count}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          {/* Forms */}
          {forms.length > 0 && (
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground mb-1">
                Формы ({forms.length})
              </div>
              <ul className="text-xs space-y-1">
                {forms.map((f, i) => (
                  <li key={i}>
                    {f.field_count} поле(й){" "}
                    {f.above_fold && (
                      <span className="text-muted-foreground">(выше fold)</span>
                    )}{" "}
                    — {(f.fields || []).map((x) => x.type).join(", ")}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Schema.org audit */}
          <SchemaAuditView audit={schemaAudit} />

          {/* JS errors */}
          {errors.length > 0 && (
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground mb-1">
                JS-ошибки ({errors.length})
              </div>
              <ul className="text-xs space-y-0.5 text-red-700 dark:text-red-400">
                {errors.slice(0, 8).map((e, i) => (
                  <li key={i}>
                    [{e.kind}] {e.message?.slice(0, 200)}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* AI рекомендации — превращаем сырые данные в план правок */}
          <div className="rounded-md border bg-muted/30 p-3 space-y-2">
            <div className="flex items-start justify-between gap-3 flex-wrap">
              <div>
                <div className="text-sm font-medium">📋 Что с этим делать</div>
                <p className="text-xs text-muted-foreground mt-0.5 max-w-xl">
                  AI прочитает данные снимка (CTA, цвета, лабораторную скорость,
                  формы, Schema) и даст конкретный план: что мешает росту
                  к топ-5 и какие правки делать сначала.
                </p>
              </div>
              <button
                type="button"
                onClick={onAnalyze}
                disabled={analyzing}
                className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-60"
              >
                {analyzing ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Microscope className="h-3.5 w-3.5" />
                )}
                {analyzing
                  ? "AI думает…"
                  : summary
                  ? "Перезапросить"
                  : "Получить рекомендации"}
              </button>
            </div>

            {analyzeErr && (
              <p className="text-xs text-red-700 dark:text-red-400">{analyzeErr}</p>
            )}

            {summary && summaryFreshness && (
              <div
                className={
                  summaryFreshness.kind === "stale"
                    ? "rounded-md border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
                    : summaryFreshness.kind === "synced"
                    ? "rounded-md border border-emerald-200 bg-emerald-50/60 px-3 py-1.5 text-xs text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200"
                    : "rounded-md border border-muted bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground"
                }
              >
                {summaryFreshness.label}
              </div>
            )}

            {summary ? (
              <div className="rounded-md border bg-card p-3 prose prose-sm prose-neutral dark:prose-invert max-w-none">
                {/* Простой Markdown без библиотеки — сохраняем структуру строк */}
                {summary.split("\n").map((line, i) => {
                  if (line.startsWith("## ")) {
                    return (
                      <h4 key={i} className="font-semibold mt-3 mb-1 text-sm">
                        {line.slice(3)}
                      </h4>
                    );
                  }
                  if (line.startsWith("### ")) {
                    return (
                      <h5 key={i} className="font-medium mt-2 mb-1 text-sm">
                        {line.slice(4)}
                      </h5>
                    );
                  }
                  if (line.startsWith("- ") || line.startsWith("* ")) {
                    return (
                      <div key={i} className="ml-4 text-xs leading-relaxed">
                        • {renderInlineBold(line.slice(2))}
                      </div>
                    );
                  }
                  if (/^\d+\. /.test(line)) {
                    return (
                      <div key={i} className="ml-4 text-xs leading-relaxed">
                        {renderInlineBold(line)}
                      </div>
                    );
                  }
                  if (!line.trim()) return <div key={i} className="h-1" />;
                  return (
                    <p key={i} className="text-xs leading-relaxed my-1">
                      {renderInlineBold(line)}
                    </p>
                  );
                })}
              </div>
            ) : (
              !analyzing && (
                <p className="text-xs text-muted-foreground italic">
                  Жми «Получить рекомендации» — AI прочитает снимок и выдаст
                  план. ~10-20 секунд, ~$0.05 на запрос.
                </p>
              )
            )}
          </div>

          {/* keyword_match — per-page gaps with «Применить» buttons.
              Sits below the AI advisor: the LLM's suggested title is
              already in `ai_summary_md` above, the owner copies it
              into the inputs here. Competitor extracts pass pageId
              null and the section auto-hides. */}
          <KeywordGapsForPage pageId={pageId} />
        </>
      )}
    </div>
  );
}

// Inline **bold** rendering without a heavy markdown library.
function renderInlineBold(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) =>
    p.startsWith("**") && p.endsWith("**") ? (
      <strong key={i}>{p.slice(2, -2)}</strong>
    ) : (
      <span key={i}>{p}</span>
    ),
  );
}

// ── Schema.org audit view ───────────────────────────────────────────
// Renders a structured audit (detected types, issues grouped by
// severity, fix recommendations) coming from the backend. Honest copy:
// no absolute claims about rich snippets, no codes shown to the owner.

const SEVERITY_ORDER: Array<SchemaAuditIssue["severity"]> = [
  "critical",
  "warning",
  "info",
];

const SEVERITY_LABEL: Record<SchemaAuditIssue["severity"], string> = {
  critical: "Критично",
  warning: "Предупреждения",
  info: "К сведению",
};

const SEVERITY_CARD: Record<SchemaAuditIssue["severity"], string> = {
  critical:
    "border border-red-300/40 bg-red-50 dark:bg-red-950/30 text-red-900 dark:text-red-200",
  warning:
    "border border-amber-300/40 bg-amber-50 dark:bg-amber-950/30 text-amber-900 dark:text-amber-200",
  info: "border border-slate-200/40 bg-slate-50 dark:bg-slate-900/40 text-slate-900 dark:text-slate-200",
};

function SeverityIcon({ severity }: { severity: SchemaAuditIssue["severity"] }) {
  if (severity === "critical") {
    return <AlertCircle className="h-4 w-4 text-red-700 dark:text-red-300 flex-shrink-0 mt-0.5" />;
  }
  if (severity === "warning") {
    return <AlertTriangle className="h-4 w-4 text-amber-700 dark:text-amber-300 flex-shrink-0 mt-0.5" />;
  }
  return <Info className="h-4 w-4 text-slate-600 dark:text-slate-300 flex-shrink-0 mt-0.5" />;
}

function SchemaAuditView({ audit }: { audit: SchemaAudit | null }) {
  // Empty / not-detected → muted info card, no overclaims.
  const isEmpty =
    !audit ||
    (audit.valid_blocks_count === 0 &&
      audit.parse_error_count === 0 &&
      audit.detected_types.length === 0 &&
      audit.issues.length === 0 &&
      audit.recommendations.length === 0);

  if (isEmpty) {
    return (
      <div>
        <div className="text-xs uppercase tracking-wide text-muted-foreground mb-1">
          Schema.org разметка
        </div>
        <div className="flex items-start gap-2 rounded-md border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          <Info className="h-4 w-4 flex-shrink-0 mt-0.5" />
          <span>
            Schema.org разметка не найдена. Это не критично — поисковик
            попытается распарсить страницу сам, но расширенный сниппет
            менее вероятен.
          </span>
        </div>
      </div>
    );
  }

  // audit is non-null here.
  const a = audit as SchemaAudit;

  // Group issues by severity, preserving SEVERITY_ORDER.
  const issuesBySeverity = SEVERITY_ORDER.map((sev) => ({
    severity: sev,
    items: a.issues.filter((i) => i.severity === sev),
  })).filter((g) => g.items.length > 0);

  return (
    <div className="space-y-3">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">
        Schema.org разметка
      </div>

      {/* Detected types + formats + counts */}
      {a.detected_types.length > 0 && (
        <div className="space-y-1">
          <div className="flex flex-wrap gap-1.5">
            {a.detected_types.map((t) => (
              <span
                key={t}
                className="inline-flex items-center gap-1 rounded-full bg-emerald-100 dark:bg-emerald-950/40 text-emerald-900 dark:text-emerald-200 px-2 py-0.5 text-xs"
              >
                <CheckCircle2 className="h-3 w-3" />
                {t}
              </span>
            ))}
          </div>
          {a.formats.length > 0 && (
            <div className="text-xs text-muted-foreground">
              Форматы: {a.formats.join(", ")}
            </div>
          )}
          {a.valid_blocks_count > 0 && (
            <div className="text-xs text-muted-foreground">
              Найдено {a.valid_blocks_count}{" "}
              {pluralBlocks(a.valid_blocks_count)}
            </div>
          )}
          {a.parse_error_count > 0 && (
            <div className="text-xs text-red-700 dark:text-red-400">
              Ошибок парсинга: {a.parse_error_count}
            </div>
          )}
        </div>
      )}

      {/* If there are no detected types but we still have a parse-error
          count > 0 — surface that on its own so the owner sees the
          breakage without a confusing empty "Detected" row. */}
      {a.detected_types.length === 0 && a.parse_error_count > 0 && (
        <div className="text-xs text-red-700 dark:text-red-400">
          Ошибок парсинга: {a.parse_error_count}
        </div>
      )}

      {/* Summary line */}
      {a.summary_ru && (
        <p className="text-xs text-muted-foreground">{a.summary_ru}</p>
      )}

      {/* Issues grouped by severity */}
      {issuesBySeverity.length > 0 && (
        <div className="space-y-3">
          {issuesBySeverity.map((group) => (
            <div key={group.severity} className="space-y-1.5">
              <div className="text-xs font-medium text-muted-foreground">
                {SEVERITY_LABEL[group.severity]} ({group.items.length})
              </div>
              <div className="space-y-1.5">
                {group.items.map((issue, idx) => (
                  <div
                    key={`${issue.code}-${idx}`}
                    className={`flex items-start gap-2 rounded-md px-3 py-2 text-xs ${SEVERITY_CARD[group.severity]}`}
                  >
                    <SeverityIcon severity={group.severity} />
                    <div className="space-y-1 min-w-0">
                      <div className="font-medium leading-snug">
                        {issue.message_ru}
                      </div>
                      {issue.evidence && (
                        <div className="opacity-80">
                          <span className="font-medium">Что вижу: </span>
                          <span className="font-mono break-all">
                            {issue.evidence}
                          </span>
                        </div>
                      )}
                      {issue.fix_ru && (
                        <div className="opacity-90">
                          <span className="font-medium">Что починить: </span>
                          {issue.fix_ru}
                        </div>
                      )}
                      <div className="opacity-60 text-[10px] uppercase tracking-wide">
                        Источник: {issue.source}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Recommendations digest */}
      {a.recommendations.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-xs font-medium text-muted-foreground">
            Что сделать в первую очередь
          </div>
          <ul className="text-xs space-y-1 list-disc pl-5">
            {a.recommendations.map((r, i) => (
              <li key={i} className="leading-snug">
                {r}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function pluralBlocks(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "блок";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return "блока";
  return "блоков";
}

function ScreenshotBox({ title, src }: { title: string; src: string }) {
  return (
    <div className="space-y-1">
      <div className="text-xs text-muted-foreground">{title}</div>
      <a href={src} target="_blank" rel="noopener noreferrer">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt={title}
          className="w-full rounded border hover:opacity-90 transition-opacity"
        />
      </a>
    </div>
  );
}

function PerfChip({
  label,
  value,
  unit,
  target,
}: {
  label: string;
  value: number | null | undefined;
  unit: string;
  target: number | null;
}) {
  const v = typeof value === "number" ? value : null;
  let kind: "ok" | "warn" | "neutral" = "neutral";
  if (v != null && target != null) {
    kind = v <= target ? "ok" : "warn";
  }
  const color =
    kind === "ok"
      ? "bg-emerald-100 text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200"
      : kind === "warn"
      ? "bg-amber-100 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
      : "bg-muted text-muted-foreground";
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 ${color}`}>
      <span>{label}:</span>
      <span className="font-medium">
        {v != null ? `${typeof v === "number" ? v.toFixed?.(2) ?? v : v}${unit}` : "—"}
      </span>
    </span>
  );
}

function ColorChip({ value, count }: { value: string; count?: number }) {
  return (
    <span
      title={value + (count ? ` · ${count}` : "")}
      className="inline-flex items-center gap-1 text-[10px] border rounded px-1.5 py-0.5"
    >
      <span
        className="inline-block h-3 w-3 rounded-sm border"
        style={{ background: value }}
      />
      <span className="font-mono">{value.replace(/\s+/g, "")}</span>
      {count && <span className="text-muted-foreground">×{count}</span>}
    </span>
  );
}
