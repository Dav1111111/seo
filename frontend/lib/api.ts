const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api/v1";

// Default site ID — in Phase 9 this becomes dynamic
export const SITE_ID = process.env.NEXT_PUBLIC_SITE_ID || "1e11339f-c87e-4742-9d38-6f79463b0d16";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "true",  // bypass ngrok interstitial
      ...init?.headers,
    },
    ...init,
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
};
