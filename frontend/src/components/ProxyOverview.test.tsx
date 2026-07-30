import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Profile, ProxyInventoryItem } from "../lib/api";
import { api } from "../lib/api";
import { UI_STATE, expectUiState } from "../lib/uiFlowRegistry";
import { ProxyOverview } from "./ProxyOverview";

vi.mock("../lib/api", () => ({
  api: {
    listProxies: vi.fn(),
    checkProxy: vi.fn(),
    createProfileFromProxy: vi.fn(),
  },
}));

const baseProxy: ProxyInventoryItem = {
  id: "proxy-1",
  label: "Residential Lisbon",
  host_masked: "198.51.100.xxx",
  port: 8080,
  username_masked: "li***on",
  has_credentials: true,
  active: true,
  check_state: "passed",
  reachable: true,
  latency_ms: 91.4,
  risk_score: 12,
  authenticity_score: 96,
  country_code: "PT",
  timezone_hint: "Europe/Lisbon",
  locale_hint: "pt-PT",
  warnings: [],
  blockers: [],
  last_checked_at: "2026-07-27T10:30:00Z",
  created_at: "2026-07-20T00:00:00Z",
  updated_at: "2026-07-27T10:31:00Z",
};

const createdProfile: Profile = {
  id: "profile-proxy-1",
  name: "Residential Lisbon",
  sandbox_id: "default",
  project_id: "qa",
  folder_path: "",
  pinned: false,
  accent_color: null,
  harness: "browser-use",
  fingerprint_seed: 42,
  proxy: "proxy-1",
  timezone: "Europe/Lisbon",
  locale: "pt-PT",
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
  extension_ids: [],
  launch_args: [],
  notes: null,
  user_data_dir: "/tmp/profile-proxy-1",
  created_at: "2026-07-27T10:31:00Z",
  updated_at: "2026-07-27T10:31:00Z",
  tags: [],
  status: "stopped",
  vnc_ws_port: null,
  cdp_url: null,
};

function proxy(overrides: Partial<ProxyInventoryItem> = {}): ProxyInventoryItem {
  return { ...baseProxy, ...overrides };
}

