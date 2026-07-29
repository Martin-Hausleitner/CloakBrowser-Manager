import { describe, expect, it } from "vitest";
import { CALLABLE_BROWSER_HARNESSES, HARNESS_OPTIONS, harnessLabel } from "./harnessOptions";
import { buildAccountRows } from "../components/AccountsOverview";
import type { Account, Profile } from "./api";

function sampleProfile(overrides: Partial<Profile> = {}): Profile {
  return {
    id: "p1",
    name: "Demo",
    sandbox_id: "default",
    project_id: "default",
    folder_path: "",
    pinned: false,
    accent_color: null,
    harness: "browser-use",
    fingerprint_seed: 1,
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
    launch_args: [],
    notes: null,
    user_data_dir: "/tmp/demo",
    tags: [],
    status: "stopped",
    created_at: "2026-07-23T00:00:00Z",
    updated_at: "2026-07-23T00:00:00Z",
    vnc_ws_port: null,
    cdp_url: null,
    ...overrides,
  };
}

describe("harnessOptions", () => {
  it("exposes Browser Use, Browser Harness, Unbrowse, and Stagehand as callable backends", () => {
    expect(CALLABLE_BROWSER_HARNESSES).toEqual([
      "browser-use",
      "browser-harness",
      "unbrowse",
      "stagehand",
    ]);
    for (const value of CALLABLE_BROWSER_HARNESSES) {
      expect(HARNESS_OPTIONS.some((option) => option.value === value)).toBe(true);
    }
  });

  it("describes Antigravity as the ACPX Grok Build managed workflow without making it callable", () => {
    const antigravity = HARNESS_OPTIONS.find((option) => option.value === "antigravity");

    expect(antigravity?.description).toBe("Managed ACPX/Grok Build workflow preset");
    expect(CALLABLE_BROWSER_HARNESSES).not.toContain("antigravity");
  });

  it("labels known harnesses", () => {
    expect(harnessLabel("unbrowse")).toBe("Unbrowse");
    expect(harnessLabel("stagehand")).toBe("Stagehand");
    expect(harnessLabel("browser-harness")).toBe("Browser Harness");
  });
});

describe("buildAccountRows", () => {
  it("uses real account metadata without inferring auth state from profile notes", () => {
    const profile = sampleProfile({
      id: "a",
      status: "running",
      notes: "password=must-not-be-rendered",
    });
    const account: Account = {
      id: "account-a",
      profile_id: "a",
      profile_id_snapshot: "a",
      sandbox_id: "default",
      project_id: "default",
      provider: "github",
      subject_label: "agent@example.invalid",
      display_name: null,
      origin: "https://github.com",
      auth_state: "needs_2fa",
      second_factor_state: "required",
      passkey_state: "off",
      has_secret_reference: true,
      has_totp_reference: false,
      last_seen_at: null,
      row_version: 1,
      created_by_kind: "agent",
      created_by_id: "agent-1",
      created_at: "2026-07-29T00:00:00Z",
      updated_at: "2026-07-29T00:00:00Z",
    };
    const rows = buildAccountRows([account], [profile]);

    expect(rows[0].session).toBe("active");
    expect(rows[0].auth).toBe("needs_2fa");
    expect(rows[0].factor).toBe("required");
    expect(rows[0].vault).toBe("linked");
    expect(JSON.stringify(rows)).not.toMatch(/password|cookie|token|api[_-]?key/i);
  });
});
