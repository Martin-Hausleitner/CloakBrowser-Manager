import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Camera,
  MonitorSmartphone,
  Play,
  RefreshCw,
  SendHorizontal,
  Settings2,
  Square,
  TerminalSquare,
} from "lucide-react";
import {
  api,
  type AcpxAgent,
  type OrcaAgentCli,
  type OrcaCapabilities,
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

type ManagedHarness = "browser-use" | "acpx" | "unbrowse" | "stagehand";
type AgentMode = ManagedHarness | "antigravity" | OrcaAgentCli;
type FullViewPanel = "view" | "viewport" | "sessions" | null;
type FullViewFitMode = "fit" | "width" | "height";
type FullViewMode = "single" | "grid";

const MAX_DESKTOP_GRID_STREAMS = 6;

const AGENT_OPTIONS: AgentMode[] = [
  "browser-use",
  "acpx",
  "unbrowse",
  "stagehand",
  "antigravity",
  "agy",
  "grok",
  "cursor-agent",
  "codex",
];
const ACPX_AGENT_OPTIONS: ReadonlyArray<{ value: AcpxAgent; label: string }> = [
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

const MANAGED_HARNESSES: readonly ManagedHarness[] = [
  "browser-use",
  "acpx",
  "unbrowse",
  "stagehand",
];

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
}: AgentBrowserWorkspaceProps) {
  const [agent, setAgent] = useState<AgentMode>(() => preferredAgent(selectedProfile));
  const [acpxAgent, setAcpxAgent] = useState<AcpxAgent>("grok-build");
  const [prompt, setPrompt] = useState("");
  const [caps, setCaps] = useState<OrcaCapabilities | null>(null);
  const [session, setSession] = useState<OrcaSession | null>(null);
  const [taskSessionId, setTaskSessionId] = useState<string | null>(null);
  const [taskRun, setTaskRun] = useState<TaskRun | null>(null);
  const [harnessPresence, setHarnessPresence] = useState<Partial<Record<ManagedHarness, TaskHarnessPresence>>>({});
  const [acpxPreflights, setAcpxPreflights] = useState<TaskHarnessAgentPreflight[]>([]);
  const [harnessCheckBusy, setHarnessCheckBusy] = useState(false);
  const [taskOutputs, setTaskOutputs] = useState<TaskOutput[]>([]);
  const [transcript, setTranscript] = useState("");
  const [cursor, setCursor] = useState(0);
  const [busy, setBusy] = useState(false);
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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const runPollRef = useRef<number | null>(null);
  const transcriptEndRef = useRef<HTMLSpanElement | null>(null);
  const appliedInitialPromptDraftIdRef = useRef<string | null>(null);
  const viewerPaneRef = useRef<HTMLElement | null>(null);
  const viewerFullscreenButtonRef = useRef<HTMLButtonElement | null>(null);
  const restoreViewerFullscreenFocusRef = useRef(false);

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
  const selectedAcpxAgent: AcpxAgent = antigravityMode ? "grok-build" : acpxAgent;
  const selectedAcpxPreflight = acpxPreflights.find((item) => item.agent === selectedAcpxAgent);
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
  const canStart =
    Boolean(selectedProfile) &&
    selectedProfile?.status === "running" &&
    hasModePermissions &&
    (managedRunMode ? Boolean(prompt.trim()) && originList.length > 0 : !unavailable) &&
    selectedHarnessReady &&
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
  const terminalMode = !managedRunMode;
  const compactWorkspaceMode = terminalMode
    ? "cli"
    : antigravityMode
      ? "acpx"
      : acpxMode
        ? "acp"
        : browserUseMode
          ? "browser"
          : "cli";
  const visibleAgentOptions = useMemo(
    () => antigravitySupported ? AGENT_OPTIONS : AGENT_OPTIONS.filter((option) => option !== "antigravity"),
    [antigravitySupported],
  );
  const chooseCompactWorkspaceMode = (mode: "cli" | "acp" | "acpx") => {
    if (sessionActive) return;
    if (mode === "cli") {
      setAgent((current) => managedRunMode ? "agy" : current);
      return;
    }
    if (mode === "acp") {
      setAgent("acpx");
      setAcpxAgent("grok-build");
      return;
    }
    if (antigravitySupported) {
      setAgent("antigravity");
      return;
    }
    setAgent("acpx");
    setAcpxAgent("grok-build");
  };

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
    } else {
      setAcpxPreflights([]);
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

  const handleHarnessCheck = useCallback(async () => {
    if (harnessCheckBusy) return;
    setHarnessCheckBusy(true);
    setError(null);
    try {
      if (!managedRunMode) {
        setCaps(await api.getOrcaCapabilities());
        return;
      }
      const presences = await Promise.all(
        MANAGED_HARNESSES.map((harness) => api.getTaskHarnessPresence(harness, {})),
      );
      setHarnessPresence(Object.fromEntries(
        presences.map((presence) => [presence.harness, presence]),
      ) as Partial<Record<ManagedHarness, TaskHarnessPresence>>);
      const preflights = await api.getTaskHarnessPreflights("acpx", {});
      setAcpxPreflights(preflights.agents);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Harness readiness check failed");
    } finally {
      setHarnessCheckBusy(false);
    }
  }, [harnessCheckBusy, managedRunMode]);

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
    setAgent(preferredAgent(selectedProfile));
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
            setAgent(preferredAgent(selectedProfile));
            return;
          }
          if (rememberedRun.harness === "acpx") {
            setAgent(selectedProfile?.harness === "antigravity" ? "antigravity" : "acpx");
            if (rememberedRun.agent) {
              setAcpxAgent(rememberedRun.agent);
            }
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
          setAgent(preferredAgent(selectedProfile));
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
    setBusy(true);
    setError(null);
    try {
      if (managedRunMode) {
        const task = prompt.trim();
        const origins = allowedOrigins(task);
        if (!origins.length) {
          throw new Error("Managed browser tasks must include an explicit http(s) URL.");
        }
        let sessionId = taskSessionId;
        if (!sessionId) {
          const created = await api.createTaskSession({
            profile_id: selectedProfile.id,
            title: task.slice(0, 120),
            metadata: {
              source: "agent-browser-workspace",
              harness: managedHarness,
              ...(acpxBackedMode ? { agent: selectedAcpxAgent } : {}),
              ...(antigravityMode ? { mode: "antigravity" } : {}),
            },
          });
          sessionId = created.id;
          setTaskSessionId(created.id);
        }
        const started = await api.createTaskRun(sessionId, {
          harness: managedHarness,
          agent: acpxBackedMode ? selectedAcpxAgent : null,
          task,
          profile_id: selectedProfile.id,
          allowed_origins: origins,
          timeout_seconds: 360,
          model_alias: browserUseMode ? "cursor-grok-4.5-low" : null,
        });
        rememberBrowserUseRun(selectedProfile.id, started.id);
        setTaskRun(started);
        setTaskOutputs(await api.listTaskRunOutputs(started.id));
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
  }, [acpxBackedMode, agent, antigravityMode, browserUseMode, canStart, managedHarness, managedRunMode, prompt, selectedAcpxAgent, selectedProfile, taskSessionId]);

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
        className="flex min-w-[22rem] w-[36%] max-w-[30rem] flex-col border-r border-[#35353b] bg-[#0d0d0f]"
        aria-label="Orca agent session"
        aria-hidden={viewerFullscreen || undefined}
        inert={viewerFullscreen || undefined}
        data-ui-state={UI_STATE.agentSessionPane}
      >
        <header className="border-b border-[#35353b] bg-[#111113] px-2.5 py-1.5">
          <div className="flex min-w-0 items-center gap-2">
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
            <button
              type="button"
              className="inline-flex h-7 w-7 items-center justify-center rounded border border-[#3c3c43] bg-[#18181b] text-[#d4d4d8] hover:border-[#60606b] hover:bg-[#232329]"
              onClick={() => setSettingsOpen((open) => !open)}
              aria-label={settingsOpen ? "Hide workspace settings" : "Show workspace settings"}
              aria-expanded={settingsOpen}
              data-testid="workspace-settings-toggle"
              title="Profiles and harness settings"
            >
              <Settings2 className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="mt-1.5 flex items-center gap-1">
            <div
              className="grid min-w-0 flex-1 grid-cols-3 rounded-md border border-[#35353b] bg-[#0b0b0d] p-0.5"
              role="group"
              aria-label="CLI ACP ACPX mode"
              data-testid="workspace-compact-mode"
            >
              {([
                { mode: "cli", label: "CLI", title: "Run a live Orca CLI session" },
                { mode: "acp", label: "ACP", title: "Run through the selected ACP adapter" },
                {
                  mode: "acpx",
                  label: antigravitySupported ? "ACPX · Grok" : "ACPX",
                  title: antigravitySupported
                    ? "Run Antigravity through the ACPX Grok Build preset"
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
            <div className="flex items-center gap-1">
              <span
                className={`max-w-[6.5rem] truncate rounded px-1.5 py-1 text-[9px] font-medium ${
                  selectedHarnessReady
                    ? "bg-emerald-950/70 text-emerald-300"
                    : "bg-amber-950/60 text-amber-300"
                }`}
                data-testid="harness-readiness"
                title={managedRunMode
                  ? selectedHarnessPresence?.reason || `${managedHarnessLabel(managedHarness)} readiness`
                  : `${agent} readiness`}
              >
                {managedRunMode ? managedHarnessLabel(managedHarness) : agent} · {harnessCheckBusy
                  ? "Checking"
                  : selectedHarnessReady
                    ? "Ready"
                    : "Unavailable"}
              </span>
              <button
                type="button"
                className="inline-flex h-7 w-7 items-center justify-center rounded border border-[#3c3c43] bg-[#18181b] text-[#d4d4d8] hover:border-[#60606b] hover:bg-[#232329] disabled:opacity-40"
                onClick={() => void handleHarnessCheck()}
                disabled={harnessCheckBusy || sessionActive}
                aria-label="Test selected harness"
                title="Refresh local harness readiness"
              >
                <RefreshCw className={`h-3 w-3 ${harnessCheckBusy ? "animate-spin" : ""}`} aria-hidden="true" />
              </button>
              <button
                type="button"
                className="btn btn-primary inline-flex h-7 items-center gap-1 px-2 text-[10px]"
                onClick={() => void handleStart()}
                disabled={!canStart}
                data-testid="orca-launch"
                title="Launch the selected mode"
              >
                <Play className="h-3 w-3" />
                Launch
              </button>
              <button
                type="button"
                className="inline-flex h-7 w-7 items-center justify-center rounded border border-[#493434] bg-[#211515] text-red-300 hover:bg-[#3b1919] disabled:opacity-40"
                onClick={() => void handleStop()}
                disabled={!canStop}
                data-testid="orca-stop"
                aria-label="Stop active run"
              >
                <Square className="h-3 w-3" />
              </button>
            </div>
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
          </details>
        </header>

        <div className="flex items-center gap-1.5 border-b border-[#35353b] bg-[#111113] px-2.5 py-1.5">
          <div
            className="flex min-w-0 flex-1 items-center gap-1.5"
            data-testid="workspace-settings"
            hidden={!settingsOpen}
          >
            <label className="sr-only" htmlFor="orca-profile">Profile</label>
            <select
              id="orca-profile"
              className="input h-7 max-w-[10rem] bg-[#18181b] py-0.5 text-[10px]"
              value={selectedProfile?.id ?? ""}
              onChange={(event) => onSelectProfile(event.target.value)}
              data-testid="orca-profile-select"
            >
              <option value="" disabled>Select profile</option>
              {profiles.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.name}{profile.status === "running" ? " · live" : ""}
                </option>
              ))}
            </select>

            <label className="sr-only" htmlFor="orca-agent">Harness</label>
            <select
              id="orca-agent"
              className="input h-7 max-w-[9rem] bg-[#18181b] py-0.5 text-[10px]"
              value={agent}
              onChange={(event) => setAgent(event.target.value as AgentMode)}
              disabled={sessionActive || (!managedRunMode && unavailable)}
              data-testid="orca-agent-select"
            >
              {visibleAgentOptions.map((option) => (
                <option key={option} value={option}>
                  {option === "browser-use" ? "Browser Use" : option === "acpx" ? "ACPX / ACP" : option === "unbrowse" ? "Unbrowse" : option === "stagehand" ? "Stagehand" : option === "antigravity" ? "Antigravity · ACPX/Grok" : option === "agy" ? "AGY · Live CLI" : option === "grok" ? "Grok · Live CLI" : option}
                </option>
              ))}
            </select>

            {acpxMode ? (
              <>
                <label className="sr-only" htmlFor="acpx-agent">ACP agent</label>
                <select
                  id="acpx-agent"
                  className="input h-7 max-w-[8rem] bg-[#18181b] py-0.5 text-[10px]"
                  value={acpxAgent}
                  onChange={(event) => setAcpxAgent(event.target.value as AcpxAgent)}
                  disabled={sessionActive}
                  data-testid="acpx-agent-select"
                  aria-label="ACP agent"
                >
                  {ACPX_AGENT_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </>
            ) : null}
          </div>
          {!settingsOpen ? (
            <span className="truncate text-[9px] text-[#a1a1aa]">
              {managedRunMode ? "Typed output" : `${agent === "agy" ? "AGY" : agent === "grok" ? "Grok" : agent} · Live terminal`} · {runningProfiles.length}/{profiles.length} live
            </span>
          ) : null}
        </div>

        <div className="sr-only" aria-live="polite">
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
                  ? "Antigravity · ACPX/Grok"
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
            data-testid="orca-send"
          >
            <SendHorizontal className="h-3.5 w-3.5" />
            {managedRunMode ? "Run" : submitStartsSession ? "Launch" : "Send"}
          </button>
        </form>
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
              key={selectedProfile.id}
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
