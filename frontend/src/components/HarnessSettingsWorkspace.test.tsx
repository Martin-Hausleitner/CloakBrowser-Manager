import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  OrcaCapabilities,
  Profile,
  ProviderReadiness,
  BrowserToolReadiness,
  TaskHarnessPresence,
  TaskHarnessSession,
  TaskRun,
} from "../lib/api";
import {
  WorkspaceRuntimeConfigProvider,
  useWorkspaceRuntimeConfig,
} from "./workspace/WorkspaceRuntimeConfig";
import { HarnessSettingsWorkspace } from "./HarnessSettingsWorkspace";

const apiMock = vi.hoisted(() => ({
  getOrcaCapabilities: vi.fn(),
  getTaskHarnessPresence: vi.fn(),
  getTaskHarnessPreflights: vi.fn(),
  getProviderReadiness: vi.fn(),
  getBrowserToolReadiness: vi.fn(),
  runProfileHealth: vi.fn(),
  getProfileHealth: vi.fn(),
  createTaskSession: vi.fn(),
  createTaskRun: vi.fn(),
  cancelTaskRun: vi.fn(),
}));

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getOrcaCapabilities: apiMock.getOrcaCapabilities,
      getTaskHarnessPresence: apiMock.getTaskHarnessPresence,
      getTaskHarnessPreflights: apiMock.getTaskHarnessPreflights,
      getProviderReadiness: apiMock.getProviderReadiness,
      getBrowserToolReadiness: apiMock.getBrowserToolReadiness,
      runProfileHealth: apiMock.runProfileHealth,
      getProfileHealth: apiMock.getProfileHealth,
      createTaskSession: apiMock.createTaskSession,
      createTaskRun: apiMock.createTaskRun,
      cancelTaskRun: apiMock.cancelTaskRun,
    },
  };
});

const runningProfile: Profile = {
  id: "profile-live",
  name: "Live Demo",
  sandbox_id: "default",
  project_id: "default",
  folder_path: "",
  pinned: false,
  accent_color: null,
  harness: "browser-use",
  fingerprint_seed: 1,
  proxy: null,
  timezone: null,
  locale: null,
  platform: "linux",
  user_agent: null,
  screen_width: 1280,
  screen_height: 720,
  gpu_vendor: null,
  gpu_renderer: null,
  hardware_concurrency: null,
  humanize: false,
  human_preset: "default",
  headless: false,
  geoip: false,
  clipboard_sync: true,
  auto_launch: false,
  color_scheme: null,
  search_engine: null,
  launch_args: [],
  notes: null,
  user_data_dir: "",
  created_at: "2026-07-29T00:00:00Z",
  updated_at: "2026-07-29T00:00:00Z",
  tags: [],
  status: "running",
  vnc_ws_port: 5901,
  cdp_url: "ws://example",
};

const caps: OrcaCapabilities = {
  available: true,
  orca_bin: "/home/coder/.local/bin/orca-ide",
  agents: ["agy", "grok", "codex"],
  operations: ["terminal.create"],
  actions: { start: true, read: true, send: true, close: true, pause: false, resume: false },
  notes: [],
};

const readiness: ProviderReadiness = {
  providers: [
    { provider: "grok", transport: "cli", ready: false, state: "unavailable", reason_code: "grok_cli_unavailable", checked_at: null, model_aliases: ["grok-cli"] },
    { provider: "grok", transport: "acp", ready: true, state: "ready", reason_code: "ok", checked_at: "now", model_aliases: ["grok-build", "grok-4"] },
    { provider: "codex", transport: "acp", ready: true, state: "ready", reason_code: "ok", checked_at: "now", model_aliases: [] },
    { provider: "claude", transport: "acp", ready: true, state: "ready", reason_code: "ok", checked_at: "now", model_aliases: [] },
    { provider: "cursor", transport: "acp", ready: true, state: "ready", reason_code: "ok", checked_at: "now", model_aliases: [] },
    { provider: "opencode", transport: "acp", ready: true, state: "ready", reason_code: "ok", checked_at: "now", model_aliases: [] },
    { provider: "gemini", transport: "acp", ready: true, state: "ready", reason_code: "ok", checked_at: "now", model_aliases: ["gemini-2.5-pro", "gemini-2.5-flash"] },
    { provider: "custom.agent", transport: "acp", ready: true, state: "ready", reason_code: "ok", checked_at: "now", model_aliases: ["unsafe"] },
    { provider: "custom_agent", transport: "acp", ready: true, state: "ready", reason_code: "ok", checked_at: "now", model_aliases: [] },
    { provider: "antigravity", transport: "cli", ready: false, state: "unavailable", reason_code: "adapter_missing", checked_at: null, model_aliases: [] },
  ],
};

