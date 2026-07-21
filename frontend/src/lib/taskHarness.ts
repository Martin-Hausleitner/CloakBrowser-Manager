export type TaskHarnessRole = "user" | "assistant" | "system" | "tool";

export interface TaskHarnessMessage {
  id: string;
  role: TaskHarnessRole;
  content: string;
  created_at: string;
  metadata?: Record<string, unknown>;
}

export interface TaskHarnessRequest {
  text: string;
  profile_id?: string | null;
  conversation_id?: string | null;
  metadata?: Record<string, unknown>;
}

export interface TaskHarnessSendOptions {
  signal?: AbortSignal;
}

export interface TaskHarnessCapabilities {
  chat: boolean;
  streaming: boolean;
  clipboard: boolean;
  browser_actions: string[];
  metadata?: Record<string, unknown>;
}

export type TaskHarnessListener = (message: TaskHarnessMessage) => void;

export const taskHarnessReadyEvent = "cloakbrowserharnessready";

export interface TaskHarness {
  capabilities: () => Promise<TaskHarnessCapabilities>;
  send: (
    request: TaskHarnessRequest,
    options?: TaskHarnessSendOptions,
  ) => Promise<TaskHarnessMessage>;
  subscribe: (listener: TaskHarnessListener) => () => void;
}

export type InjectedTaskHarness = Partial<{
  capabilities:
    | TaskHarnessCapabilities
    | (() => TaskHarnessCapabilities | Promise<TaskHarnessCapabilities>);
  send: (
    request: TaskHarnessRequest,
    options?: TaskHarnessSendOptions,
  ) => TaskHarnessMessage | Promise<TaskHarnessMessage>;
  subscribe: (
    listener: TaskHarnessListener,
  ) => void | (() => void) | { unsubscribe: () => void };
}>;

declare global {
  interface Window {
    cloakBrowserHarness?: InjectedTaskHarness;
  }
}

const unavailableCapabilities: TaskHarnessCapabilities = {
  chat: false,
  streaming: false,
  clipboard: false,
  browser_actions: [],
  metadata: { mode: "unavailable" },
};

function cloneCapabilities(capabilities: TaskHarnessCapabilities): TaskHarnessCapabilities {
  return {
    chat: Boolean(capabilities.chat),
    streaming: Boolean(capabilities.streaming),
    clipboard: Boolean(capabilities.clipboard),
    browser_actions: Array.isArray(capabilities.browser_actions)
      ? [...capabilities.browser_actions]
      : [],
    metadata: capabilities.metadata ? { ...capabilities.metadata } : undefined,
  };
}

function ensureNotAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    throw new DOMException("Task harness request aborted", "AbortError");
  }
}

function normalizeMessage(message: TaskHarnessMessage): TaskHarnessMessage {
  return {
    id: String(message.id),
    role: message.role,
    content: String(message.content),
    created_at: String(message.created_at),
    metadata: message.metadata ? { ...message.metadata } : undefined,
  };
}

function normalizeUnsubscribe(
  result: void | (() => void) | { unsubscribe: () => void },
): () => void {
  if (typeof result === "function") {
    return result;
  }
  if (result && typeof result.unsubscribe === "function") {
    return () => result.unsubscribe();
  }
  return () => undefined;
}

function createUnavailableHarness(reason: string): TaskHarness {
  return {
    capabilities: async () => ({
      ...cloneCapabilities(unavailableCapabilities),
      metadata: { mode: "unavailable", reason },
    }),
    send: async (_request, options) => {
      ensureNotAborted(options?.signal);
      throw new Error(`Codex Computer Use Bridge is unavailable: ${reason}`);
    },
    subscribe: () => () => undefined,
  };
}

function createInjectedHarnessAdapter(injected: InjectedTaskHarness): TaskHarness | null {
  if (typeof injected.send !== "function") {
    return null;
  }

  return {
    capabilities: async () => {
      const raw = typeof injected.capabilities === "function"
        ? await injected.capabilities()
        : injected.capabilities;
      return cloneCapabilities(raw ?? {
        chat: true,
        streaming: false,
        clipboard: false,
        browser_actions: [],
        metadata: { mode: "injected" },
      });
    },
    send: async (request, options) => {
      ensureNotAborted(options?.signal);
      const message = await injected.send?.(request, options);
      ensureNotAborted(options?.signal);
      if (!message) {
        throw new Error("Injected task harness returned no message");
      }
      return normalizeMessage(message);
    },
    subscribe: (listener) => normalizeUnsubscribe(injected.subscribe?.(listener)),
  };
}

export function createTaskHarness(targetWindow: Window = window): TaskHarness {
  const injected = targetWindow.cloakBrowserHarness;
  if (!injected) {
    return createUnavailableHarness("missing injected host harness");
  }

  return createInjectedHarnessAdapter(injected)
    ?? createUnavailableHarness("injected host harness is missing send()");
}
