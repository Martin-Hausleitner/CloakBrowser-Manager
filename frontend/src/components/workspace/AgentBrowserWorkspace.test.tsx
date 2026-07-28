import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OrcaCapabilities, OrcaSession, Profile, TaskRun } from "../../lib/api";
import { UI_STATE, expectUiState } from "../../lib/uiFlowRegistry";
import { AgentBrowserWorkspace } from "./AgentBrowserWorkspace";

const apiMock = vi.hoisted(() => ({
  getOrcaCapabilities: vi.fn(),
  getTaskHarnessPresence: vi.fn(),
  getTaskHarnessPreflights: vi.fn(),
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
  captureProfileScreenshot: vi.fn(),
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
    cdpUrl,
    clipboardSync,
    canInteract,
    layoutMode,
    viewportScale,
    fitMode,
    nativeFullscreenEnabled,
  }: {
    profileId: string;
    cdpUrl?: string | null;
    clipboardSync?: boolean;
    canInteract?: boolean;
    layoutMode?: string;
    viewportScale?: number;
    fitMode?: string;
    nativeFullscreenEnabled?: boolean;
  }) => (
    <div
      data-testid="mock-profile-viewer"
      data-profile-id={profileId}
      data-cdp-url={cdpUrl ?? "off"}
      data-clipboard-sync={clipboardSync ? "on" : "off"}
      data-can-interact={canInteract === false ? "off" : "on"}
      data-layout-mode={layoutMode ?? "inline"}
      data-scale={viewportScale ?? 1}
      data-fit-mode={fitMode ?? "fit"}
      data-native-fullscreen={nativeFullscreenEnabled === false ? "off" : "on"}
    >
      viewer:{profileId}
    </div>
  ),
}));

vi.mock("../LiveDevPanel", () => ({
  LiveDevPanel: ({ profileId }: { profileId: string }) => (
    <div data-testid="mock-full-view-live-metrics">metrics:{profileId}</div>
  ),
}));

