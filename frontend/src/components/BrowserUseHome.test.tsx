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
  proxy_display: null,
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
  it("keeps advanced profile details collapsed and avoids duplicate harness cards", () => {
    render(
      <BrowserUseHome
        projects={["default"]}
        projectId="default"
        profiles={[runningProfile]}
        task=""
        selectedProfile={runningProfile}
        onProjectChange={vi.fn()}
        onTaskChange={vi.fn()}
        onSelectProfile={vi.fn()}
        onOpenSettings={vi.fn()}
        onLaunchSelected={vi.fn()}
      />,
    );

    expect(screen.queryByText("Callable browser backends")).toBeNull();
    expect(screen.queryByText("Viewport")).toBeNull();
    expect(screen.getByRole("combobox", { name: "Project" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Run with browser profile" })).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Browser harness" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Attachments" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Open profile settings" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Browser settings" }));

    expect(screen.getByText("Live Demo")).toBeTruthy();
    expect(screen.getByText("1280×720")).toBeTruthy();
    expect(screen.getByText("No proxy")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Edit profile settings" })).toBeTruthy();
    expect(screen.queryByText("Callable browser backends")).toBeNull();
    expect(screen.queryByText("Expanded settings overview")).toBeNull();
  });

  it("selects a profile without opening its settings", () => {
    const onSelectProfile = vi.fn();
    const onOpenSettings = vi.fn();

    render(
      <BrowserUseHome
        projects={["default"]}
        projectId="default"
        profiles={[runningProfile]}
        task=""
        selectedProfile={null}
        onProjectChange={vi.fn()}
        onTaskChange={vi.fn()}
        onSelectProfile={onSelectProfile}
        onOpenSettings={onOpenSettings}
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

  it("shows configured proxy status from proxy_display when raw proxy is redacted", () => {
    render(
      <BrowserUseHome
        projects={["default"]}
        projectId="default"
        profiles={[{ ...runningProfile, proxy: null, proxy_display: "http://proxy.test:8080" }]}
        task=""
        selectedProfile={{ ...runningProfile, proxy: null, proxy_display: "http://proxy.test:8080" }}
        onProjectChange={vi.fn()}
        onTaskChange={vi.fn()}
        onSelectProfile={vi.fn()}
        onOpenSettings={vi.fn()}
        onLaunchSelected={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Browser settings" }));

    expect(screen.getByText("Proxy ready")).toBeTruthy();
    expect(document.body.textContent).not.toContain("proxy-user");
    expect(document.body.textContent).not.toContain("top-secret");
  });
});
