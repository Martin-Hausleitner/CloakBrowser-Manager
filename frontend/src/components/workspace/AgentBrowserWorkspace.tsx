import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Play, Square, SendHorizontal, TerminalSquare, MonitorSmartphone } from "lucide-react";
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
import { ProfileViewer } from "../ProfileViewer";
import { AgentOutputTimeline } from "./AgentOutputTimeline";

type AgentMode = "browser-use" | "acpx" | OrcaAgentCli;
type FullViewPanel = "view" | "viewport" | "sessions" | null;
type FullViewFitMode = "fit" | "width" | "height";

const AGENT_OPTIONS: AgentMode[] = ["browser-use", "acpx", "cursor-agent", "grok", "codex"];
const ACPX_AGENT_OPTIONS: ReadonlyArray<{ value: AcpxAgent; label: string }> = [
  { value: "codex", label: "Codex" },
  { value: "claude", label: "Claude" },
  { value: "cursor", label: "Cursor" },
  { value: "grok-build", label: "Grok Build" },
  { value: "opencode", label: "OpenCode" },
];
const ACTIVE_RUN_STATES = new Set(["queued", "health_check", "blocked_health", "running"]);
const BROWSER_USE_RUN_STORAGE_PREFIX = "cloakbrowser.browser-use.last-run:";

function browserUseRunStorageKey(profileId: string): string {
  return `${BROWSER_USE_RUN_STORAGE_PREFIX}${profileId}`;
}

function readRememberedBrowserUseRun(profileId: string): string | null {
  try {
    return window.sessionStorage.getItem(browserUseRunStorageKey(profileId));
  } catch {
    return null;
  }
}

function rememberBrowserUseRun(profileId: string, runId: string): void {
  try {
    window.sessionStorage.setItem(browserUseRunStorageKey(profileId), runId);
  } catch {
    // The run remains usable in memory when storage is disabled or full.
  }
}

function forgetBrowserUseRun(profileId: string): void {
  try {
    window.sessionStorage.removeItem(browserUseRunStorageKey(profileId));
  } catch {
    // Ignore unavailable storage; there is no local state left to recover.
  }
}

