import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { applyProfileViewport, toggleProfilePin } from "./App";
import { ProfileForm } from "./components/ProfileForm";
import type { Profile } from "./lib/api";

const stoppedProfile: Profile = {
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

describe("toggleProfilePin", () => {
  it("persists the inverse pinned state through profile update for admins", async () => {
    const update = vi.fn().mockResolvedValue({ ...stoppedProfile, pinned: true });

    const result = await toggleProfilePin({
      profile: stoppedProfile,
      canManageProfiles: true,
      update,
    });

    expect(result).toBe(true);
    expect(update).toHaveBeenCalledWith(stoppedProfile.id, { pinned: true });
  });

  it("does not update pin state without profile management access", async () => {
    const update = vi.fn();

    const result = await toggleProfilePin({
      profile: stoppedProfile,
      canManageProfiles: false,
      update,
    });

    expect(result).toBe(false);
    expect(update).not.toHaveBeenCalled();
  });
});

describe("ProfileForm profile organization", () => {
  it("roundtrips organization fields while keeping sandbox as a separate access boundary", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <ProfileForm
        profile={null}
        onSave={onSave}
        onCancel={vi.fn()}
      />,
    );

    const projectInput = screen.getByLabelText("Project") as HTMLInputElement;
    const folderInput = screen.getByLabelText("Folder") as HTMLInputElement;
    expect(projectInput.pattern).toBe("[A-Za-z0-9][A-Za-z0-9._-]*");
    expect(projectInput.maxLength).toBe(80);
    expect(projectInput.required).toBe(true);
    expect(folderInput.maxLength).toBe(240);
    fireEvent.change(folderInput, { target: { value: "/unsafe" } });
    expect(folderInput.checkValidity()).toBe(false);
    expect(screen.getByText(/no leading or trailing slash/i)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Profile Name"), { target: { value: "Client QA" } });
    fireEvent.change(screen.getByLabelText("Project"), { target: { value: "marketplace" } });
    fireEvent.change(screen.getByLabelText("Folder"), { target: { value: "buyers/us" } });
    fireEvent.click(screen.getByLabelText("Pinned"));
    fireEvent.change(screen.getByLabelText("Accent color"), { target: { value: "#22c55e" } });
    fireEvent.change(screen.getByLabelText("Preferred harness"), { target: { value: "opencode" } });
    fireEvent.change(screen.getByLabelText("Access sandbox"), { target: { value: "research-team" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
      name: "Client QA",
      project_id: "marketplace",
      folder_path: "buyers/us",
      pinned: true,
      accent_color: "#22c55e",
      harness: "opencode",
      sandbox_id: "research-team",
    }));
    expect(screen.getByText("Organization")).toBeTruthy();
    expect(screen.getByText("Access sandbox")).toBeTruthy();
    expect(screen.getByText(/access per sandbox/i)).toBeTruthy();
  });
});
