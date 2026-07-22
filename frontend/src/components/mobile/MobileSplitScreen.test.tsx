import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { api, type Profile } from "../../lib/api";
import { codexComputerUseProvider, taskHarnessReadyEvent } from "../../lib/taskHarness";
import { MobileSplitScreen } from "./MobileSplitScreen";

const stoppedProfile: Profile = {
  id: "profile-1",
  name: "Checkout QA",
  sandbox_id: "default",
  fingerprint_seed: 12345,
  proxy: null,
  timezone: null,
  locale: null,
  platform: "macos",
  user_agent: null,
  screen_width: 390,
  screen_height: 844,
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
  user_data_dir: "/tmp/profile-1",
  created_at: "2026-07-20T00:00:00Z",
  updated_at: "2026-07-20T00:00:00Z",
  tags: [],
  status: "stopped",
  vnc_ws_port: null,
  cdp_url: null,
};

const runningProfile: Profile = {
  ...stoppedProfile,
  id: "profile-2",
  name: "Live Checkout QA",
  status: "running",
  vnc_ws_port: 5901,
};

const secondRunningProfile: Profile = {
  ...runningProfile,
  id: "profile-3",
  name: "Payments QA",
};

function installTaskHarness() {
  const send = vi.fn().mockResolvedValue({
    id: "host-1",
    role: "assistant",
    content: "Harness accepted the task.",
    created_at: "2026-07-21T10:00:00.000Z",
    metadata: { provider: "codex-test" },
  });
  window.cloakBrowserHarness = {
    capabilities: {
      chat: true,
      streaming: true,
      clipboard: true,
      browser_actions: ["copy", "paste", "screenshot", "fullscreen"],
      metadata: { mode: "codex-test", provider: codexComputerUseProvider },
    },
    send,
  };
  return { send };
}

function renderMobileSplit(overrides: Partial<Parameters<typeof MobileSplitScreen>[0]> = {}) {
  const props: Parameters<typeof MobileSplitScreen>[0] = {
    profiles: [stoppedProfile, runningProfile, secondRunningProfile],
    selected: stoppedProfile,
    selectedId: stoppedProfile.id,
    error: null,
    authRequired: false,
    canManageProfiles: true,
    canOperate: true,
    canInteract: true,
    canManageAccess: false,
    identityName: null,
    browserView: <div>VNC stream</div>,
    browserZoom: 100,
    browserConnectionStatus: null,
    remoteToolsOpen: false,
    onRemoteToolsOpenChange: vi.fn(),
    onSelect: vi.fn(),
    onNew: vi.fn(),
    onEdit: vi.fn(),
    onLaunch: vi.fn(),
    onStop: vi.fn(),
    onViewportApply: vi.fn().mockResolvedValue(true),
    onFullscreenChange: vi.fn(),
    onBrowserZoomChange: vi.fn(),
    onAccessControls: vi.fn(),
    onLogout: vi.fn(),
    ...overrides,
  };

  function ControlledMobileSplit() {
    const [remoteToolsOpen, setRemoteToolsOpen] = useState(Boolean(props.remoteToolsOpen));

    return (
      <MobileSplitScreen
        {...props}
        remoteToolsOpen={remoteToolsOpen}
        onRemoteToolsOpenChange={(open) => {
          props.onRemoteToolsOpenChange(open);
          setRemoteToolsOpen(open);
        }}
      />
    );
  }

  return {
    ...render(<ControlledMobileSplit />),
    props,
  };
}

function openBrowserTools() {
  if (!screen.queryByLabelText("Browser tools")) {
    fireEvent.click(screen.getByLabelText("Open browser tools"));
  }
}

function runningSplit(overrides: Partial<Parameters<typeof MobileSplitScreen>[0]> = {}) {
  return renderMobileSplit({
    selected: runningProfile,
    selectedId: runningProfile.id,
    browserConnectionStatus: "connected",
    ...overrides,
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "listTaskSessions").mockResolvedValue([]);
  vi.spyOn(api, "listTaskSessionMessages").mockResolvedValue([]);
  vi.spyOn(api, "listTaskSessionEvents").mockResolvedValue([]);
  installTaskHarness();
});

afterEach(() => {
  delete window.cloakBrowserHarness;
  vi.unstubAllGlobals();
});

