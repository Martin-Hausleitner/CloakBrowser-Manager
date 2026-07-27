import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App, { applyProfileViewport, toggleProfilePin } from "./App";
import { ProfileForm } from "./components/ProfileForm";
import type { Profile } from "./lib/api";
import { UI_STATE, expectUiState } from "./lib/uiFlowRegistry";

const apiMock = vi.hoisted(() => ({
  authStatus: vi.fn(),
  logout: vi.fn(),
  setOnUnauthorized: vi.fn(),
  getOrcaCapabilities: vi.fn(),
  listProxies: vi.fn(),
}));

const useProfilesMock = vi.hoisted(() => vi.fn());

vi.mock("./lib/api", async () => {
  const actual = await vi.importActual<typeof import("./lib/api")>("./lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      authStatus: apiMock.authStatus,
      logout: apiMock.logout,
      getOrcaCapabilities: apiMock.getOrcaCapabilities,
      listProxies: apiMock.listProxies,
    },
    setOnUnauthorized: apiMock.setOnUnauthorized,
  };
});

vi.mock("./hooks/useProfiles", () => ({
  useProfiles: useProfilesMock,
}));

vi.mock("./components/ProfileViewer", () => ({
  ProfileViewer: ({ profileId }: { profileId: string }) => (
    <div data-testid="mock-profile-viewer">viewer:{profileId}</div>
  ),
}));

vi.mock("./components/LiveDevPanel", () => ({
  LiveDevPanel: () => <div data-testid="mock-live-dev-panel" />,
}));

vi.mock("./components/SessionStreamButtons", () => ({
  SessionStreamButtons: () => <div data-testid="mock-session-stream-buttons" />,
}));

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

beforeEach(() => {
  apiMock.authStatus.mockResolvedValue({
    auth_required: false,
    access_control_enabled: false,
    authenticated: true,
    identity: { kind: "anonymous", display_name: "Local operator" },
  });
  apiMock.logout.mockResolvedValue(undefined);
  apiMock.getOrcaCapabilities.mockResolvedValue({
    available: true,
    orca_bin: "/usr/local/bin/orca",
    agents: ["cursor-agent", "grok", "codex"],
    operations: ["terminal.create"],
    actions: {
      start: true,
      read: true,
      send: true,
      close: true,
      pause: false,
      resume: false,
    },
    notes: [],
  });
  apiMock.listProxies.mockResolvedValue([]);
  useProfilesMock.mockReset();
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      media: "",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  window.sessionStorage.clear();
});

describe("App Browser Use home handoff", () => {
  it("carries the home task into the selected running Agent Browser workspace once when opening it", async () => {
    const browserUseRunningProfile: Profile = {
      ...runningProfile,
      id: "profile-browser-use",
      name: "Browser Use Live",
      project_id: "default",
      harness: "browser-use",
      cdp_url: "ws://example",
    };
    useProfilesMock.mockReturnValue({
      profiles: [browserUseRunningProfile],
      loading: false,
      error: null,
      refresh: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
      launch: vi.fn(),
      stop: vi.fn(),
    });

    render(<App />);

    const task = "Open https://example.com and report the heading";
    fireEvent.change(
      await screen.findByPlaceholderText(/give the agent a task/i),
      { target: { value: task } },
    );
    fireEvent.change(screen.getByRole("combobox", { name: "Run with browser profile" }), {
      target: { value: browserUseRunningProfile.id },
    });
    fireEvent.click(screen.getByRole("button", { name: "Open or launch selected browser" }));

    const workspacePrompt = await screen.findByTestId("orca-prompt");
    expect((workspacePrompt as HTMLTextAreaElement).value).toBe(task);
  });

  it("exposes stable UI states across desktop navigation into the live workspace", async () => {
    useProfilesMock.mockReturnValue({
      profiles: [runningProfile],
      loading: false,
      error: null,
      refresh: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
      launch: vi.fn(),
      stop: vi.fn(),
    });

    render(<App />);

    await waitFor(() => expectUiState(document.body, UI_STATE.appDesktopShell));
    expectUiState(document.body, UI_STATE.appDesktopHome);

    fireEvent.click(screen.getAllByRole("button", { name: "Proxies" })[0]);
    await waitFor(() => expectUiState(document.body, UI_STATE.appDesktopProxies));
    expectUiState(document.body, UI_STATE.proxyOverview);

    fireEvent.click(screen.getAllByRole("button", { name: "Profiles" })[0]);
    await waitFor(() => expectUiState(document.body, UI_STATE.appDesktopProfiles));

    fireEvent.click(screen.getAllByText("Live Checkout QA")[0]);
    await waitFor(() => expectUiState(document.body, UI_STATE.appDesktopAgentWorkspace));
    expectUiState(document.body, UI_STATE.agentWorkspace);
  });
});

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
  it("keeps raw Chromium arguments out of the operator form", () => {
    render(
      <ProfileForm
        profile={null}
        onSave={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.queryByText("Launch Args")).toBeNull();
    expect(screen.getByText(/extensions and browser flags are managed by the control cli/i)).toBeTruthy();
  });

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
