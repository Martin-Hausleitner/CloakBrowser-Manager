import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Profile, TaskHarnessSession } from "../lib/api";
import { api } from "../lib/api";
import { SessionsOverview, MAX_SESSION_PROFILE_CALLS } from "./SessionsOverview";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listTaskSessions: vi.fn(),
    },
  };
});

const baseProfile: Profile = {
  id: "profile-1",
  name: "Checkout QA",
  sandbox_id: "default",
  project_id: "commerce",
  folder_path: "checkout",
  pinned: false,
  accent_color: null,
  harness: "browser-use",
  fingerprint_seed: 12345,
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

function session(overrides: Partial<TaskHarnessSession>): TaskHarnessSession {
  return {
    id: "session-1",
    profile_id: "profile-1",
    sandbox_id: "default",
    project_id: "commerce",
    title: "Checkout validation",
    status: "active",
    workflow_state: "open",
    done_at: null,
    archived_at: null,
    retention_class: "project",
    expires_at: null,
    activity_at: "2026-07-27T10:30:00Z",
    row_version: 1,
    created_by_kind: "user",
    created_by_id: "user-1",
    created_at: "2026-07-27T10:00:00Z",
    updated_at: "2026-07-27T10:30:00Z",
    metadata: { password: "super-secret", api_key: "hidden" },
    ...overrides,
  };
}

function setSessionsMedia(matches: boolean) {
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
  setSessionsMedia(false);
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, get: () => 1200 });
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get: () => 600 });
  vi.mocked(api.listTaskSessions).mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SessionsOverview", () => {
  it("aggregates bounded per-profile task sessions into a redacted desktop grid", async () => {
    const onSelectProfile = vi.fn();
    const profiles = Array.from({ length: MAX_SESSION_PROFILE_CALLS + 2 }, (_, index) =>
      profile({ id: `profile-${index + 1}`, name: `Profile ${index + 1}`, project_id: index === 0 ? "commerce" : "default" }),
    );
    vi.mocked(api.listTaskSessions).mockImplementation(async (profileId) => {
      if (profileId === "profile-1") {
        return [session({ id: "session-a", profile_id: profileId })];
      }
      return [];
    });

    render(<SessionsOverview profiles={profiles} selectedId={null} onSelectProfile={onSelectProfile} />);

    await waitFor(() => expect(api.listTaskSessions).toHaveBeenCalledTimes(MAX_SESSION_PROFILE_CALLS));
    expect(api.listTaskSessions).toHaveBeenCalledWith("profile-1", expect.objectContaining({ limit: 8 }));
    expect(api.listTaskSessions).not.toHaveBeenCalledWith(`profile-${MAX_SESSION_PROFILE_CALLS + 1}`, expect.anything());

    const grid = await screen.findByTestId("sessions-desktop-grid");
    for (const header of ["Profile", "Title", "Project", "Workflow", "Status", "Retention", "Activity"]) {
      expect(await within(grid).findByText(header)).toBeTruthy();
    }
    expect(await within(grid).findByText("Profile 1")).toBeTruthy();
    expect(within(grid).getByText("Checkout validation")).toBeTruthy();
    expect(within(grid).getByText("commerce")).toBeTruthy();
    expect(within(grid).getByText("open")).toBeTruthy();
    expect(within(grid).getByText("active")).toBeTruthy();
    expect(within(grid).getByText("project")).toBeTruthy();
    expect(within(grid).getByText("2026-07-27 10:30")).toBeTruthy();
    expect(screen.queryByText(/super-secret|api_key|hidden/i)).toBeNull();

    fireEvent.click(within(grid).getByRole("button", { name: "Open session Checkout validation for Profile 1" }));
    expect(onSelectProfile).toHaveBeenCalledWith("profile-1");

    fireEvent.change(screen.getByLabelText("Search sessions grid"), { target: { value: "missing" } });
    await waitFor(() => expect(within(grid).queryByText("Checkout validation")).toBeNull());
  }, 15000);

  it("shows loading, empty, and per-profile error states without inventing global sessions", async () => {
    let resolveList: (items: TaskHarnessSession[]) => void = () => undefined;
    vi.mocked(api.listTaskSessions).mockReturnValue(
      new Promise((resolve) => {
        resolveList = resolve;
      }),
    );

    const { unmount } = render(
      <SessionsOverview
        profiles={[profile({ id: "profile-pending", name: "Pending Profile" })]}
        selectedId={null}
        onSelectProfile={vi.fn()}
      />,
    );

    expect(screen.getByText(/Loading sessions/)).toBeTruthy();
    resolveList([]);
    expect(await screen.findByText("No task sessions found.")).toBeTruthy();
    unmount();

    vi.mocked(api.listTaskSessions).mockRejectedValue(new Error("Task session API offline"));
    render(
      <SessionsOverview
        profiles={[profile({ id: "profile-error", name: "Error Profile" })]}
        selectedId={null}
        onSelectProfile={vi.fn()}
      />,
    );

    expect(await screen.findByText(/Error Profile: Task session API offline/)).toBeTruthy();
    expect(screen.getByText("No task sessions found.")).toBeTruthy();
  });


  it("aborts stale profile loads and ignores their late responses", async () => {
    let staleSignal: AbortSignal | null = null;
    let resolveStale: (items: TaskHarnessSession[]) => void = () => undefined;
    vi.mocked(api.listTaskSessions).mockImplementation((profileId, options) => {
      if (profileId === "profile-stale") {
        staleSignal = options?.signal ?? null;
        return new Promise((resolve) => {
          resolveStale = resolve;
        });
      }
      return Promise.resolve([
        session({
          id: "session-fresh",
          profile_id: "profile-fresh",
          title: "Fresh session",
          activity_at: "2026-07-27T11:00:00Z",
        }),
      ]);
    });

    const { rerender } = render(
      <SessionsOverview
        profiles={[profile({ id: "profile-stale", name: "Stale Profile" })]}
        selectedId={null}
        onSelectProfile={vi.fn()}
      />,
    );
    await waitFor(() => expect(staleSignal).not.toBeNull());

    rerender(
      <SessionsOverview
        profiles={[profile({ id: "profile-fresh", name: "Fresh Profile" })]}
        selectedId={null}
        onSelectProfile={vi.fn()}
      />,
    );

    await waitFor(() => expect(staleSignal?.aborted).toBe(true));
    expect(await screen.findByText("Fresh session")).toBeTruthy();

    resolveStale([
      session({
        id: "session-stale",
        profile_id: "profile-stale",
        title: "Stale session",
        activity_at: "2026-07-27T12:00:00Z",
      }),
    ]);

    await waitFor(() => expect(screen.queryByText("Stale session")).toBeNull());
    expect(screen.getByText("Fresh session")).toBeTruthy();
  }, 15000);
  it("keeps touch devices on session cards without mounting AG Grid", async () => {
    setSessionsMedia(true);
    const onSelectProfile = vi.fn();
    vi.mocked(api.listTaskSessions).mockResolvedValue([
      session({ id: "session-mobile", profile_id: "profile-mobile", title: "Mobile session" }),
    ]);

    render(
      <SessionsOverview
        profiles={[profile({ id: "profile-mobile", name: "Mobile Profile" })]}
        selectedId={null}
        onSelectProfile={onSelectProfile}
      />,
    );

    expect(await screen.findByText("Mobile session")).toBeTruthy();
    expect(screen.queryByTestId("sessions-desktop-grid")).toBeNull();
    expect(screen.queryByRole("grid")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Mobile session/ }));
    expect(onSelectProfile).toHaveBeenCalledWith("profile-mobile");
  });
});
