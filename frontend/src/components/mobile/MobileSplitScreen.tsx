import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ChangeEvent, FormEvent, ReactNode } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Camera,
  ChevronUp,
  ClipboardCopy,
  ClipboardPaste,
  Expand,
  Grid2X2,
  Globe2,
  MessageSquareText,
  MonitorSmartphone,
  Pencil,
  Play,
  Plus,
  RotateCcw,
  Send,
  ShieldCheck,
  SlidersHorizontal,
  Shrink,
  Square,
} from "lucide-react";
import type { Profile } from "../../lib/api";
import {
  cloakServerProvider,
  codexComputerUseProvider,
  createTaskHarness,
  taskHarnessReadyEvent,
  type TaskHarnessAction,
  type TaskHarnessCapabilities,
  type TaskHarnessMessage,
} from "../../lib/taskHarness";
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
  browserConnectionStatus: "connecting" | "connected" | "reconnecting" | "failed" | null;
  remoteToolsOpen: boolean;
  onRemoteToolsOpenChange: (open: boolean) => void;
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
  id: string;
  role: "task" | "assistant" | "user" | "tool";
  text: string;
}

const presets = [
  { label: "Mobile", width: 390, height: 844 },
  { label: "Tablet", width: 768, height: 1024 },
  { label: "Desktop", width: 1440, height: 900 },
] as const;

const defaultPreviewPanePercent = 68;
// The running browser is the primary control surface; chat and settings are
// now collapsed into a compact dock by default.
const defaultLivePanePercent = 68;
const compactLivePanePercent = 65;
const compactLivePaneMaximumHeight = 700;
const defaultBrowserZoom = 100;
const collapsedLandscapeLivePanePercent = 74;
const collapsedLivePaneHeaderHeight = 44;
const livePaneLowerControlsReserveHeight = 156;
const minimumAspectFitPaneHeight = 220;
const minimumPhoneFitWidth = 320;
const minimumPhoneFitHeight = 480;

type PinnedHarnessAction = Omit<TaskHarnessAction, "kind"> & {
  kind: "screenshot" | "copy" | "paste";
};

const pinnedHarnessActions = [
  {
    id: "capture-browser",
    label: "Capture",
    kind: "screenshot",
    scope: "host",
  },
  {
    id: "copy-browser-selection",
    label: "Copy",
    kind: "copy",
    scope: "host",
  },
  {
    id: "paste-into-browser",
    label: "Paste",
    kind: "paste",
    scope: "host",
  },
] satisfies readonly PinnedHarnessAction[];

const pinnedHarnessPrompts: Record<PinnedHarnessAction["kind"], string> = {
  screenshot: "Capture the current browser view.",
  copy: "Copy the current browser selection.",
  paste: "Paste the clipboard into the focused browser field.",
};

function usesCompactLivePane() {
  return (
    typeof window !== "undefined" &&
    window.innerHeight <= compactLivePaneMaximumHeight &&
    window.innerHeight >= window.innerWidth
  );
}

function defaultPanePercent(isLiveBrowser: boolean, compactLivePane: boolean) {
  if (!isLiveBrowser) return defaultPreviewPanePercent;
  return compactLivePane ? compactLivePanePercent : defaultLivePanePercent;
}

function currentLiveViewportSize() {
  if (typeof window === "undefined") {
    return { width: presets[0].width, height: presets[0].height };
  }

  const visualViewport = window.visualViewport;
  return {
    width: Math.round(visualViewport?.width ?? window.innerWidth ?? presets[0].width),
    height: Math.round(visualViewport?.height ?? window.innerHeight ?? presets[0].height),
  };
}

function collapsedLivePaneBasis(
  width: number | null | undefined,
  height: number | null | undefined,
  liveViewportSize: { width: number; height: number },
) {
  if (
    !width ||
    !height ||
    width <= 0 ||
    height <= 0
  ) {
    return `${defaultLivePanePercent}%`;
  }

  const fittedBrowserHeight = Math.round(liveViewportSize.width * (height / width));
  const paneHeight = fittedBrowserHeight + collapsedLivePaneHeaderHeight;
  if (liveViewportSize.height >= liveViewportSize.width) {
    const maximumPaneHeight = Math.max(
      minimumAspectFitPaneHeight,
      liveViewportSize.height - livePaneLowerControlsReserveHeight,
    );
    return `${Math.min(paneHeight, maximumPaneHeight)}px`;
  }

  return `${paneHeight}px`;
}

function isInteractiveShortcutTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  if (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    target.isContentEditable
  ) {
    return true;
  }
  return Boolean(target.closest("canvas, .mobile-browser-frame, .profile-viewer"));
}

