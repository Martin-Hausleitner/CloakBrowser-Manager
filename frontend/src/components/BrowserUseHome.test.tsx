import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Profile } from "../lib/api";
import { BrowserUseHome } from "./BrowserUseHome";

const runningProfile: Profile = {
  id: "profile-live",
  name: "Live Demo",
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
  launch_args: [],
  notes: null,
  user_data_dir: "",
  created_at: "2026-07-26T00:00:00Z",
  updated_at: "2026-07-26T00:00:00Z",
  tags: [],
  status: "running",
  vnc_ws_port: 5901,
  cdp_url: "ws://example",
};

describe("BrowserUseHome", () => {
  it("selects a profile without opening its settings", () => {
    const onSelectProfile = vi.fn();
    const onOpenSettings = vi.fn();

    render(
      <BrowserUseHome
        projects={["default"]}
        projectId="default"
        harness="browser-use"
        profiles={[runningProfile]}
        task=""
        canManage
        selectedProfile={null}
        onProjectChange={vi.fn()}
        onHarnessChange={vi.fn()}
        onTaskChange={vi.fn()}
        onSelectProfile={onSelectProfile}
        onOpenSettings={onOpenSettings}
        onOpenProxies={vi.fn()}
        onOpenProfiles={vi.fn()}
        onOpenAccounts={vi.fn()}
        onCreateProjectProfile={vi.fn()}
        onLaunchSelected={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByRole("combobox", { name: "Run with browser profile" }), {
      target: { value: runningProfile.id },
    });

    expect(onSelectProfile).toHaveBeenCalledWith(runningProfile.id);
    expect(onOpenSettings).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Proxies" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Profiles" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Accounts" })).toBeNull();
    expect(screen.queryByRole("button", { name: "New in project" })).toBeNull();
  });
});
