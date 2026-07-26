import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OrcaCapabilities, OrcaSession, Profile, TaskRun } from "../../lib/api";
import { AgentBrowserWorkspace } from "./AgentBrowserWorkspace";

const apiMock = vi.hoisted(() => ({
  getOrcaCapabilities: vi.fn(),
  startOrcaSession: vi.fn(),
  readOrcaSessionOutput: vi.fn(),
  sendOrcaSessionInput: vi.fn(),
  closeOrcaSession: vi.fn(),
  createTaskSession: vi.fn(),
  createTaskRun: vi.fn(),
  getTaskRun: vi.fn(),
  listTaskRunOutputs: vi.fn(),
  cancelTaskRun: vi.fn(),
  retryTaskRunHealth: vi.fn(),
  overrideTaskRunHealth: vi.fn(),
  taskOutputScreenshotUrl: vi.fn((id: string) => `/api/task-outputs/${id}/screenshot`),
}));

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    api: apiMock,
  };
});

vi.mock("../ProfileViewer", () => ({
  ProfileViewer: ({
    profileId,
    viewportScale,
    nativeFullscreenEnabled,
  }: {
    profileId: string;
    viewportScale?: number;
    nativeFullscreenEnabled?: boolean;
  }) => (
    <div
      data-testid="mock-profile-viewer"
      data-scale={viewportScale ?? 1}
      data-native-fullscreen={nativeFullscreenEnabled === false ? "off" : "on"}
    >
      viewer:{profileId}
    </div>
  ),
}));

const capsAvailable: OrcaCapabilities = {
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
};

const capsUnavailable: OrcaCapabilities = {
  ...capsAvailable,
  available: false,
  notes: ["agent key file is missing", "Orca runtime is not ready"],
};

const runningProfile: Profile = {
  id: "profile-live",
  name: "Live Demo",
  sandbox_id: "default",
  project_id: "default",
  folder_path: "",
  pinned: false,
  accent_color: null,
  harness: "codex",
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
  created_at: "2026-07-25T00:00:00Z",
  updated_at: "2026-07-25T00:00:00Z",
  tags: [],
  status: "running",
  vnc_ws_port: 5901,
  cdp_url: "ws://example",
};

const stoppedProfile: Profile = {
  ...runningProfile,
  id: "profile-stopped",
  name: "Stopped Demo",
  status: "stopped",
  vnc_ws_port: null,
  cdp_url: null,
};

function sessionFixture(overrides: Partial<OrcaSession> = {}): OrcaSession {
  return {
    id: "orca_abc",
    profile_id: runningProfile.id,
    sandbox_id: "default",
    agent: "codex",
    terminal_handle: "term_test-handle",
    status: "running",
    created_at: 1,
    closed_at: null,
    last_error: null,
    capabilities: capsAvailable.actions,
    connection: { runtime: "orca", owned: true },
    ...overrides,
  };
}

