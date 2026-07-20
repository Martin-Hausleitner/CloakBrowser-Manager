import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ChangeEvent, FormEvent, ReactNode } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Expand,
  Grid2X2,
  Globe2,
  MessageSquareText,
  MonitorSmartphone,
  Paperclip,
  Pencil,
  Play,
  Plus,
  Send,
  ShieldCheck,
  SlidersHorizontal,
  Shrink,
  Square,
} from "lucide-react";
import type { Profile } from "../../lib/api";
import { StatusIndicator } from "../StatusIndicator";

interface MobileSplitScreenProps {
  profiles: Profile[];
  selected: Profile | null;
  selectedId: string | null;
  error: string | null;
  authRequired: boolean;
  canManageProfiles: boolean;
  canOperate: boolean;
  canInteract: boolean;
  canManageAccess: boolean;
  identityName: string | null;
  browserView: ReactNode;
  browserZoom: number;
  onSelect: (id: string) => void;
  onNew: () => void;
  onEdit: () => void;
  onLaunch: () => void;
  onStop: () => void;
  onViewportApply: (width: number, height: number) => boolean | Promise<boolean>;
  onFullscreenChange: (open: boolean) => void;
  onBrowserZoomChange: (zoom: number) => void;
  onAccessControls: () => void;
  onLogout: () => void;
}

interface ChatMessage {
  id: number;
  role: "task" | "assistant" | "user";
  text: string;
}

const presets = [
  { label: "Mobile", width: 390, height: 844 },
  { label: "Tablet", width: 768, height: 1024 },
  { label: "Desktop", width: 1440, height: 900 },
] as const;

const defaultPreviewPanePercent = 42;
const defaultLivePanePercent = 58;
const defaultBrowserZoom = 100;

const initialMessages: ChatMessage[] = [
  {
    id: 1,
    role: "task",
    text: "Agent task: compare the first three visible results and prepare a short note. Local UI state only.",
  },
  {
    id: 2,
    role: "assistant",
    text: "Ready. I will keep the browser visible while collecting notes in this mobile split view.",
  },
];

const taskSteps = [
  { label: "Step 1", detail: "Ran mobile task shell" },
  { label: "Step 2", detail: "Prepared browser viewport" },
  { label: "Step 3", detail: "Ready for screenshot notes" },
];

