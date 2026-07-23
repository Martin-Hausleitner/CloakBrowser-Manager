/**
 * API client for CloakBrowser Manager backend.
 */

export type ProfileHarness = "codex" | "antigravity" | "claude-code" | "opencode" | "browser-use";

export interface Profile {
  id: string;
  name: string;
  sandbox_id: string;
  project_id: string;
  folder_path: string;
  pinned: boolean;
  accent_color: string | null;
  harness: ProfileHarness;
  fingerprint_seed: number;
  proxy: string | null;
  timezone: string | null;
  locale: string | null;
  platform: string;
  user_agent: string | null;
  screen_width: number;
  screen_height: number;
  gpu_vendor: string | null;
  gpu_renderer: string | null;
  hardware_concurrency: number | null;
  humanize: boolean;
  human_preset: string;
  headless: boolean;
  geoip: boolean;
  clipboard_sync: boolean;
  auto_launch: boolean;
  color_scheme: string | null;
  search_engine: string | null;
  launch_args: string[];
  notes: string | null;
  user_data_dir: string;
  created_at: string;
  updated_at: string;
  tags: { tag: string; color: string | null }[];
  status: "running" | "stopped";
  vnc_ws_port: number | null;
  cdp_url: string | null;
}

export interface ProfileCreateData {
  name: string;
  sandbox_id?: string;
  project_id?: string;
  folder_path?: string;
  pinned?: boolean;
  accent_color?: string | null;
  harness?: ProfileHarness;
  fingerprint_seed?: number | null;
  proxy?: string | null;
  timezone?: string | null;
  locale?: string | null;
  platform?: string;
  user_agent?: string | null;
  screen_width?: number;
  screen_height?: number;
  gpu_vendor?: string | null;
  gpu_renderer?: string | null;
  hardware_concurrency?: number | null;
  humanize?: boolean;
  human_preset?: string;
  headless?: boolean;
  geoip?: boolean;
  clipboard_sync?: boolean;
  auto_launch?: boolean;
  color_scheme?: string | null;
  search_engine?: string | null;
  launch_args?: string[];
  notes?: string | null;
  tags?: { tag: string; color: string | null }[];
}

export interface LaunchResult {
  profile_id: string;
  status: string;
  vnc_ws_port: number;
  display: string;
  cdp_url: string | null;
}

export interface SystemStatus {
  running_count: number;
  binary_version: string;
  profiles_total: number;
}

export type ProfileHealthState = "pending" | "running" | "passed" | "warning" | "failed" | "unavailable";
export type ProfileHealthSourceState = "missing" | "measured" | "derived" | "unavailable" | "skipped";

export interface ProfileHealth {
  profile_id: string;
  state: ProfileHealthState;
  checked_at: string | null;
  proxy_configured: boolean;
  proxy_reachable: boolean | null;
  outbound_ip_masked: string | null;
  proxy_latency_ms: number | null;
  proxy_risk_score: number | null;
  proxy_authenticity_score: number | null;
  fingerprint_consistency_score: number | null;
  browser_scan_score: number | null;
  warnings: string[];
  blockers: string[];
  error_code: string | null;
  sources: Record<string, ProfileHealthSourceState>;
}

export interface BenchmarkMetricSet {
  p50_latency_ms?: number | null;
  p95_latency_ms?: number | null;
  median_latency_ms?: number | null;
  avg_latency_ms?: number | null;
  availability_pct?: number | null;
  success_rate_pct?: number | null;
  samples?: number | null;
  [key: string]: string | number | boolean | null | undefined;
}

export interface BenchmarkTimingRollup {
  min?: number | null;
  median?: number | null;
  p95?: number | null;
  max?: number | null;
}

export interface BenchmarkCandidate {
  name: string;
  version?: string | null;
  technology?: string | null;
  measured?: boolean | null;
  state?: "measured" | "not_measured" | "running" | "failed" | string | null;
  not_measured_reason?: string | null;
  missing_reason?: string | null;
  metrics?: BenchmarkMetricSet | null;
}

export interface BenchmarkRunnerCandidate {
  id?: string | null;
  name: string;
  type?: string | null;
  metadata?: Record<string, string | number | boolean | null | undefined> | null;
}

export interface BenchmarkRunnerResult {
  candidate: BenchmarkRunnerCandidate;
  status: "measured" | "not_installed" | "architecture_only" | string;
  availability?: "available" | "unavailable" | "error" | "not_measured" | string | null;
  summary?: {
    runs?: number | null;
    success_rate_pct?: number | null;
    timings_ms?: Record<string, BenchmarkTimingRollup> | null;
    [key: string]: Record<string, BenchmarkTimingRollup> | string | number | boolean | null | undefined;
  } | null;
  reason?: string | null;
}

export interface BenchmarkRun {
  id?: string | null;
  state?: "queued" | "running" | "complete" | "failed" | string | null;
  started_at?: string | null;
  finished_at?: string | null;
  generated_at?: string | null;
}