describe("AgentBrowserWorkspace", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    apiMock.getOrcaCapabilities.mockReset();
    apiMock.startOrcaSession.mockReset();
    apiMock.readOrcaSessionOutput.mockReset();
    apiMock.sendOrcaSessionInput.mockReset();
    apiMock.closeOrcaSession.mockReset();
    apiMock.createTaskSession.mockReset();
    apiMock.createTaskRun.mockReset();
    apiMock.getTaskRun.mockReset();
    apiMock.listTaskRunOutputs.mockReset();
    apiMock.cancelTaskRun.mockReset();
    apiMock.retryTaskRunHealth.mockReset();
    apiMock.overrideTaskRunHealth.mockReset();
    apiMock.getOrcaCapabilities.mockResolvedValue(capsAvailable);
  });

  afterEach(() => {
    cleanup();
  });

  it("renders dense left/right layout with profile viewer when running", async () => {
    render(
      <AgentBrowserWorkspace
        profiles={[runningProfile, stoppedProfile]}
        selectedProfile={runningProfile}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    expect(await screen.findByTestId("agent-browser-workspace")).toBeTruthy();
    expect(screen.getByTestId("mock-profile-viewer").textContent).toContain("viewer:profile-live");
    expect(screen.getByTestId("orca-cap-pause").textContent).toMatch(/unavailable/i);
    expect(screen.getByTestId("orca-cap-resume").textContent).toMatch(/unavailable/i);
  });

  it("keeps zoom and viewport controls available in app full view", async () => {
    const onViewportApply = vi.fn().mockResolvedValue(true);
    render(
      <AgentBrowserWorkspace
        profiles={[runningProfile]}
        selectedProfile={runningProfile}
        canAutomate
        canInteract
        canManageViewport
        onViewportApply={onViewportApply}
        onSelectProfile={vi.fn()}
      />,
    );

    await screen.findByTestId("agent-browser-workspace");
    fireEvent.click(screen.getByRole("button", { name: "Enter full view" }));
    expect(screen.getByTestId("agent-browser-viewer-pane").className).toContain("fixed");
    expect(screen.getByTestId("agent-browser-viewer-pane").classList.contains("flex")).toBe(true);
    expect(screen.getByRole("button", { name: "Exit full view" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Increase browser zoom" }));
    expect(screen.getByTestId("mock-profile-viewer").getAttribute("data-scale")).toBe("1.1");
    expect(screen.getByTestId("mock-profile-viewer").getAttribute("data-native-fullscreen")).toBe(
      "off",
    );

    fireEvent.click(screen.getByRole("button", { name: "Open viewport controls" }));
    fireEvent.click(screen.getByRole("button", { name: "Use phone viewport 390 by 844" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply viewport" }));
    await waitFor(() => expect(onViewportApply).toHaveBeenCalledWith(390, 844));

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("button", { name: "Enter full view" })).toBeTruthy();
  });

  it("blocks viewport changes while a Browser Use run is active", async () => {
    const browserUseProfile: Profile = { ...runningProfile, harness: "browser-use" };
    const activeRun: TaskRun = {
      id: "run-active-viewport",
      task_session_id: "task-active-viewport",
      task_message_id: "message-active-viewport",
      profile_id: browserUseProfile.id,
      profile_id_snapshot: browserUseProfile.id,
      sandbox_id: "default",
      harness: "browser-use",
      status: "running",
      launch_if_stopped: false,
      allowed_origins: [],
      max_steps: 20,
      timeout_seconds: 360,
      model_alias: "cursor-grok-4.5-low",
      deadline_at: "2026-07-26T00:06:00Z",
      health_snapshot: {},
      health_decision: {},
      retry_count: 0,
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-26T00:00:00Z",
      updated_at: "2026-07-26T00:00:00Z",
    };
    const onViewportApply = vi.fn().mockResolvedValue(true);
    window.sessionStorage.setItem(
      `cloakbrowser.browser-use.last-run:${browserUseProfile.id}`,
      activeRun.id,
    );
    apiMock.getTaskRun.mockResolvedValue(activeRun);
    apiMock.listTaskRunOutputs.mockResolvedValue([]);

    render(
      <AgentBrowserWorkspace
        profiles={[browserUseProfile]}
        selectedProfile={browserUseProfile}
        canAutomate
        canInteract
        canManageViewport
        onViewportApply={onViewportApply}
        onSelectProfile={vi.fn()}
      />,
    );

    await screen.findByText(/Managed worker/);
    fireEvent.click(screen.getByRole("button", { name: "Open viewport controls" }));
    const applyButton = screen.getByRole("button", { name: "Apply viewport" });

    expect((applyButton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(applyButton);
    expect(onViewportApply).not.toHaveBeenCalled();
  });

  it("disables launch when Orca is unavailable", async () => {
    apiMock.getOrcaCapabilities.mockResolvedValue(capsUnavailable);
    render(
      <AgentBrowserWorkspace
        profiles={[runningProfile]}
        selectedProfile={runningProfile}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    const banner = await screen.findByTestId("orca-unavailable");
    expect(banner.textContent).toContain("agent key file is missing");
    expect(banner.textContent).toContain("Orca runtime is not ready");
    expect((screen.getByTestId("orca-launch") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("orca-send") as HTMLButtonElement).disabled).toBe(true);
  });

  it("disables launch without automate/interact permissions", async () => {
    render(
      <AgentBrowserWorkspace
        profiles={[runningProfile]}
        selectedProfile={runningProfile}
        canAutomate={false}
        canInteract={false}
        onSelectProfile={vi.fn()}
      />,
    );

    await screen.findByTestId("orca-launch");
    expect((screen.getByTestId("orca-launch") as HTMLButtonElement).disabled).toBe(true);
  });

  it("starts, reads, sends, and stops an Orca session", async () => {
    const started = sessionFixture({ agent: "grok" });
    apiMock.startOrcaSession.mockResolvedValue(started);
    apiMock.readOrcaSessionOutput
      .mockResolvedValueOnce({
        session_id: started.id,
        terminal_handle: started.terminal_handle,
        cursor: 0,
        next_cursor: 2,
        output: "boot",
        status: "running",
        capabilities: capsAvailable.actions,
      })
      .mockResolvedValue({
        session_id: started.id,
        terminal_handle: started.terminal_handle,
        cursor: 2,
        next_cursor: 3,
        output: "ack",
        status: "running",
        capabilities: capsAvailable.actions,
      });
    apiMock.sendOrcaSessionInput.mockResolvedValue({
      session_id: started.id,
      ok: true,
      status: "running",
      capabilities: capsAvailable.actions,
    });
    apiMock.closeOrcaSession.mockResolvedValue(sessionFixture({ status: "closed" }));

    render(
      <AgentBrowserWorkspace
        profiles={[runningProfile]}
        selectedProfile={runningProfile}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    await screen.findByTestId("orca-launch");
    fireEvent.change(screen.getByTestId("orca-agent-select"), { target: { value: "grok" } });
    fireEvent.change(screen.getByTestId("orca-prompt"), {
      target: { value: "Use the CloakBrowser control skill" },
    });
    fireEvent.click(screen.getByTestId("orca-launch"));

    await waitFor(() => {
      expect(apiMock.startOrcaSession).toHaveBeenCalledWith({
        profile_id: "profile-live",
        agent: "grok",
        prompt: "Use the CloakBrowser control skill",
      });
    });
    expect((await screen.findByTestId("orca-transcript")).textContent).toContain("boot");

    fireEvent.change(screen.getByTestId("orca-prompt"), { target: { value: "continue" } });
    fireEvent.click(screen.getByTestId("orca-send"));
    await waitFor(() => {
      expect(apiMock.sendOrcaSessionInput).toHaveBeenCalledWith("orca_abc", {
        text: "continue",
        enter: true,
      });
    });

    fireEvent.click(screen.getByTestId("orca-stop"));
    await waitFor(() => {
      expect(apiMock.closeOrcaSession).toHaveBeenCalledWith("orca_abc");
    });
  });

  it("clears session state when switching profiles", async () => {
    const onSelectProfile = vi.fn();
    const started = sessionFixture();
    apiMock.startOrcaSession.mockResolvedValue(started);
    apiMock.readOrcaSessionOutput.mockResolvedValue({
      session_id: started.id,
      terminal_handle: started.terminal_handle,
      cursor: 0,
      next_cursor: 1,
      output: "hello",
      status: "running",
      capabilities: capsAvailable.actions,
    });

    const { rerender } = render(
      <AgentBrowserWorkspace
        profiles={[runningProfile, stoppedProfile]}
        selectedProfile={runningProfile}
        canAutomate
        canInteract
        onSelectProfile={onSelectProfile}
      />,
    );

    await screen.findByTestId("orca-launch");
    fireEvent.click(screen.getByTestId("orca-launch"));
    expect((await screen.findByTestId("orca-transcript")).textContent).toContain("hello");

    rerender(
      <AgentBrowserWorkspace
        profiles={[runningProfile, stoppedProfile]}
        selectedProfile={stoppedProfile}
        canAutomate
        canInteract
        onSelectProfile={onSelectProfile}
      />,
    );

    expect(screen.getByTestId("orca-transcript").textContent).not.toContain("hello");
    expect(screen.getByTestId("orca-viewer-empty")).toBeTruthy();
    expect(screen.getByTestId("orca-run-status").textContent).toContain("idle");
  });

  it("applies a handed-off prompt draft once without overwriting operator edits", async () => {
    const task = "Open https://example.com and report the heading";
    const onInitialPromptDraftApplied = vi.fn();
    const { rerender } = render(
      <AgentBrowserWorkspace
        profiles={[runningProfile]}
        selectedProfile={{ ...runningProfile, harness: "browser-use" }}
        canAutomate
        canInteract
        initialPromptDraft={{
          id: "handoff-1",
          profileId: runningProfile.id,
          task,
        }}
        onInitialPromptDraftApplied={onInitialPromptDraftApplied}
        onSelectProfile={vi.fn()}
      />,
    );

    await screen.findByTestId("orca-launch");
    expect((screen.getByTestId("orca-prompt") as HTMLTextAreaElement).value).toBe(task);
    expect(onInitialPromptDraftApplied).toHaveBeenCalledTimes(1);
    expect(onInitialPromptDraftApplied).toHaveBeenCalledWith("handoff-1");

    fireEvent.change(screen.getByTestId("orca-prompt"), {
      target: { value: "Operator edit in progress" },
    });

    rerender(
      <AgentBrowserWorkspace
        profiles={[{ ...runningProfile, updated_at: "2026-07-25T00:00:01Z" }]}
        selectedProfile={{
          ...runningProfile,
          harness: "browser-use",
          updated_at: "2026-07-25T00:00:01Z",
        }}
        canAutomate
        canInteract
        initialPromptDraft={{
          id: "handoff-1",
          profileId: runningProfile.id,
          task,
        }}
        onInitialPromptDraftApplied={onInitialPromptDraftApplied}
        onSelectProfile={vi.fn()}
      />,
    );

    expect((screen.getByTestId("orca-prompt") as HTMLTextAreaElement).value).toBe(
      "Operator edit in progress",
    );
    expect(onInitialPromptDraftApplied).toHaveBeenCalledTimes(1);
  });

  it("starts Browser Use for a browser-use profile and renders typed outputs", async () => {
    const browserUseProfile: Profile = { ...runningProfile, harness: "browser-use" };
    apiMock.createTaskSession.mockResolvedValue({
      id: "task-1",
      profile_id: browserUseProfile.id,
      sandbox_id: "default",
      title: "Open example.com",
      status: "active",
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-26T00:00:00Z",
      updated_at: "2026-07-26T00:00:00Z",
      metadata: {},
    });
    apiMock.createTaskRun.mockResolvedValue({
      id: "run-1",
      task_session_id: "task-1",
      task_message_id: "message-1",
      profile_id: browserUseProfile.id,
      profile_id_snapshot: browserUseProfile.id,
      sandbox_id: "default",
      harness: "browser-use",
      status: "running",
      launch_if_stopped: false,
      allowed_origins: ["https://example.com"],
      max_steps: 20,
      timeout_seconds: 360,
      model_alias: "cursor-grok-4.5-low",
      deadline_at: "2026-07-26T00:06:00Z",
      health_snapshot: {},
      health_decision: {},
      retry_count: 0,
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-26T00:00:00Z",
      updated_at: "2026-07-26T00:00:00Z",
    });
    apiMock.listTaskRunOutputs.mockResolvedValue([{
      id: "output-1",
      run_id: "run-1",
      sequence: 1,
      idempotency_key: "action-1",
      kind: "action",
      summary: "Opened example.com",
      payload: { name: "navigate", url: "https://example.com" },
      created_at: "2026-07-26T00:00:01Z",
      artifact_expired: false,
    }]);

    render(
      <AgentBrowserWorkspace
        profiles={[browserUseProfile]}
        selectedProfile={browserUseProfile}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    await screen.findByTestId("orca-launch");
    expect((screen.getByTestId("orca-agent-select") as HTMLSelectElement).value).toBe("browser-use");
    fireEvent.change(screen.getByTestId("orca-prompt"), {
      target: { value: "Open https://example.com and report the heading" },
    });
    fireEvent.click(screen.getByTestId("orca-launch"));

    await waitFor(() => {
      expect(apiMock.createTaskRun).toHaveBeenCalledWith(
        "task-1",
        expect.objectContaining({
          harness: "browser-use",
          profile_id: browserUseProfile.id,
          allowed_origins: ["https://example.com"],
          timeout_seconds: 360,
        }),
      );
    });
    expect(await screen.findByText("navigate")).toBeTruthy();
    expect(screen.getByTestId("browser-use-output")).toBeTruthy();
  });

  it("restores the last Browser Use run after the live workspace remounts", async () => {
    const completedRun: TaskRun = {
      id: "run-restored",
      task_session_id: "task-restored",
      task_message_id: "message-restored",
      profile_id: runningProfile.id,
      profile_id_snapshot: runningProfile.id,
      sandbox_id: "default",
      harness: "browser-use",
      status: "succeeded",
      launch_if_stopped: false,
      allowed_origins: ["https://example.com"],
      max_steps: 20,
      timeout_seconds: 360,
      model_alias: "cursor-grok-4.5-low",
      deadline_at: "2026-07-26T00:06:00Z",
      health_snapshot: {},
      health_decision: {},
      retry_count: 0,
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-26T00:00:00Z",
      updated_at: "2026-07-26T00:01:00Z",
    };
    window.sessionStorage.setItem(
      `cloakbrowser.browser-use.last-run:${runningProfile.id}`,
      completedRun.id,
    );
    apiMock.getTaskRun.mockResolvedValue(completedRun);
    apiMock.listTaskRunOutputs.mockResolvedValue([
      {
        id: "output-restored",
        run_id: completedRun.id,
        sequence: 1,
        idempotency_key: "summary-restored",
        kind: "summary",
        summary: "Example Domain restored from the completed run",
        payload: { success: true },
        created_at: "2026-07-26T00:01:00Z",
        artifact_expired: false,
      },
    ]);

    render(
      <AgentBrowserWorkspace
        profiles={[runningProfile]}
        selectedProfile={runningProfile}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    expect(await screen.findByText("Example Domain restored from the completed run")).toBeTruthy();
    expect((screen.getByTestId("orca-agent-select") as HTMLSelectElement).value).toBe(
      "browser-use",
    );
    expect(screen.getByTestId("orca-run-status").textContent).toContain("succeeded");
  });

  it("shows Browser Use health blockers and allows an explicit operator override", async () => {
    const browserUseProfile: Profile = { ...runningProfile, harness: "browser-use" };
    const blockedRun: TaskRun = {
      id: "run-blocked",
      task_session_id: "task-blocked",
      task_message_id: "message-blocked",
      profile_id: browserUseProfile.id,
      profile_id_snapshot: browserUseProfile.id,
      sandbox_id: "default",
      harness: "browser-use",
      status: "blocked_health",
      launch_if_stopped: false,
      allowed_origins: ["https://example.com"],
      max_steps: 20,
      timeout_seconds: 360,
      model_alias: "cursor-grok-4.5-low",
      deadline_at: "2026-07-26T00:06:00Z",
      health_snapshot: { browser_scan_score: 100 },
      health_decision: {
        allowed: false,
        waiting: false,
        failed_reasons: ["measured_authenticity_below_threshold"],
        non_overridable_reasons: [],
      },
      retry_count: 0,
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-26T00:00:00Z",
      updated_at: "2026-07-26T00:00:00Z",
    };
    apiMock.createTaskSession.mockResolvedValue({
      id: blockedRun.task_session_id,
      profile_id: browserUseProfile.id,
      sandbox_id: "default",
      title: "Inspect example.com",
      status: "active",
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-26T00:00:00Z",
      updated_at: "2026-07-26T00:00:00Z",
      metadata: {},
    });
    apiMock.createTaskRun.mockResolvedValue(blockedRun);
    apiMock.listTaskRunOutputs.mockResolvedValue([]);
    apiMock.overrideTaskRunHealth.mockResolvedValue({
      ...blockedRun,
      status: "queued",
      health_override: { applied: true },
      health_decision: { allowed: true, waiting: false, failed_reasons: [] },
    });

    render(
      <AgentBrowserWorkspace
        profiles={[browserUseProfile]}
        selectedProfile={browserUseProfile}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    fireEvent.change(await screen.findByTestId("orca-prompt"), {
      target: { value: "Inspect https://example.com" },
    });
    fireEvent.click(screen.getByTestId("orca-launch"));

    expect(await screen.findByText("Measured authenticity below threshold")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Run with override" }));
    await waitFor(() => {
      expect(apiMock.overrideTaskRunHealth).toHaveBeenCalledWith(
        blockedRun.id,
        expect.stringContaining("Operator approved"),
      );
    });
  });

  it("allows Browser Use with automate permission even when the viewer is read-only", async () => {
    const browserUseProfile: Profile = { ...runningProfile, harness: "browser-use" };
    render(
      <AgentBrowserWorkspace
        profiles={[browserUseProfile]}
        selectedProfile={browserUseProfile}
        canAutomate
        canInteract={false}
        onSelectProfile={vi.fn()}
      />,
    );

    await screen.findByTestId("orca-launch");
    fireEvent.change(screen.getByTestId("orca-prompt"), {
      target: { value: "Inspect https://example.com" },
    });
    expect((screen.getByTestId("orca-launch") as HTMLButtonElement).disabled).toBe(false);
  });
});
