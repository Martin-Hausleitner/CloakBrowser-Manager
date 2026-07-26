import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  api,
  type ProfileCreateData,
  type ProfileHarness,
  type TaskHarnessSession,
  type TaskSessionUpdateData,
} from "./api";

// Mock fetch globally
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

function jsonResponse(data: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: () => Promise.resolve(data),
  };
}

beforeEach(() => {
  mockFetch.mockReset();
});

describe("api.authStatus", () => {
  it("treats a legacy open backend as the local administrator", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ auth_required: false, authenticated: false }));

    await expect(api.authStatus()).resolves.toEqual({
      auth_required: false,
      access_control_enabled: false,
      authenticated: false,
      identity: {
        kind: "anonymous",
        id: null,
        display_name: "Local legacy access",
        role: "admin",
        grants: [],
      },
    });
  });

  it("preserves an explicit scoped identity from a current backend", async () => {
    const identity = {
      kind: "user" as const,
      id: "user-1",
      display_name: "Viewer",
      role: "viewer",
      grants: [{ sandbox_id: "research", permission: "view" as const }],
    };
    mockFetch.mockResolvedValueOnce(jsonResponse({
      auth_required: true,
      access_control_enabled: true,
      authenticated: true,
      identity,
    }));

    await expect(api.authStatus()).resolves.toEqual({
      auth_required: true,
      access_control_enabled: true,
      authenticated: true,
      identity,
    });
  });
});

// ── listProfiles ────────────────────────────────────────────────────────────

describe("api.listProfiles", () => {
  it("returns profile array on success", async () => {
    const profiles = [{ id: "1", name: "Test" }];
    mockFetch.mockResolvedValueOnce(jsonResponse(profiles));
    const result = await api.listProfiles();
    expect(result).toEqual(profiles);
    expect(mockFetch).toHaveBeenCalledWith("/api/profiles", {
      headers: { "Content-Type": "application/json" },
    });
  });
});

// ── createProfile ───────────────────────────────────────────────────────────

describe("api.createProfile", () => {
  it("sends POST with JSON body", async () => {
    const profile = { id: "2", name: "New" };
    mockFetch.mockResolvedValueOnce(jsonResponse(profile));
    await api.createProfile({ name: "New" });
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/profiles");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({ name: "New" });
  });

  it("serializes profile organization and preferred harness metadata", async () => {
    const harness: ProfileHarness = "browser-use";
    const payload = {
      name: "Buyer research",
      project_id: "marketplace",
      folder_path: "buyers/us",
      pinned: true,
      accent_color: "#06b6d4",
      harness,
    } satisfies ProfileCreateData;
    mockFetch.mockResolvedValueOnce(jsonResponse({ id: "3", ...payload }));

    await api.createProfile(payload);

    const [, options] = mockFetch.mock.calls[0];
    expect(JSON.parse(options.body)).toEqual(payload);
  });
});

// ── updateProfile ───────────────────────────────────────────────────────────

describe("api.updateProfile", () => {
  it("sends PUT with JSON body", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ id: "1", name: "Updated" }));
    await api.updateProfile("1", { name: "Updated" });
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/profiles/1");
    expect(options.method).toBe("PUT");
  });
});

// ── deleteProfile ───────────────────────────────────────────────────────────

describe("api.deleteProfile", () => {
  it("sends DELETE request", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ ok: true }));
    const result = await api.deleteProfile("1");
    expect(result).toEqual({ ok: true });
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/profiles/1");
    expect(options.method).toBe("DELETE");
  });
});

// ── launchProfile ───────────────────────────────────────────────────────────

describe("api.launchProfile", () => {
  it("sends POST to launch endpoint", async () => {
    const result = { profile_id: "1", status: "running", vnc_ws_port: 6100, display: ":100" };
    mockFetch.mockResolvedValueOnce(jsonResponse(result));
    const data = await api.launchProfile("1");
    expect(data.vnc_ws_port).toBe(6100);
    expect(mockFetch.mock.calls[0][0]).toBe("/api/profiles/1/launch");
  });
});

// ── stopProfile ─────────────────────────────────────────────────────────────

