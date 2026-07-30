import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { SetStateAction } from "react";
import {
  Activity,
  Camera,
  MonitorSmartphone,
  PanelLeftClose,
  PanelLeftOpen,
  SendHorizontal,
  Square,
  TerminalSquare,
} from "lucide-react";
import {
  api,
  type AcpxAgent,
  isSafeProviderId,
  type OrcaAgentCli,
  type OrcaCapabilities,
  type ProviderId,
  type ProviderReadiness,
  type OrcaSession,
  type Profile,
  type TaskOutput,
  type TaskHarnessAgentPreflight,
  type TaskHarnessPresence,
  type TaskRun,
} from "../../lib/api";
import { UI_STATE, uiStateAttr } from "../../lib/uiFlowRegistry";
import {
  ACTIVE_TASK_RUN_STATES,
  forgetBrowserUseRun,
  readRememberedBrowserUseRun,
  rememberBrowserUseRun,
} from "../../lib/managedTaskRunStorage";
import { ProfileViewer } from "../ProfileViewer";
import { LiveDevPanel } from "../LiveDevPanel";
import { AgentOutputTimeline } from "./AgentOutputTimeline";
import {
  providerInfo,
  providerLaunchBlockReason,
  type ProviderRoutingState,
} from "./ProviderToolControl";
import {
  useWorkspaceRuntimeConfig,
  type AgentMode,
  type ManagedHarness,
} from "./WorkspaceRuntimeConfig";

type FullViewPanel = "view" | "viewport" | "sessions" | null;
type FullViewFitMode = "fit" | "width" | "height";
type FullViewMode = "single" | "grid";

const MAX_DESKTOP_GRID_STREAMS = 6;

function acpAgentForProvider(providerId: ProviderId): AcpxAgent | null {
  if (!isSafeProviderId(providerId)) return null;
  return providerId === "grok" ? "grok-build" : providerId;
}

function acpxAgentLabel(agent: AcpxAgent): string {
  if (agent === "grok-build") return "Grok Build (grok-build)";
  return agent;
}

function providerLabel(providerId: ProviderId): string {
  const labels: Partial<Record<ProviderId, string>> = {
    antigravity: "Antigravity",
    codex: "Codex",
    claude: "Claude",
    cursor: "Cursor",
    grok: "Grok",
    opencode: "OpenCode",
  };
  return labels[providerId] ?? providerId;
}

const ACPX_AGENT_OPTIONS: ReadonlyArray<{ value: AcpxAgent; label: string }> = [
  { value: "claude", label: "Claude" },
  { value: "grok-build", label: "Grok Build" },
  { value: "codex", label: "Codex" },
  { value: "cursor", label: "Cursor" },
  { value: "opencode", label: "OpenCode" },
];
function preferredAgent(profile: Profile | null): AgentMode {
  if (profile?.harness === "browser-use") return "browser-use";
  if (profile?.harness === "acpx") return "acpx";
  if (profile?.harness === "unbrowse") return "unbrowse";
  if (profile?.harness === "stagehand") return "stagehand";
  if (profile?.harness === "antigravity") return "antigravity";
  return "agy";
}

function managedHarnessLabel(harness: ManagedHarness): string {
  if (harness === "browser-use") return "Browser Use";
  if (harness === "acpx") return "ACPX";
  if (harness === "unbrowse") return "Unbrowse";
  return "Stagehand";
}

function compactOrcaUnavailableMessage(caps: OrcaCapabilities | null): string {
  const notes = caps?.notes?.filter((note) => note.trim()) ?? [];
  if (!notes.length) {
    return "Orca is unavailable. Launch stays disabled until readiness checks pass.";
  }
  const hiddenCount = notes.length - 1;
  const hiddenLabel = hiddenCount === 1 ? "1 more check" : `${hiddenCount} more checks`;
  return hiddenCount
    ? `${notes[notes.length - 1]!} · ${hiddenLabel} in Session details`
    : notes[0]!;
}

