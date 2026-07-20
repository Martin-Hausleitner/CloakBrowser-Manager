import { useCallback, useEffect, useRef, useState } from "react";
import { ClipboardCopy, Code2, Maximize2, Minimize2 } from "lucide-react";
import { api } from "../lib/api";

interface ProfileViewerProps {
  profileId: string;
  cdpUrl: string | null;
  clipboardSync: boolean;
  layoutMode?: "inline" | "fullscreen";
  onDisconnect: () => void;
}

// X11 keysym for V key (Ctrl is already held in VNC by the time we intercept)
const XK_v = 0x0076;
const RECONNECT_DELAYS_MS = [500, 1000, 2000, 5000, 10000] as const;

type ConnectionStatus = "connecting" | "connected" | "reconnecting" | "failed";

function supportsClipboardSync() {
  return (
    typeof navigator !== "undefined" &&
    typeof navigator.clipboard?.readText === "function" &&
    typeof navigator.clipboard?.writeText === "function"
  );
}

function shouldPollClipboard() {
  return document.visibilityState !== "hidden";
}

export function ProfileViewer({
  profileId,
  cdpUrl,
  clipboardSync: initialClipboardSync,
  layoutMode = "inline",
  onDisconnect,
}: ProfileViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<any>(null);
  const [connected, setConnected] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [clipboardSupported] = useState(supportsClipboardSync);
  const [clipboardSync, setClipboardSync] = useState(
    () => initialClipboardSync && supportsClipboardSync(),
  );
  const [cdpCopied, setCdpCopied] = useState(false);
  const [pastePanelOpen, setPastePanelOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [pasteError, setPasteError] = useState<string | null>(null);
  const [pasting, setPasting] = useState(false);

  const sendRemotePasteKeystroke = useCallback(() => {
    const rfb = rfbRef.current;
    if (!rfb) return false;

    try {
      rfb.sendKey(0xffe3, "ControlLeft", true);
      rfb.sendKey(XK_v, "KeyV", true);
      rfb.sendKey(XK_v, "KeyV", false);
      rfb.sendKey(0xffe3, "ControlLeft", false);
      return true;
    } catch (err) {
      console.warn("[clipboard] remote paste keystroke failed:", err);
      return false;
    }
  }, []);

  const sendTextToRemoteClipboard = useCallback(
    async (text: string) => {
      if (!rfbRef.current || !text) return false;

      try {
        await api.setClipboard(profileId, text);

        // Send the entire Ctrl+V sequence. Clipboard reads and API calls are
        // asynchronous, so we cannot rely on the original key still being held.
        return sendRemotePasteKeystroke();
      } catch (err) {
        console.warn("[clipboard] remote paste failed:", err);
        return false;
      }
    },
    [profileId, sendRemotePasteKeystroke],
  );

  const submitManualPaste = useCallback(async () => {
    if (!pasteText) {
      setPasteError("Paste or type text before sending it to the remote browser.");
      return;
    }

    setPasting(true);
    setPasteError(null);
    const pasted = await sendTextToRemoteClipboard(pasteText);
    setPasting(false);

    if (!pasted) {
      setPasteError("The remote browser is not ready. Reconnect and try again.");
      return;
    }

    setPasteText("");
    setPastePanelOpen(false);
  }, [pasteText, sendTextToRemoteClipboard]);

  useEffect(() => {
    let rfb: any = null;
    let cancelled = false;
    let connecting = false;
    let isConnected = false;
    let terminal = false;
    let reconnectAttempts = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const clearReconnectTimer = () => {
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    };

    const failConnection = (message: string, notifyParent: boolean) => {
      terminal = true;
      clearReconnectTimer();
      isConnected = false;
      setConnected(false);
      setConnectionStatus("failed");
      setError(message);
      if (notifyParent) onDisconnect();
    };

    const scheduleReconnect = () => {
      if (cancelled || terminal) return;
      clearReconnectTimer();
      isConnected = false;
      setConnected(false);
      setConnectionStatus("reconnecting");

      const delay = RECONNECT_DELAYS_MS[reconnectAttempts];
      if (delay === undefined) {
        failConnection("Connection lost after repeated reconnect attempts.", true);
        return;
      }

      reconnectAttempts += 1;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        void connect();
      }, delay);
    };

    async function connect() {
      if (cancelled || terminal || connecting) return;
      connecting = true;
      clearReconnectTimer();
      setError(null);
      setConnectionStatus(reconnectAttempts > 0 ? "reconnecting" : "connecting");

      try {
        // Import noVNC dynamically
        const { default: RFB } = await import("@novnc/novnc/core/rfb.js");

        if (cancelled) return;
        if (!containerRef.current) throw new Error("VNC container is unavailable");

        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/api/profiles/${profileId}/vnc`;

        const instance = new RFB(containerRef.current!, wsUrl, {
          wsProtocols: ["binary"],
        });
        rfb = instance;
        rfbRef.current = instance;

        instance.scaleViewport = true;
        instance.resizeSession = false;
        instance.showDotCursor = true;

        instance.addEventListener("connect", () => {
          if (cancelled || rfbRef.current !== instance) return;
          reconnectAttempts = 0;
          isConnected = true;
          setConnected(true);
          setConnectionStatus("connected");
          setError(null);
        });

        instance.addEventListener("disconnect", () => {
          if (cancelled || terminal || rfbRef.current !== instance) return;
          scheduleReconnect();
        });

        instance.addEventListener("securityfailure", (e: any) => {
          if (cancelled || rfbRef.current !== instance) return;
          failConnection(`Security failure: ${e.detail?.reason ?? "Unknown reason"}`, true);
        });
      } catch (err) {
        if (!cancelled) {
          console.warn("[vnc] connect failed:", err);
          scheduleReconnect();
        }
      } finally {
        connecting = false;
      }
    }

    const reconnectNow = () => {
      if (cancelled || terminal || isConnected || connecting) return;
      clearReconnectTimer();
      void connect();
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") reconnectNow();
    };

    connect();
    window.addEventListener("online", reconnectNow);
    window.addEventListener("pageshow", reconnectNow);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      cancelled = true;
      clearReconnectTimer();
      window.removeEventListener("online", reconnectNow);
      window.removeEventListener("pageshow", reconnectNow);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      if (rfb) {
        try {
          rfb.disconnect();
        } catch (err) {
          console.debug("[vnc] disconnect cleanup failed:", err);
        }
      }
      rfbRef.current = null;
    };
  }, [profileId, onDisconnect]);

  // Host→VNC: intercept Ctrl+V/Cmd+V at keydown (capture phase)
  // Must fire BEFORE noVNC's canvas listener to prevent the race condition
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !clipboardSync || !connected) return;

    const handleKeyDown = async (e: KeyboardEvent) => {
      const isPaste =
        e.key === "v" && (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey;
      if (!isPaste) return;

      // Block noVNC from sending the keystroke before clipboard is updated
      e.stopPropagation();
      e.preventDefault();

      try {
        const text = await navigator.clipboard.readText();
        if (!text) return;
        const pasted = await sendTextToRemoteClipboard(text);
        if (!pasted) setClipboardSync(false);
      } catch (err) {
        console.warn("[clipboard] error:", err);
        setClipboardSync(false);
        // Preserve the user's Ctrl+V intent even when browser clipboard
        // permission is denied. The remote browser receives its native paste
        // shortcut, using the clipboard it already has available.
        sendRemotePasteKeystroke();
      }
    };

    // capture: true ensures we fire before noVNC's canvas listener
    container.addEventListener("keydown", handleKeyDown, true);
    return () => container.removeEventListener("keydown", handleKeyDown, true);
  }, [clipboardSync, connected, sendRemotePasteKeystroke, sendTextToRemoteClipboard]);

  // VNC→Host: listen for noVNC "clipboard" event (fired when proxy converts
  // KasmVNC BinaryClipboard type 180 → standard ServerCutText type 3)
  useEffect(() => {
    const rfb = rfbRef.current;
    if (!rfb || !clipboardSync || !connected) return;

    const handleClipboard = (e: any) => {
      const text = e.detail?.text;
      if (text) {
        navigator.clipboard.writeText(text).catch((err) => {
          console.warn("[clipboard] writeText failed:", err);
        });
      }
    };

    rfb.addEventListener("clipboard", handleClipboard);
    return () => {
      rfb.removeEventListener("clipboard", handleClipboard);
    };
  }, [clipboardSync, connected]);

  // VNC→Host polling: Chrome doesn't write to X11 clipboard under KasmVNC,
  // so type 180 events won't fire for Chrome copies. Poll via Playwright CDP.
  useEffect(() => {
    if (!clipboardSync || !connected) return;

    let cancelled = false;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    let lastText = "";

    const clearPollTimer = () => {
      if (pollTimer) {
        clearTimeout(pollTimer);
        pollTimer = null;
      }
    };

    const schedulePoll = () => {
      clearPollTimer();
      if (!cancelled && shouldPollClipboard()) {
        pollTimer = setTimeout(poll, 2000);
      }
    };

    const poll = async () => {
      if (cancelled) return;
      if (!shouldPollClipboard()) {
        schedulePoll();
        return;
      }

      try {
        const { text } = await api.getClipboard(profileId);
        if (text && text !== lastText) {
          lastText = text;
          await navigator.clipboard.writeText(text).catch((err) =>
            console.warn("[clipboard] poll writeText failed:", err)
          );
        }
      } catch (err) {
        console.warn("[clipboard] poll error, stopping:", err);
        cancelled = true;
        return;
      }
      schedulePoll();
    };

    const handleVisibilityChange = () => schedulePoll();
    window.addEventListener("pageshow", handleVisibilityChange);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    schedulePoll();

    return () => {
      cancelled = true;
      clearPollTimer();
      window.removeEventListener("pageshow", handleVisibilityChange);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [profileId, clipboardSync, connected]);

  const toggleFullscreen = () => {
    if (!containerRef.current) return;
    if (!document.fullscreenElement) {
      containerRef.current.requestFullscreen();
      setFullscreen(true);
    } else {
      document.exitFullscreen();
      setFullscreen(false);
    }
  };

  useEffect(() => {
    const handleFsChange = () => {
      setFullscreen(!!document.fullscreenElement);
    };
    document.addEventListener("fullscreenchange", handleFsChange);
    return () => document.removeEventListener("fullscreenchange", handleFsChange);
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const handleWheel = (e: WheelEvent) => {
      e.preventDefault();
    };

    container.addEventListener("wheel", handleWheel, { passive: false });
    return () => container.removeEventListener("wheel", handleWheel);
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;

    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    const refreshViewport = () => {
      refreshTimer = null;
      const rfb = rfbRef.current;
      if (!rfb) return;

      // noVNC 1.4 can retain the fullscreen scale when its screen returns
      // to the original client width. Re-applying the public scale option
      // fixes the geometry; repainting from its backbuffer prevents a blank
      // visible canvas until the next remote framebuffer update arrives.
      rfb.scaleViewport = true;
      const display = rfb._display;
      const bounds = container.getBoundingClientRect();
      if (
        bounds.width > 0 &&
        bounds.height > 0 &&
        typeof display?.autoscale === "function"
      ) {
        // RFB's built-in ResizeObserver can miss a CSS-only fullscreen move
        // because noVNC caches the previous client size. Use the same pinned
        // Display.autoscale implementation with the measured host bounds.
        display.autoscale(bounds.width, bounds.height);
      }
      if (
        display?.width > 0 &&
        display?.height > 0 &&
        typeof display._damage === "function" &&
        typeof display.flip === "function"
      ) {
        display._damage(0, 0, display.width, display.height);
        display.flip();
      }
    };
    const queueViewportRefresh = () => {
      if (refreshTimer !== null) clearTimeout(refreshTimer);
      refreshTimer = setTimeout(refreshViewport, 0);
    };
    const observer = new ResizeObserver(queueViewportRefresh);

    observer.observe(container);
    queueViewportRefresh();
    return () => {
      observer.disconnect();
      if (refreshTimer !== null) clearTimeout(refreshTimer);
    };
  }, [layoutMode, profileId]);

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-red-400 text-sm mb-2">Connection failed</p>
          <p className="text-gray-500 text-xs">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="relative h-full flex flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-surface-1 border-b border-border">
        <div className="flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-400" : "bg-yellow-400 animate-pulse"}`} />
          <span className="text-xs text-gray-400">
            {connectionStatus === "reconnecting"
              ? "Reconnecting..."
              : connected
                ? "Connected"
                : "Connecting..."}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {cdpUrl && (
            <button
              type="button"
              onClick={() => {
                const base = `${window.location.protocol}//${window.location.host}${cdpUrl}`;
                navigator.clipboard?.writeText(base).then(() => {
                  setCdpCopied(true);
                  setTimeout(() => setCdpCopied(false), 2000);
                }).catch((err) => console.warn("[cdp] copy failed:", err));
              }}
              className={`p-1 ${cdpCopied ? "text-emerald-400" : "text-gray-500 hover:text-gray-300"}`}
              title={cdpCopied ? "Copied!" : "Copy CDP endpoint URL"}
            >
              <Code2 className="h-3.5 w-3.5" />
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              setPasteError(null);
              setPastePanelOpen(true);
            }}
            className="rounded px-2 py-1 text-xs text-gray-300 hover:bg-surface-2 disabled:cursor-not-allowed disabled:text-gray-600"
            title="Paste text from this device into the remote browser"
            aria-label="Paste text into remote browser"
            disabled={!connected}
          >
            Paste
          </button>
          <button
            type="button"
            onClick={() => setClipboardSync(!clipboardSync)}
            className={`p-1 ${clipboardSync ? "text-accent" : "text-gray-500 hover:text-gray-300"}`}
            aria-label={clipboardSync ? "Disable clipboard sync" : "Enable clipboard sync"}
            title={
              clipboardSupported
                ? clipboardSync
                  ? "Disable clipboard sync"
                  : "Enable clipboard sync"
                : "Clipboard sync is unavailable in this browser"
            }
            disabled={!connected || !clipboardSupported}
          >
            <ClipboardCopy className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={toggleFullscreen}
            className="text-gray-500 hover:text-gray-300 p-1"
            title={fullscreen ? "Exit fullscreen" : "Fullscreen"}
          >
            {fullscreen ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          </button>
        </div>
      </div>

      {pastePanelOpen && (
        <form
          className="border-b border-border bg-surface-1 px-3 py-2"
          onSubmit={(event) => {
            event.preventDefault();
            void submitManualPaste();
          }}
        >
          <label htmlFor={`remote-paste-${profileId}`} className="block text-xs text-gray-300">
            Paste text into the remote browser
          </label>
          <textarea
            id={`remote-paste-${profileId}`}
            value={pasteText}
            onChange={(event) => setPasteText(event.target.value)}
            autoFocus
            rows={3}
            placeholder="Paste or type text here"
            className="mt-1 w-full resize-y rounded border border-border bg-surface-2 px-2 py-1.5 text-sm text-gray-100 outline-none placeholder:text-gray-500 focus:border-accent"
          />
          {pasteError && (
            <p role="alert" className="mt-1 text-xs text-red-400">
              {pasteError}
            </p>
          )}
          <div className="mt-2 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setPastePanelOpen(false);
                setPasteError(null);
              }}
              className="min-h-11 rounded px-3 py-1 text-xs text-gray-400 hover:bg-surface-2"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="min-h-11 rounded bg-accent px-3 py-1 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
              aria-label="Send pasted text to remote browser"
              disabled={!pasteText || pasting}
            >
              {pasting ? "Pasting..." : "Paste"}
            </button>
          </div>
        </form>
      )}

      {/* VNC canvas container */}
      <div
        ref={containerRef}
        data-vnc-layout={layoutMode}
        className="flex-1 bg-black overflow-hidden"
        style={{ minHeight: 0 }}
      />
    </div>
  );
}
