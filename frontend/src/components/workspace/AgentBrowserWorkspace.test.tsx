import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OrcaCapabilities, OrcaSession, Profile } from "../../lib/api";
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
  ProfileViewer: ({ profileId }: { profileId: string }) => (
    <div data-testid="mock-profile-viewer">viewer:{profileId}</div>
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
