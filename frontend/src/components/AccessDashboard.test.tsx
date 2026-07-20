import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AccessDashboard } from "./AccessDashboard";

const apiMock = vi.hoisted(() => ({
  listAccessUsers: vi.fn(),
  listAccessAgents: vi.fn(),
  listAccessSandboxes: vi.fn(),
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
    fireEvent.change(screen.getAllByLabelText("Permission for research")[1], {
      target: { value: "automate" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create agent key" }));

    await waitFor(() => expect(apiMock.createAccessAgent).toHaveBeenCalledWith({
      display_name: "Research helper",
      paperclip_agent_id: null,
      grants: [{ sandbox_id: "research", permission: "automate" }],
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

    for (const permissionSelect of screen.getAllByLabelText("Permission for research")) {
      expect(permissionSelect.className).toContain("h-11");
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
});
