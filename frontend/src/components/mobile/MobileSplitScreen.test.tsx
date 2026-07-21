import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Profile } from "../../lib/api";
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

function renderMobileSplit(overrides: Partial<Parameters<typeof MobileSplitScreen>[0]> = {}) {
  const props: Parameters<typeof MobileSplitScreen>[0] = {
    profiles: [stoppedProfile, runningProfile],
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
    onSelect: vi.fn(),
    onNew: vi.fn(),
    onEdit: vi.fn(),
    onLaunch: vi.fn(),
    onStop: vi.fn(),
    onViewportApply: vi.fn().mockResolvedValue(true),
    onFullscreenChange: vi.fn(),
    onBrowserZoomChange: vi.fn(),
    onAccessControls: vi.fn(),
    onOpenBenchmarks: vi.fn(),
    onLogout: vi.fn(),
    ...overrides,
  };

  return {
    ...render(<MobileSplitScreen {...props} />),
    props,
  };
}

function openWorkspaceTools() {
  fireEvent.click(screen.getByLabelText("Open mobile workspace tools"));
}

function openLiveViewControls() {
  fireEvent.click(screen.getByLabelText("Toggle live view controls"));
}

describe("MobileSplitScreen", () => {
  it("renders the agent task and local chat input", () => {
    renderMobileSplit();

    expect(screen.getByText(/Agent task: compare the first three visible results/)).toBeTruthy();
    expect(screen.getByText("Browser preview")).toBeTruthy();
    expect(screen.getByLabelText("Agent task steps")).toBeTruthy();
    expect(screen.getByPlaceholderText("Send a follow-up...")).toBeTruthy();
    expect(screen.getByLabelText("Attach files")).toBeTruthy();
    expect(screen.getByLabelText("Run settings")).toBeTruthy();
    expect(screen.getByLabelText("Select agent runner")).toBeTruthy();
  });

  it("adds a local user message and agent reply when submitted", () => {
    renderMobileSplit();

    fireEvent.change(screen.getByPlaceholderText("Send a follow-up..."), {
      target: { value: "Open the pricing page" },
    });
    fireEvent.click(screen.getByLabelText("Run task"));

    expect(screen.getByText("Open the pricing page")).toBeTruthy();
    expect(screen.getByText(/Agent reply queued locally/)).toBeTruthy();
  });

  it("does not submit an empty or whitespace-only agent task", () => {
    renderMobileSplit();

    fireEvent.change(screen.getByPlaceholderText("Send a follow-up..."), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByLabelText("Run task"));

    expect(screen.queryByText(/^\s+$/)).toBeNull();
    expect(screen.queryByText(/Agent reply queued locally/)).toBeNull();
    expect((screen.getByPlaceholderText("Send a follow-up...") as HTMLTextAreaElement).value).toBe("   ");
  });

  it("opens neutral agent run settings and changes the runner", () => {
    renderMobileSplit();

    fireEvent.click(screen.getByLabelText("Run settings"));
    expect(screen.getByLabelText("Agent run settings")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Select agent runner"), {
      target: { value: "local-runner" },
    });
    expect((screen.getByLabelText("Select agent runner") as HTMLSelectElement).value).toBe("local-runner");
    expect(screen.queryByText(new RegExp("Browser " + "Use", "i"))).toBeNull();
    expect(screen.queryByText(new RegExp("De" + "mo", "i"))).toBeNull();
  });

  it("updates the editable viewport from a preset", async () => {
    const { props } = renderMobileSplit();

    expect(screen.getAllByText(/390 x 844/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByLabelText("Edit browser viewport"));
    fireEvent.click(screen.getByText("Tablet"));
    const applyButton = screen.getByText("Apply");
    expect(applyButton.className).toContain("min-h-11");
    fireEvent.click(applyButton);

    expect(screen.getAllByText(/768 x 1024/).length).toBeGreaterThan(0);
    await waitFor(() => expect(props.onViewportApply).toHaveBeenCalledWith(768, 1024));
    expect(await screen.findByText("Saved")).toBeTruthy();
  });

  it("keeps the mobile gate selectors on the compact session control", () => {
    const { container } = renderMobileSplit();

    expect(container.querySelector(".mobile-toolbar")).toBeTruthy();
    expect(container.querySelector(".mobile-toolbar select.mobile-select")).toBeTruthy();
    expect(screen.getByLabelText("Edit browser viewport").getAttribute("aria-controls")).toBe("mobile-viewport-settings");
  });

  it("keeps benchmark navigation in the compact workspace tools menu", () => {
    const { props } = renderMobileSplit();

    openWorkspaceTools();
    expect(screen.getByLabelText("Mobile workspace tools")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Streaming benchmark results"));

    expect(props.onOpenBenchmarks).toHaveBeenCalledTimes(1);
  });

  it("keeps task steps compact until the feed is expanded", () => {
    renderMobileSplit();

    const stepsFeed = screen.getByLabelText("Agent task steps");
    const stepsToggle = within(stepsFeed).getByRole("button", { name: /Steps · 3/i });
    expect(stepsToggle.getAttribute("aria-expanded")).toBe("false");
    expect(within(stepsFeed).queryByText("Step 1")).toBeNull();
    expect(within(stepsFeed).getByText(/Step 3: Ready for screenshot notes/)).toBeTruthy();

    fireEvent.click(stepsToggle);

    expect(stepsToggle.getAttribute("aria-expanded")).toBe("true");
    expect(within(stepsFeed).getByText("Step 1")).toBeTruthy();
    expect(within(stepsFeed).getByText("Ran mobile task shell")).toBeTruthy();
  });

  it("keeps the browser grid and viewport tool panels mutually exclusive", () => {
    renderMobileSplit();

    const viewportButton = screen.getByLabelText("Edit browser viewport");

    openWorkspaceTools();
    const gridButton = screen.getByLabelText("Toggle grid view");
    fireEvent.click(gridButton);
    expect(screen.getByLabelText("Running browser grid")).toBeTruthy();
    expect(screen.queryByLabelText("Viewport controls")).toBeNull();

    fireEvent.click(viewportButton);
    expect(viewportButton.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByLabelText("Viewport controls")).toBeTruthy();
    expect(screen.queryByLabelText("Running browser grid")).toBeNull();

    openWorkspaceTools();
    fireEvent.click(screen.getByLabelText("Toggle grid view"));
    expect(screen.getByLabelText("Running browser grid")).toBeTruthy();
    expect(screen.queryByLabelText("Viewport controls")).toBeNull();
  });

  it("live-adjusts browser pane ratio and requests noVNC visual zoom without remounting the stream", () => {
    const { props, rerender } = renderMobileSplit({ selected: runningProfile, selectedId: runningProfile.id });

    const livePane = screen.getByTestId("mobile-browser-frame").closest("section") as HTMLElement;
    expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("50%");
    expect(livePane.style.getPropertyValue("--mobile-browser-zoom")).toBe("");
    expect(screen.queryByLabelText("Browser pane")).toBeNull();

    openLiveViewControls();

    fireEvent.change(screen.getByLabelText("Browser pane"), { target: { value: "64" } });
    fireEvent.change(screen.getByLabelText("Visual zoom"), { target: { value: "135" } });

    rerender(
      <MobileSplitScreen
        {...props}
        selected={runningProfile}
        selectedId={runningProfile.id}
        browserZoom={135}
      />,
    );

    expect(screen.getByLabelText("Browser pane size").textContent).toBe("64%");
    expect(screen.getByLabelText("Visual zoom level").textContent).toBe("135%");
    expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("64%");
    expect(livePane.style.getPropertyValue("--mobile-browser-zoom")).toBe("");
    expect(props.onBrowserZoomChange).toHaveBeenCalledWith(135);
    expect(screen.getAllByText("VNC stream")).toHaveLength(1);

    fireEvent.click(screen.getByText("Reset view"));

    rerender(
      <MobileSplitScreen
        {...props}
        selected={runningProfile}
        selectedId={runningProfile.id}
        browserZoom={100}
      />,
    );

    expect(screen.getByLabelText("Browser pane size").textContent).toBe("50%");
    expect(screen.getByLabelText("Visual zoom level").textContent).toBe("100%");
    expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("50%");
    expect(livePane.style.getPropertyValue("--mobile-browser-zoom")).toBe("");
    expect(props.onBrowserZoomChange).toHaveBeenLastCalledWith(100);
    expect(screen.getAllByText("VNC stream")).toHaveLength(1);
  });

  it("starts a short live mobile viewport compact while keeping the ratio directly adjustable", () => {
    const originalInnerHeight = window.innerHeight;
    const originalInnerWidth = window.innerWidth;
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 667 });
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 375 });

    try {
      renderMobileSplit({ selected: runningProfile, selectedId: runningProfile.id });

      const livePane = screen.getByTestId("mobile-browser-frame").closest("section") as HTMLElement;
      openLiveViewControls();
      expect(screen.getByLabelText("Browser pane size").textContent).toBe("49%");
      expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("49%");

      fireEvent.change(screen.getByLabelText("Browser pane"), { target: { value: "64" } });

      expect(screen.getByLabelText("Browser pane size").textContent).toBe("64%");
      expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("64%");
    } finally {
      Object.defineProperty(window, "innerHeight", { configurable: true, value: originalInnerHeight });
      Object.defineProperty(window, "innerWidth", { configurable: true, value: originalInnerWidth });
      fireEvent(window, new Event("resize"));
    }
  });

  it("uses compact preview sizing until a live browser is running", () => {
    const { rerender, props } = renderMobileSplit();

    let livePane = screen.getByTestId("mobile-browser-frame").closest("section") as HTMLElement;
    expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("42%");

    rerender(
      <MobileSplitScreen
        {...props}
        selected={runningProfile}
        selectedId={runningProfile.id}
      />,
    );

    livePane = screen.getByTestId("mobile-browser-frame").closest("section") as HTMLElement;
    expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("50%");
  });

  it("preserves a user-adjusted pane ratio across profile status changes", () => {
    const { rerender, props } = renderMobileSplit({ selected: runningProfile, selectedId: runningProfile.id });

    openLiveViewControls();
    fireEvent.change(screen.getByLabelText("Browser pane"), { target: { value: "64" } });

    rerender(
      <MobileSplitScreen
        {...props}
        selected={stoppedProfile}
        selectedId={stoppedProfile.id}
      />,
    );

    const livePane = screen.getByTestId("mobile-browser-frame").closest("section") as HTMLElement;
    expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("64%");
    fireEvent.click(screen.getByLabelText("Edit browser viewport"));
    expect(screen.getByLabelText("Browser pane size").textContent).toBe("64%");
  });

  it("does not add fake browser chrome around the running operator surface", () => {
    renderMobileSplit({ selected: runningProfile, selectedId: runningProfile.id });

    const frame = screen.getByTestId("mobile-browser-frame");
    expect(frame.className).toContain("mobile-browser-frame-live");
    expect(screen.getByText("VNC stream")).toBeTruthy();
    expect(screen.queryByText("Browser preview")).toBeNull();
    expect(frame.querySelector(".mobile-browser-chrome")).toBeNull();
  });

  it("renders the live VNC surface before the compact live controls", () => {
    const { container } = renderMobileSplit({ selected: runningProfile, selectedId: runningProfile.id });

    const livePane = screen.getByTestId("mobile-browser-frame").closest("section") as HTMLElement;
    const browserWrap = livePane.querySelector(".mobile-browser-wrap") as HTMLElement;

    expect(browserWrap.querySelector(".mobile-live-control-drawer")).toBeNull();
    expect(container.querySelector(".mobile-live-control-header")).toBeNull();
    expect(screen.queryByLabelText("Reset live view")).toBeNull();
    openLiveViewControls();
    expect(browserWrap.querySelector(".mobile-live-control-drawer")).toBeTruthy();
    expect(screen.getByLabelText("Reset live view").className).toContain("mobile-live-reset-button");
  });

  it("keeps pane and zoom inside an on-demand compact live control drawer", () => {
    renderMobileSplit({ selected: runningProfile, selectedId: runningProfile.id });

    expect(screen.queryByLabelText("Live view controls")).toBeNull();
    expect(screen.getByLabelText("Toggle live view controls").getAttribute("aria-expanded")).toBe("false");
    openLiveViewControls();
    const controls = screen.getByLabelText("Live view controls");
    expect(controls.querySelector(".mobile-live-control-cluster")).toBeTruthy();
    expect(controls.querySelectorAll(".mobile-live-slider")).toHaveLength(2);
    expect(screen.getByLabelText("Browser pane size").textContent).toBe("50%");
    expect(screen.getByLabelText("Visual zoom level").textContent).toBe("100%");
  });

  it("keeps live viewport controls available without profile management access", () => {
    renderMobileSplit({
      selected: runningProfile,
      selectedId: runningProfile.id,
      canManageProfiles: false,
    });

    openLiveViewControls();
    expect(screen.getByLabelText("Live view controls")).toBeTruthy();
    expect(screen.getByLabelText("Browser pane")).toBeTruthy();
    expect(screen.getByLabelText("Visual zoom")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Edit browser viewport"));
    expect(screen.queryByText("Apply")).toBeNull();
  });

  it("collapses profile administration actions while a browser is live", () => {
    renderMobileSplit({
      selected: runningProfile,
      selectedId: runningProfile.id,
      canManageAccess: true,
    });

    expect(screen.getByLabelText("Session control")).toBeTruthy();
    expect(screen.queryByLabelText("New profile")).toBeNull();
    expect(screen.queryByLabelText("Edit selected profile")).toBeNull();
    expect(screen.queryByLabelText("Browser access controls")).toBeNull();
    expect(screen.getByRole("button", { name: /Stop/i })).toBeTruthy();
  });

  it("applies a one-tap phone-fit viewport from the current visual viewport", async () => {
    const { props } = renderMobileSplit();
    vi.stubGlobal("visualViewport", { width: 412.4, height: 891.6 });

    fireEvent.click(screen.getByLabelText("Edit browser viewport"));
    fireEvent.click(screen.getByText("Phone fit"));

    await waitFor(() => expect(props.onViewportApply).toHaveBeenCalledWith(412, 892));
    expect(screen.getAllByText(/412 x 892/).length).toBeGreaterThan(0);
    vi.unstubAllGlobals();
  });

  it("keeps editable viewport settings and zoom available in fullscreen while background controls are inert", async () => {
    const { props } = renderMobileSplit({ selected: runningProfile, selectedId: runningProfile.id });

    fireEvent.click(screen.getByLabelText("Open fullscreen browser"));

    expect(screen.getByRole("dialog", { name: "Fullscreen browser viewer" })).toBeTruthy();
    expect(screen.getByLabelText("Fullscreen browser controls")).toBeTruthy();
    const controlPane = document.querySelector(".mobile-control-pane") as HTMLElement;
    expect(controlPane.hasAttribute("inert")).toBe(true);
    expect(controlPane.getAttribute("aria-hidden")).toBe("true");
    expect(screen.getByLabelText("Edit fullscreen browser viewport").className).toContain("mobile-fullscreen-action");
    expect(screen.getByLabelText("Close fullscreen browser").className).toContain("mobile-fullscreen-action");

    fireEvent.click(screen.getByLabelText("Toggle fullscreen view controls"));
    expect(screen.getByLabelText("Fullscreen view controls")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Fullscreen visual zoom"), { target: { value: "125" } });
    expect(props.onBrowserZoomChange).toHaveBeenCalledWith(125);

    fireEvent.click(screen.getByLabelText("Edit fullscreen browser viewport"));
    const editor = screen.getByLabelText("Fullscreen viewport controls");
    expect(editor).toBeTruthy();
    expect(screen.queryByLabelText("Fullscreen view controls")).toBeNull();
    fireEvent.change(screen.getByLabelText("Fullscreen viewport width"), { target: { value: "768" } });
    fireEvent.change(screen.getByLabelText("Fullscreen viewport height"), { target: { value: "1024" } });
    fireEvent.click(within(editor).getByText("Apply"));
    await waitFor(() => expect(props.onViewportApply).toHaveBeenCalledWith(768, 1024));
    expect(screen.getByRole("dialog", { name: "Fullscreen browser viewer" })).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Close fullscreen browser"));
    expect(screen.queryByRole("dialog", { name: "Fullscreen browser viewer" })).toBeNull();
    expect(document.activeElement).toBe(screen.getByLabelText("Open fullscreen browser"));
  });

  it("shows a viewport save error when persistence fails", async () => {
    renderMobileSplit({ onViewportApply: vi.fn().mockResolvedValue(false) });

    fireEvent.click(screen.getByLabelText("Edit browser viewport"));
    fireEvent.click(screen.getByText("Apply"));

    expect(await screen.findByText("Could not save viewport")).toBeTruthy();
    expect(screen.queryByText("Saved")).toBeNull();
  });

  it("shows the running session grid", () => {
    renderMobileSplit();

    openWorkspaceTools();
    fireEvent.click(screen.getByLabelText("Toggle grid view"));

    const grid = screen.getByLabelText("Running browser grid");
    expect(grid).toBeTruthy();
    expect(within(grid).getByText("Live Checkout QA")).toBeTruthy();
  });

  it("provides a mobile-sized logout action for authenticated sessions", () => {
    const { props } = renderMobileSplit({ authRequired: true, identityName: "Scoped viewer" });

    const logout = screen.getByRole("button", { name: "Log out" });
    expect(logout.className).toContain("mobile-logout-button");
    fireEvent.click(logout);
    expect(props.onLogout).toHaveBeenCalledTimes(1);
  });

  it("opens a fullscreen touch viewer without losing the stream content", () => {
    const { props } = renderMobileSplit({ selected: runningProfile, selectedId: runningProfile.id });

    fireEvent.click(screen.getByLabelText("Open fullscreen browser"));

    expect(screen.getByRole("dialog", { name: "Fullscreen browser viewer" })).toBeTruthy();
    expect(screen.getByText("VNC stream")).toBeTruthy();
    expect(screen.getAllByText("VNC stream")).toHaveLength(1);
    expect(screen.getByLabelText("Fullscreen browser controls")).toBeTruthy();
    expect(props.onFullscreenChange).toHaveBeenLastCalledWith(true);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Fullscreen browser viewer" })).toBeNull();
    expect(props.onFullscreenChange).toHaveBeenLastCalledWith(false);
  });
});