const capsAvailable: OrcaCapabilities = {
  available: true,
  orca_bin: "/home/coder/.local/bin/orca-ide",
  agents: ["cursor-agent", "grok", "agy", "codex"],
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
    apiMock.getTaskHarnessPresence.mockReset();
    apiMock.getTaskHarnessPreflights.mockReset();
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
    apiMock.captureProfileScreenshot.mockReset();
    apiMock.getOrcaCapabilities.mockResolvedValue(capsAvailable);
    apiMock.getTaskHarnessPresence.mockResolvedValue({
      harness: "acpx",
      worker_seen_recently: true,
      state: "polling",
      last_seen_at: "2026-07-27T00:00:00Z",
      reason: null,
    });
    apiMock.getTaskHarnessPreflights.mockResolvedValue({
      harness: "acpx",
      agents: ["codex", "claude", "cursor", "grok-build", "opencode"].map((agent) => ({
        agent,
        ready: true,
        state: "ready",
        reason_code: "ok",
        checked_at: "2026-07-27T00:00:00Z",
      })),
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
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
    expectUiState(document.body, UI_STATE.agentWorkspace);
    expectUiState(document.body, UI_STATE.agentSessionPane);
    expectUiState(document.body, UI_STATE.agentViewerPane);
    expectUiState(document.body, UI_STATE.profileViewer);
    expect(screen.getByTestId("mock-profile-viewer").textContent).toContain("viewer:profile-live");
    expect(screen.getByTestId("orca-cap-pause").textContent).toMatch(/unavailable/i);
    expect(screen.getByTestId("orca-cap-resume").textContent).toMatch(/unavailable/i);
  });

  it("renders a full-view grid of running browsers while keeping stopped profiles out", async () => {
    const alternate = {
      ...runningProfile,
      id: "profile-alt",
      name: "Alt Live",
      clipboard_sync: false,
    };
    render(
      <AgentBrowserWorkspace
        profiles={[runningProfile, stoppedProfile, alternate]}
        selectedProfile={runningProfile}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    await screen.findByTestId("agent-browser-workspace");
    fireEvent.click(screen.getByRole("button", { name: "Enter full view" }));
    fireEvent.click(screen.getByRole("button", { name: "Open desktop full-view Sessions controls" }));

    expect(screen.getByRole("button", { name: "Show one browser" }).getAttribute("aria-pressed")).toBe("true");
    const gridButton = screen.getByRole("button", { name: "Show browser grid" });
    expect((gridButton as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(gridButton);

    const grid = screen.getByRole("list", { name: "Running browser grid" });
    expect(grid).toBeTruthy();
    expect(screen.getAllByTestId("desktop-browser-grid-tile").map((node) => node.getAttribute("data-profile-id"))).toEqual([
      runningProfile.id,
      alternate.id,
    ]);
    expect(screen.queryByRole("button", { name: `Select ${stoppedProfile.name}` })).toBeNull();
    expect(screen.getAllByTestId("mock-profile-viewer")).toHaveLength(2);

    const selectedViewer = screen.getAllByTestId("mock-profile-viewer").find(
      (node) => node.getAttribute("data-profile-id") === runningProfile.id,
    );
    const passiveViewer = screen.getAllByTestId("mock-profile-viewer").find(
      (node) => node.getAttribute("data-profile-id") === alternate.id,
    );
    expect(selectedViewer?.getAttribute("data-can-interact")).toBe("on");
    expect(selectedViewer?.getAttribute("data-clipboard-sync")).toBe("on");
    expect(selectedViewer?.getAttribute("data-cdp-url")).toBe(runningProfile.cdp_url);
    expect(passiveViewer?.getAttribute("data-can-interact")).toBe("off");
    expect(passiveViewer?.getAttribute("data-clipboard-sync")).toBe("off");
    expect(passiveViewer?.getAttribute("data-cdp-url")).toBe("off");
  });

  it("selects a running browser from the full-view grid with an accessible button", async () => {
    const onSelectProfile = vi.fn();
    const alternate = { ...runningProfile, id: "profile-alt", name: "Alt Live" };
    render(
      <AgentBrowserWorkspace
        profiles={[runningProfile, alternate]}
        selectedProfile={runningProfile}
        canAutomate
        canInteract
        onSelectProfile={onSelectProfile}
      />,
    );

    await screen.findByTestId("agent-browser-workspace");
    fireEvent.click(screen.getByRole("button", { name: "Enter full view" }));
    fireEvent.click(screen.getByRole("button", { name: "Open desktop full-view Sessions controls" }));
    fireEvent.click(screen.getByRole("button", { name: "Show browser grid" }));

    const selected = screen.getByRole("button", { name: `Select ${runningProfile.name}` });
    const alternateButton = screen.getByRole("button", { name: `Select ${alternate.name}` });
    expect(selected.getAttribute("aria-pressed")).toBe("true");
    expect(alternateButton.getAttribute("aria-pressed")).toBe("false");
    fireEvent.keyDown(alternateButton, { key: "Enter" });
    fireEvent.click(alternateButton);
    expect(onSelectProfile).toHaveBeenCalledWith(alternate.id);
  });

  it("caps the full-view grid at six live streams and reports overflow", async () => {
    const profiles = Array.from({ length: 8 }, (_, index) => ({
      ...runningProfile,
      id: `profile-${index + 1}`,
      name: `Live ${index + 1}`,
    }));
    render(
      <AgentBrowserWorkspace
        profiles={profiles}
        selectedProfile={profiles[0]}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    await screen.findByTestId("agent-browser-workspace");
    fireEvent.click(screen.getByRole("button", { name: "Enter full view" }));
    fireEvent.click(screen.getByRole("button", { name: "Open desktop full-view Sessions controls" }));
    fireEvent.click(screen.getByRole("button", { name: "Show browser grid" }));

    expect(screen.getAllByTestId("mock-profile-viewer")).toHaveLength(6);
    expect(screen.getByText("2 more live browsers")).toBeTruthy();
  });

  it("keeps full-view grid unavailable until two browsers are running", async () => {
    render(
      <AgentBrowserWorkspace
        profiles={[runningProfile, stoppedProfile]}
        selectedProfile={runningProfile}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    await screen.findByTestId("agent-browser-workspace");
    fireEvent.click(screen.getByRole("button", { name: "Enter full view" }));
    fireEvent.click(screen.getByRole("button", { name: "Open desktop full-view Sessions controls" }));
    const gridButton = screen.getByRole("button", { name: "Show browser grid" });
    expect((gridButton as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("list", { name: "Running browser grid" })).toBeNull();
    expect(screen.getAllByTestId("mock-profile-viewer")).toHaveLength(1);
  });

  it("keeps desktop full-view controls behind exactly four compact groups", async () => {
    const onViewportApply = vi.fn().mockResolvedValue(true);
    const onSelectProfile = vi.fn();
    render(
      <AgentBrowserWorkspace
        profiles={[
          runningProfile,
          { ...runningProfile, id: "profile-alt", name: "Alt Live", screen_width: 1440, screen_height: 900 },
          stoppedProfile,
        ]}
        selectedProfile={runningProfile}
        canAutomate
        canInteract
        canManageViewport
        onViewportApply={onViewportApply}
        onSelectProfile={onSelectProfile}
      />,
    );

    await screen.findByTestId("agent-browser-workspace");
    expect(screen.queryByTestId("desktop-full-view-toolbar")).toBeNull();

    const agentSession = screen.getByLabelText("Orca agent session");
    const fullViewButton = screen.getByRole("button", { name: "Enter full view" });
    fireEvent.click(fullViewButton);

    const fullscreenViewer = screen.getByTestId("agent-browser-viewer-pane");
    const toolbar = screen.getByTestId("desktop-full-view-toolbar");
    expect(fullscreenViewer.className).toContain("fixed");
    expect(fullscreenViewer.getAttribute("role")).toBe("dialog");
    expect(fullscreenViewer.getAttribute("aria-modal")).toBe("true");
    expect(agentSession.getAttribute("aria-hidden")).toBe("true");
    expect(agentSession.hasAttribute("inert")).toBe(true);
    const fullViewGroups = screen.getAllByTestId("desktop-full-view-group");
    expect(fullViewGroups.map((node) => node.textContent)).toEqual([
      "View",
      "Viewport",
      "Sessions",
      "Exit",
    ]);
    for (const group of fullViewGroups) {
      expect(group.className).toContain("min-h-11");
      expect(group.className).toContain("min-w-11");
    }
    expect(toolbar.textContent).not.toContain("Full view");
    expect(toolbar.textContent).not.toContain("Launch");
    expect(toolbar.textContent).not.toContain("Stop");

    fireEvent.click(screen.getByRole("button", { name: "Open desktop full-view View controls" }));
    expect(screen.getByRole("button", { name: "Fit browser view" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Fit browser view to width" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Fit browser view to height" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Increase browser zoom" }));
    expect(screen.getByTestId("mock-profile-viewer").getAttribute("data-scale")).toBe("1.1");
    fireEvent.click(screen.getByRole("button", { name: "Fit browser view to width" }));
    expect(fullscreenViewer.getAttribute("data-full-view-fit")).toBe("width");
    expect(screen.getByTestId("mock-profile-viewer").getAttribute("data-fit-mode")).toBe("width");
    expect(screen.getByRole("button", { name: "Fit browser view to width" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Fit browser view to height" }));
    expect(fullscreenViewer.getAttribute("data-full-view-fit")).toBe("height");
    expect(screen.getByTestId("mock-profile-viewer").getAttribute("data-fit-mode")).toBe("height");
    expect(screen.getByRole("button", { name: "Fit browser view to height" }).getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: "Open desktop full-view Viewport controls" }));
    expect(screen.queryByRole("button", { name: "Increase browser zoom" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Fullscreen viewport width"), { target: { value: "800" } });
    fireEvent.change(screen.getByLabelText("Fullscreen viewport height"), { target: { value: "600" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply fullscreen viewport" }));
    await waitFor(() => expect(onViewportApply).toHaveBeenCalledWith(800, 600));
    expect(screen.getByTestId("agent-browser-viewer-pane").getAttribute("role")).toBe("dialog");

    fireEvent.click(screen.getByRole("button", { name: "Open desktop full-view Sessions controls" }));
    expect(screen.queryByLabelText("Fullscreen viewport width")).toBeNull();
    fireEvent.change(screen.getByLabelText("Switch full-view browser session"), {
      target: { value: "profile-alt" },
    });
    expect(onSelectProfile).toHaveBeenCalledWith("profile-alt");
    expect(screen.getByTestId("agent-browser-viewer-pane").getAttribute("role")).toBe("dialog");

    fireEvent.click(screen.getByRole("button", { name: "Exit full view" }));
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Enter full view" })),
    );
  }, 15_000);

  it("applies dynamic desktop Phone Fit and preserves full-view view state", async () => {
    const onViewportApply = vi.fn().mockResolvedValue(true);
    vi.stubGlobal("visualViewport", { width: 412.4, height: 891.6 });
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
    const fullViewButton = screen.getByRole("button", { name: "Enter full view" });
    fireEvent.click(fullViewButton);
    const fullscreenViewer = screen.getByTestId("agent-browser-viewer-pane");
    fireEvent.click(screen.getByRole("button", { name: "Open desktop full-view View controls" }));
    fireEvent.click(screen.getByRole("button", { name: "Increase browser zoom" }));
    fireEvent.click(screen.getByRole("button", { name: "Fit browser view to width" }));
    expect(screen.getByTestId("mock-profile-viewer").getAttribute("data-scale")).toBe("1.1");
    expect(fullscreenViewer.getAttribute("data-full-view-fit")).toBe("width");
    expect(screen.getByTestId("mock-profile-viewer").getAttribute("data-native-fullscreen")).toBe(
      "off",
    );

    fireEvent.click(screen.getByRole("button", { name: "Open desktop full-view Viewport controls" }));
    fireEvent.click(screen.getByRole("button", { name: "Use current phone viewport fit" }));
    await waitFor(() => expect(onViewportApply).toHaveBeenCalledWith(412, 892));
    expect(screen.getByTestId("agent-browser-viewer-pane").getAttribute("role")).toBe("dialog");

    fireEvent.click(screen.getByRole("button", { name: "Exit full view" }));
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Enter full view" })),
    );
    fireEvent.click(screen.getByRole("button", { name: "Enter full view" }));
    expect(screen.getByTestId("mock-profile-viewer").getAttribute("data-scale")).toBe("1.1");
    expect(screen.getByTestId("agent-browser-viewer-pane").getAttribute("data-full-view-fit")).toBe("width");
    expect(screen.getByTestId("mock-profile-viewer").getAttribute("data-fit-mode")).toBe("width");

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Enter full view" })),
    );
    vi.unstubAllGlobals();
  });

  it("downloads a private screenshot from compact full-view View controls", async () => {
    const createObjectUrl = vi.fn(() => "blob:profile-shot");
    const revokeObjectUrl = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectUrl });
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    apiMock.captureProfileScreenshot.mockResolvedValue(new Blob(["png"], { type: "image/png" }));

    render(
      <AgentBrowserWorkspace
        profiles={[runningProfile]}
        selectedProfile={runningProfile}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Enter full view" }));
    fireEvent.click(screen.getByRole("button", { name: "Open desktop full-view View controls" }));
    fireEvent.click(screen.getByRole("button", { name: "Capture browser screenshot" }));

    await waitFor(() => expect(apiMock.captureProfileScreenshot).toHaveBeenCalledWith(runningProfile.id));
    expect(createObjectUrl).toHaveBeenCalledTimes(1);
    expect(anchorClick).toHaveBeenCalledTimes(1);
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:profile-shot");
  });

  it("shows measured live metrics on demand inside full view", async () => {
    render(
      <AgentBrowserWorkspace
        profiles={[runningProfile]}
        selectedProfile={runningProfile}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Enter full view" }));
    expect(screen.queryByTestId("mock-full-view-live-metrics")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open desktop full-view View controls" }));
    const metricsButton = screen.getByRole("button", { name: "Show live metrics" });
    fireEvent.click(metricsButton);

    expect(metricsButton.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("mock-full-view-live-metrics").textContent).toContain(runningProfile.id);
    expect(
      screen.getByRole("dialog", { name: "Live CloakBrowser profile" }).contains(
        screen.getByTestId("mock-full-view-live-metrics"),
      ),
    ).toBe(true);
  });

  it("moves focus into desktop full view, traps tab order, redirects background focus, and restores on Escape", async () => {
    render(
      <AgentBrowserWorkspace
        profiles={[runningProfile, stoppedProfile]}
        selectedProfile={runningProfile}
        canAutomate
        canInteract
        canManageViewport
        onViewportApply={vi.fn().mockResolvedValue(true)}
        onSelectProfile={vi.fn()}
      />,
    );

    await screen.findByTestId("agent-browser-workspace");
    const launchButton = screen.getByTestId("orca-launch") as HTMLButtonElement;
    const opener = screen.getByRole("button", { name: "Enter full view" });
    fireEvent.click(opener);

    const viewGroup = screen.getByRole("button", { name: "Open desktop full-view View controls" });
    const exitGroup = screen.getByRole("button", { name: "Exit full view" });
    await waitFor(() => expect(document.activeElement).toBe(viewGroup));

    exitGroup.focus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(document.activeElement).toBe(viewGroup);

    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(exitGroup);

    launchButton.focus();
    fireEvent.focusIn(launchButton);
    expect(document.activeElement).toBe(viewGroup);

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Enter full view" })),
    );
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
    expect(fireEvent.keyDown(screen.getByTestId("orca-prompt"), { key: "Enter", code: "Enter" })).toBe(false);

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
    const promptInput = screen.getByTestId("orca-prompt");
    fireEvent.change(promptInput, {
      target: { value: "Open https://example.com and report the heading" },
    });
    expect(fireEvent.keyDown(promptInput, { key: "Enter", code: "Enter", shiftKey: true })).toBe(true);
    expect(apiMock.createTaskRun).not.toHaveBeenCalled();
    expect(fireEvent.keyDown(promptInput, { key: "Enter", code: "Enter" })).toBe(false);

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

  it("switches compact Browser Terminal and ACP modes without remounting the live viewer", async () => {
    const browserUseProfile: Profile = { ...runningProfile, harness: "browser-use" };

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
    const viewer = screen.getByTestId("mock-profile-viewer");
    const settings = screen.getByTestId("workspace-settings");
    expect(settings.hasAttribute("hidden")).toBe(true);

    fireEvent.click(screen.getByTestId("workspace-settings-toggle"));
    expect(settings.hasAttribute("hidden")).toBe(false);

    expect(screen.getByTestId("workspace-compact-mode")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Antigravity/ })).toBeNull();
    expect(screen.queryByText("Antigravity · ACPX/Grok")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "ACP" }));
    expect((screen.getByTestId("orca-agent-select") as HTMLSelectElement).value).toBe("acpx");
    expect(screen.getByRole("button", { name: "ACP" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("mock-profile-viewer")).toBe(viewer);

    fireEvent.click(screen.getByRole("button", { name: "CLI" }));
    expect((screen.getByTestId("orca-agent-select") as HTMLSelectElement).value).toBe("agy");
    expect(screen.getByRole("button", { name: "CLI" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("mock-profile-viewer")).toBe(viewer);

    fireEvent.change(screen.getByTestId("orca-agent-select"), { target: { value: "grok" } });
    expect(screen.getByRole("button", { name: "CLI" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "CLI" }));
    expect((screen.getByTestId("orca-agent-select") as HTMLSelectElement).value).toBe("grok");

    fireEvent.change(screen.getByTestId("orca-agent-select"), { target: { value: "agy" } });
    expect((screen.getByTestId("orca-agent-select") as HTMLSelectElement).value).toBe("agy");
  });

  it("starts ACPX with the selected ACP agent and renders typed outputs", async () => {
    const acpxProfile: Profile = { ...runningProfile, harness: "acpx" };
    apiMock.createTaskSession.mockResolvedValue({
      id: "task-acpx",
      profile_id: acpxProfile.id,
      sandbox_id: "default",
      title: "Inspect example.com",
      status: "active",
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      metadata: {},
    });
    apiMock.createTaskRun.mockResolvedValue({
      id: "run-acpx",
      task_session_id: "task-acpx",
      task_message_id: "message-acpx",
      profile_id: acpxProfile.id,
      profile_id_snapshot: acpxProfile.id,
      sandbox_id: "default",
      harness: "acpx",
      agent: "cursor",
      status: "running",
      launch_if_stopped: false,
      allowed_origins: ["https://example.com"],
      max_steps: 20,
      timeout_seconds: 360,
      model_alias: null,
      deadline_at: "2026-07-27T00:06:00Z",
      health_snapshot: {},
      health_decision: {},
      retry_count: 0,
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
    });
    apiMock.listTaskRunOutputs.mockResolvedValue([]);

    render(
      <AgentBrowserWorkspace
        profiles={[acpxProfile]}
        selectedProfile={acpxProfile}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    await screen.findByTestId("orca-launch");
    expect((screen.getByTestId("orca-agent-select") as HTMLSelectElement).value).toBe("acpx");
    expect(screen.getByRole("button", { name: "ACP" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByRole("button", { name: /Antigravity/ })).toBeNull();
    expect((screen.getByTestId("acpx-agent-select") as HTMLSelectElement).value).toBe("grok-build");
    fireEvent.change(screen.getByTestId("acpx-agent-select"), {
      target: { value: "opencode" },
    });
    fireEvent.change(screen.getByTestId("orca-prompt"), {
      target: { value: "Inspect https://example.com" },
    });
    fireEvent.click(screen.getByTestId("orca-launch"));

    await waitFor(() => {
      expect(apiMock.createTaskSession).toHaveBeenCalledWith(expect.objectContaining({
        metadata: { source: "agent-browser-workspace", harness: "acpx", agent: "opencode" },
      }));
      expect(apiMock.createTaskRun).toHaveBeenCalledWith(
        "task-acpx",
        expect.objectContaining({
          harness: "acpx",
          agent: "opencode",
          profile_id: acpxProfile.id,
          allowed_origins: ["https://example.com"],
        }),
      );
    });
    expect(screen.getByTestId("managed-agent-output")).toBeTruthy();
  });

  it("starts ACPX through the compact Grok Build preset without showing Antigravity on generic ACP profiles", async () => {
    const acpxProfile: Profile = { ...runningProfile, harness: "acpx" };
    apiMock.createTaskSession.mockResolvedValue({
      id: "task-acpx-claude",
      profile_id: acpxProfile.id,
      sandbox_id: "default",
      title: "Inspect example.com",
      status: "active",
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      metadata: {},
    });
    apiMock.createTaskRun.mockResolvedValue({
      id: "run-acpx-claude",
      task_session_id: "task-acpx-claude",
      task_message_id: "message-acpx-claude",
      profile_id: acpxProfile.id,
      profile_id_snapshot: acpxProfile.id,
      sandbox_id: "default",
      harness: "acpx",
      agent: "grok-build",
      status: "running",
      launch_if_stopped: false,
      allowed_origins: ["https://example.com"],
      max_steps: 20,
      timeout_seconds: 360,
      model_alias: null,
      deadline_at: "2026-07-27T00:06:00Z",
      health_snapshot: {},
      health_decision: {},
      retry_count: 0,
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
    });
    apiMock.listTaskRunOutputs.mockResolvedValue([]);

    render(
      <AgentBrowserWorkspace
        profiles={[acpxProfile]}
        selectedProfile={acpxProfile}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    await screen.findByTestId("orca-launch");
    expect(screen.queryByRole("button", { name: /Antigravity/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "ACPX" }));
    expect((screen.getByTestId("orca-agent-select") as HTMLSelectElement).value).toBe("acpx");
    expect((screen.getByTestId("acpx-agent-select") as HTMLSelectElement).value).toBe("grok-build");
    fireEvent.change(screen.getByTestId("orca-prompt"), {
      target: { value: "Inspect https://example.com" },
    });
    fireEvent.click(screen.getByTestId("orca-launch"));

    await waitFor(() => {
      expect(apiMock.createTaskSession).toHaveBeenCalledWith(expect.objectContaining({
        metadata: { source: "agent-browser-workspace", harness: "acpx", agent: "grok-build" },
      }));
      expect(apiMock.createTaskRun).toHaveBeenCalledWith(
        "task-acpx-claude",
        expect.objectContaining({
          harness: "acpx",
          agent: "grok-build",
          profile_id: acpxProfile.id,
          allowed_origins: ["https://example.com"],
        }),
      );
    });
  });

  it("starts Antigravity as the ACPX Grok Build preset with truthful labels", async () => {
    const antigravityProfile: Profile = { ...runningProfile, harness: "antigravity" };
    apiMock.createTaskSession.mockResolvedValue({
      id: "task-antigravity",
      profile_id: antigravityProfile.id,
      sandbox_id: "default",
      title: "Inspect example.com",
      status: "active",
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
      metadata: {},
    });
    apiMock.createTaskRun.mockResolvedValue({
      id: "run-antigravity",
      task_session_id: "task-antigravity",
      task_message_id: "message-antigravity",
      profile_id: antigravityProfile.id,
      profile_id_snapshot: antigravityProfile.id,
      sandbox_id: "default",
      harness: "acpx",
      agent: "grok-build",
      status: "running",
      launch_if_stopped: false,
      allowed_origins: ["https://example.com"],
      max_steps: 20,
      timeout_seconds: 360,
      model_alias: null,
      deadline_at: "2026-07-27T00:06:00Z",
      health_snapshot: {},
      health_decision: {},
      retry_count: 0,
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:00:00Z",
    });
    apiMock.listTaskRunOutputs.mockResolvedValue([]);

    render(
      <AgentBrowserWorkspace
        profiles={[antigravityProfile]}
        selectedProfile={antigravityProfile}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    await screen.findByTestId("orca-launch");
    expect((screen.getByTestId("orca-agent-select") as HTMLSelectElement).value).toBe("antigravity");
    expect(screen.getByRole("button", { name: "ACPX · Grok" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByTestId("acpx-agent-select")).toBeNull();
    expect(screen.getAllByText("Antigravity · ACPX/Grok").length).toBeGreaterThanOrEqual(1);
    fireEvent.change(screen.getByTestId("orca-prompt"), {
      target: { value: "Inspect https://example.com" },
    });
    fireEvent.click(screen.getByTestId("orca-launch"));

    await waitFor(() => {
      expect(apiMock.createTaskSession).toHaveBeenCalledWith(
        expect.objectContaining({
          metadata: {
            source: "agent-browser-workspace",
            harness: "acpx",
            agent: "grok-build",
            mode: "antigravity",
          },
        }),
      );
      expect(apiMock.createTaskRun).toHaveBeenCalledWith(
        "task-antigravity",
        expect.objectContaining({
          harness: "acpx",
          agent: "grok-build",
          profile_id: antigravityProfile.id,
          allowed_origins: ["https://example.com"],
          model_alias: null,
        }),
      );
    });
    expect(screen.getByTestId("managed-agent-output")).toBeTruthy();
  });

  it("keeps ACPX launch disabled when no fresh worker has checked in", async () => {
    const acpxProfile: Profile = { ...runningProfile, harness: "acpx" };
    apiMock.getTaskHarnessPresence.mockResolvedValue({
      harness: "acpx",
      worker_seen_recently: false,
      state: "stale",
      last_seen_at: "2026-07-26T23:59:00Z",
      reason: "The last authenticated ACPX worker check-in is stale",
    });

    render(
      <AgentBrowserWorkspace
        profiles={[acpxProfile]}
        selectedProfile={acpxProfile}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    fireEvent.change(await screen.findByTestId("orca-prompt"), {
      target: { value: "Inspect https://example.com" },
    });
    expect((screen.getByTestId("orca-launch") as HTMLButtonElement).disabled).toBe(true);
    expect(await screen.findByText("The last authenticated ACPX worker check-in is stale")).toBeTruthy();
  });

  it("keeps ACPX launch disabled when the selected ACP adapter needs authentication", async () => {
    const acpxProfile: Profile = { ...runningProfile, harness: "acpx" };
    apiMock.getTaskHarnessPreflights.mockResolvedValue({
      harness: "acpx",
      agents: [{
        agent: "grok-build",
        ready: false,
        state: "failed",
        reason_code: "auth_required",
        checked_at: "2026-07-27T00:00:00Z",
      }],
    });

    render(
      <AgentBrowserWorkspace
        profiles={[acpxProfile]}
        selectedProfile={acpxProfile}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    fireEvent.change(await screen.findByTestId("orca-prompt"), {
      target: { value: "Inspect https://example.com" },
    });
    expect((screen.getByTestId("orca-launch") as HTMLButtonElement).disabled).toBe(true);
    expect(await screen.findByText("Sign in to this ACP agent on the worker")).toBeTruthy();
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

  it("restores an ACPX run on an Antigravity profile as Antigravity mode", async () => {
    const antigravityProfile: Profile = { ...runningProfile, harness: "antigravity" };
    const completedRun: TaskRun = {
      id: "run-antigravity-restored",
      task_session_id: "task-antigravity-restored",
      task_message_id: "message-antigravity-restored",
      profile_id: antigravityProfile.id,
      profile_id_snapshot: antigravityProfile.id,
      sandbox_id: "default",
      harness: "acpx",
      agent: "grok-build",
      status: "succeeded",
      launch_if_stopped: false,
      allowed_origins: ["https://example.com"],
      max_steps: 20,
      timeout_seconds: 360,
      model_alias: null,
      deadline_at: "2026-07-27T00:06:00Z",
      health_snapshot: {},
      health_decision: {},
      retry_count: 0,
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-27T00:00:00Z",
      updated_at: "2026-07-27T00:01:00Z",
    };
    window.sessionStorage.setItem(
      `cloakbrowser.browser-use.last-run:${antigravityProfile.id}`,
      completedRun.id,
    );
    apiMock.getTaskRun.mockResolvedValue(completedRun);
    apiMock.listTaskRunOutputs.mockResolvedValue([]);

    render(
      <AgentBrowserWorkspace
        profiles={[antigravityProfile]}
        selectedProfile={antigravityProfile}
        canAutomate
        canInteract
        onSelectProfile={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect((screen.getByTestId("orca-agent-select") as HTMLSelectElement).value).toBe("antigravity");
    });
    expect(screen.getAllByText("Antigravity · ACPX/Grok").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByTestId("acpx-agent-select")).toBeNull();
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
