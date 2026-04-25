const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api/v1";
const ADMIN_PROXY = "/admin-proxy";  // Next.js server-side proxy — holds the admin key in backend env only

// Default site ID — in Phase 9 this becomes dynamic
export const SITE_ID = process.env.NEXT_PUBLIC_SITE_ID || "1e11339f-c87e-4742-9d38-6f79463b0d16";

async function apiFetch<T>(
  path: string,
  init?: RequestInit & { base?: "api" | "admin" },
): Promise<T> {
  if (path.includes("//")) {
    throw new Error(
      "API call skipped: siteId is empty (context not ready yet). " +
      "Wait for site to load and try again."
    );
  }
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

export const api = {
  // Health
  health: () => apiFetch<{ status: string; db: string; redis: string }>("/health"),

  // Dashboard
  dashboard: (siteId = SITE_ID) => apiFetch<any>(`/sites/${siteId}/dashboard`),
  trafficMetrics: (siteId = SITE_ID, days = 30) =>
    apiFetch<any>(`/sites/${siteId}/metrics/traffic?days=${days}`),
  indexingMetrics: (siteId = SITE_ID, days = 30) =>
    apiFetch<any>(`/sites/${siteId}/metrics/indexing?days=${days}`),

  // Issues
  issues: (siteId = SITE_ID, params: Record<string, string | number> = {}) => {
    const qs = new URLSearchParams(params as any).toString();
    return apiFetch<any>(`/sites/${siteId}/issues${qs ? `?${qs}` : ""}`);
  },
  updateIssue: (siteId = SITE_ID, issueId: string, body: Record<string, unknown>) =>
    apiFetch<any>(`/sites/${siteId}/issues/${issueId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  // Sites
  sites: () => apiFetch<any[]>("/sites"),
  updateSite: (siteId: string, body: Record<string, unknown>) =>
    apiFetch<any>(`/sites/${siteId}`, { method: "PATCH", body: JSON.stringify(body) }),

  // Reviews (Module 3)
  reviewsList: (siteId: string, params: Record<string, string | number> = {}) => {
    const qs = new URLSearchParams(params as any).toString();
    return apiFetch<{ total: number; items: any[] }>(
      `/reviews/sites/${siteId}/reviews${qs ? `?${qs}` : ""}`,
    );
  },
  review: (reviewId: string) =>
    apiFetch<any>(`/reviews/${reviewId}`),
  reviewsStats: (siteId: string) =>
    apiFetch<any>(`/reviews/sites/${siteId}/reviews/stats`),
  triggerSiteReview: (siteId: string, topN = 20) =>
    apiFetch<{ task_id: string; status: string }>(
      `/reviews/sites/${siteId}/run?top_n=${topN}`, { method: "POST" },
    ),

  // Reports (Module 5)
  reportsList: (siteId: string, limit = 20) =>
    apiFetch<{ total: number; items: any[] }>(`/reports/sites/${siteId}?limit=${limit}`),
  reportLatest: (siteId: string) =>
    apiFetch<any>(`/reports/sites/${siteId}/latest`),
  report: (reportId: string) =>
    apiFetch<any>(`/reports/${reportId}`),
  triggerReport: (siteId: string, weekEnd?: string) =>
    apiFetch<{ task_id: string; status: string }>(
      `/reports/sites/${siteId}/run${weekEnd ? `?week_end=${encodeURIComponent(weekEnd)}` : ""}`,
      { method: "POST" },
    ),
  reportMarkdownUrl: (reportId: string) => `${API_BASE}/reports/${reportId}/markdown`,

  // Priorities (Module 4)
  priorities: (siteId: string, params: Record<string, string | number | boolean> = {}) => {
    const qs = new URLSearchParams(params as any).toString();
    return apiFetch<{ total: number; items: any[] }>(
      `/priorities/sites/${siteId}${qs ? `?${qs}` : ""}`,
    );
  },
  weeklyPlan: (siteId: string, top_n = 10, max_per_page = 2) =>
    apiFetch<{ total_in_backlog: number; pages_represented: number; max_per_page: number; items: any[] }>(
      `/priorities/sites/${siteId}/weekly-plan?top_n=${top_n}&max_per_page=${max_per_page}`,
    ),
  triggerRescore: (siteId: string) =>
    apiFetch<{ task_id: string; status: string }>(
      `/priorities/sites/${siteId}/rescore`, { method: "POST" },
    ),
  patchRecommendation: (recId: string, body: { user_status: string; note?: string }) =>
    apiFetch<any>(`/reviews/recommendations/${recId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  // Admin — Draft Profile (Phase F/G)
  // These go through the Next.js server-side proxy (/admin-proxy/*) which
  // injects X-Admin-Key from backend env. No key ever touches the browser.
  draftProfile: (siteId: string) =>
    apiFetch<{ site_id: string; draft: any; has_draft: boolean }>(
      `/sites/${siteId}/draft-profile`, { base: "admin" },
    ),
  triggerDraftRebuild: (siteId: string) =>
    apiFetch<{ task_id: string; status: string }>(
      `/sites/${siteId}/draft-profile/rebuild`, { method: "POST", base: "admin" },
    ),
  commitDraft: (
    siteId: string,
    body: { confirm: boolean; field_overrides?: Record<string, any> } = { confirm: true },
  ) =>
    apiFetch<{ committed: boolean; preview?: boolean; target_config: any }>(
      `/sites/${siteId}/target-config/commit-draft`,
      { method: "POST", base: "admin", body: JSON.stringify(body) },
    ),
  demandMap: (siteId: string, params: Record<string, string | number> = {}) => {
    const qs = new URLSearchParams(params as any).toString();
    return apiFetch<{ clusters_total: number; items: any[] }>(
      `/sites/${siteId}/demand-map${qs ? `?${qs}` : ""}`, { base: "admin" },
    );
  },

  // Conversational Onboarding (single chat screen)
  onboardingState: (siteId: string) =>
    apiFetch<any>(`/sites/${siteId}/onboarding`, { base: "admin" }),
  triggerUnderstandingAnalyze: (siteId: string) =>
    apiFetch<{ task_id: string; status: string }>(
      `/sites/${siteId}/onboarding/understanding/analyze`,
      { method: "POST", base: "admin" },
    ),
  patchUnderstanding: (siteId: string, body: Record<string, any>) =>
    apiFetch<any>(`/sites/${siteId}/onboarding/understanding`, {
      method: "PATCH",
      base: "admin",
      body: JSON.stringify(body),
    }),
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
  triggerCompetitorDiscovery: (siteId: string, maxQueries = 20, topK = 10) =>
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
      };
    }>(`/sites/${siteId}/competitors`, { base: "admin" }),

  getContentGaps: (siteId: string, topK = 20) =>
    apiFetch<{
      site_id: string;
      own_domain?: string;
      gaps_found?: number;
      gaps?: Array<{
        query: string;
        site_position: number | null;
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
    }>(`/sites/${siteId}/activity?limit=${limit}`),

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
    }>(`/sites/${siteId}/activity/last`),

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
    }>(`/sites/${siteId}/activity/current-run`),

  triggerFullAnalysis: (siteId: string) =>
    apiFetch<{ status: string; queued: string[]; run_id: string }>(
      `/sites/${siteId}/pipeline/full`,
      { method: "POST", base: "admin" },
    ),

  // Indexation probe — asks Yandex Search API `site:domain`. Fallback
  // to Webmaster: works even when the host is stuck at HOST_NOT_LOADED.
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
    limit = 200,
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
      }>;
      coverage: {
        total: number;
        with_volume: number;
        without_volume: number;
        stale: number;
      };
    }>(
      `/studio/sites/${siteId}/queries?sort=${sort}&limit=${limit}`,
      { base: "admin" },
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
  studioGetIndexation: (siteId: string) =>
    apiFetch<{
      site_id: string;
      domain: string;
      last_check_at: string | null;
      status: "fresh" | "stale_7d+" | "never_checked" | "running" | "failed";
      pages_found: number | null;
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
};
