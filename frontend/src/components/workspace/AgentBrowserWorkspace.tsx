import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Play, Square, SendHorizontal, TerminalSquare, MonitorSmartphone } from "lucide-react";
import {
  api,
  type OrcaAgentCli,
  type OrcaCapabilities,
  type OrcaSession,
  type Profile,
} from "../../lib/api";
import { ProfileViewer } from "../ProfileViewer";

const AGENT_OPTIONS: OrcaAgentCli[] = ["cursor-agent", "grok", "codex"];

export interface AgentBrowserWorkspaceProps {
  profiles: Profile[];
  selectedProfile: Profile | null;
  canAutomate: boolean;
  canInteract: boolean;
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
  onSelectProfile,
  onConnectionStatusChange,
  onViewerDisconnect,
}: AgentBrowserWorkspaceProps) {
  const [agent, setAgent] = useState<OrcaAgentCli>("cursor-agent");
  const [prompt, setPrompt] = useState("");
  const [caps, setCaps] = useState<OrcaCapabilities | null>(null);
  const [session, setSession] = useState<OrcaSession | null>(null);
  const [transcript, setTranscript] = useState("");
  const [cursor, setCursor] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);

  const runningProfiles = useMemo(
    () => profiles.filter((profile) => profile.status === "running"),
    [profiles],
  );

  const unavailable = caps != null && !caps.available;
  const sessionActive = session?.status === "running" || session?.status === "starting";
  const canStart =
    Boolean(selectedProfile) &&
    canAutomate &&
    canInteract &&
    !unavailable &&
    !sessionActive &&
    !busy;
  const canSend = Boolean(sessionActive && canInteract && prompt.trim() && !busy);
  const canStop = Boolean(session && session.status !== "closed" && canInteract && !busy);

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

  useEffect(() => {
    const node = transcriptEndRef.current;
    if (node && typeof node.scrollIntoView === "function") {
      node.scrollIntoView({ block: "end" });
    }
  }, [transcript]);

  useEffect(() => {
    // Switching profiles stops the local session view; operator must relaunch.
    stopPolling();
    setSession(null);
    setTranscript("");
    setCursor(0);
    setError(null);
  }, [selectedProfile?.id, stopPolling]);

  const handleStart = useCallback(async () => {
    if (!selectedProfile || !canStart) return;
    setBusy(true);
    setError(null);
    try {
      const started = await api.startOrcaSession({
        profile_id: selectedProfile.id,
        agent,
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
  }, [agent, canStart, prompt, selectedProfile]);

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
    if (!session || !canStop) return;
    setBusy(true);
    setError(null);
    try {
      const closed = await api.closeOrcaSession(session.id);
      setSession(closed);
      stopPolling();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to stop Orca session");
    } finally {
      setBusy(false);
    }
  }, [canStop, session, stopPolling]);

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
            <div className="truncate text-[12px] font-semibold tracking-tight">Orca CLI</div>
            <div className="truncate text-[10px] text-[#8b8b8b]" data-testid="orca-connection-status">
              {statusLabel(session, caps)}
              {session ? ` · ${session.terminal_handle}` : ""}
            </div>
          </div>
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] ${
              sessionActive ? "bg-[#1f3d2a] text-[#9ae6b4]" : "bg-[#2a2a2a] text-[#a0a0a0]"
            }`}
            data-testid="orca-run-status"
          >
            {session?.status ?? "idle"}
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
            Agent CLI
          </label>
          <select
            id="orca-agent"
            className="input h-8 max-w-[10rem] bg-[#1a1a1a] py-1 text-[11px]"
            value={agent}
            onChange={(event) => setAgent(event.target.value as OrcaAgentCli)}
            disabled={sessionActive || unavailable}
            data-testid="orca-agent-select"
          >
            {AGENT_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
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
                unavailable
                  ? "Orca runtime unavailable"
                  : !canAutomate || !canInteract
                    ? "Requires automate and interact"
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
          <span data-testid="orca-cap-pause">pause: unavailable</span>
          <span>·</span>
          <span data-testid="orca-cap-resume">resume: unavailable</span>
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

        {unavailable ? (
          <div
            className="border-b border-amber-900/40 bg-amber-950/30 px-3 py-1.5 text-[11px] text-amber-200"
            data-testid="orca-unavailable"
          >
            {caps?.notes?.length
              ? caps.notes.join(" · ")
              : "Orca is unavailable. Launch/Stop stay disabled until readiness checks pass."}
          </div>
        ) : null}

        <pre
          className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words bg-[#0a0a0a] px-3 py-2 font-mono text-[11px] leading-relaxed text-[#d0d0d0]"
          data-testid="orca-transcript"
          aria-label="CLI transcript"
        >
          {transcript || "No Orca output yet. Launch an allowlisted agent CLI to stream a real terminal."}
          <div ref={transcriptEndRef} />
        </pre>

        <form
          className="flex items-end gap-2 border-t border-[#2a2a2a] bg-[#141414] px-3 py-2"
          onSubmit={(event) => {
            event.preventDefault();
            void handleSend();
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
              sessionActive
                ? "Send follow-up to the live Orca CLI…"
                : "Initial prompt (optional) · uses CloakBrowser control skill"
            }
            disabled={unavailable || (!sessionActive && !canStart)}
            data-testid="orca-prompt"
          />
          <button
            type="submit"
            className="btn btn-primary inline-flex h-9 items-center gap-1 px-2.5 text-[11px]"
            disabled={!canSend}
            data-testid="orca-send"
          >
            <SendHorizontal className="h-3.5 w-3.5" />
            Send
          </button>
        </form>
      </section>

      <section className="flex min-w-0 flex-1 flex-col bg-[#090909]" aria-label="Live CloakBrowser profile">
        <header className="flex items-center gap-2 border-b border-[#2a2a2a] bg-[#141414] px-3 py-2">
          <MonitorSmartphone className="h-3.5 w-3.5 text-[#8b8b8b]" />
          <div className="min-w-0 flex-1 truncate text-[12px] font-semibold">
            {selectedProfile ? selectedProfile.name : "No profile selected"}
          </div>
          <span className="text-[10px] uppercase tracking-wide text-[#8b8b8b]">
            {selectedProfile?.status ?? "none"}
          </span>
        </header>
        <div className="min-h-0 flex-1">
          {selectedProfile && selectedProfile.status === "running" ? (
            <ProfileViewer
              key={selectedProfile.id}
              profileId={selectedProfile.id}
              cdpUrl={selectedProfile.cdp_url}
              clipboardSync={selectedProfile.clipboard_sync}
              canInteract={canInteract}
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
