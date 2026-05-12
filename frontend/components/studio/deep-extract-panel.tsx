"use client";

import { useState } from "react";
import useSWR from "swr";
import { Microscope, Loader2, AlertCircle, CheckCircle2 } from "lucide-react";

import { api, type DeepExtractRow } from "@/lib/api";

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
            Открываем страницу как настоящий браузер (с JS), смотрим реальные
            кнопки, цвета, шрифты, форму, скорость загрузки, Schema-разметку.
            Можно запустить заново — кнопкой «Обновить».
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
          Пока не запускали. Нажми «Запустить» чтобы получить полный снимок.
        </p>
      )}

      {extract && <DeepExtractView extract={extract} siteId={props.siteId} />}
    </div>
  );
}

function DeepExtractView({
  extract,
  siteId,
}: {
  extract: DeepExtractRow;
  siteId: string;
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
      const res = await api.studioAnalyzeDeepExtract(siteId, extract.id);
      setSummary(res.summary_md);
      // Cached server-side responses include `model: "cached"`; in
      // that case we don't have a fresh server timestamp here, so
      // we keep whatever we already had. For a real LLM call this is
      // effectively "now" — matches what the server just persisted.
      if (res.model && res.model !== "cached") {
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
  const forms = extract.forms_inventory || [];
  const errors = extract.js_errors || [];
  const schemas = extract.schema_blocks || [];

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
              Скорость (реальный рендер)
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
                Кнопок не найдено — это уже проблема: некуда нажать = нет конверсии.
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
                    — {(f.fields || []).map((x: any) => x.type).join(", ")}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Schema */}
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground mb-1">
              Schema.org JSON-LD ({schemas.length})
            </div>
            {schemas.length === 0 ? (
              <p className="text-xs text-amber-700 dark:text-amber-400">
                Нет Schema-разметки — без неё нет rich-сниппета (звёздочек, цены) в выдаче.
              </p>
            ) : (
              <ul className="text-xs space-y-0.5">
                {schemas.map((s: any, i) => (
                  <li key={i}>
                    {s["@type"] || s.__parse_error ? "ошибка парсинга" : "?"}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* JS errors */}
          {errors.length > 0 && (
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground mb-1">
                JS-ошибки ({errors.length})
              </div>
              <ul className="text-xs space-y-0.5 text-red-700 dark:text-red-400">
                {errors.slice(0, 8).map((e: any, i) => (
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
                  AI прочитает все данные выше (кнопки, цвета, скорость, формы,
                  Schema) и даст конкретный план: что мешает топ-5 и какие
                  правки делать сначала.
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
