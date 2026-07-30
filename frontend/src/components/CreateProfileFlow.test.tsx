import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CreateProfileFlow } from "./CreateProfileFlow";

const apiMock = vi.hoisted(() => ({
  listProfileTemplates: vi.fn(),
  getExtensionDefaults: vi.fn(),
  updateExtensionDefaults: vi.fn(),
}));

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listProfileTemplates: apiMock.listProfileTemplates,
      getExtensionDefaults: apiMock.getExtensionDefaults,
      updateExtensionDefaults: apiMock.updateExtensionDefaults,
    },
  };
});

describe("CreateProfileFlow", () => {
  beforeEach(() => {
    apiMock.listProfileTemplates.mockResolvedValue([]);
    apiMock.getExtensionDefaults.mockResolvedValue({
      selected_ids: ["catalog-extension"],
      extensions: [{ id: "catalog-extension", name: "Catalog extension" }],
    });
  });

  it("does not expose extension-default configuration in the operator UI", async () => {
    render(
      <CreateProfileFlow
        projectId="default"
        harness="codex"
        onCreated={vi.fn()}
        onSave={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    await waitFor(() => expect(apiMock.listProfileTemplates).toHaveBeenCalled());
    expect(screen.queryByText("Default extensions (Comet)")).toBeNull();
    expect(apiMock.getExtensionDefaults).not.toHaveBeenCalled();
    expect(apiMock.updateExtensionDefaults).not.toHaveBeenCalled();
  });
});