describe("api.stopProfile", () => {
  it("sends POST to stop endpoint", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await api.stopProfile("1");
    expect(mockFetch.mock.calls[0][0]).toBe("/api/profiles/1/stop");
  });
});

describe("profile health API", () => {
  it("fetches the latest redacted health summary", async () => {
    const health = {
      profile_id: "profile/1",
      state: "unavailable",
      checked_at: null,
      proxy_configured: false,
      proxy_reachable: null,
      outbound_ip_masked: null,
      proxy_latency_ms: null,
      proxy_risk_score: null,
      proxy_authenticity_score: null,
      fingerprint_consistency_score: null,
      browser_scan_score: null,
      warnings: [],
      blockers: [],
      error_code: null,
      sources: {},
    };
    mockFetch.mockResolvedValueOnce(jsonResponse(health));

    await expect(api.getProfileHealth("profile/1")).resolves.toEqual(health);
    expect(mockFetch).toHaveBeenCalledWith("/api/profiles/profile%2F1/health", {
      headers: { "Content-Type": "application/json" },
    });
  });

  it("requests an asynchronous health rerun without a client supplied target", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ profile_id: "profile-1", state: "pending" }, 202));

    await api.runProfileHealth("profile-1");

    expect(mockFetch).toHaveBeenCalledWith("/api/profiles/profile-1/health/run", {
      headers: { "Content-Type": "application/json" },
      method: "POST",
    });
  });
});

// ── setClipboard ────────────────────────────────────────────────────────────

describe("api.setClipboard", () => {
  it("sends POST with text body", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await api.setClipboard("1", "hello");
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/profiles/1/clipboard");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({ text: "hello" });
  });
});

// ── getClipboard ────────────────────────────────────────────────────────────

describe("api.getClipboard", () => {
  it("returns clipboard text", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ text: "copied" }));
    const result = await api.getClipboard("1");
    expect(result.text).toBe("copied");
  });
});

describe("api.createTaskSession", () => {
  it("creates a task session with the configured provider payload", async () => {
    const session = {
      id: "server-session-1",
      profile_id: "1",
      sandbox_id: "default",
      project_id: "default",
      title: null,
      status: "active" as const,
      workflow_state: "open" as const,
      done_at: null,
      archived_at: null,
      retention_class: "project" as const,
      expires_at: null,
      activity_at: "2026-07-21T10:00:00.000Z",
      row_version: 1,
      created_by_kind: "user",
      created_by_id: "owner",
      created_at: "2026-07-21T10:00:00.000Z",
      updated_at: "2026-07-21T10:00:00.000Z",
      metadata: { source: "test" },
    } satisfies TaskHarnessSession;
    mockFetch.mockResolvedValueOnce(jsonResponse(session));

    const result = await api.createTaskSession({
      profile_id: "1",
      metadata: { source: "test" },
    });

    expect(mockFetch).toHaveBeenCalledWith("/api/task-sessions", {
      headers: { "Content-Type": "application/json" },
      method: "POST",
      signal: undefined,
      body: JSON.stringify({
        profile_id: "1",
        metadata: { source: "test" },
      }),
    });
    expect(result).toEqual(session);
  });
});

describe("api.listTaskSessions", () => {
  it("lists task sessions for a profile", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse([]));

    const sessions = await api.listTaskSessions("profile 1", { limit: 25 });

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/task-sessions?profile_id=profile%201&limit=25",
      {
        headers: { "Content-Type": "application/json" },
        signal: undefined,
      },
    );
    expect(sessions).toEqual([]);
  });
});

describe("api.getTaskSession", () => {
  it("fetches one task session", async () => {
    const session = {
      id: "server-session-1",
      profile_id: "1",
      sandbox_id: "default",
      project_id: "default",
      title: "Run",
      status: "active" as const,
      workflow_state: "open" as const,
      done_at: null,
      archived_at: null,
      retention_class: "project" as const,
      expires_at: null,
      activity_at: "2026-07-21T10:00:00.000Z",
      row_version: 1,
      created_by_kind: "user",
      created_by_id: "owner",
      created_at: "2026-07-21T10:00:00.000Z",
      updated_at: "2026-07-21T10:00:00.000Z",
      metadata: {},
    } satisfies TaskHarnessSession;
    mockFetch.mockResolvedValueOnce(jsonResponse(session));

    const result = await api.getTaskSession("server/session");

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/task-sessions/server%2Fsession",
      {
        headers: { "Content-Type": "application/json" },
        signal: undefined,
      },
    );
    expect(result).toEqual(session);
  });
});