function preferredAgent(profile: Profile | null): AgentMode {
  if (profile?.harness === "browser-use") return "browser-use";
  if (profile?.harness === "acpx") return "acpx";
  return "cursor-agent";
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
  const [acpxAgent, setAcpxAgent] = useState<AcpxAgent>("cursor");
  const [prompt, setPrompt] = useState("");
  const [caps, setCaps] = useState<OrcaCapabilities | null>(null);
  const [session, setSession] = useState<OrcaSession | null>(null);
  const [taskSessionId, setTaskSessionId] = useState<string | null>(null);
  const [taskRun, setTaskRun] = useState<TaskRun | null>(null);
  const [acpxPresence, setAcpxPresence] = useState<TaskHarnessPresence | null>(null);
  const [acpxPreflights, setAcpxPreflights] = useState<TaskHarnessAgentPreflight[]>([]);
  const [taskOutputs, setTaskOutputs] = useState<TaskOutput[]>([]);
  const [transcript, setTranscript] = useState("");
  const [cursor, setCursor] = useState(0);
  const [busy, setBusy] = useState(false);
  const [viewerZoom, setViewerZoom] = useState(100);
  const [viewerFullscreen, setViewerFullscreen] = useState(false);
  const [fullViewPanel, setFullViewPanel] = useState<FullViewPanel>(null);
  const [fullViewFitMode, setFullViewFitMode] = useState<FullViewFitMode>("fit");
  const [viewportControlsOpen, setViewportControlsOpen] = useState(false);
  const [viewportWidth, setViewportWidth] = useState(selectedProfile?.screen_width ?? 1280);
  const [viewportHeight, setViewportHeight] = useState(selectedProfile?.screen_height ?? 720);
  const [viewportApplying, setViewportApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const runPollRef = useRef<number | null>(null);
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);
  const appliedInitialPromptDraftIdRef = useRef<string | null>(null);
  const viewerPaneRef = useRef<HTMLElement | null>(null);
  const viewerFullscreenButtonRef = useRef<HTMLButtonElement | null>(null);
  const restoreViewerFullscreenFocusRef = useRef(false);

  const runningProfiles = useMemo(
    () => profiles.filter((profile) => profile.status === "running"),
    [profiles],
  );

  const unavailable = caps != null && !caps.available;
  const browserUseMode = agent === "browser-use";
  const acpxMode = agent === "acpx";
  const selectedAcpxPreflight = acpxPreflights.find((item) => item.agent === acpxAgent);
  const managedRunMode = browserUseMode || acpxMode;
  const managedRunActive = Boolean(taskRun && ACTIVE_RUN_STATES.has(taskRun.status));
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
    (!acpxMode || (
      acpxPresence?.worker_seen_recently === true && selectedAcpxPreflight?.ready === true
    )) &&
    !sessionActive &&
    !busy;
  const canSend = Boolean(!managedRunMode && sessionActive && canInteract && prompt.trim() && !busy);
  const canStop = Boolean(
    (managedRunMode ? canAutomate : canInteract) &&
    !busy &&
    (managedRunMode
      ? taskRun && ACTIVE_RUN_STATES.has(taskRun.status)
      : session && session.status !== "closed"),
  );

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
    if (!acpxMode) {
      setAcpxPresence(null);
      setAcpxPreflights([]);
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    const refresh = async () => {
      try {
        const [presence, preflights] = await Promise.all([
          api.getTaskHarnessPresence("acpx", { signal: controller.signal }),
          api.getTaskHarnessPreflights("acpx", { signal: controller.signal }),
        ]);
        if (!cancelled) {
          setAcpxPresence(presence);
          setAcpxPreflights(preflights.agents);
        }
      } catch (err) {
        if (cancelled || (err instanceof DOMException && err.name === "AbortError")) return;
        setAcpxPresence({
          harness: "acpx",
          worker_seen_recently: false,
          state: "unavailable",
          last_seen_at: null,
          reason: "ACPX worker readiness could not be verified",
        });
        setAcpxPreflights([]);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15_000);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [acpxMode]);

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
        if (!ACTIVE_RUN_STATES.has(nextRun.status)) stopRunPolling();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to read managed browser run");
      }
    },
    [stopRunPolling],
  );

  useEffect(() => {
    stopRunPolling();
    if (!managedRunMode || !taskRun || !ACTIVE_RUN_STATES.has(taskRun.status)) return;
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
          setAgent(rememberedRun.harness === "acpx" ? "acpx" : "browser-use");
          if (rememberedRun.harness === "acpx" && rememberedRun.agent) {
            setAcpxAgent(rememberedRun.agent);
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
        const managedHarness = acpxMode ? "acpx" : "browser-use";
        let sessionId = taskSessionId;
        if (!sessionId) {
          const created = await api.createTaskSession({
            profile_id: selectedProfile.id,
            title: task.slice(0, 120),
            metadata: {
              source: "agent-browser-workspace",
              harness: managedHarness,
              ...(acpxMode ? { agent: acpxAgent } : {}),
            },
          });
          sessionId = created.id;
          setTaskSessionId(created.id);
        }
        const started = await api.createTaskRun(sessionId, {
          harness: managedHarness,
          agent: acpxMode ? acpxAgent : null,
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
  }, [acpxAgent, acpxMode, agent, browserUseMode, canStart, managedRunMode, prompt, selectedProfile, taskSessionId]);

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

  const fullViewButtonClass =
    "inline-flex min-h-11 min-w-11 items-center justify-center rounded border border-[#333] px-3 text-[11px] font-medium text-[#ddd] hover:bg-[#222] focus:outline-none focus:ring-2 focus:ring-accent/50";
  const fullViewPanelButtonClass =
    "inline-flex min-h-11 min-w-11 items-center justify-center rounded border border-[#333] px-3 text-[11px] text-[#ddd] hover:bg-[#222] focus:outline-none focus:ring-2 focus:ring-accent/50";

  return (
    <div
      className="agent-browser-workspace flex h-full min-h-0 w-full overflow-hidden bg-[#0d0d0d] text-[#e6e6e6]"
      data-testid="agent-browser-workspace"
      data-ui-state={UI_STATE.agentWorkspace}
    >
      <section
        className="flex min-w-0 w-[42%] max-w-[36rem] flex-col border-r border-[#2a2a2a]"
        aria-label="Orca agent session"
        aria-hidden={viewerFullscreen || undefined}
        inert={viewerFullscreen || undefined}
        data-ui-state={UI_STATE.agentSessionPane}
      >
        <header className="flex items-center gap-2 border-b border-[#2a2a2a] bg-[#141414] px-3 py-2">
          <TerminalSquare className="h-3.5 w-3.5 text-[#8b8b8b]" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-[12px] font-semibold tracking-tight">
              {browserUseMode ? "Browser Use" : acpxMode ? "ACPX / ACP" : "Orca CLI"}
            </div>
            <div className="truncate text-[10px] text-[#8b8b8b]" data-testid="orca-connection-status">
              {managedRunMode
                ? taskRun
                  ? `Managed worker · ${taskRun.id}`
                  : acpxMode
                    ? acpxPresence?.worker_seen_recently
                      ? selectedAcpxPreflight?.ready
                        ? `Managed run · ${acpxAgent} · ACP ready`
                        : `Managed run · ${acpxAgent} · ${selectedAcpxPreflight?.state ?? "checking"}`
                      : acpxPresence
                        ? `Managed run · ${acpxAgent} · ${acpxPresence.state}`
                        : `Managed run · ${acpxAgent} · checking worker`
                    : "Managed VCVM worker"
                : statusLabel(session, caps)}
              {!managedRunMode && session ? ` · ${session.terminal_handle}` : ""}
            </div>
          </div>
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] ${
              sessionActive ? "bg-[#1f3d2a] text-[#9ae6b4]" : "bg-[#2a2a2a] text-[#a0a0a0]"
            }`}
            data-testid="orca-run-status"
          >
            {managedRunMode ? taskRun?.status ?? "idle" : session?.status ?? "idle"}
          </span>
        </header>

        <div className="flex flex-wrap items-center gap-2 border-b border-[#2a2a2a] px-3 py-2">
          <label className="sr-only" htmlFor="orca-profile">
            Profile
          </label>
          <select
            id="orca-profile"
            className="input h-8 max-w-[12rem] bg-[#1a1a1a] py-1 text-[11px]"
            value={selectedProfile?.id ?? ""}
            onChange={(event) => onSelectProfile(event.target.value)}
            data-testid="orca-profile-select"
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

          <label className="sr-only" htmlFor="orca-agent">
            Harness
          </label>
          <select
            id="orca-agent"
            className="input h-8 max-w-[10rem] bg-[#1a1a1a] py-1 text-[11px]"
            value={agent}
            onChange={(event) => setAgent(event.target.value as AgentMode)}
            disabled={sessionActive || (!managedRunMode && unavailable)}
            data-testid="orca-agent-select"
          >
            {AGENT_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option === "browser-use" ? "Browser Use" : option === "acpx" ? "ACPX / ACP" : option}
              </option>
            ))}
          </select>

          {acpxMode ? (
            <>
              <label className="sr-only" htmlFor="acpx-agent">
                ACP agent
              </label>
              <select
                id="acpx-agent"
                className="input h-8 max-w-[9rem] bg-[#1a1a1a] py-1 text-[11px]"
                value={acpxAgent}
                onChange={(event) => setAcpxAgent(event.target.value as AcpxAgent)}
                disabled={sessionActive}
                data-testid="acpx-agent-select"
                aria-label="ACP agent"
              >
                {ACPX_AGENT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </>
          ) : null}

          <div className="ml-auto flex items-center gap-1.5">
            <button
              type="button"
              className="btn btn-primary inline-flex h-8 items-center gap-1 px-2 text-[11px]"
              onClick={() => void handleStart()}
              disabled={!canStart}
              data-testid="orca-launch"
              title={
                !managedRunMode && unavailable
                  ? "Orca runtime unavailable"
                  : !hasModePermissions
                    ? managedRunMode
                      ? "Requires automate"
                      : "Requires automate and interact"
                    : managedRunMode
                      ? `Run ${acpxMode ? `ACPX with ${acpxAgent}` : "Browser Use"} on this live profile`
                      : "Launch Orca agent session"
              }
            >
              <Play className="h-3 w-3" />
              Launch
            </button>
            <button
              type="button"
              className="btn btn-secondary inline-flex h-8 items-center gap-1 px-2 text-[11px]"
              onClick={() => void handleStop()}
              disabled={!canStop}
              data-testid="orca-stop"
            >
              <Square className="h-3 w-3" />
              Stop
            </button>
          </div>
        </div>

        <div className="flex flex-wrap gap-1.5 border-b border-[#2a2a2a] px-3 py-1.5 text-[10px] text-[#8b8b8b]">
          <span data-testid="orca-cap-pause">
            {managedRunMode ? "outputs: typed" : "pause: unavailable"}
          </span>
          <span>·</span>
          <span data-testid="orca-cap-resume">
            {managedRunMode ? (acpxMode ? "session: ACP" : "worker: managed") : "resume: unavailable"}
          </span>
          <span>·</span>
          <span>
            live profiles: {runningProfiles.length}/{profiles.length}
          </span>
        </div>

        {error ? (
          <div className="border-b border-red-900/50 bg-red-950/40 px-3 py-1.5 text-[11px] text-red-300">
            {error}
          </div>
        ) : null}

        {acpxMode && acpxPresence && !acpxPresence.worker_seen_recently ? (
          <div
            className="border-b border-amber-900/40 bg-amber-950/30 px-3 py-1.5 text-[11px] text-amber-200"
            data-testid="acpx-unavailable"
          >
            {acpxPresence.reason || "ACPX worker unavailable"}
          </div>
        ) : null}

        {acpxMode && acpxPresence?.worker_seen_recently && !selectedAcpxPreflight?.ready ? (
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
            {caps?.notes?.length
              ? caps.notes.join(" · ")
              : "Orca is unavailable. Launch/Stop stay disabled until readiness checks pass."}
          </div>
        ) : null}

        {managedRunMode ? (
          <div
            className="min-h-0 flex-1 overflow-auto bg-[#0a0a0a] px-3 py-2"
            data-testid={browserUseMode ? "browser-use-output" : "managed-agent-output"}
            data-ui-state={UI_STATE.agentManagedOutput}
          >
            {taskOutputs.length ? (
              <AgentOutputTimeline outputs={taskOutputs} />
            ) : (
              <p className="text-[11px] text-[#777]">
                Add an explicit URL, then run {acpxMode ? `ACPX with ${acpxAgent}` : "Browser Use"}. Actions, screenshots, data and the
                final summary appear here as typed cards.
              </p>
            )}
          </div>
        ) : (
          <pre
            className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words bg-[#0a0a0a] px-3 py-2 font-mono text-[11px] leading-relaxed text-[#d0d0d0]"
            data-testid="orca-transcript"
            data-ui-state={UI_STATE.agentOrcaTranscript}
            aria-label="CLI transcript"
          >
            {transcript || "No Orca output yet. Launch an allowlisted agent CLI to stream a real terminal."}
            <div ref={transcriptEndRef} />
          </pre>
        )}

        <form
          className="flex items-end gap-2 border-t border-[#2a2a2a] bg-[#141414] px-3 py-2"
          onSubmit={(event) => {
            event.preventDefault();
            void (managedRunMode && !sessionActive ? handleStart() : handleSend());
          }}
        >
          <label className="sr-only" htmlFor="orca-prompt">
            Prompt
          </label>
          <textarea
            id="orca-prompt"
            className="input min-h-[2.5rem] flex-1 resize-none bg-[#1a1a1a] py-2 text-[12px]"
            rows={2}
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
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
            className="btn btn-primary inline-flex h-9 items-center gap-1 px-2.5 text-[11px]"
            disabled={managedRunMode ? !canStart : !canSend}
            data-testid="orca-send"
          >
            <SendHorizontal className="h-3.5 w-3.5" />
            {managedRunMode ? "Run" : "Send"}
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
        )}
      >
        <header className="relative flex min-h-10 flex-wrap items-center gap-2 border-b border-[#2a2a2a] bg-[#141414] px-3 py-1.5">
          <MonitorSmartphone className="h-3.5 w-3.5 text-[#8b8b8b]" />
          <div className="min-w-0 flex-1 truncate text-[12px] font-semibold">
            {selectedProfile ? selectedProfile.name : "No profile selected"}
          </div>
          <span className="text-[10px] uppercase tracking-wide text-[#8b8b8b]">
            {selectedProfile?.status ?? "none"}
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
              <label className="space-y-1">
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
            </div>
          ) : null}
        </header>
        <div
          className="min-h-0 flex-1"
          data-ui-state={uiStateAttr(
            selectedProfile?.status === "running" && UI_STATE.profileViewer,
          )}
        >
          {selectedProfile && selectedProfile.status === "running" ? (
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
