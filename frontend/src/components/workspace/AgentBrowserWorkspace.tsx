import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Play, Square, SendHorizontal, TerminalSquare, MonitorSmartphone } from "lucide-react";
import {
  api,
  type OrcaAgentCli,
  type OrcaCapabilities,
  type OrcaSession,
  type Profile,
  type TaskOutput,
  type TaskRun,
} from "../../lib/api";
import { ProfileViewer } from "../ProfileViewer";
import { AgentOutputTimeline } from "./AgentOutputTimeline";

type AgentMode = "browser-use" | OrcaAgentCli;

const AGENT_OPTIONS: AgentMode[] = ["browser-use", "cursor-agent", "grok", "codex"];
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
  return profile?.harness === "browser-use" ? "browser-use" : "cursor-agent";
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

export interface AgentBrowserWorkspaceProps {
  profiles: Profile[];
  selectedProfile: Profile | null;
  canAutomate: boolean;
  canInteract: boolean;
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

export function AgentBrowserWorkspace({
  profiles,
  selectedProfile,
  canAutomate,
  canInteract,
  canManageViewport = false,
  onViewportApply,
  onSelectProfile,
  onConnectionStatusChange,
  onViewerDisconnect,
}: AgentBrowserWorkspaceProps) {
  const [agent, setAgent] = useState<AgentMode>(() => preferredAgent(selectedProfile));
  const [prompt, setPrompt] = useState("");
  const [caps, setCaps] = useState<OrcaCapabilities | null>(null);
  const [session, setSession] = useState<OrcaSession | null>(null);
  const [taskSessionId, setTaskSessionId] = useState<string | null>(null);
  const [taskRun, setTaskRun] = useState<TaskRun | null>(null);
  const [taskOutputs, setTaskOutputs] = useState<TaskOutput[]>([]);
  const [transcript, setTranscript] = useState("");
  const [cursor, setCursor] = useState(0);
  const [busy, setBusy] = useState(false);
  const [viewerZoom, setViewerZoom] = useState(100);
  const [viewerFullscreen, setViewerFullscreen] = useState(false);
  const [viewportControlsOpen, setViewportControlsOpen] = useState(false);
  const [viewportWidth, setViewportWidth] = useState(selectedProfile?.screen_width ?? 1280);
  const [viewportHeight, setViewportHeight] = useState(selectedProfile?.screen_height ?? 720);
  const [viewportApplying, setViewportApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const runPollRef = useRef<number | null>(null);
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);

  const runningProfiles = useMemo(
    () => profiles.filter((profile) => profile.status === "running"),
    [profiles],
  );

  const unavailable = caps != null && !caps.available;
  const browserUseMode = agent === "browser-use";
  const browserUseActive = Boolean(taskRun && ACTIVE_RUN_STATES.has(taskRun.status));
  const orcaSessionActive = session?.status === "running" || session?.status === "starting";
  const sessionActive = browserUseMode ? browserUseActive : orcaSessionActive;
  const originList = useMemo(() => allowedOrigins(prompt), [prompt]);
  const healthFailedReasons = useMemo(
    () => healthReasonList(taskRun?.health_decision?.failed_reasons),
    [taskRun?.health_decision],
  );
  const nonOverridableHealthReasons = useMemo(
    () => healthReasonList(taskRun?.health_decision?.non_overridable_reasons),
    [taskRun?.health_decision],
  );
  const hasModePermissions = browserUseMode
    ? canAutomate
    : canAutomate && canInteract;
  const canStart =
    Boolean(selectedProfile) &&
    selectedProfile?.status === "running" &&
    hasModePermissions &&
    (browserUseMode ? Boolean(prompt.trim()) && originList.length > 0 : !unavailable) &&
    !sessionActive &&
    !busy;
  const canSend = Boolean(!browserUseMode && sessionActive && canInteract && prompt.trim() && !busy);
  const canStop = Boolean(
    (browserUseMode ? canAutomate : canInteract) &&
    !busy &&
    (browserUseMode
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
        setError(err instanceof Error ? err.message : "Failed to read Browser Use run");
      }
    },
    [stopRunPolling],
  );

  useEffect(() => {
    stopRunPolling();
    if (!browserUseMode || !taskRun || !ACTIVE_RUN_STATES.has(taskRun.status)) return;
    runPollRef.current = window.setInterval(() => {
      void refreshBrowserUseRun(taskRun.id);
    }, 1500);
    return stopRunPolling;
  }, [browserUseMode, refreshBrowserUseRun, stopRunPolling, taskRun?.id, taskRun?.status]);

