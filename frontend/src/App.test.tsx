import { describe, expect, it, vi } from "vitest";
import { applyProfileViewport } from "./App";
import type { Profile } from "./lib/api";

const stoppedProfile: Profile = {
  id: "profile-1",
  name: "Checkout QA",
  sandbox_id: "default",
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

const runningProfile: Profile = {
  ...stoppedProfile,
  id: "profile-2",
  name: "Live Checkout QA",
  status: "running",
  vnc_ws_port: 5901,
};

describe("applyProfileViewport", () => {
  it("saves stopped profile viewport without restarting", async () => {
    const update = vi.fn().mockResolvedValue({ ...stoppedProfile, screen_width: 768, screen_height: 1024 });
    const stop = vi.fn();
    const launch = vi.fn();

    const result = await applyProfileViewport({
      profile: stoppedProfile,
      width: 768,
      height: 1024,
      canManageProfiles: true,
      canOperateProfile: true,
      update,
      stop,
      launch,
    });

    expect(result).toBe(true);
    expect(update).toHaveBeenCalledWith(stoppedProfile.id, { screen_width: 768, screen_height: 1024 });
    expect(stop).not.toHaveBeenCalled();
    expect(launch).not.toHaveBeenCalled();
  });

  it("restarts a running profile from the updated profile after saving viewport", async () => {
    const updatedProfile = { ...runningProfile, id: "profile-2-updated", screen_width: 1024, screen_height: 576 };
    const update = vi.fn().mockResolvedValue(updatedProfile);
    const stop = vi.fn().mockResolvedValue(true);
    const launch = vi.fn().mockResolvedValue({ ws_url: "ws://127.0.0.1:5901" });

    const result = await applyProfileViewport({
      profile: runningProfile,
      width: 1024,
      height: 576,
      canManageProfiles: true,
      canOperateProfile: true,
      update,
      stop,
      launch,
    });

    expect(result).toBe(true);
    expect(update).toHaveBeenCalledWith(runningProfile.id, { screen_width: 1024, screen_height: 576 });
    expect(stop).toHaveBeenCalledWith(runningProfile.id);
    expect(launch).toHaveBeenCalledWith(updatedProfile.id);
  });

  it("aborts running profile relaunch when stop does not succeed", async () => {
    const update = vi.fn().mockResolvedValue({ ...runningProfile, screen_width: 1024, screen_height: 576 });
    const stop = vi.fn().mockResolvedValue(false);
    const launch = vi.fn();

    const result = await applyProfileViewport({
      profile: runningProfile,
      width: 1024,
      height: 576,
      canManageProfiles: true,
      canOperateProfile: true,
      update,
      stop,
      launch,
    });

    expect(result).toBe(false);
    expect(stop).toHaveBeenCalledWith(runningProfile.id);
    expect(launch).not.toHaveBeenCalled();
  });

  it("does not save a running profile viewport without operate permission", async () => {
    const update = vi.fn();
    const stop = vi.fn();
    const launch = vi.fn();

    const result = await applyProfileViewport({
      profile: runningProfile,
      width: 1024,
      height: 576,
      canManageProfiles: true,
      canOperateProfile: false,
      update,
      stop,
      launch,
    });

    expect(result).toBe(false);
    expect(update).not.toHaveBeenCalled();
    expect(stop).not.toHaveBeenCalled();
    expect(launch).not.toHaveBeenCalled();
  });

  it("returns false when a running profile relaunch does not succeed", async () => {
    const update = vi.fn().mockResolvedValue({ ...runningProfile, screen_width: 1024, screen_height: 576 });
    const stop = vi.fn().mockResolvedValue(true);
    const launch = vi.fn().mockResolvedValue(undefined);

    const result = await applyProfileViewport({
      profile: runningProfile,
      width: 1024,
      height: 576,
      canManageProfiles: true,
      canOperateProfile: true,
      update,
      stop,
      launch,
    });

    expect(result).toBe(false);
    expect(stop).toHaveBeenCalledWith(runningProfile.id);
    expect(launch).toHaveBeenCalledWith(runningProfile.id);
  });
});
