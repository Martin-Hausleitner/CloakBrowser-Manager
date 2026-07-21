import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createTaskHarness,
  type InjectedTaskHarness,
  type TaskHarnessListener,
  type TaskHarnessMessage,
} from "./taskHarness";

function windowWithHarness(harness?: InjectedTaskHarness): Window {
  return { cloakBrowserHarness: harness } as Window;
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("createTaskHarness", () => {
  it("marks the bridge unavailable when no host harness is injected", async () => {
    const harness = createTaskHarness(windowWithHarness());
    const listener = vi.fn();
    const unsubscribe = harness.subscribe(listener);

    await expect(harness.capabilities()).resolves.toEqual({
      chat: false,
      streaming: false,
      clipboard: false,
      browser_actions: [],
      metadata: {
        mode: "unavailable",
        reason: "missing injected host harness",
      },
    });

    await expect(harness.send({ text: "Open docs", profile_id: "profile-1" })).rejects.toThrow(
      "Codex Computer Use Bridge is unavailable: missing injected host harness",
    );
    expect(listener).not.toHaveBeenCalled();

    unsubscribe();
  });

  it("adapts an injected host harness without depending on a vendor API", async () => {
    const sentMessage: TaskHarnessMessage = {
      id: "host-1",
      role: "assistant",
      content: "done",
      created_at: "2026-07-21T10:00:00.000Z",
      metadata: { provider: "test-host" },
    };
    const send = vi.fn().mockResolvedValue(sentMessage);
    const hostHarness: InjectedTaskHarness = {
      capabilities: () => ({
        chat: true,
        streaming: true,
        clipboard: true,
        browser_actions: ["paste", "copy", "fullscreen"],
      }),
      send,
    };
    const harness = createTaskHarness(windowWithHarness(hostHarness));

    await expect(harness.capabilities()).resolves.toEqual({
      chat: true,
      streaming: true,
      clipboard: true,
      browser_actions: ["paste", "copy", "fullscreen"],
      metadata: undefined,
    });

    await expect(harness.send({ text: "go", conversation_id: "c1" })).resolves.toEqual(sentMessage);
    expect(send).toHaveBeenCalledWith({ text: "go", conversation_id: "c1" }, undefined);
  });

  it("normalizes injected subscribe cleanup functions", () => {
    const hostListener = vi.fn();
    const unsubscribe = vi.fn();
    const hostHarness: InjectedTaskHarness = {
      send: vi.fn(),
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
    cleanup();

    expect(appListener).toHaveBeenCalledWith({
      id: "host-event",
      role: "tool",
      content: "ready",
      created_at: "2026-07-21T10:01:00.000Z",
    });
    expect(unsubscribe).toHaveBeenCalledOnce();
  });

  it("marks the bridge unavailable when an injected object cannot send", async () => {
    const harness = createTaskHarness(windowWithHarness({ capabilities: { chat: false } }));

    await expect(harness.capabilities()).resolves.toEqual({
      chat: false,
      streaming: false,
      clipboard: false,
      browser_actions: [],
      metadata: {
        mode: "unavailable",
        reason: "injected host harness is missing send()",
      },
    });
    await expect(harness.send({ text: "" })).rejects.toThrow(
      "Codex Computer Use Bridge is unavailable: injected host harness is missing send()",
    );
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
