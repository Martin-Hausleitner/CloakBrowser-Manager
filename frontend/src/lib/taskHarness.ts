import { api } from "./api";

export type TaskHarnessRole = "user" | "assistant" | "system" | "tool";

export interface TaskHarnessMessage {
  id: string;
  role: TaskHarnessRole;
  content: string;
  created_at: string;
  metadata?: Record<string, unknown>;
}

export interface TaskHarnessConversation {
  id: string;
  profile_id: string;
  sandbox_id: string;
  title: string | null;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
  metadata?: Record<string, unknown>;
}

export interface TaskHarnessEvent {
  id: string;
  session_id: string;
  type: string;
  created_at: string;
  payload?: Record<string, unknown>;
}

export const taskHarnessActionKinds = [
  "navigate",
  "click",
  "double_click",
  "scroll",
  "type_text",
  "keypress",
  "drag",
  "move",
  "wait",
  "copy",
  "paste",
  "screenshot",
  "viewport",
  "fullscreen",
  "focus_remote",
  "focus_chat",
] as const;

export type TaskHarnessActionKind = (typeof taskHarnessActionKinds)[number];
export type TaskHarnessActionScope = "ui" | "host";

export interface TaskHarnessAction {
  id: string;
  label: string;
  kind: TaskHarnessActionKind;
  scope: TaskHarnessActionScope;
  args?: Record<string, string | number | boolean | null>;
}

export interface TaskHarnessRequest {
  text: string;
  commands?: readonly TaskHarnessAction[];
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
  browser_actions: TaskHarnessActionKind[];
  metadata?: Record<string, unknown>;
}

export type TaskHarnessListener = (message: TaskHarnessMessage) => void;

export const taskHarnessReadyEvent = "cloakbrowserharnessready";
export const codexComputerUseProvider = "codex-computer-use";
export const cloakServerProvider = "cloakhq-server";

export interface TaskHarness {
  capabilities: () => Promise<TaskHarnessCapabilities>;
  listConversations: (
    profileId: string,
    options?: TaskHarnessSendOptions,
  ) => Promise<TaskHarnessConversation[]>;
  selectConversation: (
    conversationId: string,
    options?: TaskHarnessSendOptions,
  ) => Promise<TaskHarnessConversation>;
  listMessages: (
    conversationId: string,
    options?: TaskHarnessSendOptions,
  ) => Promise<TaskHarnessMessage[]>;
  listEvents: (
    conversationId: string,
    options?: TaskHarnessSendOptions,
  ) => Promise<TaskHarnessEvent[]>;
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
  listConversations: (
    profileId: string,
    options?: TaskHarnessSendOptions,
  ) => TaskHarnessConversation[] | Promise<TaskHarnessConversation[]>;
  selectConversation: (
    conversationId: string,
    options?: TaskHarnessSendOptions,
  ) => TaskHarnessConversation | Promise<TaskHarnessConversation>;
  listMessages: (
    conversationId: string,
    options?: TaskHarnessSendOptions,
  ) => TaskHarnessMessage[] | Promise<TaskHarnessMessage[]>;
  listEvents: (
    conversationId: string,
    options?: TaskHarnessSendOptions,
  ) => TaskHarnessEvent[] | Promise<TaskHarnessEvent[]>;
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

const serverCapabilities: TaskHarnessCapabilities = {
  chat: true,
  streaming: false,
  clipboard: false,
  browser_actions: [],
  metadata: {
    mode: "server",
    provider: cloakServerProvider,
    execution: "persist-user-commands-only",
  },
};

function isCodexVerifiedHost(capabilities: TaskHarnessCapabilities): boolean {
  return Boolean(capabilities.chat && capabilities.metadata?.provider === codexComputerUseProvider);
}

const taskHarnessActionKindSet = new Set<string>(taskHarnessActionKinds);

function isTaskHarnessActionKind(value: unknown): value is TaskHarnessActionKind {
  return typeof value === "string" && taskHarnessActionKindSet.has(value);
}

function cloneCapabilities(capabilities: TaskHarnessCapabilities): TaskHarnessCapabilities {
  return {
    chat: Boolean(capabilities.chat),
    streaming: Boolean(capabilities.streaming),
    clipboard: Boolean(capabilities.clipboard),
    browser_actions: Array.isArray(capabilities.browser_actions)
      ? capabilities.browser_actions.filter(isTaskHarnessActionKind)
      : [],
    metadata: capabilities.metadata ? { ...capabilities.metadata } : undefined,
  };
}

function ensureNotAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    throw new DOMException("Task harness request aborted", "AbortError");
  }
}

function isTaskHarnessRole(value: unknown): value is TaskHarnessRole {
  return value === "user" || value === "assistant" || value === "system" || value === "tool";
}

