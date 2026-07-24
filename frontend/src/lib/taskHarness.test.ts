import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import {
  createTaskHarness,
  codexComputerUseProvider,
  cloakServerProvider,
  type InjectedTaskHarness,
  type TaskHarnessListener,
  type TaskHarnessMessage,
} from "./taskHarness";

function windowWithHarness(harness?: InjectedTaskHarness): Window {
  return { cloakBrowserHarness: harness } as Window;
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "createTaskSession");
  vi.spyOn(api, "appendTaskMessage");
  vi.spyOn(api, "getTaskSession");
  vi.spyOn(api, "listTaskSessions");
  vi.spyOn(api, "listTaskSessionMessages");
  vi.spyOn(api, "listTaskSessionEvents");
});

describe("createTaskHarness", () => {
  it("uses the server task session API when no host harness is injected", async () => {
    vi.mocked(api.createTaskSession).mockResolvedValue({
      id: "server-session-1",
      profile_id: "profile-1",
      sandbox_id: "default",
      title: null,
      status: "active",
      created_by_kind: "user",
      created_by_id: "owner",
      created_at: "2026-07-21T10:00:00.000Z",
      updated_at: "2026-07-21T10:00:00.000Z",
      metadata: { provider: cloakServerProvider },
    });
    vi.mocked(api.appendTaskMessage).mockResolvedValue({
      id: "server-msg-1",
      session_id: "server-session-1",
      role: "user",
      content: "Open profile",
      created_by_kind: "user",
      created_by_id: "owner",
      created_at: "2026-07-21T10:01:00.000Z",
      metadata: { provider: cloakServerProvider },
    });

    const harness = createTaskHarness(windowWithHarness());

    await expect(harness.capabilities()).resolves.toEqual({
      chat: true,
      streaming: false,
      clipboard: false,
      browser_actions: [],
      metadata: {
        mode: "server",
        provider: cloakServerProvider,
        execution: "persist-user-commands-only",
      },
    });

    const message = await harness.send({
      text: "Open profile",
      profile_id: "profile-1",
      metadata: { source: "test" },
    });

    expect(message).toEqual({
      id: "server-msg-1",
      role: "user",
      content: "Open profile",
      created_at: "2026-07-21T10:01:00.000Z",
      metadata: { provider: cloakServerProvider },
    });
    expect(api.createTaskSession).toHaveBeenCalledWith({
      profile_id: "profile-1",
      metadata: { source: "test" },
    }, { signal: undefined });
    expect(api.appendTaskMessage).toHaveBeenCalledWith("server-session-1", {
      text: "Open profile",
      profile_id: "profile-1",
      commands: [],
      metadata: { source: "test" },
    }, { signal: undefined });
  });

  it("retries server conversation creation after a transient failure", async () => {
    vi.mocked(api.createTaskSession)
      .mockRejectedValueOnce(new Error("temporary outage"))
      .mockResolvedValueOnce({
        id: "server-session-retry",
        profile_id: "profile-1",
        sandbox_id: "default",
        title: null,
        status: "active",
        created_by_kind: "user",
        created_by_id: "owner",
        created_at: "2026-07-21T10:00:00.000Z",
        updated_at: "2026-07-21T10:00:00.000Z",
        metadata: {},
      });
    vi.mocked(api.appendTaskMessage).mockResolvedValue({
      id: "server-msg-retry",
      session_id: "server-session-retry",
      role: "user",
      content: "retry",
      created_by_kind: "user",
      created_by_id: "owner",
      created_at: "2026-07-21T10:01:00.000Z",
      metadata: {},
    });
    const harness = createTaskHarness(windowWithHarness());

    await expect(harness.send({ text: "first", profile_id: "profile-1" }))
      .rejects.toThrow("temporary outage");
    await expect(harness.send({ text: "retry", profile_id: "profile-1" }))
      .resolves.toMatchObject({ role: "user", content: "retry" });

    expect(api.createTaskSession).toHaveBeenCalledTimes(2);
  });

  it("uses a verified injected Codex host when available and preserves host scope", async () => {
    const send = vi.fn().mockResolvedValue({
      id: "host-1",
      role: "assistant",
      content: "done",
      created_at: "2026-07-21T10:00:00.000Z",
      metadata: { provider: codexComputerUseProvider },
    } as TaskHarnessMessage);

    const harness = createTaskHarness(windowWithHarness({
      capabilities: {
        chat: true,
        streaming: true,
        clipboard: true,
        browser_actions: ["paste", "copy", "fullscreen"],
        metadata: { provider: codexComputerUseProvider, mode: "test" },
      },
      send,
    }));

    await expect(harness.capabilities()).resolves.toEqual({
      chat: true,
      streaming: true,
      clipboard: true,
      browser_actions: ["paste", "copy", "fullscreen"],
      metadata: { provider: codexComputerUseProvider, mode: "test" },
    });

    await expect(
      harness.send({
        text: "go",
        commands: [
          {
            id: "focus-remote",
            label: "Focus",
            kind: "focus_remote",
            scope: "host",
          },
        ],
      }),
    ).resolves.toEqual({
      id: "host-1",
      role: "assistant",
      content: "done",
      created_at: "2026-07-21T10:00:00.000Z",
      metadata: { provider: codexComputerUseProvider },
    });

    expect(send).toHaveBeenCalledOnce();
    expect(api.createTaskSession).not.toHaveBeenCalled();
    expect(api.appendTaskMessage).not.toHaveBeenCalled();
  });

  it("falls back to server when injected host metadata is not verified", async () => {
    vi.mocked(api.createTaskSession).mockResolvedValue({
      id: "server-session-1",
      profile_id: "profile-1",
      sandbox_id: "default",
      title: null,
      status: "active",
      created_by_kind: "user",
      created_by_id: "owner",
      created_at: "2026-07-21T10:00:00.000Z",
      updated_at: "2026-07-21T10:00:00.000Z",
      metadata: { provider: cloakServerProvider },
    });
    vi.mocked(api.appendTaskMessage).mockResolvedValue({
      id: "server-msg-1",
      session_id: "server-session-1",
      role: "user",
      content: "go",
      created_by_kind: "user",
      created_by_id: "owner",
      created_at: "2026-07-21T10:02:00.000Z",
      metadata: { provider: cloakServerProvider },
    });

    const harness = createTaskHarness(windowWithHarness({
      capabilities: {
        chat: true,
        streaming: true,
        clipboard: true,
        browser_actions: ["click"],
        metadata: { provider: "generic-test-harness" },
      },
      send: vi.fn(),
    }));

    await expect(harness.capabilities()).resolves.toEqual({
      chat: true,
      streaming: false,
      clipboard: false,
      browser_actions: [],
      metadata: {
        mode: "server",
        provider: cloakServerProvider,
        execution: "persist-user-commands-only",
      },
    });
    await expect(harness.send({ text: "go", profile_id: "profile-1" })).resolves.toMatchObject({
      content: "go",
      role: "user",
    });
    expect(api.appendTaskMessage).toHaveBeenCalledWith("server-session-1", {
      text: "go",
      profile_id: "profile-1",
      commands: [],
      metadata: undefined,
    }, { signal: undefined });
  });

  it("falls back to server when an injected object cannot send", async () => {
    vi.mocked(api.createTaskSession).mockResolvedValue({
      id: "server-session-1",
      profile_id: "profile-1",
      sandbox_id: "default",
      title: null,
      status: "active",
      created_by_kind: "user",
      created_by_id: "owner",
      created_at: "2026-07-21T10:00:00.000Z",
      updated_at: "2026-07-21T10:00:00.000Z",
      metadata: {},
    });
    vi.mocked(api.appendTaskMessage).mockResolvedValue({
      id: "server-msg-1",
      session_id: "server-session-1",
      role: "user",
      content: "run",
      created_by_kind: "user",
      created_by_id: "owner",
      created_at: "2026-07-21T10:02:00.000Z",
      metadata: {},
    });

    const harness = createTaskHarness(windowWithHarness({ capabilities: { chat: false } }));

    await expect(harness.capabilities()).resolves.toEqual({
      chat: true,
      streaming: false,
      clipboard: false,
      browser_actions: [],
      metadata: {
        mode: "server",
        provider: cloakServerProvider,
        execution: "persist-user-commands-only",
      },
    });
    await expect(harness.send({ text: "run", profile_id: "profile-1" })).resolves.toEqual({
      id: "server-msg-1",
      role: "user",
      content: "run",
      created_at: "2026-07-21T10:02:00.000Z",
      metadata: {},
    });
  });

  it("loads persistent server conversations, messages, and events", async () => {
    vi.mocked(api.listTaskSessions).mockResolvedValue([
      {
        id: "server-session-1",
        profile_id: "profile-1",
        sandbox_id: "sandbox-a",
        title: "Research",
        status: "active",
        created_by_kind: "user",
        created_by_id: "owner",
        created_at: "2026-07-21T10:00:00.000Z",
        updated_at: "2026-07-21T10:05:00.000Z",
        metadata: { provider: cloakServerProvider },
      },
    ]);
    vi.mocked(api.getTaskSession).mockResolvedValue({
      id: "server-session-1",
      profile_id: "profile-1",
      sandbox_id: "sandbox-a",
      title: "Research",
      status: "active",
      created_by_kind: "user",
      created_by_id: "owner",
      created_at: "2026-07-21T10:00:00.000Z",
      updated_at: "2026-07-21T10:05:00.000Z",
      metadata: { provider: cloakServerProvider },
    });
    vi.mocked(api.listTaskSessionMessages).mockResolvedValue([
      {
        id: "server-msg-1",
        session_id: "server-session-1",
        role: "user",
        content: "Open profile",
        created_by_kind: "user",
        created_by_id: "owner",
        created_at: "2026-07-21T10:01:00.000Z",
        metadata: {},
      },
    ]);
    vi.mocked(api.listTaskSessionEvents).mockResolvedValue([
      {
        id: "event-1",
        session_id: "server-session-1",
        type: "task_command.appended",
        created_by_kind: "user",
        created_by_id: "owner",
        created_at: "2026-07-21T10:01:00.000Z",
        payload: { message_id: "server-msg-1" },
      },
    ]);

    const harness = createTaskHarness(windowWithHarness());

    await expect(harness.listConversations("profile-1")).resolves.toEqual([
      {
        id: "server-session-1",
        profile_id: "profile-1",
        sandbox_id: "sandbox-a",
        title: "Research",
        status: "active",
        created_at: "2026-07-21T10:00:00.000Z",
        updated_at: "2026-07-21T10:05:00.000Z",
        metadata: { provider: cloakServerProvider },
      },
    ]);
    await expect(harness.selectConversation("server-session-1")).resolves.toMatchObject({
      id: "server-session-1",
      title: "Research",
    });
    await expect(harness.listMessages("server-session-1")).resolves.toEqual([
      {
        id: "server-msg-1",
        role: "user",
        content: "Open profile",
        created_at: "2026-07-21T10:01:00.000Z",
        metadata: {},
      },
    ]);
    await expect(harness.listEvents("server-session-1")).resolves.toEqual([
      {
        id: "event-1",
        session_id: "server-session-1",
        type: "task_command.appended",
        created_at: "2026-07-21T10:01:00.000Z",
        payload: { message_id: "server-msg-1" },
      },
    ]);
  });

  it("uses verified injected conversation history before server history", async () => {
    const hostHarness: InjectedTaskHarness = {
      capabilities: {
        chat: true,
        streaming: true,
        clipboard: true,
        browser_actions: [],
        metadata: { provider: codexComputerUseProvider },
      },
      send: vi.fn(),
      listConversations: vi.fn().mockResolvedValue([
        {
          id: "host-session-1",
          profile_id: "profile-1",
          sandbox_id: "sandbox-a",
          title: "Host",
          status: "active",
          created_at: "2026-07-21T10:00:00.000Z",
          updated_at: "2026-07-21T10:00:00.000Z",
        },
      ]),
      listMessages: vi.fn().mockResolvedValue([
        {
          id: "host-msg-1",
          role: "assistant",
          content: "host history",
          created_at: "2026-07-21T10:00:00.000Z",
        },
      ]),
    };

    const harness = createTaskHarness(windowWithHarness(hostHarness));

    await expect(harness.listConversations("profile-1")).resolves.toMatchObject([
      { id: "host-session-1", title: "Host" },
    ]);
    await expect(harness.listMessages("host-session-1")).resolves.toMatchObject([
      { id: "host-msg-1", content: "host history" },
    ]);
    expect(api.listTaskSessions).not.toHaveBeenCalled();
    expect(api.listTaskSessionMessages).not.toHaveBeenCalled();
  });

  it("rejects server fallback sends without a profile id instead of creating an invalid session", async () => {
    const harness = createTaskHarness(windowWithHarness());

    await expect(harness.send({ text: "run" })).rejects.toThrow(
      "Task harness server fallback requires a profile_id to create a conversation.",
    );
    expect(api.createTaskSession).not.toHaveBeenCalled();
    expect(api.appendTaskMessage).not.toHaveBeenCalled();
  });

  it("normalizes injected subscribe cleanup functions", async () => {
    const hostListener = vi.fn();
    const unsubscribe = vi.fn();
    const hostHarness: InjectedTaskHarness = {
      send: vi.fn(),
      capabilities: {
        chat: true,
        streaming: true,
        clipboard: true,
        browser_actions: [],
        metadata: { provider: codexComputerUseProvider },
      },
      subscribe: (listener: TaskHarnessListener) => {
        hostListener.mockImplementation(listener);
        return { unsubscribe };
      },
    };
    const harness = createTaskHarness(windowWithHarness(hostHarness));
    const appListener = vi.fn();

    const cleanup = harness.subscribe(appListener);
    hostListener({
      id: "host-event",
      role: "tool",
      content: "ready",
      created_at: "2026-07-21T10:01:00.000Z",
    });
    await Promise.resolve();
    cleanup();

    expect(appListener).toHaveBeenCalledWith({
      id: "host-event",
      role: "tool",
      content: "ready",
      created_at: "2026-07-21T10:01:00.000Z",
    });
    expect(unsubscribe).toHaveBeenCalledOnce();
  });

  it("drops unknown browser actions at the injected host boundary", async () => {
    const capabilities = {
      chat: true,
      streaming: false,
      clipboard: false,
      browser_actions: ["screenshot", "delete_everything"],
      metadata: { provider: codexComputerUseProvider },
    } as unknown as {
      chat: boolean;
      streaming: boolean;
      clipboard: boolean;
      browser_actions: string[];
      metadata?: Record<string, unknown>;
    };

    const harness = createTaskHarness(windowWithHarness({
      capabilities,
      send: vi.fn(),
    }));

    await expect(harness.capabilities()).resolves.toMatchObject({
      browser_actions: ["screenshot"],
    });
  });

  it("routes host-scoped commands only through a verified host", async () => {
    vi.mocked(api.createTaskSession).mockResolvedValue({
      id: "server-session-1",
      profile_id: "profile-1",
      sandbox_id: "default",
      title: null,
      status: "active",
      created_by_kind: "user",
      created_by_id: "owner",
      created_at: "2026-07-21T10:00:00.000Z",
      updated_at: "2026-07-21T10:00:00.000Z",
      metadata: { provider: cloakServerProvider },
    });

    const harness = createTaskHarness(windowWithHarness());

    await expect(
      harness.send({
        text: "capture",
        commands: [
          {
            id: "capture-browser",
            label: "Capture",
            kind: "screenshot",
            scope: "host",
          },
        ],
      }),
    ).rejects.toThrow("Task harness request includes host-scoped actions; a verified Codex host is required.");

    const verified = createTaskHarness(windowWithHarness({
      capabilities: {
        chat: true,
        streaming: true,
        clipboard: true,
        browser_actions: ["screenshot"],
        metadata: { provider: codexComputerUseProvider },
      },
      send: vi.fn().mockResolvedValue({
        id: "host-message",
        role: "assistant",
        content: "done",
        created_at: "2026-07-21T10:03:00.000Z",
      }),
    }));

    await expect(
      verified.send({
        text: "capture",
        commands: [
          {
            id: "capture-browser",
            label: "Capture",
            kind: "screenshot",
            scope: "host",
          },
        ],
      }),
    ).resolves.toMatchObject({
      id: "host-message",
    });

    expect(api.createTaskSession).toHaveBeenCalledTimes(0);
    expect(api.appendTaskMessage).toHaveBeenCalledTimes(0);
  });

  it("rejects aborted unavailable requests and does not notify subscribers", async () => {
    const harness = createTaskHarness(windowWithHarness());
    const listener = vi.fn();
    const controller = new AbortController();
    harness.subscribe(listener);
    controller.abort();

    await expect(harness.send({ text: "stop" }, { signal: controller.signal })).rejects.toThrow(
      "Task harness request aborted",
    );
    expect(listener).not.toHaveBeenCalled();
  });
});