describe("api.updateTaskSession", () => {
  it("patches lifecycle fields with the optimistic row version", async () => {
    const updated = {
      id: "server-session-1",
      profile_id: "1",
      sandbox_id: "default",
      project_id: "default",
      title: "Temp chat",
      status: "archived" as const,
      workflow_state: "done" as const,
      done_at: "2026-07-21T10:05:00.000Z",
      archived_at: "2026-07-21T10:06:00.000Z",
      retention_class: "temporary" as const,
      expires_at: "2026-07-28T10:06:00.000Z",
      activity_at: "2026-07-21T10:06:00.000Z",
      row_version: 3,
      created_by_kind: "user",
      created_by_id: "owner",
      created_at: "2026-07-21T10:00:00.000Z",
      updated_at: "2026-07-21T10:06:00.000Z",
      metadata: { source: "test" },
    } satisfies TaskHarnessSession;
    const payload = {
      row_version: 2,
      title: "Temp chat",
      workflow_state: "done",
      archived: true,
      retention_class: "temporary",
      metadata: { source: "test" },
    } satisfies TaskSessionUpdateData;
    mockFetch.mockResolvedValueOnce(jsonResponse(updated));

    const result = await api.updateTaskSession("server/session", payload);

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/task-sessions/server%2Fsession",
      {
        headers: { "Content-Type": "application/json" },
        method: "PATCH",
        signal: undefined,
        body: JSON.stringify(payload),
      },
    );
    expect(result).toEqual(updated);
  });
});

describe("api.appendTaskMessage", () => {
  it("posts task messages to the active session and returns the persisted user message", async () => {
    const reply = {
      id: "server-msg-1",
      session_id: "server-session-1",
      role: "user" as const,
      content: "Run task",
      created_by_kind: "user",
      created_by_id: "owner",
      created_at: "2026-07-21T10:00:00.000Z",
      metadata: {},
    };
    mockFetch.mockResolvedValueOnce(jsonResponse(reply));

    const message = await api.appendTaskMessage("server-session-1", {
      text: "Run task",
      profile_id: "1",
      commands: [],
    });

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/task-sessions/server-session-1/messages",
      {
        headers: { "Content-Type": "application/json" },
        method: "POST",
        signal: undefined,
        body: JSON.stringify({
          text: "Run task",
          profile_id: "1",
          commands: [],
        }),
      },
    );
    expect(message).toEqual(reply);
  });
});

describe("api.listTaskSessionMessages", () => {
  it("fetches server message history for a task session", async () => {
    const reply = [
      {
        id: "server-msg-1",
        session_id: "server-session-1",
        role: "assistant" as const,
        content: "started",
        created_by_kind: "user",
        created_by_id: "owner",
        created_at: "2026-07-21T10:00:00.000Z",
        metadata: {},
      },
    ];
    mockFetch.mockResolvedValueOnce(jsonResponse(reply));

    const messages = await api.listTaskSessionMessages("server-session-1");

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/task-sessions/server-session-1/messages",
      {
        headers: { "Content-Type": "application/json" },
        signal: undefined,
      },
    );
    expect(messages).toEqual(reply);
  });
});

describe("api.listTaskSessionEvents", () => {
  it("fetches task events for a session", async () => {
    const reply = [
      {
        id: "event-1",
        session_id: "server-session-1",
        type: "task_command.appended",
        created_by_kind: "user",
        created_by_id: "owner",
        created_at: "2026-07-21T10:00:00.000Z",
        payload: { message_id: "server-msg-1" },
      },
    ];
    mockFetch.mockResolvedValueOnce(jsonResponse(reply));

    const events = await api.listTaskSessionEvents("server-session-1", { limit: 10 });

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/task-sessions/server-session-1/events?limit=10",
      {
        headers: { "Content-Type": "application/json" },
        signal: undefined,
      },
    );
    expect(events).toEqual(reply);
  });
});