function setProxyOverviewMedia(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

function renderProxyOverview(onProfileCreated = vi.fn()) {
  render(
    <ProxyOverview
      harness="browser-use"
      projectId="qa"
      onProfileCreated={onProfileCreated}
    />,
  );
  return onProfileCreated;
}

beforeEach(() => {
  setProxyOverviewMedia(false);
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, get: () => 1200 });
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get: () => 600 });
  vi.mocked(api.listProxies).mockReset();
  vi.mocked(api.checkProxy).mockReset();
  vi.mocked(api.createProfileFromProxy).mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ProxyOverview", () => {
  it("preserves loading, error, and empty UI states", async () => {
    let resolveList: (items: ProxyInventoryItem[]) => void = () => undefined;
    vi.mocked(api.listProxies).mockReturnValue(
      new Promise((resolve) => {
        resolveList = resolve;
      }),
    );

    const { unmount } = render(<ProxyOverview harness="codex" projectId="qa" onProfileCreated={vi.fn()} />);
    expect(screen.getByText(/Loading proxy pool/)).toBeTruthy();
    expectUiState(document.body, UI_STATE.proxyOverviewLoading);

    resolveList([]);
    expect(await screen.findByText("No proxies in inventory yet.")).toBeTruthy();
    expectUiState(document.body, UI_STATE.proxyOverviewEmpty);
    unmount();

    vi.mocked(api.listProxies).mockRejectedValue(new Error("Proxy API offline"));
    render(<ProxyOverview harness="codex" projectId="qa" onProfileCreated={vi.fn()} />);
    expect(await screen.findByText("Proxy API offline")).toBeTruthy();
    expectUiState(document.body, UI_STATE.proxyOverviewError);
  });

  it("renders a desktop AG Grid with proxy fields, quick filtering, and actions", async () => {
    const onProfileCreated = vi.fn();
    vi.mocked(api.listProxies).mockResolvedValue([
      proxy(),
      proxy({
        id: "proxy-2",
        label: "Datacenter Tokyo",
        username_masked: null,
        has_credentials: false,
        active: false,
        check_state: "warning",
        latency_ms: 210,
        risk_score: 48,
        authenticity_score: 71,
        country_code: "JP",
        timezone_hint: "Asia/Tokyo",
        locale_hint: "ja-JP",
        warnings: ["elevated_risk"],
        blockers: ["proxychecker_unavailable"],
        last_checked_at: null,
      }),
    ]);
    vi.mocked(api.checkProxy).mockResolvedValue(proxy({ latency_ms: 72.2, updated_at: "2026-07-27T11:00:00Z" }));
    vi.mocked(api.createProfileFromProxy).mockResolvedValue(createdProfile);

    renderProxyOverview(onProfileCreated);

    const grid = await screen.findByTestId("proxy-desktop-grid");
    expect(screen.getByTestId("proxy-overview").className).toContain("max-w-none");
    expect(await within(grid).findByText("State")).toBeTruthy();
    expect(within(grid).getByText("Label")).toBeTruthy();
    expect(within(grid).getByText("Country")).toBeTruthy();
    expect(within(grid).getByText("Credentials")).toBeTruthy();
    expect(within(grid).getByText("Latency")).toBeTruthy();
    expect(within(grid).getByText("Risk")).toBeTruthy();
    expect(within(grid).getByText("Authenticity")).toBeTruthy();
    expect(within(grid).getByText("Timezone")).toBeTruthy();
    expect(within(grid).getByText("Locale")).toBeTruthy();
    expect(within(grid).getByText("Last checked")).toBeTruthy();
    expect(within(grid).getByText("Active")).toBeTruthy();
    expect(within(grid).getByText("Actions")).toBeTruthy();

    expect(within(grid).getByText("Residential Lisbon")).toBeTruthy();
    expect(within(grid).getByText("PT")).toBeTruthy();
    expect(within(grid).getByText("li***on")).toBeTruthy();
    expect(within(grid).getByText("91 ms")).toBeTruthy();
    expect(within(grid).getAllByText("12").length).toBeGreaterThan(0);
    expect(within(grid).getByText("96")).toBeTruthy();
    expect(within(grid).getByText("Europe/Lisbon")).toBeTruthy();
    expect(within(grid).getByText("pt-PT")).toBeTruthy();
    expect(within(grid).getByText("2026-07-27 10:30")).toBeTruthy();
    expect(within(grid).getByText("Yes")).toBeTruthy();
    expect(within(grid).getByText("No credentials")).toBeTruthy();

    fireEvent.click(within(grid).getByText("Residential Lisbon"));
    const detail = await screen.findByRole("region", {
      name: "Proxy checker details for Residential Lisbon",
    });
    expect(screen.getByTestId("proxy-desktop-grid")).toBeTruthy();
    for (const value of [
      "198.51.100.xxx",
      "8080",
      "li***on",
      "Reachable",
      "91 ms",
      "12",
      "96",
      "Europe/Lisbon",
      "pt-PT",
    ]) {
      expect(within(detail).getAllByText(value).length).toBeGreaterThan(0);
    }
    expect(detail.textContent).toContain("VCVM Proxy-Checker");
    expect(detail.textContent).not.toContain("secret-password");

    fireEvent.click(within(grid).getByRole("button", { name: "Check Residential Lisbon" }));
    await waitFor(() => expect(api.checkProxy).toHaveBeenCalledWith("proxy-1"));
    expect(await within(grid).findByText("72 ms")).toBeTruthy();

    fireEvent.click(within(grid).getByRole("button", { name: "Create profile from Residential Lisbon" }));
    await waitFor(() =>
      expect(api.createProfileFromProxy).toHaveBeenCalledWith("proxy-1", {
        harness: "browser-use",
        project_id: "qa",
        launch: false,
      }),
    );
    expect(onProfileCreated).toHaveBeenCalledWith(createdProfile);

    fireEvent.change(screen.getByLabelText("Search proxies grid"), { target: { value: "tokyo" } });
    await waitFor(() => expect(within(grid).queryByText("Residential Lisbon")).toBeNull());
    expect(within(grid).getByText("Datacenter Tokyo")).toBeTruthy();
  }, 15000);

  it("keeps mobile/coarse pointers on proxy cards without mounting AG Grid", async () => {
    setProxyOverviewMedia(true);
    vi.mocked(api.listProxies).mockResolvedValue([proxy({ id: "mobile-proxy", label: "Mobile Proxy" })]);

    renderProxyOverview();

    expect(await screen.findByText("Mobile Proxy")).toBeTruthy();
    expect(screen.queryByTestId("proxy-desktop-grid")).toBeNull();
    expect(screen.queryByRole("grid")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show details for Mobile Proxy" }));
    expect(await screen.findByRole("region", { name: "Proxy checker details for Mobile Proxy" })).toBeTruthy();
  });
});
