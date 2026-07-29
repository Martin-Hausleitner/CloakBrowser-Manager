import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import {
  api,
  isSafeProviderId,
  type AcpxAgent,
  type BrowserToolId,
  type BrowserToolReadiness,
  type BrowserToolReadinessTarget,
  type OrcaCapabilities,
  type Profile,
  type ProviderId,
  type ProviderReadiness,
  type TaskHarnessAgentPreflight,
  type TaskHarnessPresence,
} from "../lib/api";
import {
  DEFAULT_BROWSER_TOOLS,
  ProviderToolControl,
  providerLaunchBlockReason,
  type ProviderRoutingState,
} from "./workspace/ProviderToolControl";
import {
  useWorkspaceRuntimeConfig,
  type AgentMode,
  type ManagedHarness,
  type WorkspaceRuntimeMode,
} from "./workspace/WorkspaceRuntimeConfig";
import { UI_STATE } from "../lib/uiFlowRegistry";

interface HarnessSettingsWorkspaceProps {
  profiles: Profile[];
  selectedProfile: Profile | null;
  onSelectProfile?: (profileId: string) => void;
  onBack?: () => void;
  mobile?: boolean;
  runActive?: boolean;
}

const MANAGED_HARNESSES: readonly ManagedHarness[] = ["browser-use", "acpx"];
const ACPX_AGENT_OPTIONS: ReadonlyArray<{ value: AcpxAgent; label: string }> = [
  { value: "claude", label: "Claude" },
  { value: "grok-build", label: "Grok Build" },
  { value: "codex", label: "Codex" },
  { value: "cursor", label: "Cursor" },
  { value: "opencode", label: "OpenCode" },
];
const LOCAL_CLI_OPTIONS: ReadonlyArray<{ value: AgentMode; label: string }> = [
  { value: "agy", label: "AGY" },
  { value: "grok", label: "Grok" },
  { value: "cursor-agent", label: "Cursor Agent" },
  { value: "codex", label: "Codex" },
];

const TOOL_LABELS: Record<BrowserToolId, string> = {
  unbrowse: "Unbrowse",
  stagehand: "Stagehand",
  "browser-harness": "Browser Harness",
};

function managedHarnessLabel(harness: ManagedHarness): string {
  if (harness === "browser-use") return "Browser Use";
  if (harness === "acpx") return "ACPX";
  if (harness === "unbrowse") return "Unbrowse";
  return "Stagehand";
}

function acpAgentForProvider(providerId: ProviderId): AcpxAgent | null {
  if (!isSafeProviderId(providerId)) return null;
  return providerId === "grok" ? "grok-build" : providerId;
}

function harnessReady(presence: TaskHarnessPresence | null | undefined): boolean {
  return Boolean(presence?.worker_seen_recently);
}

function harnessReason(presence: TaskHarnessPresence | null | undefined): string {
  if (!presence) return "Not checked";
  if (presence.worker_seen_recently) return "Ready";
  return presence.reason || presence.state;
}

function browserToolsForOnly(toolId: BrowserToolId) {
  return DEFAULT_BROWSER_TOOLS.map((tool) => ({ ...tool, enabled: tool.id === toolId }));
}