export function MobileSplitScreen({
  profiles,
  selected,
  selectedId,
  error,
  authRequired,
  canManageProfiles,
  canOperate,
  canInteract,
  canManageAccess,
  identityName,
  browserView,
  browserZoom,
  onSelect,
  onNew,
  onEdit,
  onLaunch,
  onStop,
  onViewportApply,
  onFullscreenChange,
  onBrowserZoomChange,
  onAccessControls,
  onLogout,
}: MobileSplitScreenProps) {
  const [viewport, setViewport] = useState({
    width: selected?.screen_width ?? presets[0].width,
    height: selected?.screen_height ?? presets[0].height,
  });
  const [messages, setMessages] = useState(initialMessages);
  const [draft, setDraft] = useState("");
  const [gridOpen, setGridOpen] = useState(false);
  const [viewportOpen, setViewportOpen] = useState(false);
  const [viewportSaved, setViewportSaved] = useState(false);
  const [viewportSaveFailed, setViewportSaveFailed] = useState(false);
  const [fullscreenOpen, setFullscreenOpen] = useState(false);
  const [panePercent, setPanePercent] = useState(
    selected?.status === "running" ? defaultLivePanePercent : defaultPreviewPanePercent,
  );
  const [runSettingsOpen, setRunSettingsOpen] = useState(false);
  const [agentRunner, setAgentRunner] = useState("browser-agent");
  const [attachmentName, setAttachmentName] = useState<string | null>(null);
  const fullscreenButtonRef = useRef<HTMLButtonElement>(null);
  const attachmentInputRef = useRef<HTMLInputElement>(null);
  const viewportProfileIdRef = useRef(selected?.id);
  const paneAdjustedRef = useRef(false);

  const runningProfiles = useMemo(
    () => profiles.filter((profile) => profile.status === "running"),
    [profiles],
  );
  const liveLabel = selected?.status === "running" ? "Live browser" : "Preview";
  const browserUrl = selected?.cdp_url ?? "Browser preview";
  const isLiveBrowser = selected?.status === "running";
  const livePaneStyle = {
    "--mobile-live-pane-basis": `${panePercent}%`,
  } as CSSProperties;

  useEffect(() => {
    if (viewportProfileIdRef.current !== selected?.id) {
      setViewportSaved(false);
      setViewportSaveFailed(false);
      viewportProfileIdRef.current = selected?.id;
    }
    setViewport({
      width: selected?.screen_width ?? presets[0].width,
      height: selected?.screen_height ?? presets[0].height,
    });
  }, [selected?.id, selected?.screen_height, selected?.screen_width]);

  useEffect(() => {
    if (paneAdjustedRef.current) return;
    setPanePercent(selected?.status === "running" ? defaultLivePanePercent : defaultPreviewPanePercent);
  }, [selected?.id, selected?.status]);

  useEffect(() => {
    if (!fullscreenOpen) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;
    fullscreenButtonRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFullscreenOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);

    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      previouslyFocused?.focus();
    };
  }, [fullscreenOpen]);

  useEffect(() => {
    onFullscreenChange(fullscreenOpen);
  }, [fullscreenOpen, onFullscreenChange]);

  const sendMessage = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setMessages((current) => [
      ...current,
      { id: Date.now(), role: "user", text },
      {
        id: Date.now() + 1,
        role: "assistant",
        text: "Agent reply queued locally. Connect automation later without changing this mobile shell.",
      },
    ]);
    setDraft("");
  };

  const updateDraft = (event: ChangeEvent<HTMLTextAreaElement>) => {
    setDraft(event.target.value);
    event.target.style.height = "auto";
    event.target.style.height = `${Math.min(event.target.scrollHeight, 120)}px`;
  };

  const resetLiveViewport = () => {
    paneAdjustedRef.current = false;
    setPanePercent(isLiveBrowser ? defaultLivePanePercent : defaultPreviewPanePercent);
    onBrowserZoomChange(defaultBrowserZoom);
  };

  const updatePanePercent = (value: number) => {
    paneAdjustedRef.current = true;
    setPanePercent(value);
  };

  const renderLiveViewControls = () => (
    <>
      <div className="mobile-live-control-row">
        <label htmlFor="mobile-pane-size">
          <span className="label">Browser pane</span>
          <input
            id="mobile-pane-size"
            type="range"
            min={30}
            max={70}
            step={1}
            value={panePercent}
            onChange={(event) => updatePanePercent(Number(event.target.value))}
            aria-valuetext={`${panePercent}% of the workspace`}
          />
        </label>
        <output htmlFor="mobile-pane-size" aria-label="Browser pane size">
          {panePercent}%
        </output>
      </div>
      <div className="mobile-live-control-row">
        <label htmlFor="mobile-browser-zoom">
          <span className="label">Visual zoom</span>
          <input
            id="mobile-browser-zoom"
            type="range"
            min={75}
            max={150}
            step={5}
            value={browserZoom}
            onChange={(event) => onBrowserZoomChange(Number(event.target.value))}
            aria-valuetext={`${browserZoom}%`}
          />
        </label>
        <output htmlFor="mobile-browser-zoom" aria-label="Visual zoom level">
          {browserZoom}%
        </output>
      </div>
    </>
  );

  const renderBrowserSurface = () => (
    <div
      className={`mobile-browser-frame ${isLiveBrowser ? "mobile-browser-frame-live" : ""}`}
      data-testid="mobile-browser-frame"
    >
      {!isLiveBrowser ? (
        <div className="mobile-browser-chrome">
          <div className="flex items-center gap-1 text-gray-500">
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
          </div>
          <div className="mobile-url-bar">
            <Globe2 className="h-3.5 w-3.5 shrink-0 text-gray-500" aria-hidden="true" />
            <span className="truncate">{browserUrl}</span>
          </div>
        </div>
      ) : null}
      <div className="mobile-browser-content">
        {isLiveBrowser ? (
          browserView
        ) : (
          <div className="mobile-browser-placeholder">
            <MonitorSmartphone className="h-9 w-9 text-gray-500" aria-hidden="true" />
            <div>
              <p className="text-sm font-medium text-gray-200">No live browser connected</p>
              <p className="mt-1 text-xs text-gray-500">
                Launch a profile to stream VNC here. The task workspace stays active meanwhile.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );

  return (
    <main className="mobile-split-root bg-surface-0 text-gray-100">
      <section
        className={`mobile-live-pane ${fullscreenOpen ? "mobile-live-pane-fullscreen" : ""}`}
        style={livePaneStyle}
        role={fullscreenOpen ? "dialog" : undefined}
        aria-modal={fullscreenOpen ? true : undefined}
        aria-label={fullscreenOpen ? "Fullscreen browser viewer" : undefined}
      >
        <div className="flex items-center justify-between gap-3 px-3 py-2">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {selected ? <StatusIndicator status={selected.status} size="md" /> : null}
              <h1 className="truncate text-sm font-semibold">
                {selected?.name ?? "Mobile Browser"}
              </h1>
            </div>
            <p className="mt-0.5 text-[11px] uppercase tracking-wide text-gray-500">
              {liveLabel} · {viewport.width} x {viewport.height} · Pane {panePercent}% · Zoom {browserZoom}%
              {!canInteract && selected?.status === "running" ? " · View only" : ""}
            </p>
          </div>
          <div className="flex items-center gap-1">
            {!fullscreenOpen ? (
              <>
                <button
                  type="button"
                  onClick={() => {
                    setGridOpen((open) => !open);
                    setViewportOpen(false);
                  }}
                  className="mobile-icon-button"
                  aria-pressed={gridOpen}
                  aria-label="Toggle grid view"
                  title="Toggle grid view"
                >
                  <Grid2X2 className="h-4 w-4" />
                </button>
              </>
            ) : null}
            <button
              ref={fullscreenButtonRef}
              type="button"
              onClick={() => setFullscreenOpen((open) => !open)}
              className="mobile-icon-button"
              aria-label={fullscreenOpen ? "Close fullscreen browser" : "Open fullscreen browser"}
              title={fullscreenOpen ? "Close fullscreen browser" : "Open fullscreen browser"}
            >
              {fullscreenOpen ? <Shrink className="h-4 w-4" /> : <Expand className="h-4 w-4" />}
            </button>
          </div>
        </div>

        <div className={`mobile-browser-wrap ${isLiveBrowser ? "mobile-browser-wrap-live" : "px-3 pb-2"}`}>
          {renderBrowserSurface()}
        </div>
      </section>

      <section
        className="mobile-control-pane"
        aria-hidden={fullscreenOpen ? true : undefined}
        inert={fullscreenOpen ? true : undefined}
      >
        {error ? (
          <div className="mx-3 mt-3 rounded-md border border-red-600/30 bg-red-600/15 px-3 py-2 text-xs text-red-300">
            {error}
          </div>
        ) : null}

        <div className="mobile-toolbar mobile-session-bar" aria-label="Session control">
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-500">Session</p>
            <label>
              <span className="sr-only">Select profile</span>
              <select
                value={selectedId ?? ""}
                onChange={(event) => onSelect(event.target.value)}
                className="mobile-select mobile-session-select"
              >
                <option value="" disabled>
                  Select profile
                </option>
                {profiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="mobile-session-actions">
            {canManageProfiles && (
              <button type="button" onClick={onNew} className="mobile-icon-button" aria-label="New profile">
                <Plus className="h-4 w-4" />
              </button>
            )}
            {canManageProfiles && selected ? (
              <button type="button" onClick={onEdit} className="mobile-icon-button" aria-label="Edit selected profile">
                <Pencil className="h-4 w-4" />
              </button>
            ) : null}
            {canManageAccess && (
              <button type="button" onClick={onAccessControls} className="mobile-icon-button" aria-label="Browser access controls">
                <ShieldCheck className="h-4 w-4" />
              </button>
            )}
            {canOperate && selected?.status === "running" ? (
              <button type="button" onClick={onStop} className="btn-secondary mobile-session-run-button">
                <Square className="h-3.5 w-3.5" />
                <span>Stop</span>
              </button>
            ) : canOperate ? (
              <button
                type="button"
                onClick={onLaunch}
                className="btn-primary mobile-session-run-button"
                disabled={!selected}
              >
                <Play className="h-3.5 w-3.5" />
                <span>Launch</span>
              </button>
            ) : null}
          </div>
        </div>

        {isLiveBrowser ? (
          <div className="mobile-live-control-rail" aria-label="Live view controls">
            {renderLiveViewControls()}
            <button type="button" className="btn-secondary mobile-live-reset" onClick={resetLiveViewport}>
              Reset view
            </button>
          </div>
        ) : null}

        <div className="mobile-viewport-disclosure">
          <button
            type="button"
            onClick={() => {
              setViewportOpen((open) => !open);
              setGridOpen(false);
            }}
            className="mobile-disclosure-button"
            aria-label="Edit browser viewport"
            aria-pressed={viewportOpen}
            aria-expanded={viewportOpen}
            aria-controls="mobile-viewport-settings"
          >
            <MonitorSmartphone className="h-4 w-4" aria-hidden="true" />
            <span>Viewport settings</span>
            <span className="ml-auto text-[11px] text-gray-500">{viewport.width} x {viewport.height}</span>
          </button>
        </div>

        {viewportOpen ? (
          <div id="mobile-viewport-settings" className="mobile-viewport-editor" aria-label="Viewport controls">
            {!isLiveBrowser ? (
              <>
                {renderLiveViewControls()}
                <div className="flex items-center justify-between gap-3">
                  <p className="text-[11px] text-gray-500">Preview controls update this viewer immediately.</p>
                  <button type="button" className="btn-secondary shrink-0" onClick={resetLiveViewport}>
                    Reset view
                  </button>
                </div>
              </>
            ) : null}
            {canManageProfiles ? (
              <>
                <div className="flex gap-1 overflow-x-auto">
                  {presets.map((preset) => (
                    <button
                      key={preset.label}
                      type="button"
                      onClick={() => {
                        setViewport({ width: preset.width, height: preset.height });
                        setViewportSaved(false);
                        setViewportSaveFailed(false);
                      }}
                      className="mobile-preset-button"
                    >
                      {preset.label}
                    </button>
                  ))}
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <label>
                    <span className="label">Width</span>
                    <input
                      className="input no-spin"
                      type="number"
                      min={240}
                      max={2560}
                      value={viewport.width}
                      onChange={(event) => {
                        setViewport((current) => ({
                          ...current,
                          width: Number(event.target.value) || current.width,
                        }));
                        setViewportSaved(false);
                        setViewportSaveFailed(false);
                      }}
                    />
                  </label>
                  <label>
                    <span className="label">Height</span>
                    <input
                      className="input no-spin"
                      type="number"
                      min={320}
                      max={1600}
                      value={viewport.height}
                      onChange={(event) => {
                        setViewport((current) => ({
                          ...current,
                          height: Number(event.target.value) || current.height,
                        }));
                        setViewportSaved(false);
                        setViewportSaveFailed(false);
                      }}
                    />
                  </label>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <p className="text-[11px] text-gray-500">
                    {viewportSaveFailed
                      ? "Could not save viewport"
                      : viewportSaved
                      ? "Saved"
                      : selected?.status === "running"
                        ? "Saved dimensions apply on the next browser launch"
                        : "Applied when this profile launches"}
                  </p>
                  <button
                    type="button"
                    className="btn-primary min-h-11 shrink-0"
                    disabled={!selected}
                    onClick={async () => {
                      const saved = await onViewportApply(viewport.width, viewport.height);
                      setViewportSaved(saved);
                      setViewportSaveFailed(!saved);
                    }}
                  >
                    Apply
                  </button>
                </div>
              </>
            ) : null}
          </div>
        ) : null}

        {gridOpen ? (
          <div className="mobile-grid" aria-label="Running browser grid">
            {(runningProfiles.length > 0 ? runningProfiles : profiles.slice(0, 4)).map((profile) => (
              <button
                key={profile.id}
                type="button"
                onClick={() => onSelect(profile.id)}
                className="mobile-grid-tile"
              >
                <span className="mobile-grid-thumb" />
                <span className="flex items-center gap-1 truncate text-xs">
                  <StatusIndicator status={profile.status} />
                  <span className="truncate">{profile.name}</span>
                </span>
              </button>
            ))}
            {profiles.length === 0 ? (
              <div className="rounded-md border border-dashed border-border px-3 py-4 text-center text-xs text-gray-500">
                Grid view will show active browser sessions.
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="mobile-chat-header">
          <MessageSquareText className="h-4 w-4 text-accent" aria-hidden="true" />
          <span className="text-sm font-semibold">Task chat</span>
          <span className="ml-auto rounded bg-surface-3 px-2 py-0.5 text-[10px] uppercase tracking-wide text-gray-400">
            Agent task
          </span>
        </div>

        <div className="mobile-chat-log" aria-label="Chat history">
          <div className="mobile-step-list" aria-label="Agent task steps">
            {taskSteps.map((step) => (
              <div key={step.label} className="mobile-step-pill">
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" aria-hidden="true" />
                <span className="font-medium">{step.label}</span>
                <span className="truncate text-gray-500">{step.detail}</span>
              </div>
            ))}
          </div>
          {messages.map((message) => (
            <div
              key={message.id}
              className={`mobile-message mobile-message-${message.role}`}
            >
              {message.text}
            </div>
          ))}
        </div>

        {runSettingsOpen ? (
          <div className="mobile-run-settings" aria-label="Agent run settings">
            <div>
              <p className="text-xs font-medium text-gray-200">Run settings</p>
              <p className="mt-0.5 text-[11px] text-gray-500">Local runner controls; no cloud task is started.</p>
            </div>
            <label className="flex items-center gap-2 text-xs text-gray-300">
              <input type="checkbox" defaultChecked />
              Keep browser visible
            </label>
            <label className="flex items-center gap-2 text-xs text-gray-300">
              <span>Max steps</span>
              <input className="input ml-auto w-20" type="number" min={1} max={100} defaultValue={25} />
            </label>
          </div>
        ) : null}

        <form className="mobile-chat-form" onSubmit={sendMessage}>
          <label className="sr-only" htmlFor="mobile-task-input">
            Message
          </label>
          <textarea
            id="mobile-task-input"
            value={draft}
            onChange={updateDraft}
            className="input min-h-9 resize-none overflow-y-auto"
            rows={1}
            placeholder="Send a follow-up..."
          />
          <div className="mobile-composer-toolbar">
            <input
              ref={attachmentInputRef}
              type="file"
              className="sr-only"
              aria-label="Choose task attachment"
              onChange={(event) => setAttachmentName(event.target.files?.[0]?.name ?? null)}
            />
            <button
              type="button"
              className="mobile-composer-button"
              aria-label="Attach files"
              title="Attach files"
              onClick={() => attachmentInputRef.current?.click()}
            >
              <Paperclip className="h-4 w-4" />
            </button>
            <button
              type="button"
              className="mobile-composer-button"
              aria-label="Run settings"
              aria-expanded={runSettingsOpen}
              title="Run settings"
              onClick={() => setRunSettingsOpen((open) => !open)}
            >
              <SlidersHorizontal className="h-4 w-4" />
            </button>
            <label className="min-w-0 flex-1">
              <span className="sr-only">Select agent runner</span>
              <select
                className="mobile-composer-select"
                value={agentRunner}
                onChange={(event) => setAgentRunner(event.target.value)}
              >
                <option value="browser-agent">Browser agent</option>
                <option value="local-runner">Local runner</option>
              </select>
            </label>
            <button type="submit" className="mobile-send-button" aria-label="Run task">
              <Send className="h-4 w-4" />
            </button>
          </div>
          {attachmentName ? (
            <p className="truncate text-[11px] text-gray-500" aria-live="polite">
              Task attachment: {attachmentName}
            </p>
          ) : null}
        </form>

        {authRequired ? (
          <div className="mx-3 mb-3 flex items-center justify-between gap-2">
            {identityName ? <span className="truncate text-xs text-gray-500">Signed in as {identityName}</span> : <span />}
            <button
              type="button"
              onClick={onLogout}
              className="mobile-logout-button"
              aria-label="Log out"
            >
              Log out
            </button>
          </div>
        ) : null}
      </section>

    </main>
  );
}