function allowedOrigins(task: string): string[] {
  const origins = new Set<string>();
  for (const match of task.matchAll(/https?:\/\/[^\s<>"']+/gi)) {
    try {
      origins.add(new URL(match[0].replace(/[),.;!?]+$/, "")).origin);
    } catch {
      continue;
    }
  }
  return [...origins];
}

function healthReasonList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function healthReasonLabel(reason: string): string {
  const words = reason.replaceAll("_", " ");
  return words ? `${words.charAt(0).toUpperCase()}${words.slice(1)}` : reason;
}

function acpxPreflightLabel(preflight: TaskHarnessAgentPreflight | undefined): string {
  if (!preflight) return "ACP adapter has not been checked";
  const labels: Record<string, string> = {
    auth_required: "Sign in to this ACP agent on the worker",
    adapter_unavailable: "ACP adapter is not installed on the worker",
    version_mismatch: "ACPX worker version does not match",
    mcp_unavailable: "CloakBrowser MCP is unavailable to this ACP agent",
    protocol_error: "ACP adapter check failed",
    internal_error: "ACP adapter check failed",
    not_checked: "ACP adapter has not been checked",
    stale: "ACP adapter check is stale",
  };
  return labels[preflight.reason_code] ?? "ACP adapter is unavailable";
}

export interface AgentBrowserWorkspaceProps {
  profiles: Profile[];
  selectedProfile: Profile | null;
  canAutomate: boolean;
  canInteract: boolean;
  initialPromptDraft?: {
    id: string;
    profileId: string;
    task: string;
  } | null;
  onInitialPromptDraftApplied?: (draftId: string) => void;
  canManageViewport?: boolean;
  onViewportApply?: (width: number, height: number) => Promise<boolean>;
  onSelectProfile: (profileId: string) => void;
  onConnectionStatusChange?: (
    status: "connecting" | "connected" | "reconnecting" | "failed",
  ) => void;
  onViewerDisconnect?: () => void;
  onRunActivityChange?: (active: boolean) => void;
}

function statusLabel(session: OrcaSession | null, caps: OrcaCapabilities | null): string {
  if (!caps?.available) return "Orca unavailable";
  if (!session) return "Idle";
  if (session.status === "running") return "Connected";
  if (session.status === "starting") return "Starting";
  if (session.status === "error") return session.last_error || "Error";
  return "Stopped";
}

function currentPhoneFitViewport(fallbackWidth: number, fallbackHeight: number): { width: number; height: number } {
  const viewport = window.visualViewport;
  const layoutHeight = window.innerHeight || fallbackHeight;
  const viewportHeight = viewport?.height ?? layoutHeight;
  return {
    width: Math.round(viewport?.width ?? window.innerWidth ?? fallbackWidth),
    height: Math.round(Math.max(viewportHeight, layoutHeight, fallbackHeight)),
  };
}

export function AgentBrowserWorkspace({
  profiles,
  selectedProfile,
  canAutomate,
  canInteract,
  initialPromptDraft = null,
  onInitialPromptDraftApplied,
  canManageViewport = false,
  onViewportApply,
  onSelectProfile,
  onConnectionStatusChange,
  onViewerDisconnect,
  onRunActivityChange,
}: AgentBrowserWorkspaceProps) {
  const { config: runtimeConfig, setConfig: setRuntimeConfig } = useWorkspaceRuntimeConfig();
  const agent = runtimeConfig.agent;
  const runtimeMode = runtimeConfig.mode === "cli" && agent === "acpx" ? "acp" : runtimeConfig.mode;
  const acpxAgent = runtimeConfig.acpxAgent;
  const providerRouting = runtimeConfig.providerRouting;
  const setAgent = useCallback((next: SetStateAction<AgentMode>) => {
    setRuntimeConfig((current) => ({
      ...current,
      agent: typeof next === "function" ? next(current.agent) : next,
    }));
  }, [setRuntimeConfig]);
  const setAcpxAgent = useCallback((next: SetStateAction<AcpxAgent>) => {
    setRuntimeConfig((current) => ({
      ...current,
      acpxAgent: typeof next === "function" ? next(current.acpxAgent) : next,
    }));
  }, [setRuntimeConfig]);
  const setProviderRouting = useCallback((next: SetStateAction<ProviderRoutingState>) => {
    setRuntimeConfig((current) => ({
      ...current,
      providerRouting: typeof next === "function" ? next(current.providerRouting) : next,
    }));
  }, [setRuntimeConfig]);
  const [prompt, setPrompt] = useState("");
  const [caps, setCaps] = useState<OrcaCapabilities | null>(null);
  const [providerReadiness, setProviderReadiness] = useState<ProviderReadiness | null>(null);
  const [session, setSession] = useState<OrcaSession | null>(null);
  const [taskSessionId, setTaskSessionId] = useState<string | null>(null);
  const [taskRun, setTaskRun] = useState<TaskRun | null>(null);
  const [harnessPresence, setHarnessPresence] = useState<Partial<Record<ManagedHarness, TaskHarnessPresence>>>({});
  const [acpxPreflights, setAcpxPreflights] = useState<TaskHarnessAgentPreflight[]>([]);
  const [taskOutputs, setTaskOutputs] = useState<TaskOutput[]>([]);
  const [transcript, setTranscript] = useState("");
  const [cursor, setCursor] = useState(0);
  const [busy, setBusy] = useState(false);
  const [chatCollapsed, setChatCollapsed] = useState(false);
  const [viewerZoom, setViewerZoom] = useState(100);
  const [viewerFullscreen, setViewerFullscreen] = useState(false);
  const [fullViewPanel, setFullViewPanel] = useState<FullViewPanel>(null);
  const [fullViewFitMode, setFullViewFitMode] = useState<FullViewFitMode>("fit");
  const [fullViewMode, setFullViewMode] = useState<FullViewMode>("single");
  const [viewportControlsOpen, setViewportControlsOpen] = useState(false);
  const [viewportWidth, setViewportWidth] = useState(selectedProfile?.screen_width ?? 1280);
  const [viewportHeight, setViewportHeight] = useState(selectedProfile?.screen_height ?? 720);
  const [viewportApplying, setViewportApplying] = useState(false);
  const [screenshotBusy, setScreenshotBusy] = useState(false);
  const [fullViewMetricsOpen, setFullViewMetricsOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const runPollRef = useRef<number | null>(null);
  const transcriptEndRef = useRef<HTMLSpanElement | null>(null);
  const appliedInitialPromptDraftIdRef = useRef<string | null>(null);
  const viewerPaneRef = useRef<HTMLElement | null>(null);
  const viewerFullscreenButtonRef = useRef<HTMLButtonElement | null>(null);
  const restoreViewerFullscreenFocusRef = useRef(false);
  const selectedProfileIdRef = useRef<string | null>(selectedProfile?.id ?? null);
  selectedProfileIdRef.current = selectedProfile?.id ?? null;
  const runtimeProfileIdRef = useRef<string | null>(null);

  const runningProfiles = useMemo(
    () => profiles.filter((profile) => profile.status === "running"),
    [profiles],
  );
  const desktopGridProfiles = useMemo(() => {
    const selectedId = selectedProfile?.id;
    return [...runningProfiles]
      .sort((left, right) => {
        if (left.id === selectedId) return -1;
        if (right.id === selectedId) return 1;
        return 0;
      })
      .slice(0, MAX_DESKTOP_GRID_STREAMS);
  }, [runningProfiles, selectedProfile?.id]);
  const desktopGridAvailable = runningProfiles.length >= 2;
  const desktopGridOverflow = Math.max(0, runningProfiles.length - desktopGridProfiles.length);

  useEffect(() => {
    if (fullViewMode === "grid" && !desktopGridAvailable) {
      setFullViewMode("single");
    }
  }, [desktopGridAvailable, fullViewMode]);


  const unavailable = caps != null && !caps.available;
  const browserUseMode = agent === "browser-use";
  const acpxMode = agent === "acpx";
  const unbrowseMode = agent === "unbrowse";
  const stagehandMode = agent === "stagehand";
  const antigravityMode = agent === "antigravity";
  const antigravitySupported = selectedProfile?.harness === "antigravity";
  const acpxBackedMode = acpxMode || antigravityMode;
  const selectedProviderAgent = acpAgentForProvider(providerRouting.providerId);
  const normalizedAcpProviderMode = acpxMode && selectedProviderAgent !== null;
  const selectedAcpxAgent: AcpxAgent = antigravityMode
    ? "grok-build"
    : normalizedAcpProviderMode
      ? selectedProviderAgent
      : acpxAgent;
  const selectedAcpxPreflight = acpxPreflights.find((item) => item.agent === selectedAcpxAgent);
  const readyAcpxAgent = ACPX_AGENT_OPTIONS.find((option) =>
    acpxPreflights.some((preflight) => preflight.agent === option.value && preflight.ready),
  )?.value;
  const managedRunMode = browserUseMode || acpxBackedMode || unbrowseMode || stagehandMode;
  const managedHarness: ManagedHarness = acpxBackedMode
    ? "acpx"
    : unbrowseMode
      ? "unbrowse"
      : stagehandMode
        ? "stagehand"
        : "browser-use";
  const selectedHarnessPresence = managedRunMode ? harnessPresence[managedHarness] ?? null : null;
  const selectedCliReady = !managedRunMode && caps?.available === true && caps.agents.includes(agent as OrcaAgentCli);
  const selectedHarnessReady = managedRunMode
    ? selectedHarnessPresence?.worker_seen_recently === true && (
      !acpxBackedMode || selectedAcpxPreflight?.ready === true
    )
    : selectedCliReady;
  const managedRunActive = Boolean(taskRun && ACTIVE_TASK_RUN_STATES.has(taskRun.status));
  const managedRunNeedsTakeover = Boolean(
    taskRun && (managedRunActive || taskRun.status === "failed" || taskRun.status === "revoked"),
  );
  const orcaSessionActive = session?.status === "running" || session?.status === "starting";
  const sessionActive = managedRunMode ? managedRunActive : orcaSessionActive;
  const originList = useMemo(() => allowedOrigins(prompt), [prompt]);
  const healthFailedReasons = useMemo(
    () => healthReasonList(taskRun?.health_decision?.failed_reasons),
    [taskRun?.health_decision],
  );
  const nonOverridableHealthReasons = useMemo(
    () => healthReasonList(taskRun?.health_decision?.non_overridable_reasons),
    [taskRun?.health_decision],
  );
  const hasModePermissions = managedRunMode
    ? canAutomate
    : canAutomate && canInteract;
  const normalizedProviderBlockReason = antigravityMode
    ? providerInfo(providerReadiness, "antigravity", "cli")?.reason_code
      || "Antigravity CLI execution adapter is not available yet."
    : acpxMode
      ? !normalizedAcpProviderMode
        ? "Only normalized ACP provider launches are executable from this provider control today."
        : providerLaunchBlockReason(providerRouting, providerReadiness)
    : null;
  const canStart =
    Boolean(selectedProfile) &&
    selectedProfile?.status === "running" &&
    hasModePermissions &&
    (managedRunMode ? Boolean(prompt.trim()) && originList.length > 0 : !unavailable) &&
    selectedHarnessReady &&
    !normalizedProviderBlockReason &&
    !sessionActive &&
    !busy;
  const canSend = Boolean(!managedRunMode && sessionActive && canInteract && prompt.trim() && !busy);
  const submitStartsSession = managedRunMode || !sessionActive;
  const canStop = Boolean(
    (managedRunMode ? canAutomate : canInteract) &&
    !busy &&
    (managedRunMode
      ? taskRun && ACTIVE_TASK_RUN_STATES.has(taskRun.status)
      : session && session.status !== "closed"),
  );

  useEffect(() => {
    onRunActivityChange?.(sessionActive);
  }, [onRunActivityChange, sessionActive]);

  useEffect(() => {
    return () => onRunActivityChange?.(false);
  }, [onRunActivityChange]);
  useEffect(() => {
    const profileId = selectedProfile?.id ?? null;
    if (runtimeProfileIdRef.current === profileId || sessionActive) return;
    runtimeProfileIdRef.current = profileId;
    setRuntimeConfig((current) => {
      const nextAgent = preferredAgent(selectedProfile);
      return {
        ...current,
        mode: nextAgent === "acpx" ? "acp" : nextAgent === "antigravity" ? "acpx" : "cli",
        agent: nextAgent,
      };
    });
  }, [selectedProfile, sessionActive, setRuntimeConfig]);

  const terminalMode = !managedRunMode;
  const compactWorkspaceMode = terminalMode
    ? "cli"
    : antigravityMode
      ? "acpx"
      : acpxMode
        ? runtimeMode
        : browserUseMode
          ? "browser"
          : "cli";
  const chooseCompactWorkspaceMode = (mode: "cli" | "acp" | "acpx") => {
    if (sessionActive) return;
    if (mode === "cli") {
      setRuntimeConfig((current) => ({ ...current, mode: "cli", agent: managedRunMode ? "agy" : current.agent }));
      return;
    }
    if (mode === "acp") {
      setRuntimeConfig((current) => ({
        ...current,
        mode: "acp",
        agent: "acpx",
        acpxAgent: acpAgentForProvider(current.providerRouting.providerId) ?? current.acpxAgent,
      }));
      return;
    }
    setRuntimeConfig((current) => ({
      ...current,
      mode: "acpx",
      agent: antigravitySupported ? "antigravity" : "acpx",
      acpxAgent: current.acpxAgent,
    }));
  };

  useEffect(() => {
    if (!acpxMode || sessionActive || selectedAcpxPreflight?.ready || !readyAcpxAgent) return;
    setAcpxAgent(readyAcpxAgent);
  }, [acpxMode, readyAcpxAgent, selectedAcpxPreflight?.ready, sessionActive]);

  useEffect(() => {
    let cancelled = false;
    api
      .getOrcaCapabilities()
      .then((next) => {
        if (!cancelled) setCaps(next);
      })
      .catch(() => {
        if (!cancelled) {
          setCaps({
            available: false,
            orca_bin: "",
            agents: [],
            operations: [],
            actions: {
              start: false,
              read: false,
              send: false,
              close: false,
              pause: false,
              resume: false,
            },
            notes: ["Failed to load Orca capabilities"],
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    api
      .getProviderReadiness({ signal: controller.signal })
      .then((next) => {
        if (cancelled) return;
        setProviderReadiness(next);
        setProviderRouting((current) => {
          const currentProvider = providerInfo(next, current.providerId, current.transport);
          const modelAlias = currentProvider?.model_aliases.includes(current.modelAlias)
            ? current.modelAlias
            : currentProvider?.model_aliases[0] ?? "";
          return { ...current, modelAlias };
        });
      })
      .catch((err) => {
        if (cancelled || (err instanceof DOMException && err.name === "AbortError")) return;
        setProviderReadiness({
          providers: [
            {
              provider: "codex",
              transport: "acp",
              ready: false,
              state: "unavailable",
              reason_code: "readiness_unavailable",
              checked_at: null,
              model_aliases: [],
            },
            {
              provider: "claude",
              transport: "acp",
              ready: false,
              state: "unavailable",
              reason_code: "readiness_unavailable",
              checked_at: null,
              model_aliases: [],
            },
            {
              provider: "cursor",
              transport: "acp",
              ready: false,
              state: "unavailable",
              reason_code: "readiness_unavailable",
              checked_at: null,
              model_aliases: [],
            },
            {
              provider: "grok",
              transport: "acp",
              ready: false,
              state: "unavailable",
              reason_code: "readiness_unavailable",
              checked_at: null,
              model_aliases: [],
            },
            {
              provider: "opencode",
              transport: "acp",
              ready: false,
              state: "unavailable",
              reason_code: "readiness_unavailable",
              checked_at: null,
              model_aliases: [],
            },
            {
              provider: "antigravity",
              transport: "cli",
              ready: false,
              state: "unavailable",
              reason_code: "readiness_unavailable",
              checked_at: null,
              model_aliases: [],
            },
          ],
        });
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  const refreshSelectedHarness = useCallback(async (signal?: AbortSignal) => {
    if (!managedRunMode) {
      setCaps(await api.getOrcaCapabilities());
      return;
    }
    const presence = await api.getTaskHarnessPresence(managedHarness, { signal });
    setHarnessPresence((current) => ({ ...current, [managedHarness]: presence }));
    if (acpxBackedMode) {
      const preflights = await api.getTaskHarnessPreflights("acpx", { signal });
      setAcpxPreflights(preflights.agents);
    }
  }, [acpxBackedMode, managedHarness, managedRunMode]);

  useEffect(() => {
    if (!managedRunMode) return;
    const controller = new AbortController();
    let cancelled = false;
    const refresh = async () => {
      try {
        await refreshSelectedHarness(controller.signal);
      } catch (err) {
        if (cancelled || (err instanceof DOMException && err.name === "AbortError")) return;
        setHarnessPresence((current) => ({ ...current, [managedHarness]: {
          harness: managedHarness,
          worker_seen_recently: false,
          state: "unavailable",
          last_seen_at: null,
          reason: `${managedHarnessLabel(managedHarness)} readiness could not be verified`,
        } }));
        if (acpxBackedMode) setAcpxPreflights([]);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15_000);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [acpxBackedMode, managedHarness, managedRunMode, refreshSelectedHarness]);

  const stopPolling = useCallback(() => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const stopRunPolling = useCallback(() => {
    if (runPollRef.current != null) {
      window.clearInterval(runPollRef.current);
      runPollRef.current = null;
    }
  }, []);

  const pollOutput = useCallback(
    async (sessionId: string, nextCursor: number) => {
      try {
        const chunk = await api.readOrcaSessionOutput(sessionId, { cursor: nextCursor });
        if (chunk.output) {
          setTranscript((prev) => (prev ? `${prev}\n${chunk.output}` : chunk.output));
        }
        setCursor(chunk.next_cursor);
        setSession((prev) =>
          prev
            ? {
                ...prev,
                status: chunk.status,
                capabilities: chunk.capabilities,
              }
            : prev,
        );
        if (chunk.status === "closed" || chunk.status === "error") {
          stopPolling();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to read Orca output");
      }
    },
    [stopPolling],
  );

  useEffect(() => {
    stopPolling();
    if (!session || session.status === "closed" || session.status === "error") return;
    pollRef.current = window.setInterval(() => {
      void pollOutput(session.id, cursor);
    }, 1500);
    return stopPolling;
  }, [session?.id, session?.status, cursor, pollOutput, stopPolling]);

  const refreshBrowserUseRun = useCallback(
    async (runId: string) => {
      try {
        const [nextRun, nextOutputs] = await Promise.all([
          api.getTaskRun(runId),
          api.listTaskRunOutputs(runId),
        ]);
        setTaskRun(nextRun);
        setTaskOutputs(nextOutputs);
        if (!ACTIVE_TASK_RUN_STATES.has(nextRun.status)) stopRunPolling();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to read managed browser run");
      }
    },
    [stopRunPolling],
  );

  useEffect(() => {
    stopRunPolling();
    if (!managedRunMode || !taskRun || !ACTIVE_TASK_RUN_STATES.has(taskRun.status)) return;
    runPollRef.current = window.setInterval(() => {
      void refreshBrowserUseRun(taskRun.id);
    }, 1500);
    return stopRunPolling;
  }, [managedRunMode, refreshBrowserUseRun, stopRunPolling, taskRun?.id, taskRun?.status]);

  useEffect(() => {
    const node = transcriptEndRef.current;
    if (node && typeof node.scrollIntoView === "function") {
      node.scrollIntoView({ block: "end" });
    }
  }, [transcript]);

  useEffect(() => {
    if (!initialPromptDraft || !selectedProfile) return;
    if (initialPromptDraft.profileId !== selectedProfile.id) return;
    if (appliedInitialPromptDraftIdRef.current === initialPromptDraft.id) return;

    appliedInitialPromptDraftIdRef.current = initialPromptDraft.id;
    setPrompt((current) => (current ? current : initialPromptDraft.task));
    onInitialPromptDraftApplied?.(initialPromptDraft.id);
  }, [initialPromptDraft, onInitialPromptDraftApplied, selectedProfile?.id]);

  useEffect(() => {
    // Switching profiles stops the local session view; operator must relaunch.
    let cancelled = false;
    stopPolling();
    stopRunPolling();
    const nextAgent = preferredAgent(selectedProfile);
    setRuntimeConfig((current) => ({
      ...current,
      mode: nextAgent === "acpx" ? "acp" : nextAgent === "antigravity" ? "acpx" : "cli",
      agent: nextAgent,
    }));
    setSession(null);
    setTaskSessionId(null);
    setTaskRun(null);
    setTaskOutputs([]);
    setTranscript("");
    setCursor(0);
    setError(null);

    const profileId = selectedProfile?.id;
    const rememberedRunId = profileId ? readRememberedBrowserUseRun(profileId) : null;
    if (profileId && rememberedRunId) {
      void Promise.all([
        api.getTaskRun(rememberedRunId),
        api.listTaskRunOutputs(rememberedRunId),
      ])
        .then(([rememberedRun, rememberedOutputs]) => {
          if (cancelled) return;
          if (rememberedRun.profile_id_snapshot !== profileId) {
            forgetBrowserUseRun(profileId);
            const nextAgent = preferredAgent(selectedProfile);
    setRuntimeConfig((current) => ({
      ...current,
      mode: nextAgent === "acpx" ? "acp" : nextAgent === "antigravity" ? "acpx" : "cli",
      agent: nextAgent,
    }));
            return;
          }
          if (rememberedRun.harness === "acpx") {
            setAgent(selectedProfile?.harness === "antigravity" ? "antigravity" : "acpx");
            if (rememberedRun.agent) {
              setAcpxAgent(rememberedRun.agent);
            }
          } else if (
            rememberedRun.harness === "browser-use"
            || rememberedRun.harness === "unbrowse"
            || rememberedRun.harness === "stagehand"
          ) {
            setAgent(rememberedRun.harness);
          } else {
            setAgent("browser-use");
          }
          setTaskSessionId(rememberedRun.task_session_id);
          setTaskRun(rememberedRun);
          setTaskOutputs(rememberedOutputs);
        })
        .catch(() => {
          if (cancelled) return;
          forgetBrowserUseRun(profileId);
          const nextAgent = preferredAgent(selectedProfile);
    setRuntimeConfig((current) => ({
      ...current,
      mode: nextAgent === "acpx" ? "acp" : nextAgent === "antigravity" ? "acpx" : "cli",
      agent: nextAgent,
    }));
        });
    }

    return () => {
      cancelled = true;
    };
  }, [selectedProfile?.id, stopPolling, stopRunPolling]);

  useEffect(() => {
    setViewportWidth(selectedProfile?.screen_width ?? 1280);
    setViewportHeight(selectedProfile?.screen_height ?? 720);
  }, [selectedProfile?.id, selectedProfile?.screen_height, selectedProfile?.screen_width]);

  useEffect(() => {
    if (!viewerFullscreen) {
      setFullViewPanel(null);
      setFullViewMetricsOpen(false);
      if (restoreViewerFullscreenFocusRef.current) {
        viewerFullscreenButtonRef.current?.focus();
        restoreViewerFullscreenFocusRef.current = false;
      }
      return;
    }
    restoreViewerFullscreenFocusRef.current = true;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusableSelector = [
      "button:not([disabled])",
      "input:not([disabled])",
      "select:not([disabled])",
      "textarea:not([disabled])",
      "a[href]",
      '[tabindex]:not([tabindex="-1"])',
    ].join(",");
    const focusableNodes = () => {
      const pane = viewerPaneRef.current;
      if (!pane) return [];
      return Array.from(pane.querySelectorAll<HTMLElement>(focusableSelector)).filter(
        (node) => !node.hasAttribute("disabled") && node.getAttribute("aria-hidden") !== "true",
      );
    };
    const focusFirstControl = () => {
      const [first] = focusableNodes();
      first?.focus({ preventScroll: true });
    };
    const focusTimer = window.setTimeout(focusFirstControl, 0);
    const handleFullscreenKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setViewerFullscreen(false);
        return;
      }
      if (event.key !== "Tab") return;

      const nodes = focusableNodes();
      if (!nodes.length) {
        event.preventDefault();
        return;
      }
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (!first || !last) {
        event.preventDefault();
        return;
      }
      const active = document.activeElement;
      if (event.shiftKey) {
        if (active === first || !(active instanceof Node) || !viewerPaneRef.current?.contains(active)) {
          event.preventDefault();
          last.focus({ preventScroll: true });
        }
        return;
      }
      if (active === last || !(active instanceof Node) || !viewerPaneRef.current?.contains(active)) {
        event.preventDefault();
        first.focus({ preventScroll: true });
      }
    };
    const keepFocusInside = (event: FocusEvent) => {
      const pane = viewerPaneRef.current;
      if (!pane || !(event.target instanceof Node) || pane.contains(event.target)) return;
      focusFirstControl();
    };
    window.addEventListener("keydown", handleFullscreenKeyDown);
    document.addEventListener("focusin", keepFocusInside);
    return () => {
      window.clearTimeout(focusTimer);
      window.removeEventListener("keydown", handleFullscreenKeyDown);
      document.removeEventListener("focusin", keepFocusInside);
      document.body.style.overflow = previousOverflow;
    };
  }, [viewerFullscreen]);

  const handleStart = useCallback(async () => {
    if (!selectedProfile || !canStart) return;
    const profileAtStart = selectedProfile;
    const isStartProfileCurrent = () => selectedProfileIdRef.current === profileAtStart.id;
    setBusy(true);
    setError(null);
    try {
      if (managedRunMode) {
        const task = prompt.trim();
        const origins = allowedOrigins(task);
        if (!origins.length) {
          throw new Error("Managed browser tasks must include an explicit http(s) URL.");
        }
        const normalizedAcpRunAgent = normalizedAcpProviderMode ? selectedAcpxAgent : null;
        const exactProvider = normalizedAcpProviderMode
          ? providerInfo(providerReadiness, providerRouting.providerId, providerRouting.transport)
          : null;
        const selectedModelAlias = exactProvider?.model_aliases.includes(providerRouting.modelAlias)
          ? providerRouting.modelAlias
          : null;
        const normalizedAcpProvider = normalizedAcpRunAgent
          ? {
            id: providerRouting.providerId,
            transport: "acp" as const,
            ...(selectedModelAlias ? { model_alias: selectedModelAlias } : {}),
          }
          : null;
        const normalizedAcpRoutingPolicy = normalizedAcpRunAgent
          ? {
            mode: "ordered-fallback" as const,
            allow_second_browser: false,
            max_tool_attempts: 3,
          }
          : null;
        let sessionId = taskSessionId;
        if (!sessionId) {
          const created = await api.createTaskSession({
            profile_id: profileAtStart.id,
            title: task.slice(0, 120),
            metadata: {
              source: "agent-browser-workspace",
              harness: managedHarness,
              ...(normalizedAcpRunAgent ? { agent: normalizedAcpRunAgent, provider: normalizedAcpProvider, routing_policy: normalizedAcpRoutingPolicy } : {}),
              ...(!normalizedAcpRunAgent && acpxBackedMode ? { agent: selectedAcpxAgent } : {}),
              ...(antigravityMode ? { mode: "antigravity" } : {}),
            },
          });
          sessionId = created.id;
          if (isStartProfileCurrent()) {
            setTaskSessionId(created.id);
          }
        }
        const started = await api.createTaskRun(sessionId, {
          harness: managedHarness,
          agent: normalizedAcpRunAgent
            ? normalizedAcpRunAgent
            : acpxBackedMode
              ? selectedAcpxAgent
              : null,
          task,
          profile_id: profileAtStart.id,
          allowed_origins: origins,
          timeout_seconds: 360,
          model_alias: normalizedAcpRunAgent ? selectedModelAlias : null,
          ...(normalizedAcpRunAgent ? {
            provider: normalizedAcpProvider!,
            browser_tools: providerRouting.browserTools,
            routing_policy: normalizedAcpRoutingPolicy!,
          } : {}),
        });
        rememberBrowserUseRun(profileAtStart.id, started.id);
        if (!isStartProfileCurrent()) return;
        setTaskRun(started);
        const outputs = await api.listTaskRunOutputs(started.id);
        if (!isStartProfileCurrent()) return;
        setTaskOutputs(outputs);
        setPrompt("");
        return;
      }
      const started = await api.startOrcaSession({
        profile_id: selectedProfile.id,
        agent: agent as OrcaAgentCli,
        prompt: prompt.trim() || undefined,
      });
      setSession(started);
      setTranscript("");
      setCursor(0);
      setPrompt("");
      const first = await api.readOrcaSessionOutput(started.id, { cursor: 0 });
      if (first.output) setTranscript(first.output);
      setCursor(first.next_cursor);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start Orca session");
    } finally {
      setBusy(false);
    }
  }, [acpxBackedMode, agent, antigravityMode, browserUseMode, canStart, managedHarness, managedRunMode, normalizedAcpProviderMode, prompt, providerReadiness, providerRouting, selectedAcpxAgent, selectedProfile, taskSessionId]);

  const handleSend = useCallback(async () => {
    if (!session || !canSend) return;
    const text = prompt.trim();
    setBusy(true);
    setError(null);
    try {
      await api.sendOrcaSessionInput(session.id, { text, enter: true });
      setPrompt("");
      setTranscript((prev) => `${prev}${prev ? "\n" : ""}› ${text}`);
      await pollOutput(session.id, cursor);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send prompt");
    } finally {
      setBusy(false);
    }
  }, [canSend, cursor, pollOutput, prompt, session]);

  const handleStop = useCallback(async () => {
    if (!canStop) return;
    setBusy(true);
    setError(null);
    try {
      if (managedRunMode && taskRun) {
        const cancelled = await api.cancelTaskRun(taskRun.id);
        setTaskRun(cancelled);
        stopRunPolling();
        return;
      }
      if (!session) return;
      const closed = await api.closeOrcaSession(session.id);
      setSession(closed);
      stopPolling();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to stop Orca session");
    } finally {
      setBusy(false);
    }
  }, [canStop, managedRunMode, session, stopPolling, stopRunPolling, taskRun]);

  const handleTakeControl = useCallback(async () => {
    if (
      !managedRunNeedsTakeover ||
      !taskRun ||
      !canInteract ||
      (managedRunActive && !canAutomate) ||
      busy
    ) return;
    setBusy(true);
    setError(null);
    try {
      if (managedRunActive) {
        const cancelled = await api.cancelTaskRun(taskRun.id);
        setTaskRun(cancelled);
        stopRunPolling();
      }
      setViewerFullscreen(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to hand browser control to the operator");
    } finally {
      setBusy(false);
    }
  }, [busy, canAutomate, canInteract, managedRunActive, managedRunNeedsTakeover, stopRunPolling, taskRun]);

  const handleRetryRunHealth = useCallback(async () => {
    if (!taskRun || taskRun.status !== "blocked_health" || !canAutomate || busy) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.retryTaskRunHealth(taskRun.id);
      setTaskRun(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to refresh the profile health gate");
    } finally {
      setBusy(false);
    }
  }, [busy, canAutomate, taskRun]);

  const handleOverrideRunHealth = useCallback(async () => {
    if (
      !taskRun ||
      taskRun.status !== "blocked_health" ||
      !canAutomate ||
      busy ||
      nonOverridableHealthReasons.length > 0
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await api.overrideTaskRunHealth(
        taskRun.id,
        "Operator approved this managed browser run after reviewing the displayed profile health gate.",
      );
      setTaskRun(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to override the profile health gate");
    } finally {
      setBusy(false);
    }
  }, [busy, canAutomate, nonOverridableHealthReasons.length, taskRun]);

  const applyViewportSize = useCallback(async (nextWidth: number, nextHeight: number) => {
    if (!canManageViewport || !onViewportApply || viewportApplying || sessionActive || busy) return;
    const width = Math.max(320, Math.min(7680, Math.round(nextWidth)));
    const height = Math.max(320, Math.min(4320, Math.round(nextHeight)));
    setViewportWidth(width);
    setViewportHeight(height);
    setViewportApplying(true);
    setError(null);
    try {
      const applied = await onViewportApply(width, height);
      if (!applied) {
        setError("The profile viewport could not be applied.");
        return;
      }
      setViewportControlsOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The profile viewport could not be applied");
    } finally {
      setViewportApplying(false);
    }
  }, [busy, canManageViewport, onViewportApply, sessionActive, viewportApplying]);

  const applyViewport = useCallback(async () => {
    await applyViewportSize(viewportWidth, viewportHeight);
  }, [applyViewportSize, viewportHeight, viewportWidth]);

  const applyCurrentPhoneFit = useCallback(async () => {
    const viewport = currentPhoneFitViewport(viewportWidth, viewportHeight);
    await applyViewportSize(viewport.width, viewport.height);
  }, [applyViewportSize, viewportHeight, viewportWidth]);

  const captureBrowserScreenshot = useCallback(async () => {
    if (!selectedProfile || selectedProfile.status !== "running" || screenshotBusy) return;
    setScreenshotBusy(true);
    setError(null);
    try {
      const screenshot = await api.captureProfileScreenshot(selectedProfile.id);
      const downloadUrl = URL.createObjectURL(screenshot);
      const link = document.createElement("a");
      link.href = downloadUrl;
      link.download = `cloakbrowser-${selectedProfile.id}.png`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(downloadUrl);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not capture browser screenshot");
    } finally {
      setScreenshotBusy(false);
    }
  }, [screenshotBusy, selectedProfile]);
  const activeRouteSummary = useMemo(() => {
    if (!managedRunMode) {
      const cliLabel = agent === "agy" ? "AGY" : agent === "grok" ? "Grok" : agent;
      return `${cliLabel} · Live terminal`;
    }
    const readiness = selectedHarnessPresence
      ? selectedHarnessReady
        ? "Ready"
        : "Unavailable"
      : "Checking";
    if (acpxBackedMode) {
      const modeLabel = antigravityMode ? "Antigravity" : "ACPX";
      return `${modeLabel} · ${acpxAgentLabel(selectedAcpxAgent)} · ${providerLabel(providerRouting.providerId)} · ${readiness}`;
    }
    return `${managedHarnessLabel(managedHarness)} · ${readiness}`;
  }, [
    acpxBackedMode,
    agent,
    antigravityMode,
    managedHarness,
    managedRunMode,
    providerRouting.providerId,
    selectedAcpxAgent,
    selectedHarnessPresence,
    selectedHarnessReady,
  ]);


  const fullViewButtonClass =
    "inline-flex min-h-11 min-w-11 items-center justify-center rounded border border-[#333] px-3 text-[11px] font-medium text-[#ddd] hover:bg-[#222] focus:outline-none focus:ring-2 focus:ring-accent/50";
  const fullViewPanelButtonClass =
    "inline-flex min-h-11 min-w-11 items-center justify-center rounded border border-[#333] px-3 text-[11px] text-[#ddd] hover:bg-[#222] focus:outline-none focus:ring-2 focus:ring-accent/50";

  return (
    <div
      className="agent-browser-workspace flex h-full min-h-0 w-full overflow-hidden bg-[#09090b] text-[#f4f4f5]"
      data-testid="agent-browser-workspace"
      data-ui-state={UI_STATE.agentWorkspace}
    >
      <section
        className={`flex shrink-0 flex-col border-r border-[#35353b] bg-[#0d0d0f] transition-[width] duration-150 ${
          chatCollapsed ? "w-10 min-w-10" : "w-[19rem] min-w-[18rem] max-w-[24vw]"
        }`}
        aria-label="Orca agent session"
        aria-hidden={viewerFullscreen || undefined}
        inert={viewerFullscreen || undefined}
        data-testid="agent-session-pane"
        data-collapsed={chatCollapsed ? "true" : "false"}
        data-ui-state={UI_STATE.agentSessionPane}
      >
        <header className={`border-b border-[#35353b] bg-[#111113] ${chatCollapsed ? "px-1 py-1" : "px-2.5 py-1.5"}`}>
          <div className="flex min-w-0 items-center gap-2">
            {!chatCollapsed ? (
              <>
                <TerminalSquare className="h-3.5 w-3.5 shrink-0 text-[#c4c4cc]" />
                <div className="min-w-0 flex-1 truncate text-[11px] font-semibold tracking-tight">
                  {selectedProfile?.name ?? "Agent workspace"}
                </div>
                <span
                  className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${
                    sessionActive ? "bg-emerald-950 text-emerald-300" : "bg-[#29292e] text-[#c4c4cc]"
                  }`}
                  data-testid="orca-run-status"
                >
                  {managedRunMode ? taskRun?.status ?? "idle" : session?.status ?? "idle"}
                </span>
              </>
            ) : null}
            <button
              type="button"
              className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded border border-[#333] text-[#a1a1aa] hover:bg-[#202024] hover:text-white"
              onClick={() => setChatCollapsed((collapsed) => !collapsed)}
              aria-label={chatCollapsed ? "Expand chat panel" : "Collapse chat panel"}
              aria-expanded={!chatCollapsed}
              title={chatCollapsed ? "Expand chat" : "Collapse chat"}
            >
              {chatCollapsed ? <PanelLeftOpen className="h-3 w-3" /> : <PanelLeftClose className="h-3 w-3" />}
            </button>
          </div>
          {!chatCollapsed ? <><div
            className="mt-1.5 flex min-w-0 items-center gap-1"
            data-testid="workspace-run-bar"
          >
            <div
              className="grid w-[8.25rem] shrink-0 grid-cols-3 rounded-md border border-[#35353b] bg-[#0b0b0d] p-0.5"
              role="group"
              aria-label="CLI ACP ACPX mode"
              data-testid="workspace-compact-mode"
            >
              {([
                { mode: "cli", label: "CLI", title: "Run a live Orca CLI session" },
                { mode: "acp", label: "ACP", title: "Run through the selected ACP adapter" },
                {
                  mode: "acpx",
                  label: "ACPX",
                  title: antigravitySupported
                    ? "Antigravity CLI execution is unavailable until its adapter lands"
                    : "Run ACPX through the Grok Build adapter",
                },
              ] as const).map((option) => (
                <button
                  key={option.mode}
                  type="button"
                  aria-pressed={compactWorkspaceMode === option.mode}
                  className={`h-6 truncate rounded px-1 text-[9px] font-semibold transition-colors ${
                    compactWorkspaceMode === option.mode
                      ? "bg-[#2e2b5f] text-white"
                      : "text-[#a1a1aa] hover:bg-[#202024] hover:text-white"
                  }`}
                  onClick={() => chooseCompactWorkspaceMode(option.mode)}
                  disabled={sessionActive}
                  title={option.title}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <div
              className={`inline-flex h-7 min-w-0 flex-1 items-center rounded border px-1.5 text-[9px] font-medium ${
                selectedHarnessReady || terminalMode
                  ? "border-emerald-900/70 bg-emerald-950/60 text-emerald-300"
                  : "border-amber-900/60 bg-amber-950/50 text-amber-300"
              }`}
              data-testid="workspace-active-route"
              title={activeRouteSummary}
            >
              <span className="min-w-0 truncate">{activeRouteSummary}</span>
            </div>
            <button
              type="button"
              className={canStop
                ? "inline-flex h-7 w-7 items-center justify-center rounded border border-[#493434] bg-[#211515] text-red-300 hover:bg-[#3b1919]"
                : "sr-only"}
              onClick={() => void handleStop()}
              disabled={!canStop}
              data-testid="orca-stop"
              aria-label="Stop active run"
            >
              <Square className="h-3 w-3" />
            </button>
          </div>
          <details className="mt-1 text-[9px] text-[#a1a1aa]">
            <summary className="w-fit cursor-pointer select-none hover:text-white">Session details</summary>
            <div className="mt-1 truncate" data-testid="orca-connection-status">
              {managedRunMode
                ? taskRun
                  ? `Managed worker · ${taskRun.id}`
                  : selectedHarnessPresence?.worker_seen_recently
                    ? acpxBackedMode
                      ? selectedAcpxPreflight?.ready
                        ? `Managed run · ${selectedAcpxAgent} · ACP ready`
                        : `Managed run · ${selectedAcpxAgent} · ${selectedAcpxPreflight?.state ?? "checking"}`
                      : `Managed run · ${managedHarnessLabel(managedHarness)} · ready`
                    : `Managed run · ${managedHarnessLabel(managedHarness)} · ${selectedHarnessPresence?.state ?? "checking"}`
                : statusLabel(session, caps)}
              {!managedRunMode && session ? ` · ${session.terminal_handle}` : ""}
            </div>
          </details></> : null}
        </header>

        {!chatCollapsed ? <><div className="sr-only" aria-live="polite">
          <span data-testid="orca-cap-pause">{managedRunMode ? "outputs: typed" : "pause: unavailable"}</span>
          <span data-testid="orca-cap-resume">{managedRunMode ? (acpxBackedMode ? "session: ACP" : "worker: managed") : "resume: unavailable"}</span>
        </div>

        {error ? (
          <div className="border-b border-red-900/50 bg-red-950/40 px-3 py-1.5 text-[11px] text-red-300">
            {error}
          </div>
        ) : null}

        {managedRunMode && selectedHarnessPresence && !selectedHarnessPresence.worker_seen_recently ? (
          <div
            className="border-b border-amber-900/40 bg-amber-950/30 px-3 py-1.5 text-[11px] text-amber-200"
            data-testid={acpxBackedMode ? "acpx-unavailable" : "managed-harness-unavailable"}
          >
            {selectedHarnessPresence.reason || `${managedHarnessLabel(managedHarness)} worker unavailable`}
          </div>
        ) : null}

        {acpxBackedMode && selectedHarnessPresence?.worker_seen_recently && !selectedAcpxPreflight?.ready ? (
          <div
            className="border-b border-amber-900/40 bg-amber-950/30 px-3 py-1.5 text-[11px] text-amber-200"
            data-testid="acpx-agent-unavailable"
          >
            {acpxPreflightLabel(selectedAcpxPreflight)}
          </div>
        ) : null}

        {managedRunMode && normalizedProviderBlockReason ? (
          <div
            className="border-b border-amber-900/40 bg-amber-950/30 px-3 py-1.5 text-[11px] text-amber-200"
            data-testid="provider-launch-unavailable"
          >
            {normalizedProviderBlockReason}
          </div>
        ) : null}

        {managedRunMode && taskRun?.status === "blocked_health" ? (
          <div
            className="border-b border-amber-800/50 bg-amber-950/35 px-3 py-2 text-[11px] text-amber-100"
            data-testid="browser-use-health-gate"
          >
            <div className="font-semibold">Automation blocked by profile health</div>
            <div className="mt-1 flex flex-wrap gap-1 text-[10px] text-amber-200/90">
              {(healthFailedReasons.length ? healthFailedReasons : ["health_gate_blocked"]).map(
                (reason) => (
                  <span key={reason} className="rounded bg-amber-900/45 px-1.5 py-0.5">
                    {healthReasonLabel(reason)}
                  </span>
                ),
              )}
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <button
                type="button"
                className="btn btn-secondary h-7 px-2 text-[10px]"
                onClick={() => void handleRetryRunHealth()}
                disabled={busy || !canAutomate}
              >
                Refresh gate
              </button>
              {nonOverridableHealthReasons.length === 0 ? (
                <button
                  type="button"
                  className="btn btn-primary h-7 px-2 text-[10px]"
                  onClick={() => void handleOverrideRunHealth()}
                  disabled={busy || !canAutomate}
                >
                  Run with override
                </button>
              ) : (
                <span className="self-center text-[10px] text-amber-300">
                  This blocker must be fixed before automation can run.
                </span>
              )}
            </div>
          </div>
        ) : null}

        {!managedRunMode && unavailable ? (
          <div
            className="border-b border-amber-900/40 bg-amber-950/30 px-3 py-1.5 text-[11px] text-amber-200"
            data-testid="orca-unavailable"
          >
            {compactOrcaUnavailableMessage(caps)}
          </div>
        ) : null}

        {managedRunMode ? (
          <div
            className="min-h-0 flex-1 overflow-auto bg-[#0a0a0c] px-2 py-1.5"
            data-testid={browserUseMode ? "browser-use-output" : "managed-agent-output"}
            data-ui-state={UI_STATE.agentManagedOutput}
          >
            {taskOutputs.length ? (
              <AgentOutputTimeline outputs={taskOutputs} />
            ) : (
              <p className="text-[11px] text-[#a1a1aa]">
                Add an explicit URL, then run {antigravityMode
                  ? "Antigravity CLI"
                  : acpxMode
                    ? `ACPX with ${selectedAcpxAgent}`
                    : managedHarnessLabel(managedHarness)}. Actions, screenshots, data and the
                final summary appear here as typed cards.
              </p>
            )}
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col bg-[#08080a]">
            <div className="flex items-center justify-between gap-2 border-b border-[#24242a] bg-[#101014] px-2.5 py-1 text-[9px] text-[#b7b7c2]">
              <span className="min-w-0 leading-tight" data-testid="orca-cli-context">
                {agent === "grok"
                  ? "Grok CLI · sign in inside this terminal if prompted · profile context auto-injected"
                  : agent === "agy"
                    ? "AGY CLI · profile context and CloakBrowser control skill auto-injected"
                    : `${agent} · profile context auto-injected`}
              </span>
              <span className="shrink-0 text-emerald-400">VCVM PTY</span>
            </div>
            <pre
              className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words px-2.5 py-2 font-mono text-[11px] leading-relaxed text-[#f0f0f3]"
              data-testid="orca-transcript"
              data-ui-state={UI_STATE.agentOrcaTranscript}
              aria-label="CLI transcript"
            >
              {transcript || "Launch AGY or Grok to mirror the real Orca terminal here."}
              <span ref={transcriptEndRef} aria-hidden="true" />
            </pre>
          </div>
        )}

        <form
          className="flex items-end gap-1.5 border-t border-[#35353b] bg-[#111113] px-2.5 py-1.5"
          onSubmit={(event) => {
            event.preventDefault();
            void (submitStartsSession ? handleStart() : handleSend());
          }}
        >
          <label className="sr-only" htmlFor="orca-prompt">
            Prompt
          </label>
          <textarea
            id="orca-prompt"
            className="input min-h-8 flex-1 resize-none border-[#3c3c43] bg-[#18181b] py-1.5 text-[11px] text-white placeholder:text-[#71717a]"
            rows={1}
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }}
            placeholder={
              managedRunMode
                ? "Describe the task and include an explicit https:// URL…"
                : sessionActive
                ? "Send follow-up to the live Orca CLI…"
                : "Initial prompt (optional) · uses CloakBrowser control skill"
            }
            disabled={(!managedRunMode && unavailable) || (sessionActive && managedRunMode)}
            data-testid="orca-prompt"
          />
          <button
            type="submit"
            className="btn btn-primary inline-flex h-8 items-center gap-1 px-2 text-[10px]"
            disabled={submitStartsSession ? !canStart : !canSend}
            data-testid="orca-launch"
          >
            <SendHorizontal className="h-3.5 w-3.5" />
            {managedRunMode ? "Run" : submitStartsSession ? "Launch" : "Send"}
          </button>
        </form></> : null}
      </section>

      <section
        ref={viewerPaneRef}
        className={`flex ${
          viewerFullscreen ? "fixed inset-0 z-[80]" : "min-w-0 flex-1"
        } flex-col bg-[#090909]`}
        data-full-view-fit={viewerFullscreen ? fullViewFitMode : undefined}
        aria-label="Live CloakBrowser profile"
        role={viewerFullscreen ? "dialog" : undefined}
        aria-modal={viewerFullscreen || undefined}
        data-testid="agent-browser-viewer-pane"
        data-ui-state={uiStateAttr(
          UI_STATE.agentViewerPane,
          viewerFullscreen && UI_STATE.agentViewerFullscreen,
          viewerFullscreen && fullViewMode === "grid" && UI_STATE.agentViewerGrid,
        )}
      >
        <header className="relative flex min-h-10 flex-wrap items-center gap-2 border-b border-[#2a2a2a] bg-[#141414] px-3 py-1.5">
          <MonitorSmartphone className="h-3.5 w-3.5 text-[#8b8b8b]" />
          <div className="min-w-0 flex-1 truncate text-[12px] font-semibold">
            {viewerFullscreen && fullViewMode === "grid"
              ? "Live browser grid"
              : selectedProfile
                ? selectedProfile.name
                : "No profile selected"}
          </div>
          <span className="text-[10px] uppercase tracking-wide text-[#8b8b8b]">
            {viewerFullscreen && fullViewMode === "grid"
              ? `${desktopGridProfiles.length}/${runningProfiles.length} live`
              : selectedProfile?.status ?? "none"}
          </span>
          {managedRunNeedsTakeover && canInteract ? (
            <button
              type="button"
              className="inline-flex h-7 items-center rounded border border-emerald-700/80 bg-emerald-950/70 px-2 text-[10px] font-semibold text-emerald-100 hover:bg-emerald-900/70 disabled:opacity-40"
              onClick={() => void handleTakeControl()}
              disabled={busy || (managedRunActive && !canAutomate)}
              aria-label={managedRunActive ? "Take over browser" : "Open browser"}
              title={managedRunActive
                ? "Stop the agent and continue directly in this browser"
                : "Open the browser for direct control after the managed run stopped"}
            >
              {managedRunActive ? "Take over" : "Open browser"}
            </button>
          ) : null}
          {viewerFullscreen ? (
            <div
              className="flex items-center gap-1 text-[10px]"
              data-testid="desktop-full-view-toolbar"
              aria-label="Desktop full-view controls"
            >
              <button
                type="button"
                className={fullViewButtonClass}
                onClick={() => setFullViewPanel((panel) => (panel === "view" ? null : "view"))}
                aria-label="Open desktop full-view View controls"
                aria-expanded={fullViewPanel === "view"}
                aria-controls="desktop-full-view-view-panel"
                data-testid="desktop-full-view-group"
              >
                View
              </button>
              <button
                type="button"
                className={fullViewButtonClass}
                onClick={() => setFullViewPanel((panel) => (panel === "viewport" ? null : "viewport"))}
                aria-label="Open desktop full-view Viewport controls"
                aria-expanded={fullViewPanel === "viewport"}
                aria-controls="desktop-full-view-viewport-panel"
                data-testid="desktop-full-view-group"
              >
                Viewport
              </button>
              <button
                type="button"
                className={fullViewButtonClass}
                onClick={() => setFullViewPanel((panel) => (panel === "sessions" ? null : "sessions"))}
                aria-label="Open desktop full-view Sessions controls"
                aria-expanded={fullViewPanel === "sessions"}
                aria-controls="desktop-full-view-sessions-panel"
                data-testid="desktop-full-view-group"
              >
                Sessions
              </button>
              <button
                ref={viewerFullscreenButtonRef}
                type="button"
                className={fullViewButtonClass}
                onClick={() => setViewerFullscreen(false)}
                aria-label="Exit full view"
                data-testid="desktop-full-view-group"
              >
                Exit
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-1 text-[10px]">
              <button
                type="button"
                className="min-h-8 rounded border border-[#333] px-2 text-[#bbb] hover:bg-[#222]"
                onClick={() => setViewerZoom(100)}
                aria-label="Fit browser view"
              >
                Fit
              </button>
              <button
                type="button"
                className="min-h-8 min-w-8 rounded border border-[#333] text-[#bbb] hover:bg-[#222]"
                onClick={() => setViewerZoom((current) => Math.max(75, current - 10))}
                aria-label="Decrease browser zoom"
              >
                −
              </button>
              <span className="min-w-9 text-center text-[#999]">{viewerZoom}%</span>
              <button
                type="button"
                className="min-h-8 min-w-8 rounded border border-[#333] text-[#bbb] hover:bg-[#222]"
                onClick={() => setViewerZoom((current) => Math.min(150, current + 10))}
                aria-label="Increase browser zoom"
              >
                +
              </button>
              {canManageViewport && onViewportApply ? (
                <button
                  type="button"
                  className="min-h-8 rounded border border-[#333] px-2 text-[#bbb] hover:bg-[#222]"
                  onClick={() => setViewportControlsOpen((open) => !open)}
                  aria-label={viewportControlsOpen ? "Close viewport controls" : "Open viewport controls"}
                  aria-expanded={viewportControlsOpen}
                >
                  Viewport
                </button>
              ) : null}
              <button
                ref={viewerFullscreenButtonRef}
                type="button"
                className="min-h-8 rounded border border-[#333] px-2 text-[#bbb] hover:bg-[#222]"
                onClick={() => setViewerFullscreen((open) => !open)}
                aria-label="Enter full view"
                aria-pressed={viewerFullscreen}
              >
                Full view
              </button>
            </div>
          )}
          {!viewerFullscreen && viewportControlsOpen && canManageViewport && onViewportApply ? (
            <div className="absolute right-3 top-[2.85rem] z-20 w-64 rounded-md border border-[#333] bg-[#171717] p-2 text-[10px] text-[#bbb]">
              <div className="grid grid-cols-2 gap-2">
                <label className="space-y-1">
                  <span className="block text-[#888]">Width</span>
                  <input
                    type="number"
                    min={320}
                    max={7680}
                    value={viewportWidth}
                    onChange={(event) => setViewportWidth(Number(event.target.value))}
                    className="input h-8 w-full bg-[#0f0f0f] px-2 text-[11px]"
                    aria-label="Viewport width"
                  />
                </label>
                <label className="space-y-1">
                  <span className="block text-[#888]">Height</span>
                  <input
                    type="number"
                    min={320}
                    max={4320}
                    value={viewportHeight}
                    onChange={(event) => setViewportHeight(Number(event.target.value))}
                    className="input h-8 w-full bg-[#0f0f0f] px-2 text-[11px]"
                    aria-label="Viewport height"
                  />
                </label>
              </div>
              <div className="mt-2 flex items-center justify-between gap-2">
                <button
                  type="button"
                  className="min-h-8 rounded border border-[#333] px-2 hover:bg-[#222]"
                  onClick={() => {
                    setViewportWidth(390);
                    setViewportHeight(844);
                  }}
                  aria-label="Use phone viewport 390 by 844"
                >
                  Phone 390×844
                </button>
                <button
                  type="button"
                  className="min-h-8 rounded bg-[#4f46e5] px-2 font-medium text-white disabled:opacity-50"
                  onClick={() => void applyViewport()}
                  disabled={viewportApplying || sessionActive || busy}
                  aria-label="Apply viewport"
                  title={sessionActive ? "Stop the active agent run before changing the viewport" : undefined}
                >
                  {viewportApplying ? "Applying…" : "Apply"}
                </button>
              </div>
            </div>
          ) : null}
          {viewerFullscreen && fullViewPanel === "view" ? (
            <div
              id="desktop-full-view-view-panel"
              className="absolute right-3 top-[3.4rem] z-20 w-72 rounded-md border border-[#333] bg-[#171717] p-2 text-[10px] text-[#bbb]"
            >
              <div className="grid grid-cols-3 gap-1.5">
                <button
                  type="button"
                  className={fullViewPanelButtonClass}
                  onClick={() => setFullViewFitMode("fit")}
                  aria-label="Fit browser view"
                  aria-pressed={fullViewFitMode === "fit"}
                >
                  Fit
                </button>
                <button
                  type="button"
                  className={fullViewPanelButtonClass}
                  onClick={() => setFullViewFitMode("width")}
                  aria-label="Fit browser view to width"
                  aria-pressed={fullViewFitMode === "width"}
                >
                  Width
                </button>
                <button
                  type="button"
                  className={fullViewPanelButtonClass}
                  onClick={() => setFullViewFitMode("height")}
                  aria-label="Fit browser view to height"
                  aria-pressed={fullViewFitMode === "height"}
                >
                  Height
                </button>
              </div>
              <div className="mt-2 grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2">
                <button
                  type="button"
                  className={fullViewPanelButtonClass}
                  onClick={() => setViewerZoom((current) => Math.max(75, current - 10))}
                  aria-label="Decrease browser zoom"
                >
                  −
                </button>
                <output className="text-center text-[11px] text-[#ddd]" aria-label="Browser zoom">
                  {viewerZoom}%
                </output>
                <button
                  type="button"
                  className={fullViewPanelButtonClass}
                  onClick={() => setViewerZoom((current) => Math.min(150, current + 10))}
                  aria-label="Increase browser zoom"
                >
                  +
                </button>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-1.5">
                <button
                  type="button"
                  className={`${fullViewPanelButtonClass} gap-1.5`}
                  onClick={() => void captureBrowserScreenshot()}
                  disabled={!selectedProfile || selectedProfile.status !== "running" || screenshotBusy}
                  aria-label="Capture browser screenshot"
                >
                  <Camera className="h-3.5 w-3.5" aria-hidden="true" />
                  {screenshotBusy ? "Capturing…" : "Screenshot"}
                </button>
                <button
                  type="button"
                  className={`${fullViewPanelButtonClass} gap-1.5`}
                  onClick={() => setFullViewMetricsOpen((open) => !open)}
                  aria-label={fullViewMetricsOpen ? "Hide live metrics" : "Show live metrics"}
                  aria-pressed={fullViewMetricsOpen}
                >
                  <Activity className="h-3.5 w-3.5" aria-hidden="true" />
                  Metrics
                </button>
              </div>
            </div>
          ) : null}
          {viewerFullscreen && fullViewPanel === "viewport" ? (
            <div
              id="desktop-full-view-viewport-panel"
              className="absolute right-3 top-[3.4rem] z-20 w-72 rounded-md border border-[#333] bg-[#171717] p-2 text-[10px] text-[#bbb]"
            >
              {canManageViewport && onViewportApply ? (
                <>
                  <div className="grid grid-cols-2 gap-2">
                    <label className="space-y-1">
                      <span className="block text-[#888]">Width</span>
                      <input
                        type="number"
                        min={320}
                        max={7680}
                        value={viewportWidth}
                        onChange={(event) => setViewportWidth(Number(event.target.value))}
                        className="input h-11 w-full bg-[#0f0f0f] px-2 text-[11px]"
                        aria-label="Fullscreen viewport width"
                      />
                    </label>
                    <label className="space-y-1">
                      <span className="block text-[#888]">Height</span>
                      <input
                        type="number"
                        min={320}
                        max={4320}
                        value={viewportHeight}
                        onChange={(event) => setViewportHeight(Number(event.target.value))}
                        className="input h-11 w-full bg-[#0f0f0f] px-2 text-[11px]"
                        aria-label="Fullscreen viewport height"
                      />
                    </label>
                  </div>
                  <div className="mt-2 flex items-center justify-between gap-2">
                    <button
                      type="button"
                      className={fullViewPanelButtonClass}
                      onClick={() => void applyCurrentPhoneFit()}
                      disabled={viewportApplying || sessionActive || busy}
                      aria-label="Use current phone viewport fit"
                      title={sessionActive ? "Stop the active agent run before changing the viewport" : undefined}
                    >
                      Phone Fit
                    </button>
                    <button
                      type="button"
                      className="inline-flex min-h-11 min-w-11 items-center justify-center rounded bg-[#4f46e5] px-3 text-[11px] font-medium text-white hover:bg-[#5b55ee] focus:outline-none focus:ring-2 focus:ring-accent/50 disabled:opacity-50"
                      onClick={() => void applyViewport()}
                      disabled={viewportApplying || sessionActive || busy}
                      aria-label="Apply fullscreen viewport"
                      title={sessionActive ? "Stop the active agent run before changing the viewport" : undefined}
                    >
                      {viewportApplying ? "Applying…" : "Apply"}
                    </button>
                  </div>
                </>
              ) : (
                <p className="text-[11px] text-[#999]">Viewport changes require profile management access.</p>
              )}
            </div>
          ) : null}
          {viewerFullscreen && fullViewPanel === "sessions" ? (
            <div
              id="desktop-full-view-sessions-panel"
              className="absolute right-3 top-[3.4rem] z-20 w-72 rounded-md border border-[#333] bg-[#171717] p-2 text-[10px] text-[#bbb]"
            >
              <span className="block text-[#888]">View mode</span>
              <div className="mt-1 grid grid-cols-2 gap-1" role="group" aria-label="Full-view browser layout">
                <button
                  type="button"
                  className={fullViewPanelButtonClass}
                  onClick={() => setFullViewMode("single")}
                  aria-label="Show one browser"
                  aria-pressed={fullViewMode === "single"}
                >
                  Single
                </button>
                <button
                  type="button"
                  className={fullViewPanelButtonClass}
                  onClick={() => {
                    if (!desktopGridAvailable) return;
                    if (selectedProfile?.status !== "running" && runningProfiles[0]) {
                      onSelectProfile(runningProfiles[0].id);
                    }
                    setFullViewMode("grid");
                  }}
                  disabled={!desktopGridAvailable}
                  aria-label="Show browser grid"
                  aria-pressed={fullViewMode === "grid"}
                  title={desktopGridAvailable ? undefined : "Grid requires two running browsers"}
                >
                  Grid · {runningProfiles.length}
                </button>
              </div>
              {fullViewMode === "single" ? (
                <label className="mt-2 block space-y-1">
                  <span className="block text-[#888]">Profile</span>
                  <select
                    className="input h-11 w-full bg-[#0f0f0f] px-2 text-[11px]"
                    value={selectedProfile?.id ?? ""}
                    onChange={(event) => onSelectProfile(event.target.value)}
                    aria-label="Switch full-view browser session"
                  >
                    <option value="" disabled>
                      Select profile
                    </option>
                    {profiles.map((profile) => (
                      <option key={profile.id} value={profile.id}>
                        {profile.name}
                        {profile.status === "running" ? " · live" : ""}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <div className="mt-2 text-[10px] text-[#999]">
                  {desktopGridProfiles.length} live streams visible
                  {desktopGridOverflow > 0 ? ` · ${desktopGridOverflow} more available from Sessions` : ""}
                </div>
              )}
            </div>
          ) : null}
        </header>
        {viewerFullscreen && fullViewMetricsOpen && selectedProfile ? (
          <div className="absolute left-3 top-[3.4rem] z-10 max-w-[calc(100vw-1.5rem)] overflow-hidden rounded-md border border-[#333] bg-[#111113] shadow-xl">
            <LiveDevPanel
              profileId={selectedProfile.id}
              running={selectedProfile.status === "running"}
            />
          </div>
        ) : null}
        <div
          className="min-h-0 flex-1"
          data-ui-state={uiStateAttr(
            selectedProfile?.status === "running" && UI_STATE.profileViewer,
            viewerFullscreen && fullViewMode === "grid" && UI_STATE.agentViewerGrid,
          )}
        >
          {viewerFullscreen && fullViewMode === "grid" && desktopGridAvailable ? (
            <div
              className="grid h-full min-h-0 grid-cols-1 gap-1.5 overflow-auto bg-[#090909] p-1.5 xl:grid-cols-2 2xl:grid-cols-3"
              role="list"
              aria-label="Running browser grid"
              data-testid="desktop-browser-grid"
              data-ui-state={UI_STATE.agentViewerGrid}
            >
              {desktopGridProfiles.map((profile) => {
                const selected = profile.id === selectedProfile?.id;
                return (
                  <article
                    key={profile.id}
                    className={`flex min-h-[18rem] min-w-0 flex-col overflow-hidden rounded-md border bg-[#101012] ${
                      selected ? "border-[#6366f1]" : "border-[#2a2a2f]"
                    }`}
                    role="listitem"
                    data-testid="desktop-browser-grid-tile"
                    data-profile-id={profile.id}
                  >
                    <button
                      type="button"
                      className="flex min-h-11 w-full items-center gap-2 border-b border-[#2a2a2f] bg-[#151518] px-2 text-left text-[11px] text-[#d4d4d8] hover:bg-[#202024] focus:outline-none focus:ring-2 focus:ring-inset focus:ring-[#6366f1]"
                      onClick={() => onSelectProfile(profile.id)}
                      aria-label={`Select ${profile.name}`}
                      aria-pressed={selected}
                    >
                      <span className="h-2 w-2 shrink-0 rounded-full bg-emerald-400" aria-hidden="true" />
                      <span className="min-w-0 flex-1 truncate font-semibold">{profile.name}</span>
                      <span className="shrink-0 text-[9px] uppercase tracking-wide text-[#8b8b8b]">
                        {selected ? "Control" : "View only"}
                      </span>
                    </button>
                    <div className="min-h-0 flex-1" data-ui-state={UI_STATE.profileViewer}>
                      <ProfileViewer
                        profileId={profile.id}
                        cdpUrl={selected ? profile.cdp_url : null}
                        clipboardSync={selected && profile.clipboard_sync}
                        canInteract={selected && canInteract}
                        compactControls
                        viewportScale={viewerZoom / 100}
                        fitMode={fullViewFitMode}
                        layoutMode="fullscreen"
                        nativeFullscreenEnabled={false}
                        onConnectionStatusChange={selected ? onConnectionStatusChange : undefined}
                        onDisconnect={selected ? onViewerDisconnect ?? (() => undefined) : () => undefined}
                      />
                    </div>
                  </article>
                );
              })}
              {desktopGridOverflow > 0 ? (
                <div className="col-span-full flex min-h-11 items-center justify-center rounded-md border border-dashed border-[#333] px-3 text-[11px] text-[#8b8b8b]">
                  {desktopGridOverflow} more live browsers
                </div>
              ) : null}
            </div>
          ) : selectedProfile && selectedProfile.status === "running" ? (
            <ProfileViewer
              profileId={selectedProfile.id}
              cdpUrl={selectedProfile.cdp_url}
              clipboardSync={selectedProfile.clipboard_sync}
              canInteract={canInteract}
              viewportScale={viewerZoom / 100}
              fitMode={viewerFullscreen ? fullViewFitMode : "fit"}
              layoutMode={viewerFullscreen ? "fullscreen" : "inline"}
              nativeFullscreenEnabled={false}
              onConnectionStatusChange={onConnectionStatusChange}
              onDisconnect={onViewerDisconnect ?? (() => undefined)}
            />
          ) : (
            <div
              className="flex h-full items-center justify-center px-6 text-center text-[12px] text-[#8b8b8b]"
              data-testid="orca-viewer-empty"
            >
              {selectedProfile
                ? "Launch this CloakBrowser profile to show the live viewer."
                : "Select a profile to show the live CloakBrowser viewer."}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