const browserToolReadiness: BrowserToolReadiness = {
  tools: [
    { id: "unbrowse", ready: true, state: "ready", reason_code: "ready", checked_at: "2026-07-29T00:00:00Z" },
    { id: "stagehand", ready: false, state: "failed", reason_code: "auth_required", checked_at: "2026-07-29T00:00:01Z" },
    { id: "browser-harness", ready: false, state: "unavailable", reason_code: "not_checked", checked_at: null },
  ],
};

function presence(harness: TaskHarnessPresence["harness"], ready = true): TaskHarnessPresence {
  return {
    harness,
    worker_seen_recently: ready,
    state: ready ? "polling" : "unavailable",
    last_seen_at: ready ? "2026-07-29T00:00:00Z" : null,
    reason: ready ? null : `${harness}_missing`,
  };
}

function sessionFixture(overrides: Partial<TaskHarnessSession> = {}): TaskHarnessSession {
  return {
    id: "session-1",
    profile_id: runningProfile.id,
    sandbox_id: "default",
    project_id: "default",
    title: "Smoke",
    status: "active",
    workflow_state: "open",
    done_at: null,
    archived_at: null,
    retention_class: "temporary",
    expires_at: null,
    activity_at: "2026-07-29T00:00:00Z",
    row_version: 1,
    created_by_kind: "user",
    created_by_id: "user-1",
    created_at: "2026-07-29T00:00:00Z",
    updated_at: "2026-07-29T00:00:00Z",
    metadata: {},
    ...overrides,
  };
}

function runFixture(overrides: Partial<TaskRun> = {}): TaskRun {
  return {
    id: "run-1",
    task_session_id: "session-1",
    task_message_id: "message-1",
    profile_id: runningProfile.id,
    profile_id_snapshot: runningProfile.id,
    sandbox_id: "default",
    harness: "browser-use",
    agent: null,
    status: "queued",
    launch_if_stopped: false,
    allowed_origins: ["https://example.com"],
    max_steps: 8,
    timeout_seconds: 180,
    model_alias: null,
    deadline_at: "2026-07-29T00:03:00Z",
    health_snapshot: {},
    health_decision: {},
    health_override: null,
    retry_count: 0,
    first_action_sequence: null,
    first_action_at: null,
    cancelled_at: null,
    error_code: null,
    error_message: null,
    created_by_kind: "user",
    created_by_id: "user-1",
    created_at: "2026-07-29T00:00:00Z",
    updated_at: "2026-07-29T00:00:00Z",
    ...overrides,
  };
}

function RuntimeConfigProbe() {
  const { config } = useWorkspaceRuntimeConfig();
  return (
    <div data-testid="settings-runtime-config">
      {config.mode}:{config.agent}:{config.acpxAgent}:{config.providerRouting.providerId}:{config.providerRouting.transport}
    </div>
  );
}

function renderSettings(options: {
  profiles?: Profile[];
  selectedProfile?: Profile | null;
  onSelectProfile?: (profileId: string) => void;
} = {}) {
  return render(
    <WorkspaceRuntimeConfigProvider>
      <HarnessSettingsWorkspace
        profiles={options.profiles ?? [runningProfile]}
        selectedProfile={options.selectedProfile === undefined ? runningProfile : options.selectedProfile}
        onSelectProfile={options.onSelectProfile}
      />
      <RuntimeConfigProbe />
    </WorkspaceRuntimeConfigProvider>,
  );
}

