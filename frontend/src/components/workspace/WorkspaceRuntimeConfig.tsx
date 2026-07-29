import { createContext, useContext, useMemo, useState } from "react";
import type { Dispatch, PropsWithChildren, SetStateAction } from "react";
import type { AcpxAgent, OrcaAgentCli } from "../../lib/api";
import { DEFAULT_BROWSER_TOOLS, type ProviderRoutingState } from "./ProviderToolControl";

export type ManagedHarness = "browser-use" | "acpx" | "unbrowse" | "stagehand";
export type AgentMode = ManagedHarness | "antigravity" | OrcaAgentCli;
export type WorkspaceRuntimeMode = "cli" | "acp" | "acpx";

export interface WorkspaceRuntimeConfig {
  mode: WorkspaceRuntimeMode;
  agent: AgentMode;
  acpxAgent: AcpxAgent;
  providerRouting: ProviderRoutingState;
}

export const DEFAULT_PROVIDER_ROUTING: ProviderRoutingState = {
  providerId: "grok",
  transport: "acp",
  modelAlias: "",
  browserTools: DEFAULT_BROWSER_TOOLS,
  routingPolicy: "ordered-fallback",
};

export const DEFAULT_WORKSPACE_RUNTIME_CONFIG: WorkspaceRuntimeConfig = {
  mode: "cli",
  agent: "agy",
  acpxAgent: "grok-build",
  providerRouting: DEFAULT_PROVIDER_ROUTING,
};

interface WorkspaceRuntimeConfigContextValue {
  config: WorkspaceRuntimeConfig;
  setConfig: Dispatch<SetStateAction<WorkspaceRuntimeConfig>>;
}

const WorkspaceRuntimeConfigContext = createContext<WorkspaceRuntimeConfigContextValue | null>(null);

export function WorkspaceRuntimeConfigProvider({ children }: PropsWithChildren) {
  const [config, setConfig] = useState<WorkspaceRuntimeConfig>(DEFAULT_WORKSPACE_RUNTIME_CONFIG);
  const value = useMemo(() => ({ config, setConfig }), [config]);

  return (
    <WorkspaceRuntimeConfigContext.Provider value={value}>
      {children}
    </WorkspaceRuntimeConfigContext.Provider>
  );
}

export function useWorkspaceRuntimeConfig() {
  const context = useContext(WorkspaceRuntimeConfigContext);
  const [fallbackConfig, setFallbackConfig] = useState<WorkspaceRuntimeConfig>(DEFAULT_WORKSPACE_RUNTIME_CONFIG);
  if (context) return context;
  return { config: fallbackConfig, setConfig: setFallbackConfig };
}
