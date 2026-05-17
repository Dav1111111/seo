const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api/v1";
const ADMIN_PROXY = "/admin-proxy";  // Next.js server-side proxy — holds the admin key in backend env only

// ── Wordstat status / coverage (Studio · Запросы) ───────────────────
//
// Closed set of states a query can be in regarding Wordstat data:
//   - fresh                 : we have wordstat_volume > 0 and it's recent
//   - stale_30d_plus        : data exists but is older than 30 days
//   - fetch_returned_empty  : Wordstat replied successfully but the
//                             phrase has 0 demand (volume = 0). Legit
//                             "no demand" for niche/brand phrases.
//   - never_fetched         : wordstat_updated_at IS NULL — we haven't
//                             asked Wordstat yet. Cron runs Tuesdays.
//   - invalid_phrase        : updated_at IS NOT NULL but volume IS NULL
//                             — Wordstat rejected the phrase (URL,
//                             garbage). Data-quality flag for owner.
//
// Backend may still emit the legacy "stale_30d+" spelling during the
// rollout — both literals are accepted by the union.
export type WordstatStatus =
  | "fresh"
  | "stale_30d_plus"
  | "stale_30d+"
  | "fetch_returned_empty"
  | "never_fetched"
  | "invalid_phrase";

// Tri-state breakdown returned alongside the queries list. The backend
// agent is mid-rollout: `with_demand` / `no_demand` / `never_fetched` /
// `invalid` are the new shape; legacy `with_volume` / `without_volume`
// / `stale` are kept optional so the UI can fall back during the
// switchover.
export type QueriesCoverage = {
  total: number;
  with_demand?: number;
  no_demand?: number;
  never_fetched?: number;
  invalid?: number;
  // legacy fields — still emitted by current backend
  with_volume?: number;
  without_volume?: number;
  stale?: number;
};

// Structured Schema.org audit returned alongside the raw `schema_blocks`
// in the deep-extract response. Backend builds this from JSON-LD /
// microdata / RDFa parsing + lint rules. Each issue carries an honest
// human Russian message so the UI never has to surface raw codes.
export interface SchemaAuditIssue {
  code: string;
  severity: "critical" | "warning" | "info";
  message_ru: string;
  evidence: string | null;
  fix_ru: string;
  source: "json-ld" | "microdata" | "rdfa" | "dom";
}

export interface SchemaAudit {
  detected_types: string[];
  formats: string[]; // subset of ["json-ld","microdata","rdfa"]
  valid_blocks_count: number;
  parse_error_count: number;
  issues: SchemaAuditIssue[];
  recommendations: string[];
  summary_ru: string;
}

export interface DeepExtractRow {
  id: string;
  url: string;
  is_competitor: boolean;
  competitor_domain: string | null;
  status: string;
  error: string | null;
  extracted_at: string;
  duration_ms: number | null;
  title: string | null;
  h1: string | null;
  meta_description: string | null;
  headings_tree: Array<{ level: number; text: string }> | null;
  cta_inventory: Array<Record<string, any>> | null;
  forms_inventory: Array<Record<string, any>> | null;
  images_inventory: Array<Record<string, any>> | null;
  css_palette: Array<{ color: string; count: number }> | null;
  fonts: Array<{ family: string; count: number }> | null;
  layout_meta: Record<string, any> | null;
  performance: Record<string, any> | null;
  js_errors: Array<Record<string, any>> | null;
  schema_blocks: Array<Record<string, any>> | null;
  // Structured audit over schema_blocks (lint rules + recommendations
  // already filtered & deduped by backend). Null on legacy rows that
  // pre-date the audit pipeline — UI treats null as "not analyzed yet".
  schema_audit?: SchemaAudit | null;
  has_screenshot_desktop: boolean;
  has_screenshot_mobile: boolean;
  // AI summary + when it was generated. `ai_summary_at` is null on
  // legacy rows (set before this freshness field landed) — the panel
  // treats null as "freshness unknown" and nudges a re-analyze.
  ai_summary_md: string | null;
  ai_summary_at: string | null;
}

// One row of the deep-dive comparison — aggregated signals across a
// site's sampled pages (own or competitor). Backend source:
// `CompetitorSiteReport.to_dict()` in
// backend/app/core_audit/competitors/deep_dive.py.
export interface CompetitorDeepDivePage {
  url: string;
  status: string;
  title?: string;
  h1?: string;
  has_price?: boolean;
  has_booking_cta?: boolean;
  has_reviews?: boolean;
  has_phone?: boolean;
  has_telegram?: boolean;
  has_whatsapp?: boolean;
  schema_types?: string[];
  // TODO: type fully once backend response is documented
  [key: string]: unknown;
}

export interface CompetitorDeepDiveSite {
  domain: string;
  pages?: CompetitorDeepDivePage[];
  has_price: boolean;
  has_booking_cta: boolean;
  has_reviews: boolean;
  has_phone: boolean;
  has_telegram: boolean;
  has_whatsapp: boolean;
  schema_types: string[];
  // TODO: type fully once backend response is documented. Index
  // signature preserves the existing `Record<string, unknown>` consumer
  // contract in app/studio/competitors/page.tsx.
  [key: string]: unknown;
}

// Default site ID — in Phase 9 this becomes dynamic
export const SITE_ID = process.env.NEXT_PUBLIC_SITE_ID || "1e11339f-c87e-4742-9d38-6f79463b0d16";

