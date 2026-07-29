import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DEFAULT_BROWSER_TOOLS } from "./ProviderToolControl";
import {
  DEFAULT_WORKSPACE_RUNTIME_CONFIG,
  WorkspaceRuntimeConfigProvider,
  useWorkspaceRuntimeConfig,
} from "./WorkspaceRuntimeConfig";

function RuntimeEditor({ label }: { label: string }) {
  const { config, setConfig } = useWorkspaceRuntimeConfig();

  return (
    <section aria-label={label}>
      <output data-testid={`${label}-agent`}>{config.agent}</output>
      <output data-testid={`${label}-acpx-agent`}>{config.acpxAgent}</output>
      <output data-testid={`${label}-provider`}>
        {config.providerRouting.providerId}:{config.providerRouting.modelAlias}
      </output>
      <button
        type="button"
        onClick={() =>
          setConfig((current) => ({
            ...current,
            agent: "acpx",
            acpxAgent: "codex",
            providerRouting: {
              ...current.providerRouting,
              providerId: "codex",
              modelAlias: "gpt-5.5",
            },
          }))
        }
      >
        Update {label}
      </button>
    </section>
  );
}

describe("WorkspaceRuntimeConfig", () => {
  it("shares one runtime configuration between settings and runner consumers", () => {
    render(
      <WorkspaceRuntimeConfigProvider>
        <RuntimeEditor label="settings" />
        <RuntimeEditor label="runner" />
      </WorkspaceRuntimeConfigProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Update settings" }));

    expect(screen.getByTestId("runner-agent").textContent).toBe("acpx");
    expect(screen.getByTestId("runner-acpx-agent").textContent).toBe("codex");
    expect(screen.getByTestId("runner-provider").textContent).toBe("codex:gpt-5.5");
  });

  it("tracks runtime mode explicitly instead of deriving it from provider transport", () => {
    expect(DEFAULT_WORKSPACE_RUNTIME_CONFIG.mode).toBe("cli");
    expect(DEFAULT_WORKSPACE_RUNTIME_CONFIG.providerRouting.transport).toBe("acp");
  });

  it("keeps canonical browser tools in the default provider route", () => {
    expect(DEFAULT_WORKSPACE_RUNTIME_CONFIG.providerRouting.browserTools).toEqual(DEFAULT_BROWSER_TOOLS);
  });
});