describe("MobileSplitScreen", () => {
  it("renders the default Codex Computer Use composer with browser tools and chat collapsed", async () => {
    renderMobileSplit();

    expect(await screen.findByText("Codex Computer Use")).toBeTruthy();
    expect(await screen.findByPlaceholderText("Ask Codex Computer Use...")).toBeTruthy();
    expect(screen.getByLabelText("Open browser tools")).toBeTruthy();
    expect(screen.queryByLabelText("Browser tools")).toBeNull();
    expect(screen.queryByText("Task chat")).toBeNull();
    expect(screen.queryByLabelText("Select harness runner")).toBeNull();
    await waitFor(() => expect(window.cloakBrowserHarness?.send).toBeTruthy());
  });

  it("labels the server fallback as save-only and never fabricates an assistant reply", async () => {
    window.cloakBrowserHarness = {
      capabilities: {
        chat: true,
        streaming: true,
        clipboard: true,
        browser_actions: ["paste"],
      },
    };
    vi.spyOn(api, "createTaskSession").mockResolvedValue({
      id: "server-session-1",
      profile_id: stoppedProfile.id,
      sandbox_id: stoppedProfile.sandbox_id,
      title: null,
      status: "active",
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-21T10:00:00.000Z",
      updated_at: "2026-07-21T10:00:00.000Z",
      metadata: {},
    });
    const append = vi.spyOn(api, "appendTaskMessage").mockResolvedValue({
      id: "server-message-1",
      session_id: "server-session-1",
      role: "user",
      content: "Save this task",
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-21T10:00:01.000Z",
      metadata: {},
    });
    const { container } = renderMobileSplit();

    const input = await screen.findByPlaceholderText("Save task to server history...");
    expect((input as HTMLTextAreaElement).disabled).toBe(false);

    openBrowserTools();
    expect(screen.getByText("Save only")).toBeTruthy();
    expect(screen.getByText("Tasks are saved to scoped server history only. Nothing executes until a verified Codex host attaches.")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Close browser tools"));

    fireEvent.change(input, { target: { value: "Save this task" } });
    fireEvent.click(screen.getByLabelText("Run task"));

    await waitFor(() => expect(append).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Saved to server history · not executed.")).toBeTruthy();
    expect(screen.getAllByText("Save this task")).toHaveLength(1);
    expect(container.querySelector(".mobile-message-assistant")).toBeNull();
  });

  it("loads the latest scoped server conversation and continues it after reload", async () => {
    delete window.cloakBrowserHarness;
    vi.mocked(api.listTaskSessions).mockResolvedValue([
      {
        id: "server-session-existing",
        profile_id: stoppedProfile.id,
        sandbox_id: stoppedProfile.sandbox_id,
        title: "Checkout follow-up",
        status: "active",
        created_by_kind: "user",
        created_by_id: "user-1",
        created_at: "2026-07-21T09:00:00.000Z",
        updated_at: "2026-07-21T09:05:00.000Z",
        metadata: {},
      },
    ]);
    vi.mocked(api.listTaskSessionMessages).mockResolvedValue([
      {
        id: "history-message-1",
        session_id: "server-session-existing",
        role: "user",
        content: "Open checkout",
        created_by_kind: "user",
        created_by_id: "user-1",
        created_at: "2026-07-21T09:01:00.000Z",
        metadata: {},
      },
    ]);
    const create = vi.spyOn(api, "createTaskSession");
    const append = vi.spyOn(api, "appendTaskMessage").mockResolvedValue({
      id: "history-message-2",
      session_id: "server-session-existing",
      role: "user",
      content: "Continue checkout",
      created_by_kind: "user",
      created_by_id: "user-1",
      created_at: "2026-07-21T09:06:00.000Z",
      metadata: {},
    });

    renderMobileSplit();
    fireEvent.click(screen.getByLabelText("Expand task chat"));
    expect(await screen.findByText("Open checkout")).toBeTruthy();

    const input = await screen.findByPlaceholderText("Save task to server history...");
    fireEvent.change(input, { target: { value: "Continue checkout" } });
    fireEvent.click(screen.getByLabelText("Run task"));

    await waitFor(() => expect(append).toHaveBeenCalledWith(
      "server-session-existing",
      expect.objectContaining({ text: "Continue checkout" }),
      { signal: undefined },
    ));
    expect(create).not.toHaveBeenCalled();
  });

  it("enables the composer when a valid host bridge is injected after mount", async () => {
    delete window.cloakBrowserHarness;
    renderMobileSplit();

    const serverInput = await screen.findByPlaceholderText("Save task to server history...");
    expect((serverInput as HTMLTextAreaElement).disabled).toBe(false);

    const { send } = installTaskHarness();
    window.dispatchEvent(new Event(taskHarnessReadyEvent));

    const input = await screen.findByPlaceholderText("Ask Codex Computer Use...");
    expect((input as HTMLTextAreaElement).disabled).toBe(false);

    fireEvent.change(input, {
      target: { value: "Open the late bridge target" },
    });
    fireEvent.click(screen.getByLabelText("Run task"));

    await waitFor(() =>
      expect(send).toHaveBeenCalledWith(
        {
          text: "Open the late bridge target",
          profile_id: stoppedProfile.id,
          metadata: {
            runner: "codex-computer-use",
            execution: "host",
            browser_visible: true,
          },
        },
        undefined,
      ),
    );
    expect(await screen.findByText("Harness accepted the task.")).toBeTruthy();
  });

  it("sends a task through the injected task harness with profile and runner metadata", async () => {
    const { send } = installTaskHarness();
    renderMobileSplit();

    fireEvent.change(await screen.findByPlaceholderText("Ask Codex Computer Use..."), {
      target: { value: "Open the pricing page" },
    });
    fireEvent.click(screen.getByLabelText("Run task"));

    await waitFor(() =>
      expect(send).toHaveBeenCalledWith(
        {
          text: "Open the pricing page",
          profile_id: stoppedProfile.id,
          metadata: {
            runner: "codex-computer-use",
            execution: "host",
            browser_visible: true,
          },
        },
        undefined,
      ),
    );
    expect(await screen.findByText("Harness accepted the task.")).toBeTruthy();
  });

  it("does not submit empty or whitespace-only harness tasks", async () => {
    const { send } = installTaskHarness();
    renderMobileSplit();

    const input = await screen.findByPlaceholderText("Ask Codex Computer Use...");
    fireEvent.change(input, {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByLabelText("Run task"));

    expect(send).not.toHaveBeenCalled();
    expect(screen.queryByText(/Harness accepted/)).toBeNull();
    expect((input as HTMLTextAreaElement).value).toBe("   ");
  });

  it("fits a collapsed portrait live browser to the stream aspect height", () => {
    const originalInnerHeight = window.innerHeight;
    const originalInnerWidth = window.innerWidth;
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 844 });
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });

    try {
      runningSplit({
        selected: { ...runningProfile, screen_width: 1024, screen_height: 576 },
      });

      const livePane = screen.getByTestId("mobile-browser-frame").closest("section") as HTMLElement;
      expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("591px");
      expect(livePane.classList.contains("mobile-live-pane-fit")).toBe(true);
      expect(screen.queryByLabelText("Chat history")).toBeNull();
      expect(screen.getByLabelText("Expand task chat").getAttribute("aria-expanded")).toBe("false");

      openBrowserTools();
      expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("263px");
      expect(livePane.classList.contains("mobile-live-pane-fit")).toBe(true);
    } finally {
      Object.defineProperty(window, "innerHeight", { configurable: true, value: originalInnerHeight });
      Object.defineProperty(window, "innerWidth", { configurable: true, value: originalInnerWidth });
      fireEvent(window, new Event("resize"));
    }
  });

  it("keeps the aspect-fit live browser height when task chat is expanded", () => {
    const originalInnerHeight = window.innerHeight;
    const originalInnerWidth = window.innerWidth;
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 844 });
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });

    try {
      const { container } = runningSplit({
        selected: { ...runningProfile, screen_width: 1024, screen_height: 576 },
      });
      const workspace = container.querySelector(".mobile-split-root") as HTMLElement;
      const livePane = screen.getByTestId("mobile-browser-frame").closest("section") as HTMLElement;

      fireEvent.click(screen.getByLabelText("Expand task chat"));

      expect(workspace.classList.contains("mobile-workspace-collapsed")).toBe(false);
      expect(screen.getByLabelText("Chat history")).toBeTruthy();
      expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("263px");
      expect(livePane.classList.contains("mobile-live-pane-fit")).toBe(true);
    } finally {
      Object.defineProperty(window, "innerHeight", { configurable: true, value: originalInnerHeight });
      Object.defineProperty(window, "innerWidth", { configurable: true, value: originalInnerWidth });
      fireEvent(window, new Event("resize"));
    }
  });

  it("caps a portrait remote browser fit so lower mobile controls remain visible", () => {
    const originalInnerHeight = window.innerHeight;
    const originalInnerWidth = window.innerWidth;
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 844 });
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });

    try {
      runningSplit({
        selected: { ...runningProfile, screen_width: 390, screen_height: 844 },
      });

      const livePane = screen.getByTestId("mobile-browser-frame").closest("section") as HTMLElement;
      expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("688px");
      expect(livePane.classList.contains("mobile-live-pane-fit")).toBe(true);
    } finally {
      Object.defineProperty(window, "innerHeight", { configurable: true, value: originalInnerHeight });
      Object.defineProperty(window, "innerWidth", { configurable: true, value: originalInnerWidth });
      fireEvent(window, new Event("resize"));
    }
  });

  it("recomputes the aspect-fit live browser height on viewport resize events", async () => {
    const originalInnerHeight = window.innerHeight;
    const originalInnerWidth = window.innerWidth;
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 844 });
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });

    try {
      runningSplit({
        selected: { ...runningProfile, screen_width: 1024, screen_height: 576 },
      });
      const livePane = screen.getByTestId("mobile-browser-frame").closest("section") as HTMLElement;
      expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("591px");

      Object.defineProperty(window, "innerWidth", { configurable: true, value: 430 });
      await act(async () => {
        fireEvent(window, new Event("resize"));
      });
      await waitFor(() => expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("591px"));
    } finally {
      Object.defineProperty(window, "innerHeight", { configurable: true, value: originalInnerHeight });
      Object.defineProperty(window, "innerWidth", { configurable: true, value: originalInnerWidth });
      fireEvent(window, new Event("resize"));
    }
  });

  it("recomputes the aspect-fit live browser height on visual viewport resize events", async () => {
    let resizeHandler: (() => void) | null = null;
    vi.stubGlobal("visualViewport", {
      width: 390,
      height: 844,
      addEventListener: vi.fn((event: string, handler: () => void) => {
        if (event === "resize") resizeHandler = handler;
      }),
      removeEventListener: vi.fn(),
    });

    runningSplit({
      selected: { ...runningProfile, screen_width: 1024, screen_height: 576 },
    });

    const livePane = screen.getByTestId("mobile-browser-frame").closest("section") as HTMLElement;
    expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("591px");

    Object.assign(window.visualViewport!, { width: 420, height: 844 });
    await act(async () => {
      resizeHandler?.();
    });

    await waitFor(() => expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("591px"));
  });

  it("shrinks the workspace to the visual viewport while the mobile keyboard is open", async () => {
    const originalInnerHeight = window.innerHeight;
    const originalInnerWidth = window.innerWidth;
    let resizeHandler: (() => void) | null = null;
    vi.stubGlobal("visualViewport", {
      width: 390,
      height: 844,
      offsetTop: 0,
      addEventListener: vi.fn((event: string, handler: () => void) => {
        if (event === "resize") resizeHandler = handler;
      }),
      removeEventListener: vi.fn(),
    });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 844 });
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });

    try {
      const { container } = runningSplit();
      const workspace = container.querySelector(".mobile-split-root") as HTMLElement;
      const input = await screen.findByPlaceholderText("Ask Codex Computer Use...") as HTMLTextAreaElement;

      expect(workspace.style.getPropertyValue("--mobile-visual-viewport-height")).toBe("844px");
      expect(workspace.classList.contains("mobile-keyboard-open")).toBe(false);

      await act(async () => input.focus());
      expect(document.activeElement).toBe(input);
      Object.assign(window.visualViewport!, { height: 420 });
      await act(async () => {
        resizeHandler?.();
      });

      await waitFor(() => {
        expect(workspace.style.getPropertyValue("--mobile-visual-viewport-height")).toBe("420px");
        expect(workspace.classList.contains("mobile-keyboard-open")).toBe(true);
      });
      expect(screen.getByRole("textbox", { name: "Browser task" })).toBeTruthy();
      expect(screen.getByLabelText("Run task")).toBeTruthy();

      await act(async () => input.blur());
      await waitFor(() => expect(workspace.classList.contains("mobile-keyboard-open")).toBe(true));

      Object.assign(window.visualViewport!, { height: 844 });
      await act(async () => {
        resizeHandler?.();
      });
      await waitFor(() => expect(workspace.classList.contains("mobile-keyboard-open")).toBe(false));
    } finally {
      Object.defineProperty(window, "innerHeight", { configurable: true, value: originalInnerHeight });
      Object.defineProperty(window, "innerWidth", { configurable: true, value: originalInnerWidth });
      fireEvent(window, new Event("resize"));
    }
  });

  it("combines icon-only Full Tools Chat controls with the task composer", () => {
    const { container } = runningSplit();

    const dock = container.querySelector(".mobile-command-dock");
    expect(dock).toBeTruthy();
    expect(dock?.closest("form")).toBe(screen.getByRole("textbox", { name: "Browser task" }).closest("form"));
    expect(dock?.querySelectorAll("button")).toHaveLength(3);
    expect(within(dock as HTMLElement).getByTitle("Fullscreen browser (Ctrl+B)")).toBeTruthy();
    expect(within(dock as HTMLElement).getByTitle("Browser tools (Ctrl+K)")).toBeTruthy();
    expect(within(dock as HTMLElement).getByTitle("Toggle chat (Ctrl+J)")).toBeTruthy();
    expect(within(dock as HTMLElement).queryByText("Full")).toBeNull();
    expect(within(dock as HTMLElement).queryByText("Tools")).toBeNull();
    expect(within(dock as HTMLElement).queryByText("Chat")).toBeNull();
    expect(screen.getByLabelText("Run task")).toBeTruthy();
    expect(screen.queryByText("Task chat")).toBeNull();
    expect(screen.queryByText("Benchmarks")).toBeNull();
    expect(screen.queryByLabelText("Streaming benchmark results")).toBeNull();
  });

  it("lets the browser consume unused workspace until chat or tools are opened", () => {
    const { container } = runningSplit();
    const workspace = container.querySelector(".mobile-split-root") as HTMLElement;

    expect(workspace.classList.contains("mobile-workspace-collapsed")).toBe(true);

    openBrowserTools();
    expect(workspace.classList.contains("mobile-workspace-collapsed")).toBe(false);

    fireEvent.click(screen.getByLabelText("Close browser tools"));
    expect(workspace.classList.contains("mobile-workspace-collapsed")).toBe(true);

    fireEvent.click(screen.getByLabelText("Expand task chat"));
    expect(workspace.classList.contains("mobile-workspace-collapsed")).toBe(false);
  });

  it("keeps profile and browser actions inside the central tools sheet without a harness picker", () => {
    runningSplit({ canManageAccess: true });

    expect(screen.queryByRole("button", { name: /Stop/i })).toBeNull();
    expect(screen.queryByLabelText("New profile")).toBeNull();
    expect(screen.queryByLabelText("Edit selected profile")).toBeNull();
    expect(screen.queryByLabelText("Browser access controls")).toBeNull();

    openBrowserTools();

    const tools = screen.getByLabelText("Browser tools");
    expect(within(tools).getByRole("button", { name: /Stop/i })).toBeTruthy();
    expect(within(tools).queryByLabelText("New profile")).toBeNull();
    fireEvent.click(within(tools).getByLabelText("Toggle browser administration"));
    expect(within(tools).getByLabelText("New profile")).toBeTruthy();
    expect(within(tools).getByLabelText("Edit selected profile")).toBeTruthy();
    expect(within(tools).getByLabelText("Browser access controls")).toBeTruthy();
    expect(within(tools).queryByLabelText("Select harness runner")).toBeNull();
  });

  it("runs compact pinned browser actions only through the verified Codex host", async () => {
    const { send } = installTaskHarness();
    runningSplit();
    openBrowserTools();

    const capture = await screen.findByLabelText("Run Capture with Codex Computer Use");
    await waitFor(() => expect((capture as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(capture);

    await waitFor(() =>
      expect(send).toHaveBeenCalledWith(
        {
          text: "Capture the current browser view.",
          commands: [
            {
              id: "capture-browser",
              label: "Capture",
              kind: "screenshot",
              scope: "host",
            },
          ],
          profile_id: runningProfile.id,
          metadata: {
            runner: "codex-computer-use",
            execution: "host",
            browser_visible: true,
            source: "pinned-action",
          },
        },
        undefined,
      ),
    );
    expect(await screen.findByText("Harness accepted the task.")).toBeTruthy();
    expect(screen.queryByLabelText("Browser tools")).toBeNull();
    expect(screen.getByLabelText("Chat history")).toBeTruthy();
  });

  it("keeps viewport and grid panels mutually exclusive inside browser tools", async () => {
    const { props } = renderMobileSplit();
    const workspace = document.querySelector(".mobile-split-root") as HTMLElement;
    openBrowserTools();
    expect(workspace.classList.contains("mobile-detail-panel-open")).toBe(false);

    fireEvent.click(screen.getByLabelText("Toggle grid view"));
    expect(screen.getByLabelText("Running browser grid")).toBeTruthy();
    expect(screen.queryByLabelText("Viewport controls")).toBeNull();
    expect(workspace.classList.contains("mobile-detail-panel-open")).toBe(true);

    fireEvent.click(screen.getByLabelText("Edit browser viewport"));
    expect(screen.getByLabelText("Viewport controls")).toBeTruthy();
    expect(screen.queryByLabelText("Running browser grid")).toBeNull();
    expect(screen.queryByLabelText("Pinned browser actions")).toBeNull();
    expect(workspace.classList.contains("mobile-detail-panel-open")).toBe(true);
    expect(screen.getByRole("button", { name: "Apply" }).classList.contains("min-h-11")).toBe(true);

    fireEvent.click(screen.getByText("Tablet"));
    fireEvent.click(screen.getByText("Apply"));
    await waitFor(() => expect(props.onViewportApply).toHaveBeenCalledWith(768, 1024));
    expect(await screen.findByText("Saved")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Toggle grid view"));
    expect(screen.getByLabelText("Running browser grid")).toBeTruthy();
    expect(screen.queryByLabelText("Viewport controls")).toBeNull();
  });

  it("renders honest session grid cards with status name and resolution instead of simulated browser art", () => {
    const { container } = runningSplit();
    openBrowserTools();
    fireEvent.click(screen.getByLabelText("Toggle grid view"));

    const grid = screen.getByLabelText("Running browser grid");
    expect(within(grid).getByText("Live Checkout QA")).toBeTruthy();
    expect(within(grid).getByText("Payments QA")).toBeTruthy();
    expect(within(grid).getAllByText(/390 x 844/).length).toBeGreaterThan(0);
    expect(within(grid).getAllByText("Live").length).toBeGreaterThan(0);
    expect(container.querySelector(".mobile-grid-thumb")).toBeNull();
    expect(container.querySelector(".mobile-grid-preview")).toBeNull();
  });

  it("adjusts pane ratio and noVNC zoom from the central browser tools without remounting the stream", () => {
    const { props } = runningSplit();
    const livePane = screen.getByTestId("mobile-browser-frame").closest("section") as HTMLElement;

    expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toMatch(/px$/);
    expect(screen.queryByLabelText("Browser pane")).toBeNull();

    openBrowserTools();
    expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toMatch(/px$/);
    fireEvent.click(screen.getByLabelText("Edit browser viewport"));
    fireEvent.change(screen.getByLabelText("Browser pane"), { target: { value: "64" } });
    fireEvent.change(screen.getByLabelText("Visual zoom"), { target: { value: "135" } });

    expect(screen.getByLabelText("Browser pane size").textContent).toBe("64%");
    expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("64%");
    expect(props.onBrowserZoomChange).toHaveBeenCalledWith(135);
    expect(screen.getAllByText("VNC stream")).toHaveLength(1);

    fireEvent.click(screen.getByLabelText("Reset live view"));
    expect(screen.getByLabelText("Browser pane size").textContent).toBe("68%");
    expect(props.onBrowserZoomChange).toHaveBeenLastCalledWith(100);
    expect(screen.getAllByText("VNC stream")).toHaveLength(1);
  });

  it("keeps local view controls but hides persistent viewport fields without profile management access", () => {
    runningSplit({ canManageProfiles: false });

    openBrowserTools();

    expect(screen.getByLabelText("Edit browser viewport")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Edit browser viewport"));
    expect(screen.getByLabelText("Browser pane")).toBeTruthy();
    expect(screen.getByLabelText("Visual zoom")).toBeTruthy();
    expect(screen.queryByLabelText("Viewport width")).toBeNull();
    expect(screen.getByText("Viewport changes require profile management access.")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Open fullscreen browser"));
    expect(screen.getByLabelText("Toggle fullscreen view controls")).toBeTruthy();
    expect(screen.queryByLabelText("Edit fullscreen browser viewport")).toBeNull();
  });

  it("applies a one-tap phone-fit viewport from the current visual viewport", async () => {
    const { props } = renderMobileSplit();
    vi.stubGlobal("visualViewport", { width: 412.4, height: 891.6 });

    openBrowserTools();
    fireEvent.click(screen.getByLabelText("Edit browser viewport"));
    fireEvent.click(screen.getByText("Phone fit"));

    await waitFor(() => expect(props.onViewportApply).toHaveBeenCalledWith(412, 892));
    expect(screen.getAllByText(/412 x 892/).length).toBeGreaterThan(0);
  });

  it("keeps the complete device viewport when phone-fit is applied to a live browser", async () => {
    const { props } = runningSplit();
    vi.stubGlobal("visualViewport", { width: 390, height: 844 });

    openBrowserTools();
    fireEvent.click(screen.getByLabelText("Edit browser viewport"));
    fireEvent.click(screen.getByText("Phone fit"));

    await waitFor(() => expect(props.onViewportApply).toHaveBeenCalledWith(390, 844));
  });

  it("shows live viewport restart state and prevents duplicate apply submissions", async () => {
    const apply = deferred<boolean>();
    const onViewportApply = vi.fn().mockReturnValue(apply.promise);
    runningSplit({ onViewportApply });

    openBrowserTools();
    fireEvent.click(screen.getByLabelText("Edit browser viewport"));
    expect(screen.getByText("Restarts live browser to apply")).toBeTruthy();
    expect(screen.queryByText(/next launch/i)).toBeNull();

    const applyButton = screen.getByRole("button", { name: "Apply" });
    fireEvent.click(applyButton);

    expect(await screen.findByText("Restarting live browser...")).toBeTruthy();
    const applyingButton = screen.getByRole("button", { name: "Applying..." }) as HTMLButtonElement;
    expect(applyingButton.disabled).toBe(true);
    fireEvent.click(applyingButton);
    expect(onViewportApply).toHaveBeenCalledTimes(1);

    apply.resolve(true);
    expect(await screen.findByText("Saved")).toBeTruthy();
  });

  it("shows an apply error when a running viewport restart fails", async () => {
    runningSplit({ onViewportApply: vi.fn().mockResolvedValue(false) });

    openBrowserTools();
    fireEvent.click(screen.getByLabelText("Edit browser viewport"));
    fireEvent.click(screen.getByText("Apply"));

    expect(await screen.findByText("Could not apply viewport")).toBeTruthy();
    expect(screen.queryByText("Could not save viewport")).toBeNull();
  });

  it("keeps editable viewport settings and zoom available in fullscreen while background controls are inert", async () => {
    const { props } = runningSplit();

    fireEvent.click(screen.getByLabelText("Open fullscreen browser"));

    const fullscreenDialog = screen.getByRole("dialog", { name: "Fullscreen browser viewer" }) as HTMLElement;
    expect(fullscreenDialog).toBeTruthy();
    expect(fullscreenDialog.getAttribute("data-fullscreen-fit")).toBe("contain");
    expect(screen.getByLabelText("Fullscreen browser controls")).toBeTruthy();
    const controlPane = document.querySelector(".mobile-control-pane") as HTMLElement;
    expect(controlPane.hasAttribute("inert")).toBe(true);
    expect(controlPane.getAttribute("aria-hidden")).toBe("true");

    fireEvent.click(screen.getByLabelText("Toggle fullscreen view controls"));
    expect(screen.getByLabelText("Fullscreen view controls")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Fit fullscreen browser to width"));
    fireEvent.change(screen.getByLabelText("Fullscreen visual zoom"), { target: { value: "125" } });
    expect(screen.getByLabelText("Fit fullscreen browser to width").getAttribute("aria-pressed")).toBe("true");
    expect(fullscreenDialog.getAttribute("data-fullscreen-fit")).toBe("width");
    expect(props.onBrowserZoomChange).toHaveBeenCalledWith(125);

    fireEvent.click(screen.getByLabelText("Edit fullscreen browser viewport"));
    const editor = screen.getByLabelText("Fullscreen viewport controls");
    expect(editor).toBeTruthy();
    expect(screen.queryByLabelText("Fullscreen view controls")).toBeNull();
    fireEvent.change(screen.getByLabelText("Fullscreen viewport width"), { target: { value: "768" } });
    fireEvent.change(screen.getByLabelText("Fullscreen viewport height"), { target: { value: "1024" } });
    fireEvent.click(within(editor).getByText("Apply"));
    await waitFor(() => expect(props.onViewportApply).toHaveBeenCalledWith(768, 1024));

    fireEvent.click(screen.getByLabelText("Close fullscreen browser"));
    expect(screen.queryByRole("dialog", { name: "Fullscreen browser viewer" })).toBeNull();
    expect(document.activeElement).toBe(screen.getByLabelText("Open fullscreen browser"));
  });

  it("switches running sessions from fullscreen and keeps fullscreen panels mutually exclusive", async () => {
    const { props } = runningSplit();

    fireEvent.click(screen.getByLabelText("Open fullscreen browser"));
    fireEvent.click(screen.getByLabelText("Toggle fullscreen view controls"));
    expect(screen.getByLabelText("Fullscreen view controls")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Switch fullscreen browser session"));
    const sessions = screen.getByLabelText("Fullscreen running sessions");
    expect(sessions).toBeTruthy();
    expect(screen.queryByLabelText("Fullscreen view controls")).toBeNull();
    expect(within(sessions).getByText("Live Checkout QA")).toBeTruthy();
    expect(within(sessions).getByText("Payments QA")).toBeTruthy();
    expect(within(sessions).getAllByText("390 x 844").length).toBeGreaterThan(0);
    expect(within(sessions).getAllByText("Live").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByLabelText("Edit fullscreen browser viewport"));
    expect(screen.getByLabelText("Fullscreen viewport controls")).toBeTruthy();
    expect(screen.queryByLabelText("Fullscreen running sessions")).toBeNull();

    fireEvent.click(screen.getByLabelText("Switch fullscreen browser session"));
    fireEvent.click(within(screen.getByLabelText("Fullscreen running sessions")).getByText("Payments QA"));
    expect(props.onSelect).toHaveBeenCalledWith(secondRunningProfile.id);
    await waitFor(() => expect(screen.queryByLabelText("Fullscreen running sessions")).toBeNull());
  });

  it("applies fullscreen Phone fit through the current visual viewport", async () => {
    const { props } = runningSplit();
    vi.stubGlobal("visualViewport", { width: 412.4, height: 891.6 });

    fireEvent.click(screen.getByLabelText("Open fullscreen browser"));
    fireEvent.click(screen.getByLabelText("Edit fullscreen browser viewport"));
    fireEvent.click(within(screen.getByLabelText("Fullscreen viewport controls")).getByText("Phone fit"));

    await waitFor(() => expect(props.onViewportApply).toHaveBeenCalledWith(412, 892));
  });

  it("does not offer fullscreen when no browser is live", () => {
    renderMobileSplit();

    expect(screen.queryByLabelText("Open fullscreen browser")).toBeNull();
    fireEvent.keyDown(window, { key: "b", ctrlKey: true });
    expect(screen.queryByRole("dialog", { name: "Fullscreen browser viewer" })).toBeNull();
  });

  it("uses Ctrl or Cmd shortcuts for fullscreen chat and browser tools", () => {
    runningSplit();

    fireEvent.keyDown(window, { key: "j", ctrlKey: true });
    expect(screen.getByLabelText("Chat history")).toBeTruthy();

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByLabelText("Browser tools")).toBeTruthy();
    expect(screen.queryByLabelText("Chat history")).toBeNull();

    fireEvent.keyDown(window, { key: "b", metaKey: true });
    expect(screen.getByRole("dialog", { name: "Fullscreen browser viewer" })).toBeTruthy();

    fireEvent.keyDown(window, { key: "b", ctrlKey: true });
    expect(screen.queryByRole("dialog", { name: "Fullscreen browser viewer" })).toBeNull();
  });

  it("keeps expanded chat and browser tools mutually exclusive", () => {
    runningSplit();

    fireEvent.click(screen.getByLabelText("Expand task chat"));
    expect(screen.getByLabelText("Chat history")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Open browser tools"));
    expect(screen.getByLabelText("Browser tools")).toBeTruthy();
    expect(screen.queryByLabelText("Chat history")).toBeNull();

    fireEvent.click(screen.getByLabelText("Expand task chat"));
    expect(screen.getByLabelText("Chat history")).toBeTruthy();
    expect(screen.queryByLabelText("Browser tools")).toBeNull();
  });

  it("does not steal Ctrl or Cmd shortcuts while typing in the task input", async () => {
    runningSplit();

    const input = await screen.findByPlaceholderText("Ask Codex Computer Use...");
    fireEvent.focus(input);
    fireEvent.keyDown(input, { key: "j", ctrlKey: true });
    fireEvent.keyDown(input, { key: "k", ctrlKey: true });
    fireEvent.keyDown(input, { key: "b", metaKey: true });

    expect(screen.queryByLabelText("Chat history")).toBeNull();
    expect(screen.queryByLabelText("Browser tools")).toBeNull();
    expect(screen.queryByRole("dialog", { name: "Fullscreen browser viewer" })).toBeNull();
  });

  it("shows a viewport save error when persistence fails", async () => {
    renderMobileSplit({ onViewportApply: vi.fn().mockResolvedValue(false) });

    openBrowserTools();
    fireEvent.click(screen.getByLabelText("Edit browser viewport"));
    fireEvent.click(screen.getByText("Apply"));

    expect(await screen.findByText("Could not save viewport")).toBeTruthy();
    expect(screen.queryByText("Saved")).toBeNull();
  });

  it("provides a mobile-sized logout action for authenticated sessions", () => {
    const { props } = renderMobileSplit({ authRequired: true, identityName: "Scoped viewer" });

    expect(screen.queryByRole("button", { name: "Log out" })).toBeNull();
    openBrowserTools();

    const tools = screen.getByLabelText("Browser tools");
    expect(within(tools).getByText("Signed in as Scoped viewer")).toBeTruthy();
    const logout = within(tools).getByRole("button", { name: "Log out" });
    expect(logout.className).toContain("mobile-logout-button");
    fireEvent.click(logout);
    expect(props.onLogout).toHaveBeenCalledTimes(1);
  });
});