describe("api.getBenchmarkReport", () => {
  it("fetches a benchmark report from the configured URL", async () => {
    const report = { run: { state: "complete" }, candidates: [] };
    mockFetch.mockResolvedValueOnce(jsonResponse(report));

    await expect(api.getBenchmarkReport("/reports/latest.json")).resolves.toEqual(report);
    expect(mockFetch).toHaveBeenCalledWith("/reports/latest.json", {
      headers: { "Content-Type": "application/json" },
    });
  });

  it("does not use an unauthenticated static fallback after a missing report", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ detail: "No benchmark report" }, 404));

    await expect(api.getBenchmarkReport("/api/benchmarks/latest")).rejects.toThrow("No benchmark report");
    expect(mockFetch.mock.calls.map(([url]) => url)).toEqual(["/api/benchmarks/latest"]);
  });
});

describe("api orca sessions", () => {
  it("loads capabilities and starts a session with allowlisted agent", async () => {
    mockFetch
      .mockResolvedValueOnce(
        jsonResponse({
          available: true,
          orca_bin: "/home/coder/.local/bin/orca-ide",
          agents: ["cursor-agent", "grok", "codex"],
          operations: ["terminal.create"],
          actions: {
            start: true,
            read: true,
            send: true,
            close: true,
            pause: false,
            resume: false,
          },
          notes: [],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          {
            id: "orca_1",
            profile_id: "p1",
            sandbox_id: "default",
            agent: "codex",
            terminal_handle: "term_1",
            status: "running",
            created_at: 1,
            capabilities: {
              start: true,
              read: true,
              send: true,
              close: true,
              pause: false,
              resume: false,
            },
            connection: {},
          },
          201,
        ),
      );

    await expect(api.getOrcaCapabilities()).resolves.toMatchObject({
      available: true,
      actions: { pause: false, resume: false },
    });
    await expect(
      api.startOrcaSession({ profile_id: "p1", agent: "codex", prompt: "go" }),
    ).resolves.toMatchObject({ id: "orca_1", agent: "codex" });
    expect(mockFetch).toHaveBeenNthCalledWith(
      2,
      "/api/orca/sessions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ profile_id: "p1", agent: "codex", prompt: "go" }),
      }),
    );
  });

  it("reads sends and closes with session paths", async () => {
    mockFetch
      .mockResolvedValueOnce(
        jsonResponse({
          session_id: "orca_1",
          terminal_handle: "term_1",
          cursor: 0,
          next_cursor: 2,
          output: "hi",
          status: "running",
          capabilities: {
            start: true,
            read: true,
            send: true,
            close: true,
            pause: false,
            resume: false,
          },
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          session_id: "orca_1",
          ok: true,
          status: "running",
          capabilities: {
            start: true,
            read: true,
            send: true,
            close: true,
            pause: false,
            resume: false,
          },
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          id: "orca_1",
          profile_id: "p1",
          sandbox_id: "default",
          agent: "codex",
          terminal_handle: "term_1",
          status: "closed",
          created_at: 1,
          capabilities: {
            start: true,
            read: true,
            send: true,
            close: true,
            pause: false,
            resume: false,
          },
          connection: {},
        }),
      );

    await expect(api.readOrcaSessionOutput("orca_1", { cursor: 0 })).resolves.toMatchObject({
      output: "hi",
      next_cursor: 2,
    });
    await expect(api.sendOrcaSessionInput("orca_1", { text: "next" })).resolves.toMatchObject({
      ok: true,
    });
    await expect(api.closeOrcaSession("orca_1")).resolves.toMatchObject({ status: "closed" });
    expect(mockFetch.mock.calls.map(([url]) => url)).toEqual([
      "/api/orca/sessions/orca_1/output?cursor=0",
      "/api/orca/sessions/orca_1/send",
      "/api/orca/sessions/orca_1/close",
    ]);
  });
});

