import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Profile } from "../lib/api";
import { ProfileList } from "./ProfileList";

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

describe("ProfileList organization", () => {
  it("groups by project and folder with pinned profiles first inside each group", () => {
    render(
      <ProfileList
        profiles={[
          profile({ id: "a", name: "Unpinned Checkout", pinned: false }),
          profile({ id: "b", name: "Pinned Checkout", pinned: true }),
          profile({ id: "c", name: "Ops Worker", project_id: "operations", folder_path: "" }),
        ]}
        selectedId={null}
        onSelect={vi.fn()}
        onNew={vi.fn()}
      />,
    );

    const commerce = screen.getByRole("button", { name: "Profile group: commerce / checkout" });
    expect(commerce).toBeTruthy();
    const countId = commerce.getAttribute("aria-describedby");
    expect(countId).toBeTruthy();
    expect(document.getElementById(countId!)?.textContent).toBe("2");
    expect(screen.getByRole("button", { name: "Profile group: operations" })).toBeTruthy();
    const names = screen.getAllByRole("button")
      .map((button) => button.textContent ?? "")
      .filter((text) => text.includes("Checkout"));
    expect(names[0]).toContain("Pinned Checkout");
    expect(names[1]).toContain("Unpinned Checkout");
    expect(screen.getByLabelText("Pinned")).toBeTruthy();
    expect(screen.getAllByText("commerce / checkout").length).toBeGreaterThan(0);
  });

  it("collapses groups and toggles pins without selecting the profile", () => {
    const onSelect = vi.fn();
    const onTogglePin = vi.fn();
    render(
      <ProfileList
        profiles={[
          profile({ id: "a", name: "Unpinned Checkout", pinned: false }),
        ]}
        selectedId={null}
        onSelect={onSelect}
        onNew={vi.fn()}
        onTogglePin={onTogglePin}
        canManage
      />,
    );

    const group = screen.getByRole("button", { name: "Profile group: commerce / checkout" });
    fireEvent.click(group);
    expect(screen.queryByText("Unpinned Checkout")).toBeNull();

    fireEvent.click(group);
    fireEvent.click(screen.getByLabelText("Pin Unpinned Checkout"));

    expect(onTogglePin).toHaveBeenCalledWith("a");
    expect(onSelect).not.toHaveBeenCalled();
  });
});


it("shows bulk organize controls for managers", async () => {
  const onBulkOrganize = vi.fn();
  render(
    <ProfileList
      profiles={[
        {
          id: "1",
          name: "Alpha",
          sandbox_id: "alpha",
          project_id: "commerce",
          folder_path: "us",
          pinned: false,
          accent_color: null,
          harness: "codex",
          fingerprint_seed: 1,
          proxy: null,
          timezone: null,
          locale: null,
          platform: "windows",
          user_agent: null,
          screen_width: 1920,
          screen_height: 1080,
          gpu_vendor: null,
          gpu_renderer: null,
          hardware_concurrency: null,
          humanize: false,
          human_preset: "default",
          headless: false,
          geoip: false,
          clipboard_sync: false,
          auto_launch: false,
          color_scheme: null,
          search_engine: null,
          launch_args: [],
          notes: null,
          user_data_dir: "",
          created_at: "2026-07-22T00:00:00Z",
          updated_at: "2026-07-22T00:00:00Z",
          tags: [],
          status: "stopped",
          vnc_ws_port: null,
          cdp_url: null,
        },
      ]}
      selectedId={null}
      onSelect={() => {}}
      onNew={() => {}}
      canManage
      onBulkOrganize={onBulkOrganize}
    />,
  );
  expect(screen.getByText(/Bulk organize/i)).toBeTruthy();
  expect(screen.getByLabelText("Select Alpha")).toBeTruthy();
});