export interface BenchmarkReport {
  run?: BenchmarkRun | null;
  started_at?: string | null;
  finished_at?: string | null;
  report_url?: string | null;
  generated_at?: string | null;
  results?: BenchmarkRunnerResult[];
  candidates?: BenchmarkCandidate[];
  expected_technologies?: Array<string | { name: string; version?: string | null }>;
  notes?: string | null;
}

export type AccessPermission = "view" | "interact" | "operate" | "automate";
export type AccessRole = "admin" | "operator" | "viewer";
export type AccessIdentityKind = "bootstrap" | "user" | "agent" | "anonymous";

export interface AccessGrant {
  sandbox_id: string;
  permission: AccessPermission;
}

export interface AccessIdentity {
  kind: AccessIdentityKind;
  id: string | null;
  display_name: string;
  role: string;
  grants: AccessGrant[];
}

export interface AuthStatus {
  auth_required: boolean;
  access_control_enabled: boolean;
  authenticated: boolean;
  identity: AccessIdentity | null;
}

function normalizeAuthStatus(status: Partial<AuthStatus>): AuthStatus {
  const authRequired = Boolean(status.auth_required);

  // Older manager backends reported only `auth_required` and
  // `authenticated`. Keep a freshly deployed frontend usable against that
  // open, legacy contract instead of accidentally rendering every local
  // owner as a read-only scoped viewer until the backend is restarted.
  const identity = status.identity ?? (
    !authRequired
      ? {
          kind: "anonymous" as const,
          id: null,
          display_name: "Local legacy access",
          role: "admin",
          grants: [],
        }
      : null
  );

  return {
    auth_required: authRequired,
    access_control_enabled: Boolean(status.access_control_enabled),
    authenticated: Boolean(status.authenticated),
    identity,
  };
}

export interface AccessUser {
  id: string;
  username: string;
  role: AccessRole;
  active: boolean;
  created_at: string;
  grants: AccessGrant[];
  group_ids: string[];
  effective_grants: AccessGrant[];
}

export interface AccessGroup {
  id: string;
  name: string;
  description: string | null;
  active: boolean;
  created_at: string;
  member_user_ids: string[];
  grants: AccessGrant[];
}

export interface AccessAgent {
  id: string;
  display_name: string;
  paperclip_agent_id: string | null;
  active: boolean;
  created_at: string;
  grants: AccessGrant[];
}

export interface AccessAgentCreated extends AccessAgent {
  api_key: string;
}

export interface TaskHarnessSession {
  id: string;
  profile_id: string;
  sandbox_id: string;
  title: string | null;
  status: "active" | "archived";
  created_by_kind: string;
  created_by_id: string | null;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface TaskHarnessMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  created_by_kind: string;
  created_by_id: string | null;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface TaskHarnessEvent {
  id: string;
  session_id: string;
  type: string;
  created_by_kind: string;
  created_by_id: string | null;
  created_at: string;
  payload: Record<string, unknown>;
}

export interface AccessSandbox {
  sandbox_id: string;
  profile_count: number;
  project_ids: string[];
  folder_paths: string[];
  profile_names: string[];
}

export type LoginCredentials =
  | { token: string }
  | { username: string; password: string };

const viteEnv = (import.meta as ImportMeta & {
  env?: Record<string, string | undefined>;
}).env;

export const DEFAULT_BENCHMARK_REPORT_URL =
  viteEnv?.VITE_BENCHMARK_REPORT_URL || "/api/benchmarks/latest";

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

// Global 401 callback — set by App to trigger login page on auth failure
let _onUnauthorized: (() => void) | null = null;
export function setOnUnauthorized(cb: (() => void) | null) {
  _onUnauthorized = cb;
}

async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    if (res.status === 401 && _onUnauthorized) {
      _onUnauthorized();
      throw new ApiError(401, "Unauthorized");
    }
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail || res.statusText);
  }
  return res.json();
}

export const api = {
  authStatus: async () => normalizeAuthStatus(await request<Partial<AuthStatus>>("/api/auth/status")),

  login: (credentials: LoginCredentials | string) =>
    request<{ ok: boolean }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(typeof credentials === "string" ? { token: credentials } : credentials),
    }),

  logout: () =>
    request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),

  listProfiles: () => request<Profile[]>("/api/profiles"),

  getProfile: (id: string) => request<Profile>(`/api/profiles/${id}`),

  createProfile: (data: ProfileCreateData) =>
    request<Profile>("/api/profiles", {
      method: "POST",
      body: JSON.stringify(data),
    }),

    bulkOrganizeProfiles: (data: {
    profile_ids: string[];
    project_id?: string | null;
    folder_path?: string | null;
    pinned?: boolean | null;
  }) =>
    request<Profile[]>("/api/profiles/bulk-organize", {
      method: "POST",
      body: JSON.stringify(data),
    }),

