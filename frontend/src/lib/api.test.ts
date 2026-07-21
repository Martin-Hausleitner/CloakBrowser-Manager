import { describe, it, expect, vi, beforeEach } from "vitest";
import { api } from "./api";

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
      title: null,
      status: "active" as const,
      created_by_kind: "user",
      created_by_id: "owner",
      created_at: "2026-07-21T10:00:00.000Z",
      updated_at: "2026-07-21T10:00:00.000Z",
      metadata: { source: "test" },
    };
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
      title: "Run",
      status: "active" as const,
      created_by_kind: "user",
      created_by_id: "owner",
      created_at: "2026-07-21T10:00:00.000Z",
      updated_at: "2026-07-21T10:00:00.000Z",
      metadata: {},
    };
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
