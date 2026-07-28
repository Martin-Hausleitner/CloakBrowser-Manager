import { useEffect, useState } from "react";
import { Activity, ChevronDown, Gauge } from "lucide-react";
import { api, type LiveMetrics, type ProfileHealth, type ProfileOpenLinks } from "../lib/api";
import { UI_STATE } from "../lib/uiFlowRegistry";

interface LiveDevPanelProps {
  profileId: string | null;
  running: boolean;
  connectionStatus?: string | null;
  variant?: "desktop" | "mobile";
}

function roundedMetric(value: number | null | undefined, suffix = "") {
  return value == null ? "—" : `${Math.round(value)}${suffix}`;
}

/** Compact live telemetry driven only by measured Manager APIs. */
export function LiveDevPanel({
  profileId,
  running,
  connectionStatus,
  variant = "desktop",
}: LiveDevPanelProps) {
  const [links, setLinks] = useState<ProfileOpenLinks | null>(null);
  const [apiRttMs, setApiRttMs] = useState<number | null>(null);
  const [metrics, setMetrics] = useState<LiveMetrics | null>(null);
  const [health, setHealth] = useState<ProfileHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [linksError, setLinksError] = useState(false);
  const [healthError, setHealthError] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!profileId || !running) {
      setLinks(null);
      setApiRttMs(null);
      setMetrics(null);
      setHealth(null);
      setError(null);
      setLinksError(false);
      setHealthError(false);
      setExpanded(false);
      return;
    }

    let cancelled = false;
    const loadContext = async () => {
      const [linksResult, healthResult] = await Promise.allSettled([
        api.getProfileOpenLinks(profileId, "local", "cdp"),
        api.getProfileHealth(profileId),
      ]);
      if (cancelled) return;
      setLinks(linksResult.status === "fulfilled" ? linksResult.value : null);
      setLinksError(linksResult.status === "rejected");
      setHealth(healthResult.status === "fulfilled" ? healthResult.value : null);
      setHealthError(healthResult.status === "rejected");
    };
    const loadMetrics = async () => {
      const started = performance.now();
      try {
        const nextMetrics = await api.getLiveMetrics(profileId);
        if (cancelled) return;
        setApiRttMs(Math.round(performance.now() - started));
        setMetrics(nextMetrics);
        setError(null);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "metrics unavailable");
      }
    };

    void loadContext();
    void loadMetrics();
    const metricsTimer = window.setInterval(() => void loadMetrics(), 2000);
    const contextTimer = window.setInterval(() => void loadContext(), 10000);
    return () => {
      cancelled = true;
      window.clearInterval(metricsTimer);
      window.clearInterval(contextTimer);
    };
  }, [profileId, running]);

  if (!profileId || !running) return null;

  const cdpUrl = links?.live_url || links?.cdp_fullscreen_url || null;
  const vncUrl = links?.vnc_fullscreen_url || null;
  const streamFps = metrics?.fps ?? null;
  const streamRtt = metrics?.rtt_ms ?? null;
  const connectionState = metrics?.connection_state || connectionStatus || "unknown";
  const proxyLatency = health?.proxy_latency_ms ?? null;
  const proxyAuthenticitySource = health?.sources.proxy_authenticity;
  const proxyAuthenticityLabel = healthError || proxyAuthenticitySource === "unavailable"
    ? "Proxy auth unavailable"
    : health?.proxy_authenticity_score == null
      ? "Proxy auth —"
      : proxyAuthenticitySource === "measured"
        ? `Proxy auth ${roundedMetric(health.proxy_authenticity_score)}`
        : `Proxy auth ${roundedMetric(health.proxy_authenticity_score)} · ${proxyAuthenticitySource || "source unknown"}`;

  if (variant === "mobile") {
    return (
      <div
        className="border-b border-border bg-[#111113]/95 px-2 py-0.5 text-[10px] text-gray-400"
        aria-label="Live telemetry"
        data-ui-state={UI_STATE.mobileLiveMetrics}
      >
        <button
          type="button"
          className="flex min-h-11 w-full items-center gap-2 text-left"
          aria-label={expanded ? "Hide live metrics" : "Show live metrics"}
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          <span className="inline-flex items-center gap-1 font-medium text-cyan-300">
            <Activity className="h-3 w-3" aria-hidden="true" />
            Live
          </span>
          <span className="rounded bg-surface-3 px-1.5 py-0.5 uppercase tracking-wide text-gray-300">
            {connectionState}
          </span>
          <span>{roundedMetric(streamFps, " fps")}</span>
          <span>RTT {roundedMetric(streamRtt, " ms")}</span>
          <span className="min-w-0 truncate">Proxy {roundedMetric(proxyLatency, " ms")}</span>
          <ChevronDown
            className={`ml-auto h-3.5 w-3.5 shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`}
            aria-hidden="true"
          />
        </button>
        {expanded ? (
          <div
            className="grid grid-cols-3 gap-x-3 gap-y-1 border-t border-border/70 px-1 py-2 text-[10px] text-gray-400"
            role="region"
            aria-label="Live metrics details"
          >
            <span>API {roundedMetric(apiRttMs, " ms")}</span>
            <span>Frames {roundedMetric(metrics?.frames_received)}</span>
            <span>Reconnects {roundedMetric(metrics?.reconnect_count)}</span>
            <span>Dropped {roundedMetric(metrics?.dropped_frames)}</span>
            <span>FP {roundedMetric(health?.fingerprint_consistency_score)}</span>
            <span>Scan {roundedMetric(health?.browser_scan_score)}</span>
            <span>{proxyAuthenticityLabel}</span>
            {linksError ? <span className="col-span-3 text-amber-400">Links unavailable</span> : null}
            {healthError ? <span className="col-span-3 text-amber-400">Health unavailable</span> : null}
            {error ? <span className="col-span-3 text-amber-400">{error}</span> : null}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div
      className="flex flex-wrap items-center gap-2 border-b border-border bg-surface-1/80 px-3 py-1.5 text-[10px] text-gray-400"
      aria-label="Live developer view"
      data-ui-state={UI_STATE.agentLiveMetrics}
    >
      <span className="inline-flex items-center gap-1 text-cyan-300">
        <Activity className="h-3 w-3" aria-hidden="true" />
        Live Dev
      </span>
      <span className="rounded bg-surface-3 px-1.5 py-0.5 uppercase tracking-wide">
        {connectionState}
      </span>
      <span className="inline-flex items-center gap-1">
        <Gauge className="h-3 w-3" aria-hidden="true" />
        API {roundedMetric(apiRttMs, " ms")}
      </span>
      <span title="CDP screencast FPS from /live-metrics">
        CDP {roundedMetric(streamFps, " fps")}
      </span>
      <span title="CDP RTT from /live-metrics">RTT {roundedMetric(streamRtt, " ms")}</span>
      <span title="Measured proxy latency from profile health">Proxy {roundedMetric(proxyLatency, " ms")}</span>
      <span title="Fingerprint consistency score">FP {roundedMetric(health?.fingerprint_consistency_score)}</span>
      <span title="Browser scan score">Scan {roundedMetric(health?.browser_scan_score)}</span>
      {linksError ? <span className="text-amber-400">Links unavailable</span> : null}
      {healthError ? <span className="text-amber-400">Health unavailable</span> : null}
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
