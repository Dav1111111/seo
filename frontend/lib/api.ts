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

  // Agent runs
  agentRuns: (siteId = SITE_ID, limit = 20) =>
    apiFetch<any>(`/sites/${siteId}/agent-runs?limit=${limit}`),

  // Sites
  sites: () => apiFetch<any[]>("/sites"),
  updateSite: (siteId: string, body: Record<string, unknown>) =>
    apiFetch<any>(`/sites/${siteId}`, { method: "PATCH", body: JSON.stringify(body) }),

  // Triggers
  triggerPipeline: (siteId = SITE_ID) =>
    apiFetch<any>(`/sites/${siteId}/pipeline`, { method: "POST" }),
  triggerAgent: (siteId = SITE_ID, agent: string) =>
    apiFetch<any>(`/sites/${siteId}/analyse/${agent}`, { method: "POST" }),
  triggerCollect: (siteId = SITE_ID) =>
    apiFetch<any>(`/sites/${siteId}/collect/webmaster`, { method: "POST" }),

  // Queries
  queries: (siteId = SITE_ID, params: Record<string, string | number> = {}) => {
    const qs = new URLSearchParams(params as any).toString();
    return apiFetch<any>(`/sites/${siteId}/queries${qs ? `?${qs}` : ""}`);
  },
  queryHistory: (siteId = SITE_ID, queryId: string, days = 30) =>
    apiFetch<any>(`/sites/${siteId}/queries/${queryId}/history?days=${days}`),
  queryClusters: (siteId = SITE_ID, days = 7) =>
    apiFetch<any>(`/sites/${siteId}/queries/clusters?days=${days}`),
  renameCluster: (siteId = SITE_ID, oldName: string, newName: string) =>
    apiFetch<any>(`/sites/${siteId}/queries/clusters/${encodeURIComponent(oldName)}`, {
      method: "PATCH",
      body: JSON.stringify({ new_name: newName }),
    }),
  triggerClustering: (siteId = SITE_ID) =>
    apiFetch<any>(`/sites/${siteId}/cluster-queries`, { method: "POST" }),
  triggerQueryRecommendations: (siteId = SITE_ID) =>
    apiFetch<any>(`/sites/${siteId}/analyse/query-recommendations`, { method: "POST" }),

  // SEO Tasks
  tasks: (siteId = SITE_ID, params: Record<string, string | number> = {}) => {
    const qs = new URLSearchParams(params as any).toString();
    return apiFetch<any>(`/sites/${siteId}/tasks${qs ? `?${qs}` : ""}`);
  },
  getTask: (siteId = SITE_ID, taskId: string) =>
    apiFetch<any>(`/sites/${siteId}/tasks/${taskId}`),
  updateTask: (siteId = SITE_ID, taskId: string, body: Record<string, unknown>) =>
    apiFetch<any>(`/sites/${siteId}/tasks/${taskId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteTask: (siteId = SITE_ID, taskId: string) =>
    apiFetch<any>(`/sites/${siteId}/tasks/${taskId}`, { method: "DELETE" }),
  triggerGenerateTasks: (siteId = SITE_ID) =>
    apiFetch<any>(`/sites/${siteId}/generate-tasks`, { method: "POST" }),
  triggerCrawl: (siteId = SITE_ID) =>
    apiFetch<any>(`/sites/${siteId}/crawl`, { method: "POST" }),
  pages: (siteId = SITE_ID) =>
    apiFetch<any>(`/sites/${siteId}/pages`),
  agentStatus: (siteId = SITE_ID) =>
    apiFetch<any>(`/sites/${siteId}/agent-status`),

  // Chat
  chat: (siteId = SITE_ID, message: string, history: any[] = [], issueId?: string) =>
    apiFetch<{ reply: string; cost_usd: number }>(`/sites/${siteId}/chat`, {
      method: "POST",
      body: JSON.stringify({ message, history, issue_id: issueId }),
    }),

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

  // Этап 1 — Conversational Onboarding
  onboardingState: (siteId: string) =>
    apiFetch<any>(`/sites/${siteId}/onboarding`, { base: "admin" }),
  triggerUnderstandingAnalyze: (siteId: string) =>
    apiFetch<{ task_id: string; status: string }>(
      `/sites/${siteId}/onboarding/understanding/analyze`,
      { method: "POST", base: "admin" },
    ),
  patchOnboardingStep: (siteId: string, onboarding_step: string) =>
    apiFetch<any>(`/sites/${siteId}/onboarding/step`, {
      method: "PATCH",
      base: "admin",
      body: JSON.stringify({ onboarding_step }),
    }),
  patchUnderstanding: (siteId: string, body: Record<string, any>) =>
    apiFetch<any>(`/sites/${siteId}/onboarding/understanding`, {
      method: "PATCH",
      base: "admin",
      body: JSON.stringify(body),
    }),
  patchOnboardingProducts: (siteId: string, body: {
    primary_product?: string | null;
    service_weights?: Record<string, number>;
    secondary_products?: string[];
  }) =>
    apiFetch<any>(`/sites/${siteId}/onboarding/products`, {
      method: "PATCH",
      base: "admin",
      body: JSON.stringify(body),
    }),
  patchOnboardingCompetitors: (siteId: string, body: {
    competitor_domains?: string[];
    competitor_brands?: string[];
  }) =>
    apiFetch<any>(`/sites/${siteId}/onboarding/competitors`, {
      method: "PATCH",
      base: "admin",
      body: JSON.stringify(body),
    }),
  patchClusterReview: (siteId: string, clusterId: string, body: {
    user_confirmed?: boolean | null;
    growth_intent?: string | null;
  }) =>
    apiFetch<any>(`/sites/${siteId}/onboarding/clusters/${clusterId}`, {
      method: "PATCH",
      base: "admin",
      body: JSON.stringify(body),
    }),
  patchOnboardingKpi: (siteId: string, body: Record<string, any>) =>
    apiFetch<any>(`/sites/${siteId}/onboarding/kpi`, {
      method: "PATCH",
      base: "admin",
      body: JSON.stringify(body),
    }),
  completeOnboarding: (siteId: string) =>
    apiFetch<any>(`/sites/${siteId}/onboarding/complete`, {
      method: "POST", base: "admin",
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
};