function toChatMessage(message: TaskHarnessMessage): ChatMessage {
  return {
    id: message.id,
    role: message.role === "assistant"
      ? "assistant"
      : message.role === "user"
        ? "user"
        : "tool",
    text: message.content,
  };
}

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
  browserConnectionStatus,
  remoteToolsOpen,
  onRemoteToolsOpenChange,
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
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [historyPending, setHistoryPending] = useState(false);
  const [harnessNotice, setHarnessNotice] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [gridOpen, setGridOpen] = useState(false);
  const [viewportOpen, setViewportOpen] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
  const [viewportApplying, setViewportApplying] = useState(false);
  const [viewportSaved, setViewportSaved] = useState(false);
  const [viewportSaveFailed, setViewportSaveFailed] = useState(false);
  const [fullscreenOpen, setFullscreenOpen] = useState(false);
  const [fullscreenViewOpen, setFullscreenViewOpen] = useState(false);
  const [fullscreenViewportOpen, setFullscreenViewportOpen] = useState(false);
  const [liveViewportSize, setLiveViewportSize] = useState(currentLiveViewportSize);
  const [compactLivePane, setCompactLivePane] = useState(usesCompactLivePane);
  const [panePercent, setPanePercent] = useState(() =>
    defaultPanePercent(selected?.status === "running", usesCompactLivePane()),
  );
  const [paneAdjusted, setPaneAdjusted] = useState(false);
  const [chatCollapsed, setChatCollapsed] = useState(true);
  const [harnessCapabilities, setHarnessCapabilities] = useState<TaskHarnessCapabilities | null>(null);
  const [harnessPending, setHarnessPending] = useState(false);
  const [harnessError, setHarnessError] = useState<string | null>(null);
  const fullscreenOpenButtonRef = useRef<HTMLButtonElement>(null);
  const fullscreenCloseButtonRef = useRef<HTMLButtonElement>(null);
  const restoreFullscreenFocusRef = useRef(false);
  const viewportProfileIdRef = useRef(selected?.id);
  const chatAdjustedRef = useRef(false);
  const taskHarnessRef = useRef<ReturnType<typeof createTaskHarness> | null>(null);

  const runningProfiles = useMemo(
    () => profiles.filter((profile) => profile.status === "running"),
    [profiles],
  );
  const liveLabel = selected?.status === "running" ? "Live browser" : "Preview";
  const browserUrl = selected?.cdp_url ?? "Browser preview";
  const isLiveBrowser = selected?.status === "running";
  const preferredPanePercent = defaultPanePercent(isLiveBrowser, compactLivePane);
  const collapsedPaneBasis =
    liveViewportSize.height <= 500 && liveViewportSize.width > liveViewportSize.height
      ? `${collapsedLandscapeLivePanePercent}%`
      : collapsedLivePaneBasis(selected?.screen_width, selected?.screen_height, liveViewportSize);
  const fitLivePaneToBrowser = isLiveBrowser && !paneAdjusted;
  const effectivePanePercent =
    fitLivePaneToBrowser
      ? collapsedPaneBasis
      : `${panePercent}%`;
  const livePaneStyle = {
    "--mobile-live-pane-basis": effectivePanePercent,
  } as CSSProperties;
  const connectionLabel =
    browserConnectionStatus === "connected"
      ? "Connected"
      : browserConnectionStatus === "reconnecting"
        ? "Reconnecting"
        : browserConnectionStatus === "failed"
          ? "Failed"
          : isLiveBrowser
            ? "Connecting"
            : "Offline";
  const connectionTone =
    browserConnectionStatus === "connected"
      ? "mobile-connection-live"
      : browserConnectionStatus === "failed"
        ? "mobile-connection-failed"
        : "mobile-connection-pending";
  const harnessProvider = harnessCapabilities?.metadata?.provider;
  const codexHostReady = Boolean(
    harnessCapabilities?.chat && harnessProvider === codexComputerUseProvider,
  );
  const serverHistoryReady = Boolean(
    harnessCapabilities?.chat && harnessProvider === cloakServerProvider,
  );
  const harnessReady = codexHostReady || serverHistoryReady;
  const composerReady = Boolean(harnessReady && canInteract && selected);
  const harnessLabel = harnessCapabilities === null
    ? "Task connection · checking"
    : codexHostReady
      ? "Codex Computer Use · connected"
      : serverHistoryReady
        ? "Server history · save only"
        : "Task connection · unavailable";
  const harnessPlaceholder = harnessCapabilities === null
    ? "Checking task connection..."
    : !selected
      ? "Select a browser profile"
      : !canInteract
        ? "View-only access"
        : codexHostReady
          ? "Ask Codex Computer Use..."
          : serverHistoryReady
            ? "Save task to server history..."
            : "Task connection unavailable";
  const compactWorkspace = chatCollapsed && !remoteToolsOpen && !fullscreenOpen;
  const toolPanelOpen = viewportOpen || gridOpen || adminOpen;

  const closeTools = () => {
    onRemoteToolsOpenChange(false);
    setViewportOpen(false);
    setGridOpen(false);
    setAdminOpen(false);
  };

  const openTools = () => {
    chatAdjustedRef.current = true;
    setChatCollapsed(true);
    onRemoteToolsOpenChange(true);
  };

  useEffect(() => {
    let cancelled = false;
    let requestId = 0;
    const refreshHarness = () => {
      const currentRequestId = ++requestId;
      const harness = createTaskHarness(window);
      taskHarnessRef.current = harness;
      setHarnessCapabilities(null);
      harness.capabilities()
        .then((capabilities) => {
          if (!cancelled && currentRequestId === requestId) {
            setHarnessCapabilities(capabilities);
          }
        })
        .catch((err) => {
          console.warn("[task-harness] capabilities failed:", err);
          if (!cancelled && currentRequestId === requestId) {
            setHarnessCapabilities({
              chat: false,
              streaming: false,
              clipboard: false,
              browser_actions: [],
              metadata: { mode: "unavailable", reason: "capabilities failed" },
            });
          }
        });
    };

    refreshHarness();
    window.addEventListener(taskHarnessReadyEvent, refreshHarness);
    return () => {
      cancelled = true;
      window.removeEventListener(taskHarnessReadyEvent, refreshHarness);
    };
  }, []);

  useEffect(() => {
    const profileId = selected?.id;
    const harness = taskHarnessRef.current;
    const controller = new AbortController();
    let cancelled = false;

    setMessages([]);
    setConversationId(null);
    setHarnessNotice(null);
    setHarnessError(null);

    if (!profileId || !harness || !harnessCapabilities?.chat) {
      setHistoryPending(false);
      return () => controller.abort();
    }

    setHistoryPending(true);
    void harness.listConversations(profileId, { signal: controller.signal })
      .then(async (conversations) => {
        if (cancelled) return;
        const latest = conversations.find((conversation) => conversation.status === "active")
          ?? conversations[0];
        if (!latest) return;
        const history = await harness.listMessages(latest.id, { signal: controller.signal });
        if (cancelled) return;
        setConversationId(latest.id);
        setMessages(history.map(toChatMessage));
      })
      .catch((err) => {
        if (cancelled || (err instanceof DOMException && err.name === "AbortError")) return;
        console.warn("[task-harness] history failed:", err);
        setHarnessNotice("History is temporarily unavailable.");
      })
      .finally(() => {
        if (!cancelled) setHistoryPending(false);
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [harnessCapabilities, selected?.id]);

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
    const updateLiveViewport = () => {
      const nextSize = currentLiveViewportSize();
      setLiveViewportSize((current) =>
        current.width === nextSize.width && current.height === nextSize.height ? current : nextSize,
      );
      setCompactLivePane(usesCompactLivePane());
    };
    updateLiveViewport();
    window.addEventListener("resize", updateLiveViewport);
    window.visualViewport?.addEventListener("resize", updateLiveViewport);
    return () => {
      window.removeEventListener("resize", updateLiveViewport);
      window.visualViewport?.removeEventListener("resize", updateLiveViewport);
    };
  }, []);

  useEffect(() => {
    if (paneAdjusted) return;
    setPanePercent(preferredPanePercent);
  }, [paneAdjusted, preferredPanePercent, selected?.id, selected?.status]);

  useEffect(() => {
    chatAdjustedRef.current = false;
    setChatCollapsed(true);
  }, [selected?.id]);

  useEffect(() => {
    if (!fullscreenOpen) {
      if (restoreFullscreenFocusRef.current) {
        fullscreenOpenButtonRef.current?.focus();
        restoreFullscreenFocusRef.current = false;
      }
      return;
    }

    fullscreenCloseButtonRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setFullscreenOpen(false);
        setFullscreenViewOpen(false);
        setFullscreenViewportOpen(false);
      }
    };
    document.addEventListener("keydown", closeOnEscape);

    return () => {
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [fullscreenOpen]);

  useEffect(() => {
    onFullscreenChange(fullscreenOpen);
  }, [fullscreenOpen, onFullscreenChange]);

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      const key = event.key.toLowerCase();
      if (isInteractiveShortcutTarget(event.target)) return;
      if (key === "j") {
        event.preventDefault();
        chatAdjustedRef.current = true;
        closeTools();
        setChatCollapsed((collapsed) => !collapsed);
        return;
      }
      if (key === "b") {
        event.preventDefault();
        if (fullscreenOpen) {
          closeFullscreen();
        } else {
          openFullscreen();
        }
        return;
      }
      if (key === "g") {
        event.preventDefault();
        openTools();
        setGridOpen((open) => !open);
        setViewportOpen(false);
        setAdminOpen(false);
        return;
      }
      if (key === "k") {
        event.preventDefault();
        if (remoteToolsOpen) {
          closeTools();
        } else {
          openTools();
        }
      }
    };

    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  });

  const openFullscreen = () => {
    if (!isLiveBrowser) return;
    restoreFullscreenFocusRef.current = true;
    setGridOpen(false);
    setViewportOpen(false);
    setFullscreenViewOpen(false);
    setFullscreenViewportOpen(false);
    closeTools();
    setFullscreenOpen(true);
  };

  const closeFullscreen = () => {
    setFullscreenOpen(false);
    setFullscreenViewOpen(false);
    setFullscreenViewportOpen(false);
  };

  const runHarnessTask = async (text: string, commands?: readonly TaskHarnessAction[]) => {
    if (!text || harnessPending || !composerReady) return;
    const localMessageId = `local-${Date.now()}`;
    const userMessage: ChatMessage = { id: localMessageId, role: "user", text };
    chatAdjustedRef.current = true;
    closeTools();
    setChatCollapsed(false);
    setMessages((current) => [
      ...current,
      userMessage,
    ]);
    setDraft("");
    setHarnessPending(true);
    setHarnessError(null);
    setHarnessNotice(null);
    try {
      const reply = await (taskHarnessRef.current ?? createTaskHarness(window)).send({
        text,
        ...(commands ? { commands } : {}),
        profile_id: selected?.id ?? null,
        ...(conversationId ? { conversation_id: conversationId } : {}),
        metadata: {
          runner: codexHostReady ? codexComputerUseProvider : cloakServerProvider,
          execution: codexHostReady ? "host" : "persist-only",
          browser_visible: true,
          ...(commands ? { source: "pinned-action" } : {}),
        },
      });
      if (reply.role === "user") {
        setMessages((current) => current.map((message) => (
          message.id === localMessageId ? toChatMessage(reply) : message
        )));
        setHarnessNotice(
          serverHistoryReady
            ? "Saved to server history · not executed."
            : "Task recorded · no execution result received.",
        );
      } else {
        setMessages((current) => [...current, toChatMessage(reply)]);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Task harness request failed";
      setHarnessError(message);
    } finally {
      setHarnessPending(false);
    }
  };

  const sendMessage = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || harnessPending || !composerReady) return;
    setDraft("");
    await runHarnessTask(text);
  };

  const runPinnedHarnessAction = async (action: PinnedHarnessAction) => {
    if (!codexHostReady || !harnessCapabilities?.browser_actions.includes(action.kind)) return;
    await runHarnessTask(pinnedHarnessPrompts[action.kind], [action]);
  };

  const updateDraft = (event: ChangeEvent<HTMLTextAreaElement>) => {
    setDraft(event.target.value);
    event.target.style.height = "auto";
    event.target.style.height = `${Math.min(event.target.scrollHeight, 120)}px`;
  };

  const resetLiveViewport = () => {
    setPaneAdjusted(false);
    setPanePercent(preferredPanePercent);
    onBrowserZoomChange(defaultBrowserZoom);
  };

  const updatePanePercent = (value: number) => {
    setPaneAdjusted(true);
    setPanePercent(value);
  };

  const applyViewportSize = async (nextViewport: { width: number; height: number }) => {
    if (viewportApplying) return;

    setViewportSaved(false);
    setViewportSaveFailed(false);
    if (!selected || !canManageProfiles) return;

    setViewportApplying(true);
    try {
      const saved = await onViewportApply(nextViewport.width, nextViewport.height);
      setViewportSaved(saved);
      setViewportSaveFailed(!saved);
    } finally {
      setViewportApplying(false);
    }
  };

  const applyPhoneFitViewport = async () => {
    const visualViewport = typeof window !== "undefined" ? window.visualViewport : null;
    const nextViewport = {
      width: Math.round(Math.max(minimumPhoneFitWidth, visualViewport?.width ?? window.innerWidth ?? presets[0].width)),
      height: Math.round(
        Math.max(
          minimumPhoneFitHeight,
          visualViewport?.height ?? window.innerHeight ?? presets[0].height,
        ),
      ),
    };
    setViewport(nextViewport);
    await applyViewportSize(nextViewport);
  };

  const applyViewport = async () => {
    await applyViewportSize(viewport);
  };

  const renderViewportEditor = (surface: "inline" | "fullscreen") => {
    const fullscreen = surface === "fullscreen";
    const editorId = fullscreen ? "mobile-fullscreen-viewport-settings" : "mobile-viewport-settings";
    const widthInputId = fullscreen ? "mobile-fullscreen-viewport-width" : "mobile-viewport-width";
    const heightInputId = fullscreen ? "mobile-fullscreen-viewport-height" : "mobile-viewport-height";

    return (
      <div
        id={editorId}
        className={`mobile-viewport-editor ${fullscreen ? "mobile-fullscreen-viewport-editor" : ""}`}
        aria-label={fullscreen ? "Fullscreen viewport controls" : "Viewport controls"}
      >
        {fullscreen ? (
          <p className="mobile-viewport-editor-note">
            {selected?.status === "running"
              ? "Restarts live browser to apply this viewport."
              : "Save the viewport for the next launch."}
          </p>
        ) : null}
        {!fullscreen ? (
          <>
            {renderLiveViewControls()}
            <p className="mobile-viewport-editor-note">Pane and zoom update this viewer immediately.</p>
          </>
        ) : null}
        {canManageProfiles ? (
          <>
            <div className="flex gap-1 overflow-x-auto">
              <button
                type="button"
                onClick={applyPhoneFitViewport}
                className="mobile-preset-button mobile-phone-fit-button"
                disabled={viewportApplying}
              >
                Phone fit
              </button>
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
                  disabled={viewportApplying}
                >
                  {preset.label}
                </button>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-2">
              <label htmlFor={widthInputId}>
                <span className="label">Width</span>
                <input
                  id={widthInputId}
                  aria-label={fullscreen ? "Fullscreen viewport width" : "Viewport width"}
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
                  disabled={viewportApplying}
                />
              </label>
              <label htmlFor={heightInputId}>
                <span className="label">Height</span>
                <input
                  id={heightInputId}
                  aria-label={fullscreen ? "Fullscreen viewport height" : "Viewport height"}
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
                  disabled={viewportApplying}
                />
              </label>
            </div>
            <div className="flex items-center justify-between gap-3">
              <p className="text-[11px] text-gray-500">
                {viewportApplying
                  ? selected?.status === "running"
                    ? "Restarting live browser..."
                    : "Saving viewport..."
                  : viewportSaveFailed
                  ? selected?.status === "running"
                    ? "Could not apply viewport"
                    : "Could not save viewport"
                  : viewportSaved
                    ? "Saved"
                    : selected?.status === "running"
                      ? "Restarts live browser to apply"
                      : "Applied when this profile launches"}
              </p>
              <button
                type="button"
                className="btn-primary min-h-11 shrink-0"
                disabled={!selected || viewportApplying}
                onClick={applyViewport}
              >
                {viewportApplying ? "Applying..." : "Apply"}
              </button>
            </div>
          </>
        ) : (
          <p className="mobile-viewport-editor-note">Viewport changes require profile management access.</p>
        )}
      </div>
    );
  };

  const renderLiveViewControls = () => (
    <div className="mobile-live-control-cluster">
      <label className="mobile-live-slider" htmlFor="mobile-pane-size">
        <span className="mobile-live-slider-label">
          <span>Pane</span>
          <output htmlFor="mobile-pane-size" aria-label="Browser pane size" aria-live="polite">
            {panePercent}%
          </output>
        </span>
        <input
          id="mobile-pane-size"
          type="range"
          min={42}
          max={82}
          step={1}
          value={panePercent}
          onChange={(event) => updatePanePercent(Number(event.target.value))}
          aria-label="Browser pane"
          aria-valuetext={`${panePercent}% of the workspace`}
        />
      </label>
      <label className="mobile-live-slider" htmlFor="mobile-browser-zoom">
        <span className="mobile-live-slider-label">
          <span>Zoom</span>
          <output htmlFor="mobile-browser-zoom" aria-label="Visual zoom level" aria-live="polite">
            {browserZoom}%
          </output>
        </span>
        <input
          id="mobile-browser-zoom"
          type="range"
          min={75}
          max={150}
          step={5}
          value={browserZoom}
          onChange={(event) => onBrowserZoomChange(Number(event.target.value))}
          aria-label="Visual zoom"
          aria-valuetext={`${browserZoom}%`}
        />
      </label>
      <button
        type="button"
        className="mobile-live-reset-button"
        onClick={resetLiveViewport}
        aria-label="Reset live view"
        title="Reset live view"
      >
        <RotateCcw className="h-4 w-4" aria-hidden="true" />
        <span className="sr-only">Reset view</span>
      </button>
    </div>
  );

  const renderFullscreenViewControls = () => (
    <div id="mobile-fullscreen-view-controls" className="mobile-fullscreen-tools-panel" aria-label="Fullscreen view controls">
      <label className="mobile-fullscreen-zoom" htmlFor="mobile-fullscreen-pane-size">
        <span className="text-[10px] font-semibold uppercase text-gray-500">Pane</span>
        <input
          id="mobile-fullscreen-pane-size"
          type="range"
          min={42}
          max={82}
          step={1}
          value={panePercent}
          onChange={(event) => updatePanePercent(Number(event.target.value))}
          aria-label="Fullscreen browser pane"
          aria-valuetext={`${panePercent}% of the workspace`}
        />
        <output htmlFor="mobile-fullscreen-pane-size" aria-label="Fullscreen browser pane size" aria-live="polite">
          {panePercent}%
        </output>
      </label>
      <label className="mobile-fullscreen-zoom" htmlFor="mobile-fullscreen-browser-zoom">
        <span className="text-[10px] font-semibold uppercase text-gray-500">Zoom</span>
        <input
          id="mobile-fullscreen-browser-zoom"
          type="range"
          min={75}
          max={150}
          step={5}
          value={browserZoom}
          onChange={(event) => onBrowserZoomChange(Number(event.target.value))}
          aria-label="Fullscreen visual zoom"
          aria-valuetext={`${browserZoom}%`}
        />
        <output htmlFor="mobile-fullscreen-browser-zoom" aria-label="Fullscreen visual zoom level" aria-live="polite">
          {browserZoom}%
        </output>
      </label>
    </div>
  );

  const renderFullscreenControls = () => (
    <>
      <div className="mobile-fullscreen-strip" aria-label="Fullscreen browser controls">
        <button
          type="button"
          className="mobile-fullscreen-action"
          aria-label="Reset fullscreen browser view"
          onClick={() => {
            setFullscreenViewportOpen(false);
            resetLiveViewport();
          }}
        >
          <RotateCcw className="h-4 w-4" aria-hidden="true" />
          <span>{browserZoom}%</span>
        </button>
        <button
          type="button"
          className="mobile-fullscreen-action"
          aria-label="Toggle fullscreen view controls"
          aria-expanded={fullscreenViewOpen}
          aria-controls="mobile-fullscreen-view-controls"
          onClick={() => {
            setFullscreenViewOpen((open) => !open);
            setFullscreenViewportOpen(false);
          }}
        >
          <SlidersHorizontal className="h-4 w-4" aria-hidden="true" />
          <span>View</span>
        </button>
        {canManageProfiles ? (
          <button
            type="button"
            className="mobile-fullscreen-action"
            aria-label="Edit fullscreen browser viewport"
            aria-expanded={fullscreenViewportOpen}
            aria-controls="mobile-fullscreen-viewport-settings"
            onClick={() => {
              setFullscreenViewOpen(false);
              setFullscreenViewportOpen((open) => !open);
            }}
          >
            <MonitorSmartphone className="h-4 w-4" aria-hidden="true" />
            <span>Viewport</span>
          </button>
        ) : null}
        <button
          ref={fullscreenCloseButtonRef}
          type="button"
          className="mobile-fullscreen-action"
          aria-label="Close fullscreen browser"
          onClick={closeFullscreen}
        >
          <Shrink className="h-4 w-4" aria-hidden="true" />
          <span>Exit</span>
        </button>
      </div>

      {fullscreenViewOpen ? renderFullscreenViewControls() : null}
      {canManageProfiles && fullscreenViewportOpen ? renderViewportEditor("fullscreen") : null}
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
    <main className={`mobile-split-root ${compactWorkspace ? "mobile-workspace-collapsed" : ""} bg-surface-0 text-gray-100`}>
      <section
        className={`mobile-live-pane ${isLiveBrowser ? "mobile-live-pane-running" : ""} ${fitLivePaneToBrowser ? "mobile-live-pane-fit" : ""} ${fullscreenOpen ? "mobile-live-pane-fullscreen" : ""}`}
        style={livePaneStyle}
        role={fullscreenOpen ? "dialog" : undefined}
        aria-modal={fullscreenOpen ? true : undefined}
        aria-label={fullscreenOpen ? "Fullscreen browser viewer" : undefined}
      >
        {!fullscreenOpen ? (
          <>
            <div className="mobile-live-header">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  {selected ? <StatusIndicator status={selected.status} size="md" /> : null}
                  <label className="min-w-0 flex-1">
                    <span className="sr-only">Select profile</span>
                    <select
                      value={selectedId ?? ""}
                      onChange={(event) => onSelect(event.target.value)}
                      className="mobile-top-profile-select"
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
                <p className="mt-0.5 truncate text-[11px] uppercase tracking-wide text-gray-500">
                  {liveLabel} · {viewport.width} x {viewport.height}
                  {!canInteract && selected?.status === "running" ? " · View only" : ""}
                </p>
              </div>
              <span className={`mobile-connection-pill ${connectionTone}`} aria-label={`Browser connection ${connectionLabel}`}>
                {connectionLabel}
              </span>
            </div>
          </>
        ) : null}

        <div className={`mobile-browser-wrap ${isLiveBrowser ? "mobile-browser-wrap-live" : "px-3 pb-2"}`}>
          {fullscreenOpen ? renderFullscreenControls() : null}
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

        <div className="mobile-command-dock" aria-label="Browser command dock">
          {isLiveBrowser ? (
            <button
              ref={fullscreenOpenButtonRef}
              type="button"
              onClick={openFullscreen}
              className="mobile-command-button"
              aria-label="Open fullscreen browser"
              title="Fullscreen browser (Ctrl+B)"
            >
              <Expand className="h-4 w-4" aria-hidden="true" />
              <span>Full</span>
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => {
              if (remoteToolsOpen) {
                closeTools();
              } else {
                openTools();
              }
            }}
            className={`mobile-command-button ${remoteToolsOpen ? "mobile-command-button-active" : ""}`}
            aria-label={remoteToolsOpen ? "Close browser tools" : "Open browser tools"}
            aria-expanded={remoteToolsOpen}
            aria-controls="mobile-tools-sheet"
            title="Browser tools (Ctrl+K)"
          >
            <SlidersHorizontal className="h-4 w-4" aria-hidden="true" />
            <span>Tools</span>
          </button>
          <button
            type="button"
            onClick={() => {
              chatAdjustedRef.current = true;
              closeTools();
              setChatCollapsed((collapsed) => !collapsed);
            }}
            className={`mobile-command-button ${!chatCollapsed ? "mobile-command-button-active" : ""}`}
            aria-label={chatCollapsed ? "Expand task chat" : "Collapse task chat"}
            aria-expanded={!chatCollapsed}
            aria-controls="mobile-task-chat-panel"
            title="Toggle chat (Ctrl+J)"
          >
            <MessageSquareText className="h-4 w-4" aria-hidden="true" />
            <span>Chat</span>
          </button>
        </div>

        {remoteToolsOpen ? (
          <div id="mobile-tools-sheet" className="mobile-tools-sheet" aria-label="Browser tools">
            {!toolPanelOpen && canOperate ? (
              <div className="mobile-tools-row mobile-tools-row-primary">
                {selected?.status === "running" ? (
                  <button type="button" onClick={onStop} className="mobile-tool-action mobile-tool-action-danger">
                    <Square className="h-4 w-4" />
                    <span>Stop</span>
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={onLaunch}
                    className="mobile-tool-action mobile-tool-action-primary"
                    disabled={!selected}
                  >
                    <Play className="h-4 w-4" />
                    <span>Launch</span>
                  </button>
                )}
              </div>
            ) : null}

            {!toolPanelOpen ? (
              <section className="mobile-tool-section" aria-label="Pinned browser actions">
                <div className="mobile-tool-section-header">
                  <span>Quick actions</span>
                  <span>
                    {codexHostReady
                      ? "Codex ready"
                      : serverHistoryReady
                        ? "Save only"
                        : "Unavailable"}
                  </span>
                </div>
                <div className="mobile-tools-row mobile-pinned-actions">
                  {pinnedHarnessActions.map((action) => {
                    const available = Boolean(
                      codexHostReady && harnessCapabilities?.browser_actions.includes(action.kind),
                    );
                    return (
                      <button
                        key={action.id}
                        type="button"
                        className="mobile-tool-action"
                        aria-label={`Run ${action.label} with Codex Computer Use`}
                        disabled={!available || harnessPending}
                        title={available ? `${action.label} with Codex Computer Use` : `${action.label} is unavailable in this host`}
                        onClick={() => void runPinnedHarnessAction(action)}
                      >
                        {action.kind === "screenshot" ? <Camera className="h-4 w-4" aria-hidden="true" /> : null}
                        {action.kind === "copy" ? <ClipboardCopy className="h-4 w-4" aria-hidden="true" /> : null}
                        {action.kind === "paste" ? <ClipboardPaste className="h-4 w-4" aria-hidden="true" /> : null}
                        <span>{action.label}</span>
                      </button>
                    );
                  })}
                </div>
              </section>
            ) : null}

            <div id="mobile-remote-browser-tools-portal" className={toolPanelOpen ? "hidden" : undefined} />

            <div className="mobile-tools-row">
              <button
                type="button"
                onClick={() => {
                  setViewportOpen((open) => !open);
                  setGridOpen(false);
                  setAdminOpen(false);
                }}
                className={`mobile-tool-action ${viewportOpen ? "mobile-command-button-active" : ""}`}
                aria-label="Edit browser viewport"
                aria-expanded={viewportOpen}
                aria-controls="mobile-viewport-settings"
              >
                <SlidersHorizontal className="h-4 w-4" aria-hidden="true" />
                <span>View</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  setGridOpen((open) => !open);
                  setViewportOpen(false);
                  setAdminOpen(false);
                }}
                className={`mobile-tool-action ${gridOpen ? "mobile-command-button-active" : ""}`}
                aria-expanded={gridOpen}
                aria-controls="mobile-running-grid"
                aria-label="Toggle grid view"
              >
                <Grid2X2 className="h-4 w-4" aria-hidden="true" />
                <span>Sessions</span>
              </button>
              {canManageProfiles || canManageAccess ? (
                <button
                  type="button"
                  onClick={() => {
                    setAdminOpen((open) => !open);
                    setViewportOpen(false);
                    setGridOpen(false);
                  }}
                  className={`mobile-tool-action ${adminOpen ? "mobile-command-button-active" : ""}`}
                  aria-label="Toggle browser administration"
                  aria-expanded={adminOpen}
                  aria-controls="mobile-admin-tools"
                >
                  <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                  <span>Admin</span>
                </button>
              ) : null}
            </div>

            {viewportOpen ? renderViewportEditor("inline") : null}

            {adminOpen ? (
              <div id="mobile-admin-tools" className="mobile-tools-row mobile-admin-tools" aria-label="Browser administration">
                {canManageProfiles ? (
                  <button type="button" onClick={onNew} className="mobile-tool-action" aria-label="New profile">
                    <Plus className="h-4 w-4" aria-hidden="true" />
                    <span>New</span>
                  </button>
                ) : null}
                {canManageProfiles && selected ? (
                  <button type="button" onClick={onEdit} className="mobile-tool-action" aria-label="Edit selected profile">
                    <Pencil className="h-4 w-4" aria-hidden="true" />
                    <span>Edit</span>
                  </button>
                ) : null}
                {canManageAccess ? (
                  <button type="button" onClick={onAccessControls} className="mobile-tool-action" aria-label="Browser access controls">
                    <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                    <span>Access</span>
                  </button>
                ) : null}
              </div>
            ) : null}

            {gridOpen ? (
              <div id="mobile-running-grid" className="mobile-grid" aria-label="Running browser grid">
                {(runningProfiles.length > 0 ? runningProfiles : profiles.slice(0, 4)).map((profile) => (
                  <button
                    key={profile.id}
                    type="button"
                    onClick={() => onSelect(profile.id)}
                    className={`mobile-grid-tile ${profile.id === selectedId ? "mobile-grid-tile-active" : ""}`}
                  >
                    <span className="mobile-grid-card-main">
                      <StatusIndicator status={profile.status} />
                      <span className="min-w-0">
                        <span className="block truncate text-xs font-semibold text-gray-100">{profile.name}</span>
                        <span className="block truncate text-[11px] text-gray-500">
                          {profile.platform} · {profile.screen_width} x {profile.screen_height}
                        </span>
                      </span>
                    </span>
                    <span className="mobile-grid-card-state">
                      {profile.status === "running" ? "Live" : profile.status}
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

            <p className="mobile-tools-meta">
              {codexHostReady
                ? "Tasks run through the verified Codex Computer Use host; browser credentials stay outside the chat UI."
                : serverHistoryReady
                  ? "Tasks are saved to scoped server history only. Nothing executes until a verified Codex host attaches."
                  : "A verified Codex Computer Use host or the scoped server history is required."}
            </p>

            {authRequired ? (
              <div className="mobile-account-row">
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
          </div>
        ) : null}

        {!chatCollapsed ? (
          <section
            id="mobile-task-chat-panel"
            className="mobile-chat-panel"
            aria-label="Task chat"
          >
            <div className="mobile-chat-header">
              <MessageSquareText className="h-4 w-4 text-accent" aria-hidden="true" />
              <span className="text-sm font-semibold">Task chat</span>
              <span className="ml-auto truncate text-[11px] text-gray-500" aria-live="polite">
                {harnessLabel}
              </span>
              <button
                type="button"
                className="mobile-chat-collapse-button"
                aria-label="Collapse task chat"
                aria-expanded
                onClick={() => {
                  chatAdjustedRef.current = true;
                  setChatCollapsed(true);
                }}
              >
                <ChevronUp className="h-4 w-4 transition-transform" aria-hidden="true" />
              </button>
            </div>
            <div className="mobile-chat-log" aria-label="Chat history">
              {!historyPending && messages.length === 0 ? (
                <div className="mobile-message mobile-message-tool">
                  No saved tasks for this browser yet.
                </div>
              ) : null}
              {messages.map((message) => (
                <div
                  key={message.id}
                  className={`mobile-message mobile-message-${message.role}`}
                >
                  {message.text}
                </div>
              ))}
              {harnessError ? (
                <div className="mobile-message mobile-message-tool" role="alert">
                  {harnessError}
                </div>
              ) : null}
              {harnessNotice ? (
                <div className="mobile-message mobile-message-tool" aria-live="polite">
                  {harnessNotice}
                </div>
              ) : null}
            </div>
          </section>
        ) : null}

        <form className={`mobile-chat-form ${chatCollapsed ? "mobile-chat-form-collapsed" : ""}`} onSubmit={sendMessage}>
          <label className="sr-only" htmlFor="mobile-task-input">
            Browser task
          </label>
          <textarea
            id="mobile-task-input"
            value={draft}
            onChange={updateDraft}
            className="input min-h-9 resize-none overflow-y-auto"
            rows={1}
            placeholder={harnessPlaceholder}
            disabled={harnessPending || !composerReady}
          />
          <div className="mobile-composer-toolbar">
            <span className="sr-only" aria-live="polite">
              {codexHostReady ? "Codex Computer Use" : harnessLabel}
            </span>
            <button type="submit" className="mobile-send-button" aria-label="Run task" disabled={harnessPending || !composerReady || !draft.trim()}>
              <Send className="h-4 w-4" />
            </button>
          </div>
        </form>

      </section>

    </main>
  );
}
