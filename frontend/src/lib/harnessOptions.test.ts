import { describe, expect, it } from "vitest";
import { CALLABLE_BROWSER_HARNESSES, HARNESS_OPTIONS, harnessLabel } from "./harnessOptions";
import { deriveAccountRows } from "../components/AccountsOverview";
import type { Profile } from "./api";

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

  it("labels known harnesses", () => {
    expect(harnessLabel("unbrowse")).toBe("Unbrowse");
    expect(harnessLabel("stagehand")).toBe("Stagehand");
    expect(harnessLabel("browser-harness")).toBe("Browser Harness");
  });
});

describe("deriveAccountRows", () => {
  it("derives session and 2FA state without inventing secrets", () => {
    const rows = deriveAccountRows([
      sampleProfile({
        id: "a",
        status: "running",
        notes: "signed-in account · bitwarden",
      }),
      sampleProfile({
        id: "b",
        name: "Needs MFA",
        status: "stopped",
        tags: [{ tag: "needs-2fa", color: null }],
      }),
    ]);

    expect(rows[0].session).toBe("active");
    expect(rows[0].auth).toBe("signed_in");
    expect(rows[0].bitwarden).toBe("planned");
    expect(rows[1].session).toBe("idle");
    expect(rows[1].auth).toBe("needs_2fa");
    expect(JSON.stringify(rows)).not.toMatch(/password|cookie|token|api[_-]?key/i);
  });
});
