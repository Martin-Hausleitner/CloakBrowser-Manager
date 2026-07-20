import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Profile } from "../../lib/api";
import { MobileSplitScreen } from "./MobileSplitScreen";

const stoppedProfile: Profile = {
  id: "profile-1",
  name: "Checkout QA",
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
    browserView: <div>VNC stream</div>,
    onSelect: vi.fn(),
    onNew: vi.fn(),
    onEdit: vi.fn(),
    onLaunch: vi.fn(),
    onStop: vi.fn(),
    onViewportApply: vi.fn().mockResolvedValue(true),
    onFullscreenChange: vi.fn(),
    onLogout: vi.fn(),
    ...overrides,
  };

  return {
    ...render(<MobileSplitScreen {...props} />),
    props,
  };
}

describe("MobileSplitScreen", () => {
  it("renders the demo task and local chat input", () => {
    renderMobileSplit();

    expect(screen.getByText(/Demo task: compare the first three visible results/)).toBeTruthy();
    expect(screen.getByText("https://demo.local/task")).toBeTruthy();
    expect(screen.getByLabelText("Demo task steps")).toBeTruthy();
    expect(screen.getByPlaceholderText("Send a follow-up...")).toBeTruthy();
  });

  it("adds a local user message and demo reply when submitted", () => {
    renderMobileSplit();

    fireEvent.change(screen.getByPlaceholderText("Send a follow-up..."), {
      target: { value: "Open the pricing page" },
    });
    fireEvent.click(screen.getByLabelText("Send message"));

    expect(screen.getByText("Open the pricing page")).toBeTruthy();
    expect(screen.getByText(/Demo reply queued locally/)).toBeTruthy();
  });

  it("updates the editable viewport from a preset", () => {
    const { props } = renderMobileSplit();

    expect(screen.getByText(/390 x 844/)).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Edit browser viewport"));
    fireEvent.click(screen.getByText("Tablet"));
    fireEvent.click(screen.getByText("Apply"));

    expect(screen.getByText(/768 x 1024/)).toBeTruthy();
    expect(props.onViewportApply).toHaveBeenCalledWith(768, 1024);
  });

  it("shows the running session grid", () => {
    renderMobileSplit();

    fireEvent.click(screen.getByLabelText("Toggle grid view"));

    const grid = screen.getByLabelText("Running browser grid");
    expect(grid).toBeTruthy();
    expect(within(grid).getByText("Live Checkout QA")).toBeTruthy();
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