async function waitForSettingsReady() {
  await screen.findByTestId("harness-settings-workspace");
  await waitFor(() => expect(apiMock.getProviderReadiness).toHaveBeenCalled());
  await waitFor(() => expect(apiMock.getBrowserToolReadiness).toHaveBeenCalled());
  await waitFor(() => expect(screen.getByTestId("provider-tool-summary").textContent).toContain("Grok"));
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.getOrcaCapabilities.mockResolvedValue(caps);
  apiMock.getProviderReadiness.mockResolvedValue(readiness);
  apiMock.getBrowserToolReadiness.mockResolvedValue(browserToolReadiness);
  apiMock.getTaskHarnessPresence.mockImplementation((harness: TaskHarnessPresence["harness"]) =>
    Promise.resolve(presence(harness, harness !== "stagehand")),
  );
  apiMock.getTaskHarnessPreflights.mockResolvedValue({
    harness: "acpx",
    agents: [
      { agent: "grok-build", ready: true, state: "ready", reason_code: "ok", checked_at: "now" },
      { agent: "codex", ready: true, state: "ready", reason_code: "ok", checked_at: "now" },
      { agent: "claude", ready: true, state: "ready", reason_code: "ok", checked_at: "now" },
      { agent: "cursor", ready: true, state: "ready", reason_code: "ok", checked_at: "now" },
      { agent: "opencode", ready: true, state: "ready", reason_code: "ok", checked_at: "now" },
      { agent: "gemini", ready: true, state: "ready", reason_code: "ok", checked_at: "now" },
    ],
  });
  apiMock.runProfileHealth.mockResolvedValue({});
  apiMock.getProfileHealth.mockResolvedValue({});
  apiMock.createTaskSession.mockResolvedValue(sessionFixture());
  apiMock.createTaskRun.mockResolvedValue(runFixture());
  apiMock.cancelTaskRun.mockResolvedValue(runFixture({ status: "cancelled", cancelled_at: "2026-07-29T00:00:01Z" }));
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("HarnessSettingsWorkspace", () => {
  it("renders runtime, harness, provider, model and browser-tool settings with honest readiness", async () => {
    renderSettings();
    await waitForSettingsReady();

    expect(screen.getByRole("button", { name: "CLI" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "ACP" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "ACPX" })).toBeTruthy();
    expect(await screen.findByText("Browser Use")).toBeTruthy();
    expect(screen.getAllByTestId("provider-tool-control")).toHaveLength(1);
    expect(screen.getAllByText("Unbrowse").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Stagehand").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Browser Harness").length).toBeGreaterThan(0);
    expect(screen.getByTestId("settings-browser-tool-unbrowse").textContent).toContain("ready");
    expect(screen.getByTestId("settings-browser-tool-stagehand").textContent).toContain("failed");
    expect(screen.getByTestId("settings-browser-tool-stagehand").textContent).toContain("auth_required");
    expect(screen.getByTestId("settings-browser-tool-browser-harness").textContent).toContain("unavailable");
    expect(screen.getByTestId("settings-browser-tool-browser-harness").textContent).toContain("not_checked");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Toggle Browser Tools settings" }));
    });
    expect(screen.getByText("Ready · 2026-07-29T00:00:00Z")).toBeTruthy();
    expect(screen.getByText("auth_required · 2026-07-29T00:00:01Z")).toBeTruthy();
    expect(screen.getByText("not_checked")).toBeTruthy();
    expect(screen.getByTestId("settings-browser-tool-stagehand").getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByTestId("settings-browser-tool-browser-harness").getAttribute("aria-disabled")).toBe("true");
  });

  it("renders normalized ACP providers, disables Antigravity CLI, and filters unsafe provider ids", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      renderSettings();
      await waitForSettingsReady();

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Toggle KI Provider settings" }));
      });
      const providerPanel = await screen.findByTestId("provider-tool-provider-section");

      for (const label of ["Codex", "Claude", "Cursor", "Grok", "OpenCode", "Gemini"]) {
        expect(within(providerPanel).getByRole("radio", { name: label })).toHaveProperty("disabled", false);
      }
      expect(within(providerPanel).getByRole("radio", { name: "Antigravity" })).toHaveProperty("disabled", true);
      expect(await screen.findByText("grok_cli_unavailable")).toBeTruthy();
      expect(await screen.findByText("adapter_missing")).toBeTruthy();
      expect(providerPanel.textContent).toContain("Custom.agent");
      expect(providerPanel.textContent).toContain("Custom_agent");
      expect(providerPanel.textContent).not.toContain("custom/agent");
      expect(consoleError.mock.calls.some(([message]) => String(message).includes("same key"))).toBe(false);
    } finally {
      consoleError.mockRestore();
    }
  });

  it("selects installed managed harnesses for the next run through shared runtime config", async () => {
    apiMock.getTaskHarnessPresence.mockImplementation((candidate: TaskHarnessPresence["harness"]) =>
      Promise.resolve(presence(candidate, true)),
    );
    renderSettings();
    await waitForSettingsReady();

    const acpxRow = await screen.findByTestId("settings-harness-acpx");
    await act(async () => {
      fireEvent.click(within(acpxRow).getByRole("button", { name: "Select ACPX for next run" }));
    });
    expect(acpxRow.getAttribute("data-selected")).toBe("true");
    expect(screen.getByTestId("settings-runtime-config").textContent).toBe("acpx:acpx:grok-build:grok:acp");

    const unbrowseRow = await screen.findByTestId("settings-harness-unbrowse");
    await act(async () => {
      fireEvent.click(within(unbrowseRow).getByRole("button", { name: "Select Unbrowse for next run" }));
    });
    expect(unbrowseRow.getAttribute("data-selected")).toBe("true");
    expect(screen.getByTestId("settings-runtime-config").textContent).toBe("cli:unbrowse:grok-build:grok:acp");
  });

  it("sends exact provider transport, selected model, browser tools and routing only for ACPX smoke tests", async () => {
    renderSettings();
    await waitForSettingsReady();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Toggle KI Provider settings" }));
    });
    const providerPanel = await screen.findByTestId("provider-tool-provider-section");
    await act(async () => {
      fireEvent.click(within(providerPanel).getByRole("radio", { name: "Gemini" }));
    });
    await act(async () => {
      fireEvent.change(screen.getByLabelText("Model alias"), { target: { value: "gemini-2.5-flash" } });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Toggle Browser Tools settings" }));
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("checkbox", { name: "Enable Stagehand" }));
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Test ACPX" }));
    });

    await waitFor(() => expect(apiMock.createTaskRun).toHaveBeenCalledTimes(1));
    expect(apiMock.createTaskRun).toHaveBeenCalledWith(
      "session-1",
      expect.objectContaining({
        harness: "acpx",
        agent: "gemini",
        profile_id: runningProfile.id,
        provider: { id: "gemini", transport: "acp", model_alias: "gemini-2.5-flash" },
        browser_tools: [
          { id: "unbrowse", enabled: true },
          { id: "stagehand", enabled: false },
          { id: "browser-harness", enabled: true },
        ],
        routing_policy: {
          mode: "ordered-fallback",
          allow_second_browser: false,
          max_tool_attempts: 3,
        },
      }),
      expect.any(Object),
    );
  });

  it("runs Browser Use smoke against the selected live profile after a fresh health gate without provider routing", async () => {
    renderSettings();
    await waitForSettingsReady();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Test Browser Use" }));
    });

    await waitFor(() => expect(apiMock.createTaskRun).toHaveBeenCalledTimes(1));
    expect(apiMock.createTaskSession).toHaveBeenCalledWith(
      expect.objectContaining({ profile_id: runningProfile.id }),
      expect.any(Object),
    );
    const [, payload] = apiMock.createTaskRun.mock.calls[0];
    expect(payload).toEqual(expect.objectContaining({
      harness: "browser-use",
      agent: null,
      profile_id: runningProfile.id,
      allowed_origins: ["https://example.com"],
      max_steps: 8,
      timeout_seconds: 180,
    }));
    expect(payload).not.toHaveProperty("provider");
    expect(payload).not.toHaveProperty("browser_tools");
    expect(payload).not.toHaveProperty("routing_policy");
    expect(apiMock.runProfileHealth.mock.invocationCallOrder[0])
      .toBeLessThan(apiMock.createTaskSession.mock.invocationCallOrder[0]);
    expect(apiMock.getProfileHealth.mock.invocationCallOrder[0])
      .toBeLessThan(apiMock.createTaskSession.mock.invocationCallOrder[0]);
  });

  it.each([
    ["Unbrowse", "unbrowse"],
    ["Stagehand", "stagehand"],
  ] as const)("maps the %s smoke test to the real %s managed run", async (label, harness) => {
    apiMock.getTaskHarnessPresence.mockImplementation((candidate: TaskHarnessPresence["harness"]) =>
      Promise.resolve(presence(candidate, true)),
    );
    apiMock.createTaskSession.mockResolvedValue(sessionFixture({ id: `smoke-${harness}` }));
    apiMock.createTaskRun.mockResolvedValue(runFixture({ id: `run-${harness}`, task_session_id: `smoke-${harness}`, harness }));
    renderSettings();
    await waitForSettingsReady();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: `Test ${label}` }));
    });

    await waitFor(() => expect(apiMock.createTaskRun).toHaveBeenCalledWith(
      `smoke-${harness}`,
      expect.objectContaining({ harness, agent: null, max_steps: 8, timeout_seconds: 180 }),
      expect.any(Object),
    ));
  });

  it("keeps unavailable harness smoke disabled and preserves successful checks when ACPX preflight fails", async () => {
    apiMock.getTaskHarnessPresence.mockImplementation((harness: TaskHarnessPresence["harness"]) =>
      Promise.resolve(presence(harness, harness !== "acpx" && harness !== "stagehand")),
    );
    apiMock.getTaskHarnessPreflights.mockRejectedValue(new Error("ACPX preflight unavailable"));

    renderSettings();
    await waitForSettingsReady();

    const browserUse = await screen.findByTestId("settings-harness-browser-use");
    const unbrowse = await screen.findByTestId("settings-harness-unbrowse");
    const acpx = await screen.findByTestId("settings-harness-acpx");
    const stagehand = await screen.findByTestId("settings-harness-stagehand");
    expect(browserUse.textContent).toMatch(/Ready/i);
    expect(unbrowse.textContent).toMatch(/Ready/i);
    expect(acpx.textContent).toMatch(/acpx_missing/i);
    expect(stagehand.textContent).toMatch(/stagehand_missing/i);
    expect(within(stagehand).getByRole("button", { name: "Test Stagehand" })).toHaveProperty("disabled", true);
  });

  it("blocks noncanonical browser tool order before creating an ACPX smoke session", async () => {
    renderSettings();
    await waitForSettingsReady();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Toggle Browser Tools settings" }));
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Move Stagehand up" }));
    });
    expect(screen.getByTestId("provider-tool-summary").getAttribute("title")).toMatch(/Browser tool order/i);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Test ACPX" }));
    });

    await waitFor(() => expect(document.body.textContent).toMatch(/Browser tool order is not executable/i));
    expect(apiMock.createTaskSession).not.toHaveBeenCalled();
    expect(apiMock.createTaskRun).not.toHaveBeenCalled();
  });

  it("shows a busy smoke state and ignores a duplicate click while the run is starting", async () => {
    let resolveSession!: (session: TaskHarnessSession) => void;
    apiMock.createTaskSession.mockImplementation(() => new Promise((resolve) => {
      resolveSession = resolve;
    }));
    renderSettings();
    await waitForSettingsReady();

    const smokeButton = screen.getByRole("button", { name: "Test Browser Use" });
    await act(async () => {
      fireEvent.click(smokeButton);
    });

    await waitFor(() => expect(apiMock.createTaskSession).toHaveBeenCalledTimes(1));
    expect(smokeButton).toHaveProperty("disabled", true);
    fireEvent.click(smokeButton);
    expect(apiMock.createTaskSession).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveSession(sessionFixture());
    });
    await waitFor(() => expect(apiMock.createTaskRun).toHaveBeenCalledTimes(1));
  });

  it("does not attach or start a stale smoke run after the selected profile changes before session creation resolves", async () => {
    let resolveSession!: (session: TaskHarnessSession) => void;
    const nextProfile: Profile = { ...runningProfile, id: "profile-next", name: "Next profile" };
    apiMock.createTaskSession.mockImplementation(() => new Promise((resolve) => {
      resolveSession = resolve;
    }));
    const view = renderSettings({ profiles: [runningProfile, nextProfile] });
    await waitForSettingsReady();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Test Browser Use" }));
    });
    await waitFor(() => expect(apiMock.createTaskSession).toHaveBeenCalledTimes(1));
    view.rerender(
      <WorkspaceRuntimeConfigProvider>
        <HarnessSettingsWorkspace profiles={[runningProfile, nextProfile]} selectedProfile={nextProfile} />
      </WorkspaceRuntimeConfigProvider>,
    );

    await act(async () => {
      resolveSession(sessionFixture());
    });
    await waitFor(() => expect(screen.getByText("Next profile · live")).toBeTruthy());
    expect(apiMock.createTaskRun).not.toHaveBeenCalled();
    expect(apiMock.cancelTaskRun).not.toHaveBeenCalled();
  });

  it("cancels a smoke run created while the selected profile changes", async () => {
    let resolveRun!: (run: TaskRun) => void;
    const nextProfile: Profile = { ...runningProfile, id: "profile-next-during-run", name: "Next profile during run" };
    apiMock.createTaskRun.mockImplementation(() => new Promise((resolve) => {
      resolveRun = resolve;
    }));
    const view = renderSettings({ profiles: [runningProfile, nextProfile] });
    await waitForSettingsReady();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Test Browser Use" }));
    });
    await waitFor(() => expect(apiMock.createTaskRun).toHaveBeenCalledTimes(1));
    view.rerender(
      <WorkspaceRuntimeConfigProvider>
        <HarnessSettingsWorkspace profiles={[runningProfile, nextProfile]} selectedProfile={nextProfile} />
      </WorkspaceRuntimeConfigProvider>,
    );

    await act(async () => {
      resolveRun(runFixture({ id: "smoke-run-during-switch" }));
    });
    await waitFor(() => expect(apiMock.cancelTaskRun).toHaveBeenCalledWith("smoke-run-during-switch"));
  });

  it("restores focus when Settings provider and browser-tool panels close with Escape", async () => {
    renderSettings();
    await waitForSettingsReady();

    const providerTrigger = screen.getByRole("button", { name: "Toggle KI Provider settings" });
    providerTrigger.focus();
    await act(async () => {
      fireEvent.click(providerTrigger);
    });
    expect(await screen.findByTestId("provider-tool-provider-section")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("provider-tool-provider-section")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(providerTrigger));

    const toolsTrigger = screen.getByRole("button", { name: "Toggle Browser Tools settings" });
    toolsTrigger.focus();
    await act(async () => {
      fireEvent.click(toolsTrigger);
    });
    expect(await screen.findByTestId("provider-tool-tools-section")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("provider-tool-tools-section")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(toolsTrigger));
  });
});