describe("api.taskRuns", () => {
  it("creates polls cancels and lists typed outputs for a Browser Use run", async () => {
    const run = {
      id: "run-1",
      task_session_id: "session-1",
      task_message_id: "message-1",
      profile_id: "profile-1",
      profile_id_snapshot: "profile-1",
      sandbox_id: "default",
      harness: "browser-use",
      agent: null,
      status: "queued",
      launch_if_stopped: false,
      allowed_origins: ["https://example.com"],
      max_steps: 20,
      timeout_seconds: 360,
      model_alias: "cursor-grok-4.5-low",
      deadline_at: "2026-07-26T00:06:00Z",
      health_snapshot: {},
      health_decision: {
        allowed: true,
        waiting: false,
        failed_reasons: [],
        non_overridable_reasons: [],
        policy_version: "v1",
      },
      retry_count: 0,
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-26T00:00:00Z",
      updated_at: "2026-07-26T00:00:00Z",
    };
    const outputs = [{
      id: "output-1",
      run_id: "run-1",
      sequence: 1,
      idempotency_key: "action-1",
      kind: "action",
      summary: "Opened example.com",
      payload: { name: "navigate", url: "https://example.com" },
      created_at: "2026-07-26T00:00:01Z",
      artifact_expired: false,
    }];
    mockFetch
      .mockResolvedValueOnce(jsonResponse(run, 201))
      .mockResolvedValueOnce(jsonResponse(run))
      .mockResolvedValueOnce(jsonResponse(outputs))
      .mockResolvedValueOnce(jsonResponse({ ...run, status: "cancelled" }));

    await expect(api.createTaskRun("session-1", {
      harness: "browser-use",
      task: "Open example.com",
      profile_id: "profile-1",
      allowed_origins: ["https://example.com"],
      timeout_seconds: 360,
      model_alias: "cursor-grok-4.5-low",
    })).resolves.toMatchObject({ id: "run-1", status: "queued" });
    await expect(api.getTaskRun("run-1")).resolves.toMatchObject({ id: "run-1" });
    await expect(api.listTaskRunOutputs("run-1", { afterSequence: 0 })).resolves.toEqual(outputs);
    await expect(api.cancelTaskRun("run-1")).resolves.toMatchObject({ status: "cancelled" });
    expect(api.taskOutputScreenshotUrl("output/1")).toBe(
      "/api/task-outputs/output%2F1/screenshot",
    );

    expect(mockFetch.mock.calls.map(([url]) => url)).toEqual([
      "/api/task-sessions/session-1/runs",
      "/api/task-runs/run-1",
      "/api/task-runs/run-1/outputs?after_sequence=0",
      "/api/task-runs/run-1/cancel",
    ]);
  });

  it("serializes ACPX agent selection and reads redacted worker readiness", async () => {
    const run = {
      id: "run-acpx",
      harness: "acpx",
      agent: "cursor",
      status: "queued",
    };
    const presence = {
      harness: "acpx",
      worker_seen_recently: true,
      state: "polling",
      last_seen_at: "2026-07-27T00:00:00Z",
      reason: null,
    };
    mockFetch
      .mockResolvedValueOnce(jsonResponse(run, 201))
      .mockResolvedValueOnce(jsonResponse(presence));

    await api.createTaskRun("session-acpx", {
      harness: "acpx",
      agent: "cursor",
      task: "Inspect https://example.com",
      profile_id: "profile-acpx",
      allowed_origins: ["https://example.com"],
    });
    await expect(api.getTaskHarnessPresence("acpx")).resolves.toEqual(presence);

    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toMatchObject({
      harness: "acpx",
      agent: "cursor",
    });
    expect(mockFetch.mock.calls.map(([url]) => url)).toEqual([
      "/api/task-sessions/session-acpx/runs",
      "/api/task-harnesses/acpx/presence",
    ]);
  });
});

// ── Error handling ──────────────────────────────────────────────────────────

describe("error handling", () => {
  it("throws ApiError with detail on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: () => Promise.resolve({ detail: "Profile not found" }),
    });
    await expect(api.getProfile("bad")).rejects.toThrow("Profile not found");
  });

  it("falls back to statusText when response is not JSON", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      json: () => Promise.reject(new Error("not json")),
    });
    await expect(api.getStatus()).rejects.toThrow("Internal Server Error");
  });
});
