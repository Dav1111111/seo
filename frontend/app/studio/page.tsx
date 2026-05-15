/**
 * Studio home — unified advice feed.
 *
 * Replaces the legacy module-grid dashboard with a single ranked
 * stream of «советы» served by the backend advisor module
 * (`GET /admin/studio/sites/{id}/advice`). Cards are produced by every
 * upstream module (brain rules, robots audit, schema audit, keyword
 * gaps, technical failures, funnel coverage) and surfaced here in one
 * sorted feed — no more per-module dashboards on this page.
 *
 * The page is a thin server-component wrapper. The actual feed (SWR,
 * filter state, refresh) lives in `<AdviceFeedShell />` which is a
 * client component that wires the current site from `useSite()`.
 * That mirrors the existing `BrainPlanCard` / `KeywordGapsCard`
 * pattern — the deep-dive sub-pages under `/studio/*` are unchanged.
 */

import { AdviceFeedShell } from "@/components/studio/advice-feed-shell";

export default function StudioHome() {
  return (
    <div className="container mx-auto px-4 py-8 max-w-4xl">
      <header className="mb-8">
        <h1 className="text-3xl font-bold">Что делать</h1>
        <p className="text-muted-foreground mt-2 max-w-2xl">
          Все советы и срочные задачи в одном месте, отсортированы по
          важности. Сверху — то, на что стоит обратить внимание прямо
          сейчас.
        </p>
      </header>

      <AdviceFeedShell />
    </div>
  );
}
