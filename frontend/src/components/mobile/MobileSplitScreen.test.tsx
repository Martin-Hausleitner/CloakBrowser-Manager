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
    onLogout: vi.fn(),
    ...overrides,
  };

  return {
    ...render(<MobileSplitScreen {...props} />),
    props,
  };
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

  it("live-adjusts browser pane ratio and requests noVNC visual zoom without remounting the stream", () => {
    const { props, rerender } = renderMobileSplit({ selected: runningProfile, selectedId: runningProfile.id });

    expect(screen.getByLabelText("Live view controls")).toBeTruthy();
    const livePane = screen.getByTestId("mobile-browser-frame").closest("section") as HTMLElement;
    expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("58%");
    expect(livePane.style.getPropertyValue("--mobile-browser-zoom")).toBe("");

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

    expect(screen.getByLabelText("Browser pane size").textContent).toBe("58%");
    expect(screen.getByLabelText("Visual zoom level").textContent).toBe("100%");
    expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("58%");
    expect(livePane.style.getPropertyValue("--mobile-browser-zoom")).toBe("");
    expect(props.onBrowserZoomChange).toHaveBeenLastCalledWith(100);
    expect(screen.getAllByText("VNC stream")).toHaveLength(1);
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
    expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("58%");
  });

  it("preserves a user-adjusted pane ratio across profile status changes", () => {
    const { rerender, props } = renderMobileSplit({ selected: runningProfile, selectedId: runningProfile.id });

    fireEvent.click(screen.getByLabelText("Edit browser viewport"));
    fireEvent.change(screen.getByLabelText("Browser pane"), { target: { value: "64" } });

    rerender(
      <MobileSplitScreen
        {...props}
        selected={stoppedProfile}
        selectedId={stoppedProfile.id}
      />,
    );

    const livePane = screen.getByTestId("mobile-browser-frame").closest("section") as HTMLElement;
    expect(screen.getByLabelText("Browser pane size").textContent).toBe("64%");
    expect(livePane.style.getPropertyValue("--mobile-live-pane-basis")).toBe("64%");
  });

  it("does not add fake browser chrome around the running operator surface", () => {
    renderMobileSplit({ selected: runningProfile, selectedId: runningProfile.id });

    const frame = screen.getByTestId("mobile-browser-frame");
    expect(frame.className).toContain("mobile-browser-frame-live");
    expect(screen.getByText("VNC stream")).toBeTruthy();
    expect(screen.queryByText("Browser preview")).toBeNull();
    expect(frame.querySelector(".mobile-browser-chrome")).toBeNull();
  });

  it("keeps live viewport controls available without profile management access", () => {
    renderMobileSplit({
      selected: runningProfile,
      selectedId: runningProfile.id,
      canManageProfiles: false,
    });

    expect(screen.getByLabelText("Live view controls")).toBeTruthy();
    expect(screen.getByLabelText("Browser pane")).toBeTruthy();
    expect(screen.getByLabelText("Visual zoom")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Edit browser viewport"));
    expect(screen.queryByText("Apply")).toBeNull();
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
    expect(props.onFullscreenChange).toHaveBeenLastCalledWith(true);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Fullscreen browser viewer" })).toBeNull();
    expect(props.onFullscreenChange).toHaveBeenLastCalledWith(false);
  });
});
