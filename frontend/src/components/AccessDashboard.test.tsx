import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AccessDashboard } from "./AccessDashboard";

const apiMock = vi.hoisted(() => ({
  listAccessUsers: vi.fn(),
  listAccessAgents: vi.fn(),
  listAccessSandboxes: vi.fn(),
  listProfiles: vi.fn(),
  createAccessUser: vi.fn(),
  updateAccessUser: vi.fn(),
  createAccessAgent: vi.fn(),
  updateAccessAgent: vi.fn(),
  rotateAccessAgentKey: vi.fn(),
}));

vi.mock("../lib/api", () => ({ api: apiMock }));

describe("AccessDashboard", () => {
  beforeEach(() => {
    apiMock.listAccessUsers.mockReset();
    apiMock.listAccessAgents.mockReset();
    apiMock.listAccessSandboxes.mockReset();
    apiMock.listProfiles.mockReset();
    apiMock.createAccessUser.mockReset();
    apiMock.updateAccessUser.mockReset();
    apiMock.createAccessAgent.mockReset();
    apiMock.updateAccessAgent.mockReset();
    apiMock.rotateAccessAgentKey.mockReset();

    apiMock.listAccessUsers.mockResolvedValue([
      {
        id: "user-1",
        username: "alice",
        role: "viewer",
        active: true,
        created_at: "2026-07-20T00:00:00Z",
        grants: [{ sandbox_id: "research", permission: "view" }],
      },
    ]);
    apiMock.listAccessAgents.mockResolvedValue([]);
    apiMock.listAccessSandboxes.mockResolvedValue([
      { sandbox_id: "research", profile_count: 2 },
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
    expect(screen.getByText(/research: View/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Display name"), {
      target: { value: "Research helper" },
    });
    fireEvent.change(screen.getAllByLabelText("Browser control for research")[1], {
      target: { value: "operate" },
    });
    fireEvent.click(screen.getAllByLabelText("CDP automation for research")[1]);
    fireEvent.click(screen.getByRole("button", { name: "Create agent key" }));

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

    for (const permissionSelect of screen.getAllByLabelText("Browser control for research")) {
      expect(permissionSelect.className).toContain("h-11");
    }
    for (const automationToggle of screen.getAllByLabelText("CDP automation for research")) {
      expect(automationToggle.closest("label")?.className).toContain("min-h-11");
    }
    expect(screen.getByLabelText("Username").className).toContain("min-h-11");
    expect(screen.getByLabelText("Password").className).toContain("min-h-11");
    expect(screen.getByLabelText("Role").className).toContain("h-11");
    expect(screen.getByLabelText("Display name").className).toContain("min-h-11");
    expect(screen.getByLabelText("Paperclip agent ID (optional)").className).toContain("min-h-11");
    expect(screen.getByRole("button", { name: "Edit alice" }).className).toBe("mobile-icon-button");
    expect(screen.getByRole("button", { name: "Rotate key for Research helper" }).className).toBe("mobile-icon-button");
    expect(screen.getByRole("button", { name: "Edit Research helper" }).className).toBe("mobile-icon-button");
  });

  it("allows long sandbox identifiers to shrink instead of widening a mobile form", async () => {
    apiMock.listAccessSandboxes.mockResolvedValue([
      { sandbox_id: "paperclip-automation-sandbox-with-a-long-name", profile_count: 1 },
    ]);

    render(<AccessDashboard onClose={vi.fn()} />);

    const longSandbox = "paperclip-automation-sandbox-with-a-long-name";
    const permission = await screen.findAllByLabelText(`Browser control for ${longSandbox}`);
    const grantFieldset = permission[0].closest("fieldset");
    const peopleSection = screen.getByRole("heading", { name: "People" }).closest("section");
    const dashboardGrid = peopleSection?.parentElement;

    expect(grantFieldset?.className).toContain("min-w-0");
    expect(permission[0].parentElement?.className).toContain("min-w-0");
    expect(permission[0].className).toContain("shrink-0");
    expect(peopleSection?.className).toContain("min-w-0");
    expect(dashboardGrid?.className).toContain("grid-cols-1");
  });

  it("shows the effective profile scope and keeps unrelated sandboxes hidden", async () => {
    render(<AccessDashboard onClose={vi.fn()} />);

    expect(await screen.findByText("alice")).toBeTruthy();
    expect(screen.getByText("Effective access · 1 browser")).toBeTruthy();
    const preview = screen.getByLabelText("Effective browser access for alice");
    expect(preview.textContent).toContain("Research browser");
    expect(preview.textContent).toContain("View");
    expect(preview.textContent).not.toContain("Private browser");
  });
});
