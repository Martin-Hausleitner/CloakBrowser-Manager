import { useEffect, useState } from "react";
import { Activity, Gauge } from "lucide-react";
import { api, type ProfileOpenLinks } from "../lib/api";

interface LiveDevPanelProps {
  profileId: string | null;
  running: boolean;
  connectionStatus?: string | null;
}

/** Compact live developer view driven by `/api/profiles/{id}/open-links`. */
export function LiveDevPanel({ profileId, running, connectionStatus }: LiveDevPanelProps) {
  const [links, setLinks] = useState<ProfileOpenLinks | null>(null);
  const [rttMs, setRttMs] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!profileId || !running) {
      setLinks(null);
      setRttMs(null);
      return;
    }
    let cancelled = false;
    const load = async () => {
      try {
        const started = performance.now();
        // mode=cdp prefers live_url /session/<id>/live as open_url; fields still include VNC.
        const next = await api.getProfileOpenLinks(profileId, "local", "cdp");
        if (cancelled) return;
        setLinks(next);
        setRttMs(Math.round(performance.now() - started));
        setError(null);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "metrics unavailable");
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [profileId, running]);

  if (!profileId || !running) return null;

  const cdpUrl = links?.live_url || links?.cdp_fullscreen_url || null;
  const vncUrl = links?.vnc_fullscreen_url || null;

  return (
    <div
      className="flex flex-wrap items-center gap-2 border-b border-border bg-surface-1/80 px-3 py-1.5 text-[10px] text-gray-400"
      aria-label="Live developer view"
    >
      <span className="inline-flex items-center gap-1 text-cyan-300">
        <Activity className="h-3 w-3" />
        Live Dev
      </span>
      <span className="rounded bg-surface-3 px-1.5 py-0.5 uppercase tracking-wide">
        {connectionStatus || "connected"}
      </span>
      <span className="inline-flex items-center gap-1">
        <Gauge className="h-3 w-3" />
        API {rttMs != null ? `${rttMs} ms` : "…"}
      </span>
      {cdpUrl ? (
        <a
          className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-emerald-300 hover:bg-emerald-500/25"
          href={cdpUrl}
          target="_blank"
          rel="noreferrer"
          title="CDP-direct fullscreen via open-links live_url"
        >
          CDP fullscreen
        </a>
      ) : null}
      {vncUrl ? (
        <a
          className="rounded bg-sky-500/15 px-1.5 py-0.5 text-sky-300 hover:bg-sky-500/25"
          href={vncUrl}
          title="VNC fullscreen via open-links vnc_fullscreen_url"
        >
          VNC fullscreen
        </a>
      ) : null}
      {error ? <span className="text-amber-400">{error}</span> : null}
    </div>
  );
}
