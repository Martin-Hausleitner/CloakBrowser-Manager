import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Profile } from "../lib/api";
import { AccountsOverview, deriveAccountRows } from "./AccountsOverview";

const baseProfile: Profile = {
  id: "profile-1",
  name: "Checkout Ops",
  sandbox_id: "default",
  project_id: "commerce",
  folder_path: "checkout",
  pinned: false,
  accent_color: null,
  harness: "browser-use",
  fingerprint_seed: 12345,
  proxy: null,
  timezone: null,
  locale: null,
  platform: "macos",
  user_agent: null,
  screen_width: 1280,
  screen_height: 800,
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
  user_data_dir: "/tmp/profile-1",
  created_at: "2026-07-20T00:00:00Z",
  updated_at: "2026-07-20T00:00:00Z",
  tags: [],
  status: "stopped",
  vnc_ws_port: null,
  cdp_url: null,
};

function profile(overrides: Partial<Profile>): Profile {
  return { ...baseProfile, ...overrides };
}

function setAccountsMedia(matches: boolean) {
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

beforeEach(() => {
  setAccountsMedia(false);
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, get: () => 1200 });
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get: () => 600 });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AccountsOverview", () => {
  it("renders the desktop accounts grid with the requested columns and summary metrics", async () => {
    render(
      <AccountsOverview
        profiles={[
          profile({ id: "checkout", name: "Checkout Ops", status: "running", notes: "signed-in account" }),
          profile({ id: "billing", name: "Billing Admin", tags: [{ tag: "needs-2fa", color: null }] }),
        ]}
        selectedId="checkout"
        onSelect={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Profiles count").textContent).toBe("2");
    expect(screen.getByLabelText("Live sessions count").textContent).toBe("1");
    expect(screen.getByLabelText("Needs 2FA review count").textContent).toBe("1");


    const grid = screen.getByTestId("accounts-desktop-grid");
    for (const header of ["Profile", "Project", "Harness", "Session", "Auth", "Bitwarden", "Keypad", "Notes", "Actions"]) {
      expect(await within(grid).findByText(header)).toBeTruthy();
    }
    expect(await within(grid).findByText("Checkout Ops")).toBeTruthy();
    expect(within(grid).getAllByText("commerce").length).toBeGreaterThan(0);

  }, 15000);

  it("keeps selection and open actions wired to onSelect", async () => {
    const onSelect = vi.fn();

    render(
      <AccountsOverview
        profiles={[
          profile({ id: "checkout", name: "Checkout Ops" }),
          profile({ id: "billing", name: "Billing Admin" }),
        ]}
        selectedId="billing"
        onSelect={onSelect}
      />,
    );

    const grid = screen.getByTestId("accounts-desktop-grid");
    fireEvent.click(await within(grid).findByRole("button", { name: "Select Checkout Ops" }));
    expect(onSelect).toHaveBeenCalledWith("checkout");

    fireEvent.click(within(grid).getByRole("button", { name: "Open Billing Admin" }));
    expect(onSelect).toHaveBeenCalledWith("billing");
  }, 15000);

  it("filters by quick search and needs-2fa without exposing raw secrets", async () => {
    render(
      <AccountsOverview
        profiles={[
          profile({
            id: "checkout",
            name: "Checkout Ops",
            notes: "signed-in account password=supersecret cookie=session-token",
          }),
          profile({ id: "billing", name: "Billing Admin", tags: [{ tag: "needs-2fa", color: null }] }),
        ]}
        selectedId={null}
        onSelect={vi.fn()}
      />,
    );

    expect(JSON.stringify(deriveAccountRows([profile({ notes: "password=supersecret cookie=session-token" })]))).not.toMatch(
      /supersecret|session-token/i,
    );
    expect(screen.queryByText(/supersecret|session-token/i)).toBeNull();

    fireEvent.change(screen.getByLabelText("Search accounts grid"), { target: { value: "billing" } });
    const grid = screen.getByTestId("accounts-desktop-grid");
    await waitFor(() => expect(within(grid).queryByText("Checkout Ops")).toBeNull());
    await waitFor(() => expect(within(grid).getByText("Billing Admin")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Search accounts grid"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Show needs 2FA accounts" }));
    await waitFor(() => expect(within(grid).queryByText("Checkout Ops")).toBeNull());
    await waitFor(() => expect(within(grid).getByText("Billing Admin")).toBeTruthy());

  }, 15000);

  it("keeps the touch-card list on coarse pointers without mounting AG Grid", () => {
    setAccountsMedia(true);
    const onSelect = vi.fn();

    render(
      <AccountsOverview
        profiles={[profile({ id: "mobile", name: "Mobile Account", tags: [{ tag: "needs-2fa", color: null }] })]}
        selectedId={null}
        onSelect={onSelect}
      />,
    );

    expect(screen.queryByTestId("accounts-desktop-grid")).toBeNull();
    expect(screen.queryByRole("grid")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Mobile Account/ }));
    expect(onSelect).toHaveBeenCalledWith("mobile");
  });
});
