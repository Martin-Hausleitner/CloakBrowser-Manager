import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import { UI_STATE, expectUiState } from "../lib/uiFlowRegistry";
import { LiveDevPanel } from "./LiveDevPanel";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getProfileOpenLinks: vi.fn(),
      getLiveMetrics: vi.fn(),
      getProfileHealth: vi.fn(),
    },
  };
});

beforeEach(() => {
  vi.mocked(api.getProfileOpenLinks).mockResolvedValue({
    profile_id: "profile-live",
    prefer: "local",
    mode: "cdp",
    open_url: "/open",
    session_viewer_url: "/session",
    live_url: "/live/profile-live",
    vnc_fullscreen_url: "/vnc/profile-live",
  });
  vi.mocked(api.getLiveMetrics).mockResolvedValue({
    profile_id: "profile-live",
    transport: "cdp",
    connection_state: "connected",
    fps: 28.4,
    rtt_ms: 41.2,
    frames_received: 1200,
    reconnect_count: 2,
    dropped_frames: 3,
    updated_at: "2026-07-28T20:00:00Z",
  });
  vi.mocked(api.getProfileHealth).mockResolvedValue({
    profile_id: "profile-live",
    state: "passed",
    checked_at: "2026-07-28T20:00:00Z",
    proxy_configured: true,
    proxy_reachable: true,
    outbound_ip_masked: "203.0.113.x",
    proxy_latency_ms: 24.5,
    proxy_risk_score: 4,
    proxy_authenticity_score: 91,
    fingerprint_consistency_score: 98,
    browser_scan_score: 96,
    warnings: [],
    blockers: [],
    error_code: null,
    sources: {
      proxy: "measured",
      proxy_authenticity: "measured",
      fingerprint: "measured",
      browser_scan: "measured",
    },
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("LiveDevPanel", () => {
  it("keeps mobile telemetry compact until expanded and uses measured stream and health values", async () => {
    render(
      <LiveDevPanel
        profileId="profile-live"
        running
        connectionStatus="connected"
        variant="mobile"
      />,
    );

    const toggle = await screen.findByRole("button", { name: "Show live metrics" });
    expectUiState(document.body, UI_STATE.mobileLiveMetrics);
    await waitFor(() => expect(toggle.textContent).toMatch(/28 fps/));
    expect(toggle.textContent).toMatch(/41 ms/);
    expect(toggle.textContent).toMatch(/Proxy 25 ms/);
    expect(screen.queryByRole("region", { name: "Live metrics details" })).toBeNull();
    expect(screen.queryByRole("link", { name: "CDP fullscreen" })).toBeNull();

    fireEvent.click(toggle);
    const details = screen.getByRole("region", { name: "Live metrics details" });
    for (const value of ["Frames 1200", "Reconnects 2", "Dropped 3", "FP 98", "Scan 96", "Proxy auth 91"]) {
      expect(within(details).getByText(value)).toBeTruthy();
    }
    expect(screen.getByRole("button", { name: "Hide live metrics" }).getAttribute("aria-expanded")).toBe("true");
  });

  it("keeps desktop stream links and measured proxy and anti-stealth context visible", async () => {
    render(<LiveDevPanel profileId="profile-live" running connectionStatus="connected" />);

    const panel = await screen.findByLabelText("Live developer view");
    expectUiState(document.body, UI_STATE.agentLiveMetrics);
    await waitFor(() => expect(within(panel).getByText("CDP 28 fps")).toBeTruthy());
    expect(within(panel).getByText("RTT 41 ms")).toBeTruthy();
    expect(within(panel).getByText("Proxy 25 ms")).toBeTruthy();
    expect(within(panel).getByText("FP 98")).toBeTruthy();
    expect(within(panel).getByText("Scan 96")).toBeTruthy();
    expect(within(panel).getByRole("link", { name: "CDP fullscreen" }).getAttribute("href")).toBe("/live/profile-live");
    expect(within(panel).getByRole("link", { name: "VNC fullscreen" }).getAttribute("href")).toBe("/vnc/profile-live");
  });

  it("shows unavailable sources and never invents a connected state", async () => {
    vi.mocked(api.getProfileOpenLinks).mockRejectedValue(new Error("links offline"));
    vi.mocked(api.getLiveMetrics).mockRejectedValue(new Error("metrics offline"));
    vi.mocked(api.getProfileHealth).mockRejectedValue(new Error("health offline"));

    render(<LiveDevPanel profileId="profile-live" running variant="mobile" />);

    const toggle = await screen.findByRole("button", { name: "Show live metrics" });
    await waitFor(() => expect(toggle.textContent).toContain("unknown"));
    expect(toggle.textContent).not.toContain("connected");
    fireEvent.click(toggle);
    const details = screen.getByRole("region", { name: "Live metrics details" });
    expect(within(details).getByText("Links unavailable")).toBeTruthy();
    expect(within(details).getByText("Health unavailable")).toBeTruthy();
    expect(within(details).getByText("metrics offline")).toBeTruthy();
    expect(within(details).getByText("Proxy auth unavailable")).toBeTruthy();
  });

  it("labels a derived proxy authenticity score instead of presenting it as measured", async () => {
    vi.mocked(api.getProfileHealth).mockResolvedValue({
      profile_id: "profile-live",
      state: "warning",
      checked_at: "2026-07-28T20:00:00Z",
      proxy_configured: true,
      proxy_reachable: true,
      outbound_ip_masked: "203.0.113.x",
      proxy_latency_ms: 24.5,
      proxy_risk_score: 4,
      proxy_authenticity_score: 91,
      fingerprint_consistency_score: 98,
      browser_scan_score: 96,
      warnings: [],
      blockers: [],
      error_code: null,
      sources: { proxy_authenticity: "derived" },
    });

    render(<LiveDevPanel profileId="profile-live" running variant="mobile" />);
    fireEvent.click(await screen.findByRole("button", { name: "Show live metrics" }));
    expect(await screen.findByText("Proxy auth 91 · derived")).toBeTruthy();
  });
});
