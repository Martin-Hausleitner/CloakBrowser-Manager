import { describe, expect, it } from "vitest";
import type { Profile } from "./api";
import { compareOrganizedProfiles, profileOrganizationLabel } from "./profileOrganization";

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

describe("profileOrganizationLabel", () => {
  it("formats project and folder labels with default project fallback", () => {
    expect(profileOrganizationLabel(profile({ project_id: "commerce", folder_path: "checkout" }))).toBe(
      "commerce / checkout",
    );
    expect(profileOrganizationLabel(profile({ project_id: "commerce", folder_path: "" }))).toBe("commerce");
    expect(profileOrganizationLabel(profile({ project_id: "", folder_path: "checkout" }))).toBe(
      "default / checkout",
    );
  });
});

describe("compareOrganizedProfiles", () => {
  it("sorts by pinned, project, folder, name, created date, and id deterministically", () => {
    const profiles = [
      profile({
        id: "unpinned-a",
        name: "Alpha",
        pinned: false,
        project_id: "commerce",
        folder_path: "checkout",
      }),
      profile({
        id: "pinned-z",
        name: "Zulu",
        pinned: true,
        project_id: "marketplace",
        folder_path: "sales",
      }),
      profile({
        id: "pinned-project-a",
        name: "Zulu",
        pinned: true,
        project_id: "commerce",
        folder_path: "sales",
      }),
      profile({
        id: "pinned-folder-a",
        name: "Zulu",
        pinned: true,
        project_id: "commerce",
        folder_path: "checkout",
      }),
      profile({
        id: "pinned-name-a",
        name: "Alpha",
        pinned: true,
        project_id: "commerce",
        folder_path: "checkout",
      }),
      profile({
        id: "pinned-created-new",
        name: "Alpha",
        pinned: true,
        project_id: "commerce",
        folder_path: "checkout",
        created_at: "2026-07-21T00:00:00Z",
      }),
      profile({
        id: "pinned-created-old-b",
        name: "Alpha",
        pinned: true,
        project_id: "commerce",
        folder_path: "checkout",
        created_at: "2026-07-19T00:00:00Z",
      }),
      profile({
        id: "pinned-created-old-a",
        name: "Alpha",
        pinned: true,
        project_id: "commerce",
        folder_path: "checkout",
        created_at: "2026-07-19T00:00:00Z",
      }),
    ];

    expect([...profiles].sort(compareOrganizedProfiles).map((sorted) => sorted.id)).toEqual([
      "pinned-created-old-a",
      "pinned-created-old-b",
      "pinned-name-a",
      "pinned-created-new",
      "pinned-folder-a",
      "pinned-project-a",
      "pinned-z",
      "unpinned-a",
    ]);
  });
});