updateProfile: (id: string, data: Partial<ProfileCreateData>) =>
    request<Profile>(`/api/profiles/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  deleteProfile: (id: string) =>
    request<{ ok: boolean }>(`/api/profiles/${id}`, { method: "DELETE" }),

  launchProfile: (id: string) =>
    request<LaunchResult>(`/api/profiles/${id}/launch`, { method: "POST" }),

  stopProfile: (id: string) =>
    request<{ ok: boolean }>(`/api/profiles/${id}/stop`, { method: "POST" }),

  getProfileHealth: (id: string) =>
    request<ProfileHealth>(`/api/profiles/${encodeURIComponent(id)}/health`),

  runProfileHealth: (id: string) =>
    request<ProfileHealth>(`/api/profiles/${encodeURIComponent(id)}/health/run`, { method: "POST" }),

  getStatus: () => request<SystemStatus>("/api/status"),

  getBenchmarkReport: (url = DEFAULT_BENCHMARK_REPORT_URL) =>
    request<BenchmarkReport>(url),

  setClipboard: (id: string, text: string) =>
    request<{ ok: boolean }>(`/api/profiles/${id}/clipboard`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  getClipboard: (id: string) =>
    request<{ text: string }>(`/api/profiles/${id}/clipboard`),

  getAccessMe: () => request<AccessIdentity>("/api/access/me"),

  listAccessUsers: () => request<AccessUser[]>("/api/access/users"),

  createAccessUser: (data: {
    username: string;
    password: string;
    role: AccessRole;
    grants: AccessGrant[];
    group_ids?: string[];
  }) =>
    request<AccessUser>("/api/access/users", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateAccessUser: (id: string, data: Partial<{
    password: string;
    role: AccessRole;
    active: boolean;
    grants: AccessGrant[];
    group_ids: string[];
  }>) =>
    request<AccessUser>(`/api/access/users/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  listAccessGroups: () => request<AccessGroup[]>("/api/access/groups"),

  createAccessGroup: (data: {
    name: string;
    description: string | null;
    active: boolean;
    member_user_ids: string[];
    grants: AccessGrant[];
  }) =>
    request<AccessGroup>("/api/access/groups", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateAccessGroup: (id: string, data: Partial<{
    name: string;
    description: string | null;
    active: boolean;
    member_user_ids: string[];
    grants: AccessGrant[];
  }>) =>
    request<AccessGroup>(`/api/access/groups/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  listAccessAgents: () => request<AccessAgent[]>("/api/access/agents"),

  createAccessAgent: (data: {
    display_name: string;
    paperclip_agent_id?: string | null;
    grants: AccessGrant[];
  }) =>
    request<AccessAgentCreated>("/api/access/agents", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateAccessAgent: (id: string, data: Partial<{
    display_name: string;
    paperclip_agent_id: string | null;
    active: boolean;
    grants: AccessGrant[];
  }>) =>
    request<AccessAgent>(`/api/access/agents/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  rotateAccessAgentKey: (id: string) =>
    request<AccessAgentCreated>(`/api/access/agents/${id}/rotate-key`, {
      method: "POST",
    }),

  createTaskSession: (data: {
    profile_id: string;
    title?: string | null;
    metadata?: Record<string, unknown>;
  }, options?: { signal?: AbortSignal }) => request<TaskHarnessSession>("/api/task-sessions", {
    method: "POST",
    signal: options?.signal,
    body: JSON.stringify(data),
  }),

  listTaskSessions: (
    profileId: string,
    options?: { limit?: number; signal?: AbortSignal },
  ) => request<TaskHarnessSession[]>(
    `/api/task-sessions?profile_id=${encodeURIComponent(profileId)}${
      options?.limit ? `&limit=${encodeURIComponent(String(options.limit))}` : ""
    }`,
    { signal: options?.signal },
  ),

  getTaskSession: (sessionId: string, options?: { signal?: AbortSignal }) =>
    request<TaskHarnessSession>(
      `/api/task-sessions/${encodeURIComponent(sessionId)}`,
      { signal: options?.signal },
    ),

  appendTaskMessage: (sessionId: string, data: {
    text: string;
    profile_id?: string | null;
    commands?: ReadonlyArray<
      {
        id: string;
        label: string;
        kind: string;
        scope: string;
        args?: Record<string, string | number | boolean | null>;
      }
    >;
    metadata?: Record<string, unknown>;
  }, options?: { signal?: AbortSignal }) => request<TaskHarnessMessage>(
    `/api/task-sessions/${encodeURIComponent(sessionId)}/messages`,
    {
      method: "POST",
      signal: options?.signal,
      body: JSON.stringify(data),
    },
  ),

  listTaskSessionMessages: (
    sessionId: string,
    options?: { limit?: number; signal?: AbortSignal },
  ) => request<TaskHarnessMessage[]>(
    `/api/task-sessions/${encodeURIComponent(sessionId)}/messages${
      options?.limit ? `?limit=${encodeURIComponent(String(options.limit))}` : ""
    }`,
    { signal: options?.signal },
  ),

  listTaskSessionEvents: (
    sessionId: string,
    options?: { limit?: number; signal?: AbortSignal },
  ) => request<TaskHarnessEvent[]>(
    `/api/task-sessions/${encodeURIComponent(sessionId)}/events${
      options?.limit ? `?limit=${encodeURIComponent(String(options.limit))}` : ""
    }`,
    { signal: options?.signal },
  ),

  listAccessSandboxes: () => request<AccessSandbox[]>("/api/access/sandboxes"),
};