  useEffect(() => {
    const node = transcriptEndRef.current;
    if (node && typeof node.scrollIntoView === "function") {
      node.scrollIntoView({ block: "end" });
    }
  }, [transcript]);

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
      setAgent("browser-use");
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
    if (!viewerFullscreen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const exitOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setViewerFullscreen(false);
    };
    window.addEventListener("keydown", exitOnEscape);
    return () => {
      window.removeEventListener("keydown", exitOnEscape);
      document.body.style.overflow = previousOverflow;
    };
  }, [viewerFullscreen]);

  const handleStart = useCallback(async () => {
    if (!selectedProfile || !canStart) return;
    setBusy(true);
    setError(null);
    try {
      if (browserUseMode) {
        const task = prompt.trim();
        const origins = allowedOrigins(task);
        if (!origins.length) {
          throw new Error("Browser Use tasks must include an explicit http(s) URL.");
        }
        let sessionId = taskSessionId;
        if (!sessionId) {
          const created = await api.createTaskSession({
            profile_id: selectedProfile.id,
            title: task.slice(0, 120),
            metadata: { source: "agent-browser-workspace", harness: "browser-use" },
          });
          sessionId = created.id;
          setTaskSessionId(created.id);
        }
        const started = await api.createTaskRun(sessionId, {
          harness: "browser-use",
          task,
          profile_id: selectedProfile.id,
          allowed_origins: origins,
          timeout_seconds: 360,
          model_alias: "cursor-grok-4.5-low",
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
  }, [agent, browserUseMode, canStart, prompt, selectedProfile, taskSessionId]);

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
      if (browserUseMode && taskRun) {
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
  }, [browserUseMode, canStop, session, stopPolling, stopRunPolling, taskRun]);

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
        "Operator approved this Browser Use run after reviewing the displayed profile health gate.",
      );
      setTaskRun(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to override the profile health gate");
    } finally {
      setBusy(false);
    }
  }, [busy, canAutomate, nonOverridableHealthReasons.length, taskRun]);

  const applyViewport = useCallback(async () => {
    if (!canManageViewport || !onViewportApply || viewportApplying || sessionActive || busy) return;
    const width = Math.max(320, Math.min(7680, Math.round(viewportWidth)));
    const height = Math.max(320, Math.min(4320, Math.round(viewportHeight)));
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
  }, [busy, canManageViewport, onViewportApply, sessionActive, viewportApplying, viewportHeight, viewportWidth]);

  return (
    <div
      className="agent-browser-workspace flex h-full min-h-0 w-full overflow-hidden bg-[#0d0d0d] text-[#e6e6e6]"
      data-testid="agent-browser-workspace"
    >
      <section
        className="flex min-w-0 w-[42%] max-w-[36rem] flex-col border-r border-[#2a2a2a]"
        aria-label="Orca agent session"
      >
        <header className="flex items-center gap-2 border-b border-[#2a2a2a] bg-[#141414] px-3 py-2">
          <TerminalSquare className="h-3.5 w-3.5 text-[#8b8b8b]" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-[12px] font-semibold tracking-tight">
              {browserUseMode ? "Browser Use" : "Orca CLI"}
            </div>
            <div className="truncate text-[10px] text-[#8b8b8b]" data-testid="orca-connection-status">
              {browserUseMode
                ? taskRun
                  ? `Managed worker · ${taskRun.id}`
                  : "Managed VCVM worker"
                : statusLabel(session, caps)}
              {!browserUseMode && session ? ` · ${session.terminal_handle}` : ""}
            </div>
          </div>
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] ${
              sessionActive ? "bg-[#1f3d2a] text-[#9ae6b4]" : "bg-[#2a2a2a] text-[#a0a0a0]"
            }`}
            data-testid="orca-run-status"
          >
            {browserUseMode ? taskRun?.status ?? "idle" : session?.status ?? "idle"}
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
            disabled={sessionActive || (!browserUseMode && unavailable)}
            data-testid="orca-agent-select"
          >
            {AGENT_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option === "browser-use" ? "Browser Use" : option}
              </option>
            ))}
          </select>

          <div className="ml-auto flex items-center gap-1.5">
            <button
              type="button"
              className="btn btn-primary inline-flex h-8 items-center gap-1 px-2 text-[11px]"
              onClick={() => void handleStart()}
              disabled={!canStart}
              data-testid="orca-launch"
              title={
                !browserUseMode && unavailable
                  ? "Orca runtime unavailable"
                  : !hasModePermissions
                    ? browserUseMode
                      ? "Requires automate"
                      : "Requires automate and interact"
                    : browserUseMode
                      ? "Run Browser Use on this live profile"
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
            {browserUseMode ? "outputs: typed" : "pause: unavailable"}
          </span>
          <span>·</span>
          <span data-testid="orca-cap-resume">
            {browserUseMode ? "worker: managed" : "resume: unavailable"}
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

        {browserUseMode && taskRun?.status === "blocked_health" ? (
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

        {!browserUseMode && unavailable ? (
          <div
            className="border-b border-amber-900/40 bg-amber-950/30 px-3 py-1.5 text-[11px] text-amber-200"
            data-testid="orca-unavailable"
          >
            {caps?.notes?.length
              ? caps.notes.join(" · ")
              : "Orca is unavailable. Launch/Stop stay disabled until readiness checks pass."}
          </div>
        ) : null}

        {browserUseMode ? (
          <div
            className="min-h-0 flex-1 overflow-auto bg-[#0a0a0a] px-3 py-2"
            data-testid="browser-use-output"
          >
            {taskOutputs.length ? (
              <AgentOutputTimeline outputs={taskOutputs} />
            ) : (
              <p className="text-[11px] text-[#777]">
                Add an explicit URL, then run Browser Use. Actions, screenshots, data and the
                final summary appear here as typed cards.
              </p>
            )}
          </div>
        ) : (
          <pre
            className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words bg-[#0a0a0a] px-3 py-2 font-mono text-[11px] leading-relaxed text-[#d0d0d0]"
            data-testid="orca-transcript"
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
            void (browserUseMode && !sessionActive ? handleStart() : handleSend());
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
              browserUseMode
                ? "Describe the task and include an explicit https:// URL…"
                : sessionActive
                ? "Send follow-up to the live Orca CLI…"
                : "Initial prompt (optional) · uses CloakBrowser control skill"
            }
            disabled={(!browserUseMode && unavailable) || (sessionActive && browserUseMode)}
            data-testid="orca-prompt"
          />
          <button
            type="submit"
            className="btn btn-primary inline-flex h-9 items-center gap-1 px-2.5 text-[11px]"
            disabled={browserUseMode ? !canStart : !canSend}
            data-testid="orca-send"
          >
            <SendHorizontal className="h-3.5 w-3.5" />
            {browserUseMode ? "Run" : "Send"}
          </button>
        </form>
      </section>

      <section
        className={`flex ${
          viewerFullscreen ? "fixed inset-0 z-[80]" : "min-w-0 flex-1"
        } flex-col bg-[#090909]`}
        aria-label="Live CloakBrowser profile"
        data-testid="agent-browser-viewer-pane"
      >
        <header className="relative flex min-h-10 flex-wrap items-center gap-2 border-b border-[#2a2a2a] bg-[#141414] px-3 py-1.5">
          <MonitorSmartphone className="h-3.5 w-3.5 text-[#8b8b8b]" />
          <div className="min-w-0 flex-1 truncate text-[12px] font-semibold">
            {selectedProfile ? selectedProfile.name : "No profile selected"}
          </div>
          <span className="text-[10px] uppercase tracking-wide text-[#8b8b8b]">
            {selectedProfile?.status ?? "none"}
          </span>
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
              type="button"
              className="min-h-8 rounded border border-[#333] px-2 text-[#bbb] hover:bg-[#222]"
              onClick={() => setViewerFullscreen((open) => !open)}
              aria-label={viewerFullscreen ? "Exit full view" : "Enter full view"}
              aria-pressed={viewerFullscreen}
            >
              {viewerFullscreen ? "Exit" : "Full view"}
            </button>
          </div>
          {viewportControlsOpen && canManageViewport && onViewportApply ? (
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
        </header>
        <div className="min-h-0 flex-1">
          {selectedProfile && selectedProfile.status === "running" ? (
            <ProfileViewer
              key={selectedProfile.id}
              profileId={selectedProfile.id}
              cdpUrl={selectedProfile.cdp_url}
              clipboardSync={selectedProfile.clipboard_sync}
              canInteract={canInteract}
              viewportScale={viewerZoom / 100}
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
