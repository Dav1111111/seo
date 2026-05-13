const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api/v1";
const ADMIN_PROXY = "/admin-proxy";  // Next.js server-side proxy — holds the admin key in backend env only

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
  has_screenshot_desktop: boolean;
  has_screenshot_mobile: boolean;
  // AI summary + when it was generated. `ai_summary_at` is null on
  // legacy rows (set before this freshness field landed) — the panel
  // treats null as "freshness unknown" and nudges a re-analyze.
  ai_summary_md: string | null;
  ai_summary_at: string | null;
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
      self: any;
      competitors: any[];
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
  studioAnalyzeDeepExtract: (siteId: string, extractId: string) =>
    apiFetch<{ extract_id: string; summary_md: string; cost_usd: number; model: string }>(
      `/studio/sites/${siteId}/deep-extracts/${extractId}/analyze`,
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
  ) =>
    apiFetch<{
      site_id: string;
      total: number;
      items: Array<{
        query_id: string;
        query_text: string;
        is_branded: boolean;
        cluster: string | null;
        wordstat_volume: number | null;
        wordstat_status:
          | "fresh"
          | "stale_30d+"
          | "never_fetched"
          | "fetch_returned_empty";
        wordstat_updated_at: string | null;
        wordstat_trend: Array<{ date: string; count: number | null }> | null;
        last_position: number | null;
        last_impressions_14d: number | null;
        last_seen_at: string | null;
        relevance: "own" | "adjacent" | "disputed" | "spam" | "unclassified";
        relevance_set_by: "rules" | "llm" | "user" | null;
        relevance_set_at: string | null;
        relevance_reason_ru: string | null;
        // 2026-05-13: strategic_focus tag. True iff query_text matches
        // any focus token; false when no focus is set.
        in_focus: boolean;
      }>;
      coverage: {
        total: number;
        with_volume: number;
        without_volume: number;
        stale: number;
      };
      relevance_counts: {
        own: number;
        adjacent: number;
        disputed: number;
        spam: number;
        unclassified: number;
      };
    }>(
      `/studio/sites/${siteId}/queries?sort=${sort}&limit=${limit}`,
      { base: "admin" },
    ),

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
    relevance: "own" | "adjacent" | "disputed" | "spam",
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
        source: "priority" | "opportunity";
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
