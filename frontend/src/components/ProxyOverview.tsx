import { useCallback, useEffect, useState } from "react";
import { Globe2, Loader2, Plus, RefreshCw, Shield } from "lucide-react";
import {
  api,
  type Profile,
  type ProfileHarness,
  type ProxyInventoryItem,
} from "../lib/api";

interface ProxyOverviewProps {
  harness: ProfileHarness;
  projectId: string;
  onProfileCreated: (profile: Profile) => void;
}

export function ProxyOverview({ harness, projectId, onProfileCreated }: ProxyOverviewProps) {
  const [items, setItems] = useState<ProxyInventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await api.listProxies());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load proxies");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col gap-4 p-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-100">Proxies</h2>
          <p className="mt-1 text-sm text-gray-500">
            VCVM inventory with Proxy-Checker scores. Credentials stay on the server.
          </p>
        </div>
        <button
          type="button"
          className="btn btn-secondary inline-flex items-center gap-1.5 text-xs"
          onClick={() => void refresh()}
          disabled={loading}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {error ? (
        <div className="rounded-md border border-red-600/30 bg-red-600/10 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      ) : null}

      {loading ? (
        <div className="flex flex-1 items-center justify-center text-sm text-gray-500">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading proxy pool…
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-border bg-surface-1 px-6 py-16 text-center">
          <Globe2 className="mb-3 h-8 w-8 text-gray-600" />
          <p className="text-sm text-gray-400">No proxies in inventory yet.</p>
          <p className="mt-1 text-xs text-gray-600">Ingest via API or VCVM admin tooling.</p>
        </div>
      ) : (
        <div className="space-y-2 overflow-y-auto">
          {items.map((item) => (
            <div
              key={item.id}
              className="rounded-xl border border-border bg-surface-1 px-4 py-3"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-medium text-gray-100">{item.label}</span>
                    <StatePill state={item.check_state} />
                    {item.country_code ? (
                      <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[10px] uppercase text-gray-400">
                        {item.country_code}
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-gray-500">
                    {item.username_masked ? <span>user {item.username_masked}</span> : null}
                    {item.timezone_hint ? <span>{item.timezone_hint}</span> : null}
                    {item.locale_hint ? <span>{item.locale_hint}</span> : null}
                    {item.latency_ms != null ? <span>{Math.round(item.latency_ms)} ms</span> : null}
                    {item.risk_score != null ? <span>risk {item.risk_score}</span> : null}
                    {item.authenticity_score != null ? (
                      <span>auth {item.authenticity_score}</span>
                    ) : null}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <button
                    type="button"
                    className="btn btn-secondary inline-flex h-9 items-center gap-1 px-2.5 text-xs"
                    disabled={busyId === item.id}
                    onClick={async () => {
                      setBusyId(item.id);
                      try {
                        const checked = await api.checkProxy(item.id);
                        setItems((current) =>
                          current.map((row) => (row.id === item.id ? checked : row)),
                        );
                      } catch (err) {
                        setError(err instanceof Error ? err.message : "Proxy check failed");
                      } finally {
                        setBusyId(null);
                      }
                    }}
                  >
                    <Shield className="h-3.5 w-3.5" />
                    Check
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary inline-flex h-9 items-center gap-1 px-2.5 text-xs"
                    disabled={busyId === item.id}
                    onClick={async () => {
                      setBusyId(item.id);
                      try {
                        const profile = await api.createProfileFromProxy(item.id, {
                          harness,
                          project_id: projectId || "proxied",
                          launch: false,
                        });
                        onProfileCreated(profile);
                      } catch (err) {
                        setError(err instanceof Error ? err.message : "Profile create failed");
                      } finally {
                        setBusyId(null);
                      }
                    }}
                  >
                    <Plus className="h-3.5 w-3.5" />
                    Auto profile
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StatePill({ state }: { state: ProxyInventoryItem["check_state"] }) {
  const styles: Record<ProxyInventoryItem["check_state"], string> = {
    missing: "bg-surface-3 text-gray-400",
    passed: "bg-emerald-500/15 text-emerald-300",
    warning: "bg-amber-500/15 text-amber-300",
    failed: "bg-red-500/15 text-red-300",
    unavailable: "bg-surface-3 text-gray-500",
  };
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${styles[state]}`}>
      {state}
    </span>
  );
}
