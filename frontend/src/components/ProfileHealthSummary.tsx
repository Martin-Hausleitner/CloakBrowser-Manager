import { Activity, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type ProfileHealth,
  type ProfileHealthSourceState,
  type ProfileHealthState,
} from "../lib/api";

interface ProfileHealthSummaryProps {
  profileId: string;
  canRun: boolean;
  running: boolean;
}

const stateTone: Record<ProfileHealthState, string> = {
  pending: "text-amber-300",
  running: "text-cyan-300",
  passed: "text-emerald-300",
  warning: "text-amber-300",
  failed: "text-red-300",
  unavailable: "text-gray-400",
};

const sourceLabel: Record<ProfileHealthSourceState, string> = {
  missing: "Missing",
  measured: "Measured",
  derived: "Derived",
  unavailable: "Unavailable",
  skipped: "Skipped",
};

function humanize(value: string) {
  const text = value.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function score(value: number | null) {
  return value === null ? "Unavailable" : `${value}/100`;
}

export function ProfileHealthSummary({ profileId, canRun, running }: ProfileHealthSummaryProps) {
  const [health, setHealth] = useState<ProfileHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [runningCheck, setRunningCheck] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      setHealth(await api.getProfileHealth(profileId));
      setLoadFailed(false);
    } catch {
      setLoadFailed(true);
    } finally {
      setLoading(false);
    }
  }, [profileId]);

  useEffect(() => {
    setHealth(null);
    setLoading(true);
    setLoadFailed(false);
    void load();
  }, [load]);

  useEffect(() => {
    if (health?.state !== "pending" && health?.state !== "running") return;
    const timer = window.setTimeout(() => void load(), 1500);
    return () => window.clearTimeout(timer);
  }, [health?.state, load]);

  const sources = useMemo(
    () => Object.entries(health?.sources ?? {}).sort(([a], [b]) => a.localeCompare(b)),
    [health?.sources],
  );

  const run = async () => {
    setRunningCheck(true);
    try {
      setHealth(await api.runProfileHealth(profileId));
      setLoadFailed(false);
    } catch {
      setLoadFailed(true);
    } finally {
      setRunningCheck(false);
    }
  };

  const state = health?.state ?? "unavailable";
  const compactLabel = loading ? "Health …" : loadFailed ? "Health unavailable" : `Health ${state}`;

  return (
    <details className="relative">
      <summary
        className={`flex cursor-pointer list-none items-center gap-1 rounded px-1.5 py-1 text-[11px] hover:bg-surface-2 ${stateTone[state]}`}
      >
        <Activity className="h-3 w-3" aria-hidden="true" />
        <span>{compactLabel}</span>
      </summary>
      <div className="absolute left-0 top-full z-40 mt-1 w-72 rounded-md border border-border bg-surface-2 p-3 shadow-xl">
        <div className="mb-3 flex items-start justify-between gap-2">
          <div>
            <p className="text-xs font-semibold text-gray-200">Profile health</p>
            <p className="mt-0.5 text-[10px] text-gray-500">
              Latest redacted runtime observation
            </p>
          </div>
          {canRun ? (
            <button
              type="button"
              onClick={() => void run()}
              disabled={!running || runningCheck}
              className="inline-flex h-8 w-8 items-center justify-center rounded border border-border text-gray-400 hover:bg-surface-3 disabled:cursor-not-allowed disabled:opacity-40"
              aria-label="Run health check"
              title={running ? "Run health check" : "Launch the profile before rerunning"}
            >
              <RefreshCw className={`h-3.5 w-3.5 ${runningCheck ? "animate-spin" : ""}`} />
            </button>
          ) : null}
        </div>

        {health ? (
          <div className="space-y-2 text-[11px]">
            <div className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1">
              <span className="text-gray-500">Outbound IP</span>
              <span className="text-gray-300">{health.outbound_ip_masked ?? "Unavailable"}</span>
              <span className="text-gray-500">Proxy authenticity</span>
              <span className="text-gray-300">{score(health.proxy_authenticity_score)}</span>
              <span className="text-gray-500">Fingerprint consistency</span>
              <span className="text-gray-300">{score(health.fingerprint_consistency_score)}</span>
              <span className="text-gray-500">BrowserScan authenticity</span>
              <span className="text-gray-300">{score(health.browser_scan_score)}</span>
              {health.proxy_latency_ms !== null ? (
                <>
                  <span className="text-gray-500">Proxy latency</span>
                  <span className="text-gray-300">{health.proxy_latency_ms.toFixed(1)} ms</span>
                </>
              ) : null}
            </div>

            {sources.length > 0 ? (
              <div className="border-t border-border pt-2">
                <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-gray-500">Sources</p>
                <div className="space-y-1">
                  {sources.map(([name, sourceState]) => (
                    <div key={name} className="flex items-center justify-between gap-3">
                      <span className="truncate text-gray-500">{humanize(name)}</span>
                      <span className="text-gray-300">{sourceLabel[sourceState]}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {health.warnings.length > 0 || health.blockers.length > 0 ? (
              <div className="flex flex-wrap gap-1 border-t border-border pt-2">
                {health.warnings.map((warning) => (
                  <span key={`warning-${warning}`} className="rounded bg-amber-500/10 px-1.5 py-0.5 text-amber-300">
                    {humanize(warning)}
                  </span>
                ))}
                {health.blockers.map((blocker) => (
                  <span key={`blocker-${blocker}`} className="rounded bg-surface-4 px-1.5 py-0.5 text-gray-400">
                    {humanize(blocker)}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        ) : (
          <p className="text-xs text-gray-500">{loadFailed ? "Health data unavailable" : "Loading health…"}</p>
        )}
      </div>
    </details>
  );
}