export function HarnessSettingsWorkspace({
  profiles,
  selectedProfile,
  onSelectProfile,
  onBack,
  mobile = false,
  runActive = false,
}: HarnessSettingsWorkspaceProps) {
  const { config, setConfig } = useWorkspaceRuntimeConfig();
  const [caps, setCaps] = useState<OrcaCapabilities | null>(null);
  const [providerReadiness, setProviderReadiness] = useState<ProviderReadiness | null>(null);
  const [browserToolReadiness, setBrowserToolReadiness] = useState<BrowserToolReadiness | null>(null);
  const [harnessPresence, setHarnessPresence] = useState<Partial<Record<ManagedHarness, TaskHarnessPresence>>>({});
  const [acpxPreflights, setAcpxPreflights] = useState<TaskHarnessAgentPreflight[]>([]);
  const [loading, setLoading] = useState(false);
  const [smokeTarget, setSmokeTarget] = useState<ManagedHarness | BrowserToolId | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const selectedProfileIdRef = useRef<string | null>(selectedProfile?.id ?? null);

  const runtimeMode = config.mode;
  const selectedProfileIsRunnable = Boolean(selectedProfile && selectedProfile.status === "running");

  useEffect(() => {
    selectedProfileIdRef.current = selectedProfile?.id ?? null;
  }, [selectedProfile?.id]);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setMessage(null);
    try {
      const [capsResult, providerResult, toolResult, presenceResults] = await Promise.all([
        api.getOrcaCapabilities({ signal }),
        api.getProviderReadiness({ signal }),
        api.getBrowserToolReadiness({ signal }),
        Promise.all(MANAGED_HARNESSES.map(async (harness) => ({
          harness,
          presence: await api.getTaskHarnessPresence(harness, { signal }),
        }))),
      ]);
      const nextPresence: Partial<Record<ManagedHarness, TaskHarnessPresence>> = {};
      for (const result of presenceResults) nextPresence[result.harness] = result.presence;
      setCaps(capsResult);
      setProviderReadiness(providerResult);
      setBrowserToolReadiness(toolResult);
      setHarnessPresence(nextPresence);
      try {
        const preflightResult = await api.getTaskHarnessPreflights("acpx", { signal });
        setAcpxPreflights(preflightResult.agents);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setAcpxPreflights([]);
        setMessage(error instanceof Error ? error.message : "ACPX preflight refresh failed");
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setMessage(error instanceof Error ? error.message : "Settings readiness refresh failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  const setRuntimeMode = (mode: WorkspaceRuntimeMode) => {
    if (runActive) return;
    setConfig((current) => {
      if (mode === "cli") return { ...current, mode, agent: "agy" };
      const providerAgent = acpAgentForProvider(current.providerRouting.providerId) ?? current.acpxAgent;
      return {
        ...current,
        mode,
        agent: "acpx",
        acpxAgent: mode === "acp" ? providerAgent : current.acpxAgent,
      };
    });
  };

  const setProviderRouting = (providerRouting: ProviderRoutingState) => {
    if (runActive) return;
    setConfig((current) => ({
      ...current,
      providerRouting,
      acpxAgent: acpAgentForProvider(providerRouting.providerId) ?? current.acpxAgent,
    }));
  };

  const selectManagedHarness = (harness: ManagedHarness) => {
    if (runActive) return;
    setConfig((current) => ({
      ...current,
      mode: harness === "acpx" ? "acpx" : "cli",
      agent: harness,
      acpxAgent: harness === "acpx"
        ? acpAgentForProvider(current.providerRouting.providerId) ?? current.acpxAgent
        : current.acpxAgent,
    }));
  };

  const selectBrowserTool = (toolId: BrowserToolId) => {
    if (runActive) return;
    setConfig((current) => ({
      ...current,
      mode: "acpx",
      agent: "acpx",
      acpxAgent: acpAgentForProvider(current.providerRouting.providerId) ?? current.acpxAgent,
      providerRouting: {
        ...current.providerRouting,
        browserTools: browserToolsForOnly(toolId),
      },
    }));
  };

  const runSmokeTest = async (harness: ManagedHarness) => {
    if (runActive || !selectedProfile || selectedProfile.status !== "running" || smokeTarget) return;
    const profileAtStart = selectedProfile;
    setSmokeTarget(harness);
    setMessage(null);
    try {
      const presence = await api.getTaskHarnessPresence(harness, {});
      setHarnessPresence((current) => ({ ...current, [harness]: presence }));
      if (!presence.worker_seen_recently) {
        throw new Error(presence.reason || `${managedHarnessLabel(harness)} worker unavailable`);
      }
      const normalizedProviderRun = harness === "acpx";
      if (normalizedProviderRun) {
        const blockReason = providerLaunchBlockReason(config.providerRouting, providerReadiness);
        if (blockReason) throw new Error(blockReason);
      }
      await api.runProfileHealth(profileAtStart.id);
      await api.getProfileHealth(profileAtStart.id);
      if (selectedProfileIdRef.current !== profileAtStart.id) return;
      const session = await api.createTaskSession({
        profile_id: profileAtStart.id,
        title: `${managedHarnessLabel(harness)} smoke test`,
        metadata: { source: "settings-harness-smoke-test", harness, smoke_test: true },
      }, {});
      if (selectedProfileIdRef.current !== profileAtStart.id) return;
      const startedRun = await api.createTaskRun(session.id, {
        harness,
        agent: normalizedProviderRun ? config.acpxAgent : null,
        task: "Open https://example.com/ and report the page title.",
        profile_id: profileAtStart.id,
        launch_if_stopped: false,
        allowed_origins: ["https://example.com"],
        max_steps: 8,
        timeout_seconds: 180,
        model_alias: null,
        ...(normalizedProviderRun ? {
          provider: {
            id: config.providerRouting.providerId,
            transport: config.providerRouting.transport,
            ...(config.providerRouting.modelAlias ? { model_alias: config.providerRouting.modelAlias } : {}),
          },
          browser_tools: config.providerRouting.browserTools,
          routing_policy: {
            mode: "ordered-fallback" as const,
            allow_second_browser: false,
            max_tool_attempts: 3,
          },
        } : {}),
      }, {});
      if (selectedProfileIdRef.current !== profileAtStart.id) {
        await api.cancelTaskRun(startedRun.id);
        return;
      }
      setMessage(`${managedHarnessLabel(harness)} smoke test started for ${profileAtStart.name}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Smoke test failed to start");
    } finally {
      setSmokeTarget(null);
    }
  };

  const runBrowserToolSmokeTest = async (toolId: BrowserToolId) => {
    if (runActive || !selectedProfile || selectedProfile.status !== "running" || smokeTarget) return;
    const profileAtStart = selectedProfile;
    setSmokeTarget(toolId);
    setMessage(null);
    try {
      const presence = await api.getTaskHarnessPresence("acpx", {});
      setHarnessPresence((current) => ({ ...current, acpx: presence }));
      if (!presence.worker_seen_recently) {
        throw new Error(presence.reason || "ACPX worker unavailable");
      }
      const tool = browserToolById[toolId];
      if (!tool?.ready) {
        throw new Error(tool?.reason_code || `${TOOL_LABELS[toolId]} is unavailable`);
      }
      const blockReason = providerLaunchBlockReason({
        ...config.providerRouting,
        browserTools: browserToolsForOnly(toolId),
      }, providerReadiness);
      if (blockReason) throw new Error(blockReason);
      await api.runProfileHealth(profileAtStart.id);
      await api.getProfileHealth(profileAtStart.id);
      if (selectedProfileIdRef.current !== profileAtStart.id) return;
      const session = await api.createTaskSession({
        profile_id: profileAtStart.id,
        title: `${TOOL_LABELS[toolId]} smoke test`,
        metadata: {
          source: "settings-browser-tool-smoke-test",
          harness: "acpx",
          browser_tool: toolId,
          smoke_test: true,
        },
      }, {});
      if (selectedProfileIdRef.current !== profileAtStart.id) return;
      const provider = providerReadiness?.providers.find((item) =>
        item.provider === config.providerRouting.providerId && item.transport === config.providerRouting.transport,
      );
      const selectedModelAlias = provider?.model_aliases.includes(config.providerRouting.modelAlias)
        ? config.providerRouting.modelAlias
        : null;
      const startedRun = await api.createTaskRun(session.id, {
        harness: "acpx",
        agent: config.acpxAgent,
        task: "Open https://example.com/ and report the page title.",
        profile_id: profileAtStart.id,
        launch_if_stopped: false,
        allowed_origins: ["https://example.com"],
        max_steps: 8,
        timeout_seconds: 180,
        model_alias: selectedModelAlias,
        provider: {
          id: config.providerRouting.providerId,
          transport: config.providerRouting.transport,
          ...(selectedModelAlias ? { model_alias: selectedModelAlias } : {}),
        },
        browser_tools: browserToolsForOnly(toolId),
        routing_policy: {
          mode: "ordered-fallback" as const,
          allow_second_browser: false,
          max_tool_attempts: 3,
        },
      }, {});
      if (selectedProfileIdRef.current !== profileAtStart.id) {
        await api.cancelTaskRun(startedRun.id);
        return;
      }
      setMessage(`${TOOL_LABELS[toolId]} smoke test started for ${profileAtStart.name}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Smoke test failed to start");
    } finally {
      setSmokeTarget(null);
    }
  };

  const browserToolById = useMemo(() => {
    const next: Partial<Record<BrowserToolId, BrowserToolReadinessTarget>> = {};
    for (const tool of browserToolReadiness?.tools ?? []) next[tool.id] = tool;
    return next;
  }, [browserToolReadiness]);

  const toolReadiness = useMemo(() => {
    const next: Partial<Record<BrowserToolId, { ready: boolean; reason: string | null; test?: string }>> = {};
    for (const tool of browserToolReadiness?.tools ?? []) {
      next[tool.id] = {
        ready: tool.ready,
        reason: tool.ready ? null : tool.checked_at ? `${tool.reason_code} · ${tool.checked_at}` : tool.reason_code,
        test: tool.ready ? tool.checked_at ?? "passed" : undefined,
      };
    }
    return next;
  }, [browserToolReadiness]);

  return (
    <main
      className={`${mobile ? "h-dvh" : "h-full"} min-h-0 overflow-y-auto bg-[#0d0d0f] text-[#ededf0]`}
      data-testid="harness-settings-workspace"
      data-ui-state={UI_STATE.appDesktopSettings}
    >
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-3 p-3" data-testid="harness-settings-main-content">
        <header className="flex items-center justify-between gap-3 border-b border-[#2d2d33] pb-3">
          <div className="min-w-0">
            <h1 className="truncate text-base font-semibold">Harness Settings</h1>
            <p className="text-[11px] text-[#9ca3af]">Runtime, providers, models, browser tools and readiness checks.</p>
          </div>
          <div className="flex items-center gap-2">
            {onBack ? (
              <button type="button" className="btn btn-secondary h-8 px-2 text-xs" onClick={onBack}>Back</button>
            ) : null}
            <button
              type="button"
              className="inline-flex h-8 items-center gap-1 rounded border border-[#3c3c43] px-2 text-xs text-[#d4d4d8] disabled:opacity-50"
              onClick={() => void refresh()}
              disabled={loading}
              aria-label="Refresh Settings readiness"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </button>
          </div>
        </header>

        {message ? <div className="rounded border border-[#3b3b43] bg-[#151519] px-3 py-2 text-xs text-[#d4d4d8]">{message}</div> : null}

        <section className="rounded-md border border-[#303038] bg-[#111114] p-3">
          <h2 className="text-sm font-semibold">Runtime</h2>
          <div className="mt-2 grid gap-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div>
              <div className="grid max-w-xs grid-cols-3 rounded-md border border-[#35353b] bg-[#0b0b0d] p-0.5" role="group" aria-label="Runtime mode">
                {(["cli", "acp", "acpx"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    className={`h-8 rounded text-xs font-semibold ${runtimeMode === mode ? "bg-[#2e2b5f] text-white" : "text-[#a1a1aa] hover:bg-[#202024]"}`}
                    aria-pressed={runtimeMode === mode}
                    disabled={runActive}
                    onClick={() => setRuntimeMode(mode)}
                  >
                    {mode.toUpperCase()}
                  </button>
                ))}
              </div>
              <div className="mt-3 text-xs font-medium">Local CLI</div>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {LOCAL_CLI_OPTIONS.map((option) => {
                  const detected = caps?.agents.includes(option.value) ?? false;
                  return (
                    <button
                      key={option.value}
                      type="button"
                      className={`rounded border px-2 py-1 text-[11px] ${config.agent === option.value ? "border-indigo-400 bg-indigo-950/60 text-indigo-200" : "border-[#34343b] bg-[#17171b] text-[#d4d4d8]"}`}
                      onClick={() => setConfig((current) => ({ ...current, mode: "cli", agent: option.value }))}
                      disabled={runActive || (caps ? !detected : false)}
                    >
                      {option.label} · {caps ? (detected ? "Detected" : "Unavailable") : "Checking"}
                    </button>
                  );
                })}
              </div>
            </div>
            <label className="block text-xs">
              <span className="font-medium">Selected browser profile for tests</span>
              <select
                className="input mt-1 h-9 w-full bg-[#18181b] text-xs"
                value={selectedProfile?.id ?? ""}
                onChange={(event) => {
                  if (runActive) return;
                  onSelectProfile?.(event.target.value);
                }}
                aria-label="Selected browser profile for tests"
                disabled={runActive}
              >
                <option value="" disabled>Choose live profile</option>
                {profiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>{profile.name}{profile.status === "running" ? " · live" : ""}</option>
                ))}
              </select>
              <span className="mt-1 block text-[11px] text-[#9ca3af]">
                {selectedProfileIsRunnable ? "Tests reuse the selected live profile." : "Start a profile before running smoke tests."}
              </span>
            </label>
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            <div className="rounded border border-[#2f2f36] bg-[#151519] p-2">
              <div className="text-xs font-medium">ACPX agent</div>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {ACPX_AGENT_OPTIONS.map((option) => {
                  const preflight = acpxPreflights.find((item) => item.agent === option.value);
                  return (
                    <button
                      key={option.value}
                      type="button"
                      className={`rounded border px-2 py-1 text-[11px] ${config.acpxAgent === option.value ? "border-indigo-400 bg-indigo-950/60 text-indigo-200" : "border-[#34343b] bg-[#17171b] text-[#d4d4d8]"}`}
                      disabled={runActive || (preflight ? !preflight.ready : false)}
                      title={preflight?.reason_code || undefined}
                      onClick={() => setConfig((current) => ({ ...current, mode: "acpx", agent: "acpx", acpxAgent: option.value }))}
                    >
                      {option.label} · {preflight ? (preflight.ready ? "Ready" : preflight.reason_code || preflight.state) : "Checking"}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="rounded border border-[#2f2f36] bg-[#151519] p-2">
              <div className="text-xs font-medium">Installed managed harnesses</div>
              <div className="mt-1 grid gap-1.5 sm:grid-cols-2">
                {MANAGED_HARNESSES.map((harness) => {
                  const presence = harnessPresence[harness];
                  const ready = harnessReady(presence);
                  const label = managedHarnessLabel(harness);
                  return (
                    <div
                      key={harness}
                      data-testid={`settings-harness-${harness}`}
                      data-selected={config.agent === harness ? "true" : "false"}
                      className={`flex items-center gap-2 rounded border px-2 py-1.5 ${config.agent === harness ? "border-indigo-400 bg-indigo-950/50" : "border-[#303038] bg-[#101014]"}`}
                    >
                      <span className={`h-2 w-2 rounded-full ${ready ? "bg-emerald-400" : "bg-amber-400"}`} />
                      <span className="min-w-0 flex-1 truncate text-xs">{label}</span>
                      <span className="text-[10px] text-[#a1a1aa]">{harnessReason(presence)}</span>
                      <button
                        type="button"
                        className="inline-flex h-7 items-center rounded border border-[#4f46e5] px-1.5 text-[10px] text-indigo-100 disabled:opacity-40"
                        onClick={() => selectManagedHarness(harness)}
                        disabled={runActive || !ready}
                        aria-label={`Select ${label} for next run`}
                        aria-pressed={config.agent === harness}
                      >
                        {config.agent === harness ? "Selected" : "Select"}
                      </button>
                      <button
                        type="button"
                        className="inline-flex h-7 items-center gap-1 rounded border border-[#3f3f48] px-1.5 text-[10px] disabled:opacity-40"
                        onClick={() => void runSmokeTest(harness)}
                        disabled={runActive || !ready || !selectedProfileIsRunnable || smokeTarget !== null}
                        aria-label={`Test ${label}`}
                      >
                        <Activity className={`h-3 w-3 ${smokeTarget === harness ? "animate-pulse" : ""}`} />
                        Test
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </section>

        <section className="rounded-md border border-[#303038] bg-[#111114] p-3">
          <h2 className="text-sm font-semibold">Provider</h2>
          <div className="mt-2">
            <ProviderToolControl
              state={config.providerRouting}
              readiness={providerReadiness}
              toolReadiness={toolReadiness}
              onChange={setProviderRouting}
              disabled={runActive}
            />
          </div>
          <div className="mt-3 grid gap-1.5 text-[11px] text-[#c4c4cc] sm:grid-cols-2 lg:grid-cols-3">
            {providerReadiness?.providers.map((provider) => (
              <div key={`${provider.provider}:${provider.transport}`} className="rounded border border-[#303038] bg-[#151519] px-2 py-1.5">
                <span className={provider.ready ? "text-emerald-300" : "text-amber-300"}>{provider.provider}</span>
                <span> · {provider.transport} · {provider.ready ? "Ready" : provider.reason_code || provider.state}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-md border border-[#303038] bg-[#111114] p-3">
          <h2 className="text-sm font-semibold">Browser tools</h2>
          <div className="mt-2 grid gap-2 md:grid-cols-3">
            {DEFAULT_BROWSER_TOOLS.map((tool) => {
              const info = browserToolById[tool.id];
              const ready = info?.ready === true;
              const acpxReady = harnessReady(harnessPresence.acpx);
              const providerReady = !providerLaunchBlockReason({
                ...config.providerRouting,
                browserTools: browserToolsForOnly(tool.id),
              }, providerReadiness);
              const canUseTool = ready && acpxReady && providerReady;
              return (
                <div
                  key={tool.id}
                  data-testid={`settings-browser-tool-${tool.id}`}
                  className={`rounded border border-[#303038] bg-[#151519] p-2 ${ready ? "" : "opacity-70"}`}
                  aria-disabled={!ready}
                >
                  <div className="text-xs font-medium">{TOOL_LABELS[tool.id]}</div>
                  <div className="mt-1 text-[11px] text-[#a1a1aa]">
                    {info ? `${info.state} · ${info.reason_code}${info.checked_at ? ` · ${info.checked_at}` : ""}` : "Not checked"}
                  </div>
                  <div className="mt-2 flex gap-1">
                    <button
                      type="button"
                      className="inline-flex h-7 items-center rounded border border-[#4f46e5] px-1.5 text-[10px] text-indigo-100 disabled:opacity-40"
                      onClick={() => selectBrowserTool(tool.id)}
                      disabled={runActive || !ready}
                      aria-label={`Select ${TOOL_LABELS[tool.id]} for next ACPX run`}
                    >
                      Select
                    </button>
                    <button
                      type="button"
                      className="inline-flex h-7 items-center gap-1 rounded border border-[#3f3f48] px-1.5 text-[10px] disabled:opacity-40"
                      onClick={() => void runBrowserToolSmokeTest(tool.id)}
                      disabled={runActive || !selectedProfileIsRunnable || smokeTarget !== null || !canUseTool}
                      aria-label={`Test ${TOOL_LABELS[tool.id]}`}
                      title={!canUseTool ? "ACPX, provider and browser tool readiness are required." : undefined}
                    >
                      <Activity className={`h-3 w-3 ${smokeTarget === tool.id ? "animate-pulse" : ""}`} />
                      Test
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      </div>
    </main>
  );
}