function normalizeMessage(message: TaskHarnessMessage): TaskHarnessMessage {
  return {
    id: String(message.id),
    role: isTaskHarnessRole(message.role) ? message.role : "tool",
    content: String(message.content),
    created_at: String(message.created_at),
    metadata: message.metadata ? { ...message.metadata } : undefined,
  };
}

function normalizeConversation(conversation: TaskHarnessConversation): TaskHarnessConversation {
  return {
    id: String(conversation.id),
    profile_id: String(conversation.profile_id),
    sandbox_id: String(conversation.sandbox_id),
    title: conversation.title === null || conversation.title === undefined
      ? null
      : String(conversation.title),
    status: conversation.status === "archived" ? "archived" : "active",
    created_at: String(conversation.created_at),
    updated_at: String(conversation.updated_at),
    metadata: conversation.metadata ? { ...conversation.metadata } : undefined,
  };
}

function normalizeEvent(event: TaskHarnessEvent): TaskHarnessEvent {
  return {
    id: String(event.id),
    session_id: String(event.session_id),
    type: String(event.type),
    created_at: String(event.created_at),
    payload: event.payload ? { ...event.payload } : undefined,
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

function createInjectedHarnessAdapter(injected: InjectedTaskHarness): TaskHarness | null {
  if (typeof injected.send !== "function") {
    return null;
  }

  let capabilitiesPromise: Promise<TaskHarnessCapabilities> | null = null;
  const readCapabilities = () => {
    if (!capabilitiesPromise) {
      capabilitiesPromise = Promise.resolve(
        typeof injected.capabilities === "function"
          ? injected.capabilities()
          : injected.capabilities,
      ).then((raw) => {
        const capabilities = cloneCapabilities(raw ?? unavailableCapabilities);
        if (capabilities.metadata?.provider !== codexComputerUseProvider) {
          return {
            ...cloneCapabilities(unavailableCapabilities),
            metadata: {
              mode: "unavailable",
              reason: "host bridge is not verified as Codex Computer Use",
            },
          };
        }
        return capabilities;
      });
    }
    return capabilitiesPromise;
  };

  return {
    capabilities: readCapabilities,
    listConversations: async (profileId, options) => {
      ensureNotAborted(options?.signal);
      if (typeof injected.listConversations !== "function") {
        return [];
      }
      const conversations = await injected.listConversations(profileId, options);
      ensureNotAborted(options?.signal);
      return conversations.map(normalizeConversation);
    },
    selectConversation: async (conversationId, options) => {
      ensureNotAborted(options?.signal);
      if (typeof injected.selectConversation !== "function") {
        throw new Error("Injected task harness does not support conversation selection");
      }
      const conversation = await injected.selectConversation(conversationId, options);
      ensureNotAborted(options?.signal);
      return normalizeConversation(conversation);
    },
    listMessages: async (conversationId, options) => {
      ensureNotAborted(options?.signal);
      if (typeof injected.listMessages !== "function") {
        return [];
      }
      const messages = await injected.listMessages(conversationId, options);
      ensureNotAborted(options?.signal);
      return messages.map(normalizeMessage);
    },
    listEvents: async (conversationId, options) => {
      ensureNotAborted(options?.signal);
      if (typeof injected.listEvents !== "function") {
        return [];
      }
      const events = await injected.listEvents(conversationId, options);
      ensureNotAborted(options?.signal);
      return events.map(normalizeEvent);
    },
    send: async (request, options) => {
      ensureNotAborted(options?.signal);
      const capabilities = await readCapabilities();
      if (!capabilities.chat || capabilities.metadata?.provider !== codexComputerUseProvider) {
        throw new Error(
          "Codex Computer Use Bridge is unavailable: host bridge is not verified as Codex Computer Use",
        );
      }
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

function createServerHarnessAdapter(): TaskHarness {
  let sessionIdPromise: Promise<string> | null = null;
  let sessionProfileId: string | null = null;

  const hostCommandCount = (commands?: readonly TaskHarnessAction[]) => {
    if (!Array.isArray(commands) || commands.length === 0) {
      return 0;
    }
    return commands.filter((command) => command.scope === "host").length;
  };

  const ensureSession = async (
    request: TaskHarnessRequest,
    options?: TaskHarnessSendOptions,
  ): Promise<string> => {
    if (request.conversation_id) {
      return request.conversation_id;
    }

    if (!request.profile_id) {
      throw new Error("Task harness server fallback requires a profile_id to create a conversation.");
    }

    if (sessionProfileId !== (request.profile_id ?? null) || !sessionIdPromise) {
      sessionProfileId = request.profile_id ?? null;
      const creation = api
        .createTaskSession({
          profile_id: request.profile_id,
          metadata: request.metadata ? { ...request.metadata } : undefined,
        }, { signal: options?.signal })
        .then((session) => session.id);
      sessionIdPromise = creation;
      creation.catch(() => {
        if (sessionIdPromise === creation) {
          sessionIdPromise = null;
          sessionProfileId = null;
        }
      });
    }

    return sessionIdPromise;
  };

  return {
    capabilities: async () => ({
      ...cloneCapabilities(serverCapabilities),
      metadata: { ...serverCapabilities.metadata },
    }),
    listConversations: async (profileId, options) => {
      ensureNotAborted(options?.signal);
      const conversations = await api.listTaskSessions(profileId, { signal: options?.signal });
      ensureNotAborted(options?.signal);
      return conversations.map((conversation) => normalizeConversation(conversation as TaskHarnessConversation));
    },
    selectConversation: async (conversationId, options) => {
      ensureNotAborted(options?.signal);
      const conversation = await api.getTaskSession(conversationId, { signal: options?.signal });
      ensureNotAborted(options?.signal);
      return normalizeConversation(conversation as TaskHarnessConversation);
    },
    listMessages: async (conversationId, options) => {
      ensureNotAborted(options?.signal);
      const messages = await api.listTaskSessionMessages(conversationId, { signal: options?.signal });
      ensureNotAborted(options?.signal);
      return messages.map((message) => normalizeMessage(message as TaskHarnessMessage));
    },
    listEvents: async (conversationId, options) => {
      ensureNotAborted(options?.signal);
      const events = await api.listTaskSessionEvents(conversationId, { signal: options?.signal });
      ensureNotAborted(options?.signal);
      return events.map((event) => normalizeEvent(event as TaskHarnessEvent));
    },
    send: async (request, options) => {
      ensureNotAborted(options?.signal);

      if (hostCommandCount(request.commands) > 0) {
        throw new Error("Task harness request includes host-scoped actions; a verified Codex host is required.");
      }

      const sessionId = await ensureSession(request, options);
      const message = await api.appendTaskMessage(sessionId, {
        text: request.text,
        profile_id: request.profile_id ?? null,
        commands: request.commands ? request.commands.map((command) => ({
          id: command.id,
          label: command.label,
          kind: command.kind,
          scope: command.scope,
          args: command.args ? { ...command.args } : undefined,
        })) : [],
        metadata: request.metadata ? { ...request.metadata } : undefined,
      }, { signal: options?.signal });

      ensureNotAborted(options?.signal);
      if (!message) {
        throw new Error("Task session message endpoint returned no message");
      }

      return normalizeMessage(message as TaskHarnessMessage);
    },
    subscribe: () => () => undefined,
  };
}

export function createTaskHarness(targetWindow: Window = window): TaskHarness {
  const injected = targetWindow.cloakBrowserHarness;
  const injectedHarness = injected
    ? createInjectedHarnessAdapter(injected)
    : null;
  const serverHarness = createServerHarnessAdapter();

  if (!injectedHarness) {
    return serverHarness;
  }

  return {
    capabilities: async () => {
      const harnessCapabilities = await injectedHarness.capabilities();
      if (isCodexVerifiedHost(harnessCapabilities)) {
        return harnessCapabilities;
      }
      return serverHarness.capabilities();
    },
    listConversations: async (profileId, options) => {
      const harnessCapabilities = await injectedHarness.capabilities();
      if (isCodexVerifiedHost(harnessCapabilities)) {
        const conversations = await injectedHarness.listConversations(profileId, options);
        if (conversations.length > 0) {
          return conversations;
        }
      }
      return serverHarness.listConversations(profileId, options);
    },
    selectConversation: async (conversationId, options) => {
      const harnessCapabilities = await injectedHarness.capabilities();
      if (isCodexVerifiedHost(harnessCapabilities)) {
        try {
          return await injectedHarness.selectConversation(conversationId, options);
        } catch (error) {
          if (!(error instanceof Error) || !error.message.includes("does not support")) {
            throw error;
          }
        }
      }
      return serverHarness.selectConversation(conversationId, options);
    },
    listMessages: async (conversationId, options) => {
      const harnessCapabilities = await injectedHarness.capabilities();
      if (isCodexVerifiedHost(harnessCapabilities)) {
        const messages = await injectedHarness.listMessages(conversationId, options);
        if (messages.length > 0) {
          return messages;
        }
      }
      return serverHarness.listMessages(conversationId, options);
    },
    listEvents: async (conversationId, options) => {
      const harnessCapabilities = await injectedHarness.capabilities();
      if (isCodexVerifiedHost(harnessCapabilities)) {
        const events = await injectedHarness.listEvents(conversationId, options);
        if (events.length > 0) {
          return events;
        }
      }
      return serverHarness.listEvents(conversationId, options);
    },
    send: async (request, options) => {
      ensureNotAborted(options?.signal);

      const harnessCapabilities = await injectedHarness.capabilities();
      if (isCodexVerifiedHost(harnessCapabilities)) {
        return injectedHarness.send(request, options);
      }

      return serverHarness.send(request, options);
    },
    subscribe: (listener) => {
      return normalizeUnsubscribe(injectedHarness.subscribe((message) => {
        listener(normalizeMessage(message));
      }));
    },
  };
}
