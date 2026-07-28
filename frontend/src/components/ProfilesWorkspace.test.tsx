import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Profile } from "../lib/api";
import { ProfilesWorkspace } from "./ProfilesWorkspace";

const baseProfile: Profile = {
  id: "profile-1",
  name: "Checkout QA",
  sandbox_id: "default",
  project_id: "commerce",
  folder_path: "checkout",
  pinned: false,
  accent_color: null,
  harness: "codex",
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

function setProfilesWorkspaceMedia(matches: boolean) {
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
  window.localStorage.clear();
  setProfilesWorkspaceMedia(false);
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, get: () => 1200 });
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get: () => 600 });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ProfilesWorkspace", () => {
  it("renders the desktop profiles grid with selection, quick filtering, and settings", async () => {
    const onSelect = vi.fn();
    const onEdit = vi.fn();

    render(
      <ProfilesWorkspace
        profiles={[
          profile({ id: "unpinned", name: "Checkout QA", pinned: false, updated_at: "2026-07-20T00:00:00Z" }),
          profile({
            id: "pinned",
            name: "Pinned Browser Use",
            pinned: true,
            harness: "browser-use",
            platform: "linux",
            screen_width: 1440,
            screen_height: 900,
            proxy: "http://alice:top-secret@84.55.0.94:5432",
            status: "running",
            updated_at: "2026-07-21T12:30:00Z",
          }),
        ]}
        selectedId="pinned"
        onSelect={onSelect}
        onEdit={onEdit}
        canManage
      />,
    );

    const grid = screen.getByTestId("profiles-desktop-grid");
    expect(await within(grid).findByText("Status")).toBeTruthy();
    expect(within(grid).getByText("Project/Folder")).toBeTruthy();
    expect(within(grid).getByText("Viewport")).toBeTruthy();
    expect(await within(grid).findByText("Pinned Browser Use")).toBeTruthy();
    expect(within(grid).getByText("1440 x 900")).toBeTruthy();
    expect(within(grid).getByText("Configured")).toBeTruthy();
    expect(within(grid).queryByText("alice")).toBeNull();
    expect(within(grid).queryByText("top-secret")).toBeNull();
    expect(within(grid).queryByText("http://alice:top-secret@84.55.0.94:5432")).toBeNull();

    const names = within(grid)
      .getAllByText(/Checkout QA|Pinned Browser Use/)
      .map((node) => node.textContent);
    expect(names[0]).toBe("Pinned Browser Use");

    fireEvent.click(within(grid).getByRole("button", { name: "Select Checkout QA" }));
    expect(onSelect).toHaveBeenCalledWith("unpinned");

    fireEvent.click(within(grid).getByRole("button", { name: "Settings for Pinned Browser Use" }));
    expect(onEdit).toHaveBeenCalledWith("pinned");

    fireEvent.change(screen.getByLabelText("Search profiles grid"), { target: { value: "browser use" } });
    await waitFor(() => expect(within(grid).queryByText("Checkout QA")).toBeNull());
    expect(within(grid).getByText("Pinned Browser Use")).toBeTruthy();
  }, 15000);

  it("keeps the card workspace on coarse pointers without mounting AG Grid", () => {
    setProfilesWorkspaceMedia(true);
    const onSelect = vi.fn();
    const onEdit = vi.fn();

    render(
      <ProfilesWorkspace
        profiles={[profile({ id: "mobile", name: "Mobile Card Profile" })]}
        selectedId={null}
        onSelect={onSelect}
        onEdit={onEdit}
        canManage
      />,
    );

    expect(screen.queryByTestId("profiles-desktop-grid")).toBeNull();
    expect(screen.queryByRole("grid")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Mobile Card Profile/ }));
    expect(onSelect).toHaveBeenCalledWith("mobile");
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(onEdit).toHaveBeenCalledWith("mobile");
  });

  it("offers persistent density and column controls for the middle table view", async () => {
    const { unmount } = render(
      <ProfilesWorkspace
        profiles={[profile({ id: "desktop", name: "Desktop Profile" })]}
        selectedId={null}
        onSelect={vi.fn()}
        onEdit={vi.fn()}
        canManage
      />,
    );

    const grid = screen.getByTestId("profiles-desktop-grid");
    expect(within(grid).getByText("1 row")).toBeTruthy();

    fireEvent.change(within(grid).getByLabelText("Grid density"), {
      target: { value: "comfortable" },
    });
    expect(grid.getAttribute("data-density")).toBe("comfortable");

    fireEvent.click(within(grid).getByRole("button", { name: "Columns" }));
    const viewportToggle = within(grid).getByRole("checkbox", { name: "Show Viewport" });
    fireEvent.click(viewportToggle);
    await waitFor(() => expect(within(grid).queryByRole("columnheader", { name: "Viewport" })).toBeNull());

    unmount();

    render(
      <ProfilesWorkspace
        profiles={[profile({ id: "desktop", name: "Desktop Profile" })]}
        selectedId={null}
        onSelect={vi.fn()}
        onEdit={vi.fn()}
        canManage
      />,
    );

    const restoredGrid = screen.getByTestId("profiles-desktop-grid");
    expect(restoredGrid.getAttribute("data-density")).toBe("comfortable");
    await waitFor(() => expect(within(restoredGrid).queryByRole("columnheader", { name: "Viewport" })).toBeNull());

    fireEvent.click(within(restoredGrid).getByRole("button", { name: "Reset table view" }));
    expect(restoredGrid.getAttribute("data-density")).toBe("compact");
    await waitFor(() => expect(within(restoredGrid).getByRole("columnheader", { name: "Viewport" })).toBeTruthy());
  }, 15000);
});
