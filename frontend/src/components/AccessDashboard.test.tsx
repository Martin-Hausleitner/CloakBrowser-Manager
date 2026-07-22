import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AccessDashboard } from "./AccessDashboard";

const apiMock = vi.hoisted(() => ({
  listAccessUsers: vi.fn(),
  listAccessAgents: vi.fn(),
  listAccessGroups: vi.fn(),
  listAccessSandboxes: vi.fn(),
  listProfiles: vi.fn(),
  createAccessUser: vi.fn(),
  updateAccessUser: vi.fn(),
  createAccessAgent: vi.fn(),
  updateAccessAgent: vi.fn(),
  createAccessGroup: vi.fn(),
  updateAccessGroup: vi.fn(),
  rotateAccessAgentKey: vi.fn(),
}));

vi.mock("../lib/api", () => ({ api: apiMock }));

describe("AccessDashboard", () => {
  beforeEach(() => {
    apiMock.listAccessUsers.mockReset();
    apiMock.listAccessAgents.mockReset();
    apiMock.listAccessGroups.mockReset();
    apiMock.listAccessSandboxes.mockReset();
    apiMock.listProfiles.mockReset();
    apiMock.createAccessUser.mockReset();
    apiMock.updateAccessUser.mockReset();
    apiMock.createAccessAgent.mockReset();
    apiMock.updateAccessAgent.mockReset();
    apiMock.createAccessGroup.mockReset();
    apiMock.updateAccessGroup.mockReset();
    apiMock.rotateAccessAgentKey.mockReset();

    apiMock.listAccessUsers.mockResolvedValue([
      {
        id: "user-1",
        username: "alice",
        role: "viewer",
        active: true,
        created_at: "2026-07-20T00:00:00Z",
        grants: [{ sandbox_id: "research", permission: "view" }],
        group_ids: ["group-1"],
        effective_grants: [
          { sandbox_id: "research", permission: "view" },
          { sandbox_id: "private", permission: "operate" },
        ],
      },
    ]);
    apiMock.listAccessAgents.mockResolvedValue([]);
    apiMock.listAccessGroups.mockResolvedValue([
      {
        id: "group-1",
        name: "Research team",
        description: null,
        active: true,
        created_at: "2026-07-20T00:00:00Z",
        member_user_ids: ["user-1"],
        grants: [{ sandbox_id: "private", permission: "operate" }],
      },
    ]);
    apiMock.listAccessSandboxes.mockResolvedValue([
      { sandbox_id: "research", profile_count: 2 },
      { sandbox_id: "private", profile_count: 1 },
    ]);
    apiMock.listProfiles.mockResolvedValue([
      {
        id: "profile-1",
        name: "Research browser",
        sandbox_id: "research",
        status: "running",
      },
      {
        id: "profile-2",
        name: "Private browser",
        sandbox_id: "private",
        status: "stopped",
      },
    ]);
  });

  it("defaults to identities and hides add/edit forms until explicit actions", async () => {
    render(<AccessDashboard onClose={vi.fn()} />);

    expect(await screen.findByText("alice")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Identities" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByLabelText("Username")).toBeNull();
    expect(screen.queryByLabelText("Display name")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Add person" }));
    expect(screen.getByLabelText("Username")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Username")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Edit alice" }));
    expect(screen.getByLabelText("Username")).toBeTruthy();
    expect((screen.getByLabelText("Username") as HTMLInputElement).disabled).toBe(true);
  });

  it("loads existing scopes and creates a Paperclip key with an explicit grant", async () => {
    apiMock.createAccessAgent.mockResolvedValue({
      id: "agent-1",
      display_name: "Research helper",
      paperclip_agent_id: "paperclip-research",
      active: true,
      created_at: "2026-07-20T00:00:00Z",
      grants: [{ sandbox_id: "research", permission: "automate" }],
      api_key: "test-agent-key-only",
    });

    render(<AccessDashboard onClose={vi.fn()} />);

    expect(await screen.findByText("alice")).toBeTruthy();
    expect(screen.getByText(/Direct: research: View/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Create agent key" }));
    fireEvent.change(screen.getByLabelText("Display name"), {
      target: { value: "Research helper" },
    });
    fireEvent.change(screen.getByLabelText("Browser control for research"), {
      target: { value: "operate" },
    });
    fireEvent.click(screen.getByLabelText("CDP automation for research"));
    fireEvent.click(screen.getByRole("button", { name: "Create key" }));

    await waitFor(() => expect(apiMock.createAccessAgent).toHaveBeenCalledWith({
      display_name: "Research helper",
      paperclip_agent_id: null,
      grants: [
        { sandbox_id: "research", permission: "operate" },
        { sandbox_id: "research", permission: "automate" },
      ],
    }));
    expect((await screen.findByLabelText("New Paperclip agent key") as HTMLInputElement).value)
      .toBe("test-agent-key-only");
  });

  it("creates and updates groups with membership and sandbox grants", async () => {
    apiMock.createAccessGroup.mockResolvedValue({
      id: "group-2",
      name: "Ops",
      description: "Operators",
      active: true,
      created_at: "2026-07-21T00:00:00Z",
      member_user_ids: ["user-1"],
      grants: [{ sandbox_id: "research", permission: "operate" }],
    });

    render(<AccessDashboard onClose={vi.fn()} />);

    expect(await screen.findByText("alice")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Groups" }));
    expect(screen.getByText("Research team")).toBeTruthy();
    expect(screen.queryByText("Shared private research browsers")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Add group" }));
    expect((screen.getByLabelText("Group name") as HTMLInputElement).maxLength).toBe(120);
    expect((screen.getByLabelText("Short description") as HTMLTextAreaElement).maxLength).toBe(500);
    fireEvent.change(screen.getByLabelText("Group name"), { target: { value: "Ops" } });
    fireEvent.change(screen.getByLabelText("Short description"), { target: { value: "Operators" } });
    fireEvent.click(screen.getByLabelText("Group member alice"));
    fireEvent.change(screen.getByLabelText("Browser control for research"), {
      target: { value: "operate" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create group" }));

    await waitFor(() => expect(apiMock.createAccessGroup).toHaveBeenCalledWith({
      name: "Ops",
      description: "Operators",
      active: true,
      member_user_ids: ["user-1"],
      grants: [{ sandbox_id: "research", permission: "operate" }],
    }));

    fireEvent.click(screen.getByRole("button", { name: "Edit group Research team" }));
    expect((screen.getByLabelText("Short description") as HTMLTextAreaElement).value).toBe("");
    fireEvent.click(screen.getByLabelText("Group active"));
    fireEvent.click(screen.getByRole("button", { name: "Save group" }));

    await waitFor(() => expect(apiMock.updateAccessGroup).toHaveBeenCalledWith("group-1", {
      name: "Research team",
      description: null,
      active: false,
      member_user_ids: ["user-1"],
      grants: [{ sandbox_id: "private", permission: "operate" }],
    }));
  });

  it("updates user group assignments in person payloads", async () => {
    render(<AccessDashboard onClose={vi.fn()} />);

    expect(await screen.findByText("alice")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Edit alice" }));
    fireEvent.click(screen.getByLabelText("User group Research team"));
    fireEvent.click(screen.getByRole("button", { name: "Save person" }));

    await waitFor(() => expect(apiMock.updateAccessUser).toHaveBeenCalledWith("user-1", {
      role: "viewer",
      active: true,
      grants: [{ sandbox_id: "research", permission: "view" }],
      group_ids: [],
    }));
  });

  it("keeps mobile access controls at touch-target size", async () => {
    apiMock.listAccessAgents.mockResolvedValue([
      {
        id: "agent-1",
        display_name: "Research helper",
        paperclip_agent_id: "paperclip-research",
        active: true,
        created_at: "2026-07-20T00:00:00Z",
        grants: [{ sandbox_id: "research", permission: "automate" }],
      },
    ]);

    render(<AccessDashboard onClose={vi.fn()} />);

    expect(await screen.findByText("alice")).toBeTruthy();

    for (const buttonName of ["Refresh", "Close", "Add person", "Create agent key"]) {
      expect(screen.getByRole("button", { name: buttonName }).className).toContain("min-h-11");
    }
    expect(screen.getByRole("button", { name: "Edit alice" }).className).toContain("h-11");
    expect(screen.getByRole("button", { name: "Rotate key for Research helper" }).className).toContain("h-11");
    expect(screen.getByRole("button", { name: "Edit Research helper" }).className).toContain("h-11");

    fireEvent.click(screen.getByRole("button", { name: "Add person" }));
    expect(screen.getByLabelText("Username").className).toContain("min-h-11");
    expect(screen.getByLabelText("Password").className).toContain("min-h-11");
    expect(screen.getByLabelText("Role").className).toContain("h-11");
    expect(screen.getByLabelText("Browser control for research").className).toContain("h-11");
    expect(screen.getByLabelText("CDP automation for research").closest("label")?.className).toContain("min-h-11");
    expect(screen.getByLabelText("User group Research team").closest("label")?.className).toContain("min-h-11");

    fireEvent.click(screen.getByRole("tab", { name: "Groups" }));
    fireEvent.click(screen.getByRole("button", { name: "Add group" }));
    expect(screen.getByLabelText("Group name").className).toContain("min-h-11");
    expect(screen.getByLabelText("Short description").className).toContain("min-h-11");
    expect(screen.getByLabelText("Group active").closest("label")?.className).toContain("min-h-11");
    expect(screen.getByLabelText("Group member alice").closest("label")?.className).toContain("min-h-11");
  });

  it("allows long sandbox identifiers to shrink instead of widening a mobile form", async () => {
    apiMock.listAccessSandboxes.mockResolvedValue([
      { sandbox_id: "paperclip-automation-sandbox-with-a-long-name", profile_count: 1 },
    ]);

    render(<AccessDashboard onClose={vi.fn()} />);

    expect(await screen.findByText("alice")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Add person" }));

    const longSandbox = "paperclip-automation-sandbox-with-a-long-name";
    const permission = screen.getByLabelText(`Browser control for ${longSandbox}`);
    const grantFieldset = permission.closest("fieldset");
    const identitiesPanel = screen.getByRole("tabpanel", { name: "Identities" });

    expect(grantFieldset?.className).toContain("min-w-0");
    expect(permission.parentElement?.className).toContain("min-w-0");
    expect(permission.className).toContain("shrink-0");
    expect(identitiesPanel.className).toContain("min-w-0");
  });

  it("uses inherited effective grants for people access preview", async () => {
    render(<AccessDashboard onClose={vi.fn()} />);

    expect(await screen.findByText("alice")).toBeTruthy();
    expect(screen.getByText("Effective access · 2 browsers")).toBeTruthy();
    const preview = screen.getByLabelText("Effective browser access for alice");
    expect(preview.textContent).toContain("Research browser");
    expect(preview.textContent).toContain("Private browser");
    expect(preview.textContent).toContain("Operate");
    expect(within(screen.getByLabelText("Groups for alice")).getByText("Research team")).toBeTruthy();
  });
});