async function apiFetch<T>(
  path: string,
  init?: RequestInit & { base?: "api" | "admin" },
): Promise<T> {
  const { base = "api", ...rest } = init || {};
  const prefix = base === "admin" ? ADMIN_PROXY : API_BASE;
  const res = await fetch(`${prefix}${path}`, {
    headers: {
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "true",
      ...rest.headers,
    },
    ...rest,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

async function apiDownload(
  path: string,
  init?: RequestInit & { base?: "api" | "admin" },
): Promise<Blob> {
  const { base = "api", ...rest } = init || {};
  const prefix = base === "admin" ? ADMIN_PROXY : API_BASE;
  const res = await fetch(`${prefix}${path}`, {
    ...rest,
    headers: {
      "ngrok-skip-browser-warning": "true",
      ...rest.headers,
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.blob();
}

// V2 etap 7 Phase E — strategic focus shape, used by /studio/profile
// editor and the chat «Применить» dialog. Server contract lives in
// backend/app/core_audit/strategic_focus.py.
export type StudioStrategicFocusInput = {
  label: string;
  products: string[];
  regions: string[];
  query_signals: string[];
  deprioritised: string[];
  exit_criterion: string | null;
  owner_note: string | null;
  deadline: string | null;
};

export type StudioStrategicFocus = StudioStrategicFocusInput & {
  active_since: string;
  set_by: "owner_via_ui" | "owner_via_chat";
};

export type ChatMode = "answer" | "discussion" | "battle_plan";

// ── robots.txt audit (Yandex-tuned) ────────────────────────────────
// Backend module: `app.core_audit.yandex_robots` + the `studio.py`
// endpoints `POST/GET /admin/sites/{site_id}/robots-audit`.
// Field shape is frozen by `YandexRobotsAuditResult.to_dict()` —
// any change there must be mirrored here.
export type RobotsIssue = {
  code: string;
  severity: "critical" | "warning" | "info";
  message_ru: string;
  evidence: string;
  fix_ru: string;
};

export type RobotsUrlCheck = {
  url: string;
  path: string;
  user_agent: string;
  allowed: boolean;
  matched_user_agent: string;
  matched_rule: string;
  risk: "ok" | "warning" | "blocked";
  explanation_ru: string;
};

export type RobotsAuditResult = {
  robots_url: string;
  http_status: number | null;
  is_accessible: boolean;
  size_bytes: number;
  valid_for_yandex: boolean;
  matched_groups: string[];
  sitemaps: string[];
  clean_params: string[];
  issues: RobotsIssue[];
  url_checks: RobotsUrlCheck[];
  summary_ru: string;
  recommendations_ru: string[];
  // Optional — backend may attach when result was served from cache.
  cached_at?: string;
};

// POST — trigger a fresh audit and return the result inline.
export async function runRobotsAudit(
  siteId: number | string,
): Promise<RobotsAuditResult> {
  return apiFetch<RobotsAuditResult>(
    `/studio/sites/${siteId}/robots-audit`,
    { method: "POST", base: "admin" },
  );
}

// GET — fetch the last cached audit. Backend returns 404 when no
// audit has been run yet; we surface that as `null` so callers can
// distinguish «never ran» from «failed».
export async function getRobotsAudit(
  siteId: number | string,
): Promise<RobotsAuditResult | null> {
  try {
    return await apiFetch<RobotsAuditResult>(
      `/studio/sites/${siteId}/robots-audit`,
      { base: "admin" },
    );
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    // apiFetch throws `API <status>: <body>` on non-2xx — peel out
    // the 404 case and treat it as «no audit yet».
    if (/^API 404\b/.test(msg)) {
      return null;
    }
    throw e;
  }
}

// ── Advice feed (unified) ───────────────────────────────────────────
// Backend module: backend/app/core_audit/advisor/ — synthesises a
// single ranked list of «cards» across every module (brain rules,
// robots audit, schema audit, keyword gaps, technical failures,
// funnel coverage). Replaces the multi-card Studio dashboard with one
// stream the owner can scroll top-to-bottom.
export type AdviceSeverity = "critical" | "high" | "medium" | "low" | "info";
export type AdviceCardWorkflowStatus =
  | "pending"
  | "in_progress"
  | "applied"
  | "dismissed"
  | "snoozed";
export type AdviceCategory =
  | "technical"
  | "health"
  | "funnel"
  | "schema"
  | "keywords"
  | "seo_content";

// Status of the immediate technical re-verification that runs after
// «Применил». Distinct from the 14-day SEO outcome (which lives on
// outcome_snapshot). `null` when the card has never been applied.
//
//   pending           — Celery task queued, not finished yet (5-60s)
//   verified          — Crawler/check confirmed the change is live
//   not_yet_visible   — Re-crawl ran but the change isn't on page yet
//                       (CDN cache, deployment lag, owner edited the
//                       wrong page)
//   user_attested     — No automated check exists for this advice type,
//                       trusting the owner's word
//   failed            — Verification job errored — owner can re-trigger
export type AdviceCardVerificationStatus =
  | "pending"
  | "verified"
  | "not_yet_visible"
  | "user_attested"
  | "failed";

// Extracted to a named interface so the verification-pill component
// and other callers can reuse the shape without reaching into
// `AdviceCard["state"]`. Backend contract: backend/app/models/
// advice_card_state.py.
export interface AdviceCardState {
  status: AdviceCardWorkflowStatus;
  applied_at: string | null;
  dismissed_at: string | null;
  snoozed_until: string | null;
  updated_at: string | null;
  // Technical re-verification of the card's fact on the page —
  // separate from the 14-day SEO outcome. Null when the card has
  // never been applied. Set to "pending" the moment we PATCH
  // status="applied"; resolves async to one of the 4 terminals.
  verification_status?: AdviceCardVerificationStatus | null;
  verified_at?: string | null;
  // Free-form JSON the backend attaches to explain the verdict
  // («factually X on page, expected Y»). Shape is per-check-type so
  // the UI treats it as opaque and renders best-effort.
  verification_evidence?: Record<string, unknown> | null;
}

export interface AdviceCard {
  id: string;
  severity: AdviceSeverity;
  category: AdviceCategory;
  title_ru: string;
  body_ru: string;
  action_ru: string;
  expected_impact_ru: string | null;
  link: string | null;        // path like "/studio/pages/{id}" or absolute https://…
  cta_ru: string | null;      // button label, null for info cards
  sort_score: number;
  source_module: string;
  why_ru: string | null;
  source_ru: string | null;
  target_ru: string | null;
  evidence_ru: string[];
  verification_ru: string | null;
  state: AdviceCardState;
}

export interface AdviceFeed {
  site_id: string;
  computed_at: string;
  counts_by_severity: Record<string, number>;
  counts_by_category: Record<string, number>;
  cards: AdviceCard[];        // already sorted by sort_score DESC by backend
}

export type GrowthPlanStage =
  | "found"
  | "in_progress"
  | "snoozed"
  | "awaiting_followup"
  | "measured"
  | "dismissed";

export interface GrowthPlanItem {
  kind: "advice" | "outcome";
  stage: GrowthPlanStage;
  id: string;
  title_ru: string;
  body_ru: string;
  action_ru: string;
  severity: AdviceSeverity;
  category: AdviceCategory;
  source_module: string;
  source_ru: string | null;
  target_ru: string | null;
  evidence_ru: string[];
  verification_ru: string | null;
  expected_impact_ru: string | null;
  link: string | null;
  cta_ru: string | null;
  state: AdviceCard["state"] | null;
  outcome: {
    snapshot_id: string;
    recommendation_id: string;
    source: "priority" | "opportunity" | "advice" | string;
    page_url: string | null;
    applied_at: string;
    followup_at: string | null;
    days_since_applied: number;
    days_until_followup: number;
    baseline_metrics: Record<string, unknown> | null;
    followup_metrics: Record<string, unknown> | null;
    delta: Record<string, unknown> | null;
    note_ru: string | null;
  } | null;
}

export interface GrowthPlanResponse {
  site_id: string;
  computed_at: string;
  stats: Record<GrowthPlanStage | "total_open", number>;
  columns: Record<GrowthPlanStage, GrowthPlanItem[]>;
}

// Returns null on 404 («never computed for this site yet»). Any other
// error propagates so the UI can render a real error block.
export async function getAdviceFeed(
  siteId: string,
  options?: { includeHidden?: boolean },
): Promise<AdviceFeed | null> {
  try {
    const query = options?.includeHidden ? "?include_hidden=true" : "";
    return await apiFetch<AdviceFeed>(
      `/studio/sites/${siteId}/advice${query}`,
      { base: "admin" },
    );
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    if (/^API 404\b/.test(msg)) return null;
    throw e;
  }
}

export async function patchAdviceCardState(
  siteId: string,
  cardId: string,
  body: {
    status: AdviceCardWorkflowStatus;
    snooze_days?: number | null;
    page_url?: string | null;
    note_ru?: string | null;
  },
): Promise<{
  site_id: string;
  card_id: string;
  state: AdviceCard["state"];
  outcome_snapshot_id: string | null;
  cancelled_outcome_snapshot_id: string | null;
}> {
  return apiFetch<{
    site_id: string;
    card_id: string;
    state: AdviceCard["state"];
    outcome_snapshot_id: string | null;
    cancelled_outcome_snapshot_id: string | null;
  }>(
    `/studio/sites/${siteId}/advice/${encodeURIComponent(cardId)}/state`,
    {
      method: "PATCH",
      base: "admin",
      body: JSON.stringify(body),
    },
  );
}

// Manually re-trigger the technical verification check (when the
// owner says «check again» after fixing the page or waiting for CDN
// to flush). Returns the queued Celery task ids — UI does not need
// them, just SWR-refetches the feed until verification_status leaves
// "pending".
export async function reVerifyAdviceCard(
  siteId: string,
  cardId: string,
): Promise<{ status: "queued"; task_id: string; run_id: string }> {
  return apiFetch<{ status: "queued"; task_id: string; run_id: string }>(
    `/studio/sites/${siteId}/advice/${encodeURIComponent(cardId)}/re-verify`,
    { method: "POST", base: "admin" },
  );
}

export async function getGrowthPlan(
  siteId: string,
): Promise<GrowthPlanResponse> {
  return apiFetch<GrowthPlanResponse>(
    `/studio/sites/${siteId}/growth-plan`,
    { base: "admin" },
  );
}

// ── Keyword gaps (Wordstat-driven recommendations) ──────────────────
// Backend: backend/app/core_audit/keyword_match/ + the studio.py
// endpoints under `/studio/sites/{site_id}/keyword-gaps`. Per-page
// detail at `/studio/pages/{page_id}/keyword-gaps`. Apply pipeline
// creates a `PageReviewRecommendation` the owner sees on /studio/pages.

export type KeywordGapTopGap = {
  query: string;
  wordstat_volume: number;
  current_position: number | null;
  expected_clicks_uplift: number;
  missing_tokens: string[];
  is_off_season: boolean;
};

export type KeywordGapsTopPage = {
  page_id: string;
  page_url: string;
  page_title: string | null;
  gaps_count: number;
  page_potential_clicks: number;
  top_gap: KeywordGapTopGap;
};

export type KeywordGapsSummary = {
  computed_at: string;
  total_gaps: number;
  total_potential_clicks_per_month: number;
  pages_with_gaps: number;
  top_pages: KeywordGapsTopPage[];
};

export type KeywordGapDetail = {
  query: string;
  query_id: string;
  wordstat_volume: number;
  wordstat_volume_peak_3mo: number | null;
  is_off_season: boolean;
  current_position: number | null;
  expected_clicks_uplift: number;
  missing_in_title_lemmas: string[];
  missing_in_h1_lemmas: string[];
  missing_in_h2_lemmas: string[];
  missing_in_first_para_lemmas: string[];
  has_synonym_in_title: boolean;
};

export type PageKeywordGaps = {
  page_id: string;
  page_url: string;
  computed_at: string;
  gaps: KeywordGapDetail[];
};

export type KeywordGapsRefreshResult = {
  status: "queued";
  task_id: string;
  run_id: string;
};

export type KeywordPlacementApplyResult = {
  recommendation_id: string;
  priority: "high" | "medium";
  priority_score: number;
};

// Site-level summary. Returns null when the analysis has never run
// (backend 404). Other errors propagate so callers can surface them.
export async function getKeywordGapsSummary(
  siteId: string,
): Promise<KeywordGapsSummary | null> {
  try {
    return await apiFetch<KeywordGapsSummary>(
      `/studio/sites/${siteId}/keyword-gaps`,
      { base: "admin" },
    );
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    if (/^API 404\b/.test(msg)) return null;
    throw e;
  }
}

// Per-page detail. `null` means «site analysis never ran» (404).
// An empty `gaps: []` is a valid 200 response (no work to do) — the
// UI distinguishes the two.
export async function getPageKeywordGaps(
  pageId: string,
): Promise<PageKeywordGaps | null> {
  try {
    return await apiFetch<PageKeywordGaps>(
      `/studio/pages/${pageId}/keyword-gaps`,
      { base: "admin" },
    );
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    if (/^API 404\b/.test(msg)) return null;
    throw e;
  }
}

export async function refreshKeywordGaps(
  siteId: string,
): Promise<KeywordGapsRefreshResult> {
  return apiFetch<KeywordGapsRefreshResult>(
    `/studio/sites/${siteId}/keyword-gaps/refresh`,
    { method: "POST", base: "admin" },
  );
}

export async function applyKeywordPlacement(args: {
  page_id: string;
  query_id: string;
  new_title?: string;
  new_h1?: string;
}): Promise<KeywordPlacementApplyResult> {
  return apiFetch<KeywordPlacementApplyResult>(
    `/studio/recommendations/keyword-placement/apply`,
    {
      method: "POST",
      base: "admin",
      body: JSON.stringify(args),
    },
  );
}

export const api = {
  // Health
  health: () => apiFetch<{ status: string; db: string; redis: string }>("/health"),

  // Dashboard
  dashboard: (siteId = SITE_ID) =>
    apiFetch<any>(`/sites/${siteId}/dashboard`, { base: "admin" }),
  trafficMetrics: (siteId = SITE_ID, days = 30) =>
    apiFetch<any>(`/sites/${siteId}/metrics/traffic?days=${days}`, {
      base: "admin",
    }),

  // Sites
  sites: () => apiFetch<any[]>("/sites"),
  updateSite: (siteId: string, body: Record<string, unknown>) =>
    apiFetch<any>(`/sites/${siteId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
      base: "admin",
    }),

  // Reviews (Module 3)
  review: (reviewId: string) =>
    apiFetch<any>(`/reviews/${reviewId}`, { base: "admin" }),

  // Reports (Module 5)
  reportsList: (siteId: string, limit = 20) =>
    apiFetch<{ total: number; items: any[] }>(
      `/reports/sites/${siteId}?limit=${limit}`,
      { base: "admin" },
    ),
  reportLatest: (siteId: string) =>
    apiFetch<any>(`/reports/sites/${siteId}/latest`, { base: "admin" }),
  report: (reportId: string) =>
    apiFetch<any>(`/reports/${reportId}`, { base: "admin" }),
  triggerReport: (siteId: string, weekEnd?: string) =>
    apiFetch<{ task_id: string; status: string }>(
      `/reports/sites/${siteId}/run${weekEnd ? `?week_end=${encodeURIComponent(weekEnd)}` : ""}`,
      { method: "POST", base: "admin" },
    ),
  reportMarkdownUrl: (reportId: string) => `${API_BASE}/reports/${reportId}/markdown`,

  // Priorities (Module 4)
  priorities: (siteId: string, params: Record<string, string | number | boolean> = {}) => {
    const qs = new URLSearchParams(params as any).toString();
    return apiFetch<{ total: number; items: any[] }>(
      `/priorities/sites/${siteId}${qs ? `?${qs}` : ""}`,
      { base: "admin" },
    );
  },
  weeklyPlan: (siteId: string, top_n = 10, max_per_page = 2) =>
    apiFetch<{ total_in_backlog: number; pages_represented: number; max_per_page: number; items: any[] }>(
      `/priorities/sites/${siteId}/weekly-plan?top_n=${top_n}&max_per_page=${max_per_page}`,
      { base: "admin" },
    ),
  triggerRescore: (siteId: string) =>
    apiFetch<{ task_id: string; status: string }>(
      `/priorities/sites/${siteId}/rescore`, { method: "POST", base: "admin" },
    ),
  patchRecommendation: (recId: string, body: { user_status: string; note?: string }) =>
    apiFetch<any>(`/reviews/recommendations/${recId}`, {
      method: "PATCH",
      base: "admin",
      body: JSON.stringify(body),
    }),

  // Conversational Onboarding (single chat screen)
  onboardingState: (siteId: string) =>
    apiFetch<any>(`/sites/${siteId}/onboarding`, { base: "admin" }),
  triggerUnderstandingAnalyze: (siteId: string) =>
    apiFetch<{ task_id: string; status: string }>(
      `/sites/${siteId}/onboarding/understanding/analyze`,
      { method: "POST", base: "admin" },
    ),
  onboardingChatStart: (siteId: string) =>
    apiFetch<{
      site_id: string;
      messages: { role: "assistant" | "user"; content: string }[];
      current: {
        services: string[];
        geo_primary: string[];
        geo_secondary: string[];
        narrative_ru: string;
      };
      round: number;
      status: "active" | "confirmed" | "capped" | "pending";
    }>(`/sites/${siteId}/onboarding/chat/start`, {
      method: "POST", base: "admin",
    }),
  onboardingChatMessage: (siteId: string, message: string) =>
    apiFetch<{
      site_id: string;
      messages: { role: "assistant" | "user"; content: string }[];
      current: {
        services: string[];
        geo_primary: string[];
        geo_secondary: string[];
        narrative_ru: string;
      };
      round: number;
      status: "active" | "confirmed" | "capped" | "pending";
      needs_more_info: boolean;
    }>(`/sites/${siteId}/onboarding/chat/message`, {
      method: "POST",
      base: "admin",
      body: JSON.stringify({ message }),
    }),
  onboardingChatFinalize: (siteId: string, overrides?: {
    services?: string[];
    geo_primary?: string[];
    geo_secondary?: string[];
    narrative_ru?: string;
  }) =>
    apiFetch<{
      site_id: string;
      onboarding_step: string;
      target_config: {
        services: string[];
        geo_primary: string[];
        geo_secondary: string[];
        narrative_ru: string;
      };
    }>(`/sites/${siteId}/onboarding/chat/finalize`, {
      method: "POST",
      base: "admin",
      body: JSON.stringify(overrides || {}),
    }),

  // Competitor discovery (SERP-based)
  triggerCompetitorDiscovery: (siteId: string, maxQueries = 30, topK = 10) =>
    apiFetch<{ task_id: string; status: string }>(
      `/sites/${siteId}/competitors/discover?max_queries=${maxQueries}&top_k=${topK}`,
      { method: "POST", base: "admin" },
    ),
  getCompetitors: (siteId: string) =>
    apiFetch<{
      site_id: string;
      domain: string;
      competitor_domains: string[];
      profile: {
        queries_probed?: number;
        queries_with_results?: number;
        competitors?: Array<{
          domain: string;
          serp_hits: number;
          best_position: number;
          avg_position: number;
          example_url: string;
          example_title: string;
          example_query: string;
        }>;
        cost_usd?: number;
        errors?: Record<string, number>;
        query_serps?: Record<string, any[]>;
        computed_at?: string;
      };
    }>(`/sites/${siteId}/competitors`, { base: "admin" }),

  getContentGaps: (siteId: string, topK = 50) =>
    apiFetch<{
      site_id: string;
      own_domain?: string;
      gaps_found?: number;
      gaps?: Array<{
        query: string;
        site_position: number | null;
        serp_depth: number;
        competitor_domain: string;
        competitor_position: number;
        competitor_url: string;
        competitor_title: string;
        other_competitors: string[];
      }>;
      note?: string;
    }>(`/sites/${siteId}/competitors/content-gaps?top_k=${topK}`, { base: "admin" }),

  triggerCompetitorDeepDive: (siteId: string) =>
    apiFetch<{ task_id: string; status: string }>(
      `/sites/${siteId}/competitors/deep-dive`,
      { method: "POST", base: "admin" },
    ),
  getCompetitorDeepDive: (siteId: string) =>
    apiFetch<{
      site_id: string;
      own_domain: string;
      // Aggregated signals for a site (own or competitor). Backend
      // shape: CompetitorSiteReport.to_dict() in core_audit/competitors/
      // deep_dive.py. Empty object when never run.
      self: CompetitorDeepDiveSite | Record<string, never>;
      competitors: CompetitorDeepDiveSite[];
    }>(`/sites/${siteId}/competitors/deep-dive`, { base: "admin" }),

  getGrowthOpportunities: (siteId: string) =>
    apiFetch<{
      site_id: string;
      own_domain: string;
      count: number;
      opportunities: Array<{
        id: string;
        source: "content_gap" | "feature_diff" | "schema_diff";
        category:
          | "new_page"
          | "strengthen_existing_page"
          | "crossover_page"
          | "on_page_feature"
          | "schema"
          | "contact";
        priority: "high" | "medium" | "low";
        title_ru: string;
        reasoning_ru: string;
        suggested_action_ru: string;
        evidence: Record<string, any>;
      }>;
    }>(`/sites/${siteId}/competitors/opportunities`, { base: "admin" }),

  getActivity: (siteId: string, limit = 20) =>
    apiFetch<{
      events: Array<{
        id: number;
        stage: string;
        status: string;
        message: string;
        ts: string;
        extra: Record<string, any>;
        run_id: string | null;
      }>;
    }>(`/sites/${siteId}/activity?limit=${limit}`, { base: "admin" }),

  getActivityByStage: (siteId: string) =>
    apiFetch<{
      by_stage: Record<
        string,
        {
          id: number;
          stage: string;
          status: string;
          message: string;
          ts: string;
          extra: Record<string, any>;
          run_id: string | null;
        }
      >;
    }>(`/sites/${siteId}/activity/last`, { base: "admin" }),

  // Events of the latest pipeline run only — lets LastRunSummary show
  // a single clean run without events from previous clicks mixing in.
  getCurrentRun: (siteId: string) =>
    apiFetch<{
      run_id: string | null;
      events: Array<{
        id: number;
        stage: string;
        status: string;
        message: string;
        ts: string;
        extra: Record<string, any>;
        run_id: string | null;
      }>;
    }>(`/sites/${siteId}/activity/current-run`, { base: "admin" }),

  triggerFullAnalysis: (siteId: string) =>
    apiFetch<{ status: string; queued: string[]; run_id: string }>(
      `/sites/${siteId}/pipeline/full`,
      { method: "POST", base: "admin" },
    ),

  // Deep extract: Playwright-rendered snapshot (own pages + competitor URLs)
  // NOTE: admin-proxy already prepends `/api/v1/admin/` — paths here
  // start with `/studio/...`, NOT `/admin/studio/...`.
  studioTriggerDeepExtractOwnPage: (siteId: string, pageId: string) =>
    apiFetch<{ status: string; task_id: string; url: string }>(
      `/studio/sites/${siteId}/pages/${pageId}/deep-extract`,
      { method: "POST", base: "admin" },
    ),
  studioTriggerDeepExtractCompetitor: (siteId: string, url: string) =>
    apiFetch<{ status: string; task_id: string; url: string }>(
      `/studio/sites/${siteId}/competitors/deep-extract`,
      { method: "POST", base: "admin", body: JSON.stringify({ url }) },
    ),
  studioGetDeepExtractForPage: (siteId: string, pageId: string) =>
    apiFetch<DeepExtractRow | null>(
      `/studio/sites/${siteId}/pages/${pageId}/deep-extract`,
      { base: "admin" },
    ),
  studioListCompetitorDeepExtracts: (siteId: string) =>
    apiFetch<{ items: DeepExtractRow[] }>(
      `/studio/sites/${siteId}/competitors/deep-extracts`,
      { base: "admin" },
    ),
  studioDeepExtractScreenshotUrl: (
    siteId: string,
    extractId: string,
    kind: "desktop" | "mobile",
  ) =>
    `/admin-proxy/studio/sites/${siteId}/deep-extracts/${extractId}/screenshot/${kind}`,
  studioAnalyzeDeepExtract: (siteId: string, extractId: string, force = false) =>
    apiFetch<{
      extract_id: string;
      summary_md: string;
      cost_usd: number;
      model: string;
      ai_summary_at: string | null;
    }>(
      `/studio/sites/${siteId}/deep-extracts/${extractId}/analyze${force ? "?force=1" : ""}`,
      { method: "POST", base: "admin" },
    ),

  // Indexation probe — asks Yandex Search API `site:domain` for a
  // visible sample. Webmaster per-URL remains the exact index source.
  triggerIndexationCheck: (siteId: string) =>
    apiFetch<{
      status: string;
      task_id: string;
      run_id: string;
      domain: string;
    }>(
      `/sites/${siteId}/indexation/check`,
      { method: "POST", base: "admin" },
    ),

  // IndexNow — ask Yandex to re-crawl a list of URLs. Bypasses the
  // Webmaster re-crawl button (which is locked while host stuck at
  // HOST_NOT_LOADED). Three-step setup: get key → upload file → verify.
  indexnowSetup: (siteId: string) =>
    apiFetch<{
      key: string;
      file_url: string;
      file_content: string;
      verified_at: string | null;
      last_pinged_at: string | null;
      last_result: {
        accepted: boolean;
        status_code: number | null;
        url_count: number;
        error: string | null;
      } | null;
      instructions_ru: string;
    }>(`/sites/${siteId}/indexnow/setup`, { base: "admin" }),

  indexnowVerify: (siteId: string) =>
    apiFetch<{
      verified: boolean;
      verified_at?: string;
      reason?: string;
      hint_ru?: string;
    }>(`/sites/${siteId}/indexnow/verify`, { method: "POST", base: "admin" }),

  indexnowPing: (siteId: string) =>
    apiFetch<{ status: string; task_id: string; run_id: string }>(
      `/sites/${siteId}/indexnow/ping`,
      { method: "POST", base: "admin" },
    ),

  // Connector status board — one place to see every external integration
  // and its real (not assumed) state. Live checks hit real endpoints.
  listConnectors: () =>
    apiFetch<{
      connectors: Array<{
        id: string;
        category: string;
        name: string;
        description_ru: string;
        configured: boolean;
        missing_setting: string | null;
      }>;
      count: number;
    }>(`/health/connectors`),

  testConnector: (id: string) =>
    apiFetch<{
      id: string;
      name: string;
      category: string;
      ok: boolean;
      latency_ms: number;
      sample_data: Record<string, unknown> | null;
      error: string | null;
      checked_at: string;
    }>(`/health/connectors/${id}/test`, { method: "POST" }),

  // Playground — step-by-step scenarios. Each step returns a preview
  // of the real request sent to the external API + a summary of the
  // response, so the owner sees exactly what's happening rather than
  // reading pipeline logs.
  listPlaygroundScenarios: () =>
    apiFetch<{
      scenarios: Array<{
        id: string;
        title_ru: string;
        description_ru: string;
        inputs: Array<{
          key: string;
          label_ru: string;
          placeholder_ru: string;
          required: boolean;
        }>;
        step_count: number;
      }>;
    }>(`/playground/scenarios`),

  runPlaygroundStep: (body: {
    scenario_id: string;
    step_index: number;
    inputs: Record<string, string>;
    prior: Array<Record<string, unknown>>;
  }) =>
    apiFetch<{
      step_index: number;
      step_title_ru: string;
      step_description_ru: string;
      request_shown: {
        endpoint?: string;
        body_preview?: Record<string, unknown>;
      } | null;
      response_summary: Record<string, unknown>;
      ok: boolean;
      error: string | null;
      next_available: boolean;
      next_hint_ru: string | null;
      human_summary_ru: string | null;
      human_summary_level: string;
    }>(`/playground/run`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  testAllConnectors: () =>
    apiFetch<{
      results: Array<{
        id: string;
        name: string;
        category: string;
        ok: boolean;
        latency_ms: number;
        sample_data: Record<string, unknown> | null;
        error: string | null;
        checked_at: string;
      }>;
      total: number;
      ok_count: number;
      failing: string[];
    }>(`/health/connectors/test-all`, { method: "POST" }),

  // BusinessTruth — 3-picture reconciliation of the site as a business.
  getBusinessTruth: (siteId: string) =>
    apiFetch<{
      directions: Array<{
        service: string;
        geo: string;
        strength_understanding: number;
        strength_content: number;
        strength_traffic: number;
        pages: string[];
        queries_sample: string[];
        mentioned_in: string[];
        is_confirmed: boolean;
        is_blind_spot: boolean;
        is_content_only: boolean;
        is_traffic_only: boolean;
        divergence_ru: string | null;
      }>;
      sources_used: Record<string, number>;
      built_at: string | null;
      traffic_coverage?: {
        total_impressions: number;
        unclassified_impressions: number;
        coverage_share: number;
      } | null;
    }>(`/sites/${siteId}/business-truth`, { base: "admin" }),

  rebuildBusinessTruth: (siteId: string) =>
    apiFetch<{ status: string; run_id: string }>(
      `/sites/${siteId}/business-truth/rebuild`,
      { method: "POST", base: "admin" },
    ),

  markApplied: (siteId: string, recommendationId: string, source: string, pageUrl?: string) =>
    apiFetch<{ status: string; snapshot_id: string }>(
      `/sites/${siteId}/outcomes/applied`,
      {
        method: "POST",
        base: "admin",
        body: JSON.stringify({
          recommendation_id: recommendationId,
          source,
          page_url: pageUrl || null,
        }),
      },
    ),

  getOutcomes: (siteId: string) =>
    apiFetch<{
      outcomes: Array<{
        id: string;
        recommendation_id: string;
        source: string;
        page_url: string | null;
        applied_at: string;
        followup_at: string | null;
        delta: Record<string, any>;
        baseline_metrics: Record<string, any>;
        followup_metrics: Record<string, any>;
        note_ru: string | null;
      }>;
    }>(`/sites/${siteId}/outcomes`, { base: "admin" }),

  updateCompetitorsList: (siteId: string, domains: string[]) =>
    apiFetch<{ status: string; competitor_domains: string[] }>(
      `/sites/${siteId}/competitors/list`,
      {
        method: "PUT",
        base: "admin",
        body: JSON.stringify({ domains }),
      },
    ),

  restartOnboarding: (siteId: string) =>
    apiFetch<{ status: string; onboarding_step: string }>(
      `/sites/${siteId}/onboarding/restart`,
      { method: "POST", base: "admin" },
    ),

  // ── Studio · Queries module (PR-S2) ──────────────────────────────────
  // Backend contract: backend/app/api/v1/studio.py.
  // Cache namespacing handled separately via studioKey() — see
  // frontend/lib/studio-keys.ts and IMPLEMENTATION.md §2.2.

  studioListQueries: (
    siteId: string,
    sort: "volume" | "recent" | "alpha" | "position" = "volume",
    limit = 1000,
    layer?: string | null,
  ) => {
    const params = new URLSearchParams({
      sort,
      limit: String(limit),
    });
    if (layer) params.set("layer", layer);
    return apiFetch<{
      site_id: string;
      total: number;
      items: Array<{
        query_id: string;
        query_text: string;
        is_branded: boolean;
        cluster: string | null;
        wordstat_volume: number | null;
        wordstat_status: WordstatStatus;
        wordstat_updated_at: string | null;
        wordstat_trend: Array<{ date: string; count: number | null }> | null;
        last_position: number | null;
        last_impressions_14d: number | null;
        last_seen_at: string | null;
        // Legacy taxonomy + funnel taxonomy (post-backfill). Widened
        // so the UI's RelevanceKey union (which includes funnel values)
        // can be assigned back into this row type without casts.
        relevance:
          | "own"
          | "adjacent"
          | "disputed"
          | "spam"
          | "unclassified"
          | "direct_product"
          | "funnel_warm"
          | "funnel_top"
          | "out_of_market";
        relevance_set_by: "rules" | "llm" | "user" | null;
        relevance_set_at: string | null;
        relevance_reason_ru: string | null;
        strategy_code: string;
        strategy_label_ru: string;
        strategy_reason_ru: string;
        strategy_action_ru: string;
        coverage_status: string;
        coverage_score: number;
        coverage_reason_ru: string;
        coverage_action_ru: string;
        best_page_id: string | null;
        best_page_url: string | null;
        best_page_title: string | null;
        best_page_match_source: string[];
        // 2026-05-13: strategic_focus tag. True iff query_text matches
        // any focus token; false when no focus is set.
        in_focus: boolean;
      }>;
      coverage: QueriesCoverage;
      relevance_counts: {
        // Legacy taxonomy — still emitted by backend during rollout.
        own: number;
        adjacent: number;
        disputed: number;
        spam: number;
        unclassified: number;
        // Funnel taxonomy — written by the rewritten classifier
        // (commit 13481f2). Optional so old responses still type-check.
        direct_product?: number;
        funnel_warm?: number;
        funnel_top?: number;
        out_of_market?: number;
      };
    }>(
      `/studio/sites/${siteId}/queries?${params.toString()}`,
      { base: "admin" },
    );
  },

  studioClassifyQueries: (siteId: string) =>
    apiFetch<{
      status: "queued" | "deduped";
      task_id: string | null;
      run_id: string;
      deduped: boolean;
    }>(
      `/studio/sites/${siteId}/queries/classify`,
      { method: "POST", base: "admin" },
    ),

  studioOverrideRelevance: (
    siteId: string,
    queryId: string,
    relevance:
      | "own"
      | "adjacent"
      | "disputed"
      | "spam"
      | "unclassified"
      | "direct_product"
      | "funnel_warm"
      | "funnel_top"
      | "out_of_market",
  ) =>
    apiFetch<{
      query_id: string;
      relevance: string;
      relevance_set_by: string;
      relevance_set_at: string;
    }>(
      `/studio/sites/${siteId}/queries/${queryId}/relevance`,
      {
        method: "PATCH",
        base: "admin",
        body: JSON.stringify({ relevance }),
      },
    ),

  // Day 5 — harmful visibility: queries we rank for that are spam or
  // disputed. Read-only lens on existing classified data.
  studioHarmfulVisibility: (siteId: string) =>
    apiFetch<{
      site_id: string;
      counts: { spam: number; disputed: number; total: number };
      items: Array<{
        query_id: string;
        query_text: string;
        relevance: "spam" | "disputed";
        relevance_set_by: "rules" | "llm" | "user" | null;
        relevance_reason_ru: string | null;
        last_position: number | null;
        last_impressions_14d: number | null;
        wordstat_volume: number | null;
        suggested_action_ru: string;
        // Day 6: detailed LLM diagnosis (matched URL + cause + fixes).
        harmful_diagnosis: {
          matched_url: string | null;
          matched_position: number | null;
          cause_ru: string;
          fixes: {
            title_change?: string | null;
            h1_change?: string | null;
            meta_description_change?: string | null;
            content_change_ru?: string | null;
            schema_recommendation?: string | null;
            noindex_recommended?: boolean;
          };
          model: string | null;
          diagnosed_at: string;
          skipped?: "no_match" | "no_page_in_db";
        } | null;
        harmful_diagnosed_at: string | null;
        // 2026-05-13: strategic_focus tag — see studioListQueries.
        in_focus: boolean;
      }>;
    }>(`/studio/sites/${siteId}/queries/harmful`, { base: "admin" }),

  // Day 6: trigger LLM diagnosis on all undiagnosed harmful queries.
  studioTriggerHarmfulDiagnose: (siteId: string) =>
    apiFetch<{
      status: "queued" | "deduped";
      task_id: string | null;
      run_id: string;
      deduped: boolean;
    }>(
      `/studio/sites/${siteId}/queries/harmful/diagnose`,
      { method: "POST", base: "admin" },
    ),

  studioDiscoverQueries: (siteId: string) =>
    apiFetch<{
      status: "queued" | "deduped";
      task_id: string | null;
      run_id: string;
      deduped: boolean;
    }>(
      `/studio/sites/${siteId}/queries/discover`,
      { method: "POST", base: "admin" },
    ),

  studioRefreshWordstat: (siteId: string) =>
    apiFetch<{
      status: "queued" | "deduped";
      task_id: string | null;
      run_id: string;
      deduped: boolean;
    }>(
      `/studio/sites/${siteId}/queries/wordstat-refresh`,
      { method: "POST", base: "admin" },
    ),

  // Wordstat-based discovery: «что ищут со словом X» semantic expansion
  // for each service × geo from target_config. Distinct from
  // studioDiscoverQueries (Cartesian + Suggest + LLM) — see CONCEPT.md
  // §2.2 and the studio.py docstring.
  studioWordstatDiscover: (siteId: string) =>
    apiFetch<{
      status: "queued" | "deduped";
      task_id: string | null;
      run_id: string;
      deduped: boolean;
    }>(
      `/studio/sites/${siteId}/queries/wordstat-discover`,
      { method: "POST", base: "admin" },
    ),

  // ── PR-S3 · Indexation module ────────────────────────────────────
  // Backend: backend/app/api/v1/studio.py (IndexationState model).
  // `pages_in_index_live` is the authoritative big-headline number
  // (live COUNT of Webmaster-confirmed indexed pages). The Search API
  // event count is kept as `pages_in_index_searchapi` for the
  // secondary tile; UI hides it when they agree. `pages_found` is the
  // legacy field — now mirrors the live count, retained for backward
  // compat (other call sites still read it).
  studioGetIndexation: (siteId: string) =>
    apiFetch<{
      site_id: string;
      domain: string;
      last_check_at: string | null;
      status: "fresh" | "stale_7d+" | "never_checked" | "running" | "failed";
      pages_found: number | null;
      pages_in_index_live: number;
      pages_in_index_searchapi: number | null;
      pages: Array<{ url: string; title: string; position: number }>;
      diagnosis: {
        verdict: string;
        cause_ru: string;
        action_ru: string;
        severity: "critical" | "high" | "medium" | "low";
      } | null;
      is_running: boolean;
      error: string | null;
    }>(`/studio/sites/${siteId}/indexation`, { base: "admin" }),

  // Studio v2 etap 1+2 — 4-source reconciliation.
  studioGetIndexationSources: (siteId: string) =>
    apiFetch<{
      site_id: string;
      domain: string;
      sources: Record<
        "sitemap" | "crawler" | "webmaster" | "search_api",
        {
          count: number | null;
          last_updated_at: string | null;
          status: string;
          note: string;
        }
      >;
    }>(`/studio/sites/${siteId}/indexation/sources`, { base: "admin" }),

  studioGetIndexationUrls: (
    siteId: string,
    only:
      | "all"
      | "missing_in_search"
      | "only_in_search"
      | "broken_http"
      | "yandex_excluded"
      | "yandex_unknown" = "all",
    limit = 1000,
  ) =>
    apiFetch<{
      site_id: string;
      total: number;
      filtered_total: number;
      truncated: boolean;
      items: Array<{
        page_id: string;
        url: string;
        path: string;
        in_sitemap: boolean;
        in_index: boolean;
        http_status: number | null;
        last_crawled_at: string | null;
        found_in_search_api: boolean;
        title: string | null;
        in_yandex_index: boolean | null;
        yandex_excluded_reason: string | null;
        yandex_index_checked_at: string | null;
      }>;
      only_in_sitemap: number;
      only_in_search: number;
      fully_aligned: number;
    }>(
      `/studio/sites/${siteId}/indexation/urls?only=${only}&limit=${limit}`,
      { base: "admin" },
    ),

  studioTriggerUrlIndexationRefresh: (siteId: string) =>
    apiFetch<{
      status: "queued" | "deduped";
      task_id: string | null;
      run_id: string;
      deduped: boolean;
    }>(
      `/studio/sites/${siteId}/indexation/refresh-urls`,
      { method: "POST", base: "admin" },
    ),

  studioTriggerIndexationCheck: (siteId: string) =>
    apiFetch<{
      status: "queued" | "deduped";
      task_id: string | null;
      run_id: string;
      deduped: boolean;
    }>(
      `/studio/sites/${siteId}/indexation/check`,
      { method: "POST", base: "admin" },
    ),

  // ── PR-S4 · Pages module ─────────────────────────────────────────
  studioListPages: (
    siteId: string,
    sort: "recent_review" | "crawl" | "alpha" | "recs" = "recent_review",
    limit = 100,
  ) =>
    apiFetch<{
      site_id: string;
      total: number;
      items: Array<{
        page_id: string;
        url: string;
        path: string;
        title: string | null;
        in_yandex_index: boolean | null;
        yandex_excluded_reason: string | null;
        yandex_index_checked_at: string | null;
        in_sitemap: boolean;
        http_status: number | null;
        last_crawled_at: string | null;
        has_review: boolean;
        last_reviewed_at: string | null;
        n_recommendations: number;
        n_pending: number;
        n_applied: number;
        // 2026-05-13: strategic_focus tag — see studioListQueries.
        in_focus: boolean;
      }>;
    }>(
      `/studio/sites/${siteId}/pages?sort=${sort}&limit=${limit}`,
      { base: "admin" },
    ),

  // ── V2 prerequisite · Profile editor ─────────────────────────────
  // Backend: backend/app/api/v1/studio.py · get_profile / put_profile
  studioGetProfile: (siteId: string) =>
    apiFetch<{
      site_id: string;
      domain: string;
      profile: {
        primary_product: string;
        services: string[];
        secondary_products: string[];
        geo_primary: string[];
        geo_secondary: string[];
        narrative_ru: string;
      };
      last_edited_at: string | null;
      last_edited_by: string | null;
    }>(`/studio/sites/${siteId}/profile`, { base: "admin" }),

  studioPutProfile: (
    siteId: string,
    body: {
      primary_product: string;
      services: string[];
      secondary_products: string[];
      geo_primary: string[];
      geo_secondary: string[];
      narrative_ru: string;
    },
  ) =>
    apiFetch<{
      site_id: string;
      domain: string;
      profile: typeof body;
      last_edited_at: string | null;
      last_edited_by: string | null;
    }>(`/studio/sites/${siteId}/profile`, {
      method: "PUT",
      base: "admin",
      body: JSON.stringify(body),
    }),

  // ── PR-S8 · Outcomes module ──────────────────────────────────────
  studioListOutcomes: (siteId: string) =>
    apiFetch<{
      site_id: string;
      stats: {
        total: number;
        awaiting_followup: number;
        measured: number;
        avg_impressions_pct: number | null;
        avg_clicks_pct: number | null;
        avg_position_delta: number | null;
      };
      items: Array<{
        snapshot_id: string;
        recommendation_id: string;
        source: "priority" | "opportunity" | "advice";
        page_url: string | null;
        applied_at: string;
        followup_at: string | null;
        baseline_metrics: Record<string, unknown> | null;
        followup_metrics: Record<string, unknown> | null;
        delta: Record<string, unknown> | null;
        note_ru: string | null;
        days_since_applied: number;
      }>;
    }>(`/studio/sites/${siteId}/outcomes`, { base: "admin" }),

  // ── PR-S6 · Analytics module ─────────────────────────────────────
  studioGetAnalytics: (siteId: string, days = 90) =>
    apiFetch<{
      site_id: string;
      days: number;
      series: Array<{
        date: string;
        impressions: number | null;
        clicks: number | null;
        avg_position: number | null;
        visits: number | null;
        pageviews: number | null;
        bounce_rate: number | null;
        avg_duration_sec: number | null;
        pages_indexed: number | null;
      }>;
      totals: {
        impressions_sum: number;
        clicks_sum: number;
        visits_sum: number;
        pageviews_sum: number;
        avg_position_mean: number | null;
        avg_bounce_rate_mean: number | null;
        indexed_latest: number | null;
        days_with_search_data: number;
        days_with_traffic_data: number;
      };
      webmaster_latest_date: string | null;
      metrica_latest_date: string | null;
      metrica_status: {
        // Closed sets — backend pulls these from Metrica API. If Metrica
        // ever adds a new code, we still parse it (TypeScript falls back
        // to the literal-union default in formatCounterStatus), and on
        // the frontend the mapper has an `??` fallback so it never
        // crashes.
        counter_status: "Active" | "Pending" | "Deleted" | string | null;
        counter_activity_status: "Active" | "Pending" | string | null;
        counter_code_status:
          | "CS_OK"
          | "CS_ERR_UNKNOWN"
          | "CS_ERR_NOT_INSTALLED"
          | "CS_ERR_HTTP_ERROR"
          | "CS_ERR_INVISIBLE"
          | string
          | null;
        counter_site: { site: string } | string | null;
        has_recent_visits: boolean;
        warning: string | null;
      };
      metrica_top_pages: Array<{
        page_id: string | null;
        url: string;
        visits: number;
        pageviews: number;
        bounce_rate: number | null;
        avg_duration_sec: number | null;
        mapped_to_page: boolean;
      }>;
      metrica_sources: Array<{
        source: string;
        visits: number;
        pageviews: number;
      }>;
      metrica_goals: Array<{
        goal_id: string;
        name: string | null;
        goal_type: string | null;
        reaches: number;
        target_visits: number;
        conversion_rate: number | null;
      }>;
    }>(`/studio/sites/${siteId}/analytics?days=${days}`, { base: "admin" }),

  // V2 etap 3 — trigger per-page review on demand (Reviewer pipeline).
  studioTriggerPageReview: (siteId: string, pageId: string) =>
    apiFetch<{
      status: "queued" | "deduped";
      task_id: string | null;
      run_id: string;
      deduped: boolean;
    }>(
      `/studio/sites/${siteId}/pages/${pageId}/review`,
      { method: "POST", base: "admin" },
    ),

  // Studio — re-fetch a single page on demand (after owner edits the page).
  studioTriggerPageRecrawl: (siteId: string, pageId: string) =>
    apiFetch<{
      status: "queued" | "deduped";
      task_id: string | null;
      run_id: string;
      deduped: boolean;
    }>(
      `/studio/sites/${siteId}/pages/${pageId}/recrawl`,
      { method: "POST", base: "admin" },
    ),

  studioGetPage: (siteId: string, pageId: string) =>
    apiFetch<{
      page_id: string;
      site_id: string;
      url: string;
      path: string;
      title: string | null;
      h1: string | null;
      meta_description: string | null;
      word_count: number | null;
      in_yandex_index: boolean | null;
      yandex_excluded_reason: string | null;
      yandex_index_checked_at: string | null;
      in_sitemap: boolean;
      http_status: number | null;
      has_schema: boolean;
      last_crawled_at: string | null;
      review: {
        review_id: string;
        status: string;
        skip_reason: string | null;
        reviewer_model: string;
        reviewed_at: string;
        cost_usd: number;
        page_level_summary: Record<string, unknown> | null;
        top_queries_snapshot: Record<string, unknown> | null;
        recommendations: Array<{
          rec_id: string;
          category: string;
          priority: string;
          source_finding_id: string | null;
          user_status: string;
          before_text: string | null;
          after_text: string | null;
          reasoning_ru: string;
          // plain_ru: owner-facing plain-language explanation of the
          // recommendation. Null when not yet generated — the studio
          // page workspace auto-fires studioExplainRec to fill it in,
          // and also offers a manual «Объяснить простым языком» button.
          plain_ru: string | null;
          priority_score: number | null;
          impact_score: number | null;
          confidence_score: number | null;
          ease_score: number | null;
        }>;
      } | null;
      outcomes: Array<{
        snapshot_id: string;
        recommendation_id: string;
        source: string;
        applied_at: string;
        baseline_metrics: Record<string, unknown> | null;
        followup_at: string | null;
        followup_metrics: Record<string, unknown> | null;
        delta: Record<string, unknown> | null;
        note_ru: string | null;
      }>;
      cross_links: Record<string, boolean>;
    }>(`/studio/sites/${siteId}/pages/${pageId}`, { base: "admin" }),

  // V2 etap 6 — missing landing pages (services in narrative without
  // a dedicated URL).
  studioTriggerMissingLandingsScan: (siteId: string) =>
    apiFetch<{
      status: "queued" | "deduped";
      task_id: string | null;
      run_id: string;
      deduped: boolean;
    }>(
      `/studio/sites/${siteId}/missing-landings/scan`,
      { method: "POST", base: "admin" },
    ),

  // V2 etap 7 — brain. Synthesised «do this first» plan from all
  // module data. Pure SQL + Russian rules, no LLM.
  // `computed_at` is the moment the plan was built (always "now") and
  // is therefore misleading for freshness. UI uses the data-source
  // anchors below to show "данные собраны: …" / a stale-warning badge.
  studioGetBrainPlan: (siteId: string) =>
    apiFetch<{
      site_id: string;
      domain: string;
      computed_at: string;
      diagnostics: string[];
      last_webmaster_at: string | null;
      last_wordstat_at: string | null;
      last_crawl_at: string | null;
      actions: Array<{
        id: string;
        severity: "critical" | "high" | "medium" | "low";
        title: string;
        body_ru: string;
        what_to_do_ru: string;
        link_to: string;
        link_label: string;
        examples: Array<{
          label: string;
          kind: string;
          hint?: string | null;
        }>;
        evidence: Record<string, unknown>;
        in_focus: boolean;
      }>;
    }>(`/studio/sites/${siteId}/plan`, { base: "admin" }),

  studioDownloadRecommendations: (siteId: string) =>
    apiDownload(`/studio/sites/${siteId}/recommendations/export`, {
      base: "admin",
    }),

  // Generate (or fetch cached) owner-facing «plain language» explanation
  // of a recommendation. Server contract: idempotent — if plain_ru is
  // already persisted, returns it with cached=true and cost_usd=0.
  // Otherwise calls the LLM, persists the result, and charges cost_usd.
  // UI: studio /pages/[page_id] fires this automatically on load for
  // any rec where plain_ru is null, and also exposes a manual button.
  studioExplainRec: (recId: string) =>
    apiFetch<{
      id: string;
      plain_ru: string;
      cached: boolean;
      cost_usd: number;
    }>(
      `/studio/recommendations/${recId}/explain`,
      { method: "POST", base: "admin" },
    ),

  // V2 etap 7 Phase C+D+E — free chat about whole site, persisted
  // in DB. Pass `conversation_id=null` to start a new thread; the
  // response carries `conversation_id` to continue. Server reads
  // history from DB regardless of what the client thinks.
  // Phase E: response may include a `proposal` when the LLM picked
  // the propose_strategic_focus tool — UI shows a modal asking the
  // owner to confirm before anything is written.
  studioBrainFreeChat: (
    siteId: string,
    message: string,
    conversationId: string | null,
    mode: ChatMode = "answer",
  ) =>
    apiFetch<{
      conversation_id: string;
      reply: string | null;
      proposal: {
        label: string;
        products: string[];
        regions: string[];
        query_signals: string[];
        deprioritised: string[];
        exit_criterion: string | null;
        owner_note: string | null;
        deadline: string | null;
        rationale: string;
      } | null;
      cost_usd: number;
      model: string | null;
      input_tokens: number | null;
      output_tokens: number | null;
      // Set when the LLM hit max_tokens — UI shows a warning + retry.
      truncated: boolean;
      // Set when the server served from the 60s idempotency cache
      // (double-click / F5 / second tab during in-flight). UI skips
      // optimistic updates because nothing new happened server-side.
      deduped: boolean;
    }>(`/studio/sites/${siteId}/chat`, {
      method: "POST",
      base: "admin",
      body: JSON.stringify({
        message,
        conversation_id: conversationId,
        mode,
      }),
    }),

  studioListConversations: (siteId: string, limit = 30) =>
    apiFetch<
      Array<{
        id: string;
        title: string | null;
        message_count: number;
        total_cost_usd: number;
        last_message_at: string | null;
        created_at: string;
      }>
    >(
      `/studio/sites/${siteId}/conversations?limit=${limit}`,
      { base: "admin" },
    ),

  studioGetConversation: (siteId: string, conversationId: string) =>
    apiFetch<{
      id: string;
      title: string | null;
      message_count: number;
      total_cost_usd: number;
      last_message_at: string | null;
      created_at: string;
      messages: Array<{
        id: string;
        role: "user" | "assistant";
        content: string;
        model: string | null;
        cost_usd: number;
        input_tokens: number;
        output_tokens: number;
        created_at: string;
      }>;
    }>(
      `/studio/sites/${siteId}/conversations/${conversationId}`,
      { base: "admin" },
    ),

  studioDeleteConversation: (siteId: string, conversationId: string) =>
    apiFetch<null>(
      `/studio/sites/${siteId}/conversations/${conversationId}`,
      { method: "DELETE", base: "admin" },
    ),

  // V2 etap 7 Phase E — strategic focus.
  // The /studio/profile «Стратегический фокус» editor uses GET/PUT/
  // DELETE; the chat «Применить» dialog uses POST .../from-proposal
  // (semantically identical PUT but tags set_by='owner_via_chat').
  studioGetStrategicFocus: (siteId: string) =>
    apiFetch<StudioStrategicFocus | null>(
      `/studio/sites/${siteId}/strategic-focus`,
      { base: "admin" },
    ),

  studioSetStrategicFocus: (
    siteId: string,
    focus: StudioStrategicFocusInput,
  ) =>
    apiFetch<StudioStrategicFocus>(
      `/studio/sites/${siteId}/strategic-focus`,
      {
        method: "PUT",
        base: "admin",
        body: JSON.stringify(focus),
      },
    ),

  studioClearStrategicFocus: (siteId: string) =>
    apiFetch<null>(
      `/studio/sites/${siteId}/strategic-focus`,
      { method: "DELETE", base: "admin" },
    ),

  studioApplyStrategicFocusProposal: (
    siteId: string,
    focus: StudioStrategicFocusInput,
  ) =>
    apiFetch<StudioStrategicFocus>(
      `/studio/sites/${siteId}/strategic-focus/from-proposal`,
      {
        method: "POST",
        base: "admin",
        body: JSON.stringify(focus),
      },
    ),

  // V2 etap 7 Phase B — chat about a specific brain plan action.
  // Stateless: client sends full history each turn.
  studioBrainActionChat: (
    siteId: string,
    actionId: string,
    message: string,
    history: Array<{ role: "user" | "assistant"; content: string }>,
  ) =>
    apiFetch<{
      reply: string;
      cost_usd: number;
      model: string | null;
      input_tokens: number | null;
      output_tokens: number | null;
    }>(
      // action ids contain a colon (e.g. "queries:harmful") — let
      // the URL constructor handle encoding so the colon round-trips.
      `/studio/sites/${siteId}/plan/${encodeURIComponent(actionId)}/chat`,
      {
        method: "POST",
        base: "admin",
        body: JSON.stringify({ message, history }),
      },
    ),

  studioGetMissingLandings: (siteId: string) =>
    apiFetch<{
      site_id: string;
      summary_ru: string;
      model: string | null;
      cost_usd: number | null;
      input_pages: number | null;
      rejected_no_evidence: number | null;
      computed_at: string | null;
      items: Array<{
        service_name: string;
        evidence_quote: string;
        closest_existing_url: string | null;
        suggested_url_path: string;
        why_it_matters_ru: string;
        priority: "high" | "medium" | "low";
      }>;
    }>(`/studio/sites/${siteId}/missing-landings`, { base: "admin" }),
};
