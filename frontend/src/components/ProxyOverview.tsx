import type { ICellRendererParams, ValueGetterParams } from "ag-grid-community";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, Globe2, Loader2, Plus, RefreshCw, Search, Shield, X } from "lucide-react";
import {
  api,
  type Profile,
  type ProfileHarness,
  type ProxyInventoryItem,
} from "../lib/api";
import { UI_STATE, uiStateAttr } from "../lib/uiFlowRegistry";
import { CompactDataGrid } from "./data-grid/CompactDataGrid";

const PROXY_GRID_FALLBACK_QUERY = "(max-width: 767px), (pointer: coarse)";

interface ProxyOverviewProps {
  harness: ProfileHarness;
  projectId: string;
  onProfileCreated: (profile: Profile) => void;
}

function useProxyGridFallback() {
  const [fallback, setFallback] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia(PROXY_GRID_FALLBACK_QUERY).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;
    const query = window.matchMedia(PROXY_GRID_FALLBACK_QUERY);
    const update = () => setFallback(query.matches);
    update();
    query.addEventListener?.("change", update);
    query.addListener?.(update);
    return () => {
      query.removeEventListener?.("change", update);
      query.removeListener?.(update);
    };
  }, []);

  return fallback;
}

function formatDateTime(value: string | null | undefined) {
  if (!value) return "Unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString().slice(0, 16).replace("T", " ");
}

function formatLatency(value: number | null) {
  return value == null ? "Unknown" : `${Math.round(value)} ms`;
}

function formatScore(value: number | null) {
  return value == null ? "Unknown" : String(value);
}

function credentialLabel(item: ProxyInventoryItem) {
  if (item.username_masked) return item.username_masked;
  return item.has_credentials ? "Credentials set" : "No credentials";
}

export function ProxyOverview({ harness, projectId, onProfileCreated }: ProxyOverviewProps) {
  const [items, setItems] = useState<ProxyInventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [quickFilter, setQuickFilter] = useState("");
  const [selectedProxyId, setSelectedProxyId] = useState<string | null>(null);
  const useFallbackCards = useProxyGridFallback();
  const selectedProxy = items.find((item) => item.id === selectedProxyId) ?? null;

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

  const checkProxy = useCallback(async (item: ProxyInventoryItem) => {
    setBusyId(item.id);
    try {
      const checked = await api.checkProxy(item.id);
      setItems((current) => current.map((row) => (row.id === item.id ? checked : row)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Proxy check failed");
    } finally {
      setBusyId(null);
    }
  }, []);

  const createProfile = useCallback(
    async (item: ProxyInventoryItem) => {
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
    },
    [harness, onProfileCreated, projectId],
  );

  const columns = useMemo(
    () => [
      {
        headerName: "State",
        field: "check_state" as const,
        width: 110,
        cellRenderer: ({ data }: ICellRendererParams<ProxyInventoryItem>) =>
          data ? <StatePill state={data.check_state} /> : null,
      },
      { headerName: "Label", field: "label" as const, minWidth: 170, flex: 1.2 },
      {
        headerName: "Country",
        valueGetter: ({ data }: ValueGetterParams<ProxyInventoryItem>) => data?.country_code || "Unknown",
        width: 105,
      },
      {
        headerName: "Credentials",
        valueGetter: ({ data }: ValueGetterParams<ProxyInventoryItem>) => (data ? credentialLabel(data) : ""),
        minWidth: 145,
        flex: 0.9,
      },
      {
        headerName: "Latency",
        valueGetter: ({ data }: ValueGetterParams<ProxyInventoryItem>) => (data ? formatLatency(data.latency_ms) : ""),
        width: 115,
      },
      {
        headerName: "Risk",
        valueGetter: ({ data }: ValueGetterParams<ProxyInventoryItem>) => (data ? formatScore(data.risk_score) : ""),
        width: 90,
      },
      {
        headerName: "Authenticity",
        valueGetter: ({ data }: ValueGetterParams<ProxyInventoryItem>) =>
          data ? formatScore(data.authenticity_score) : "",
        width: 130,
      },
      {
        headerName: "Timezone",
        valueGetter: ({ data }: ValueGetterParams<ProxyInventoryItem>) => data?.timezone_hint || "Unknown",
        minWidth: 145,
        flex: 0.9,
      },
      {
        headerName: "Locale",
        valueGetter: ({ data }: ValueGetterParams<ProxyInventoryItem>) => data?.locale_hint || "Unknown",
        width: 105,
      },
      {
        headerName: "Last checked",
        valueGetter: ({ data }: ValueGetterParams<ProxyInventoryItem>) =>
          data ? formatDateTime(data.last_checked_at || data.updated_at) : "",
        width: 155,
      },
      {
        headerName: "Active",
        valueGetter: ({ data }: ValueGetterParams<ProxyInventoryItem>) => (data?.active ? "Yes" : "No"),
        width: 95,
      },
      {
        headerName: "Actions",
        sortable: false,
        filter: false,
        width: 145,
        cellRenderer: ({ data }: ICellRendererParams<ProxyInventoryItem>) =>
          data ? (
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                className="inline-flex h-7 items-center justify-center rounded border border-border bg-surface-3 px-2 text-[11px] text-gray-200 hover:bg-surface-4 focus:outline-none focus:ring-2 focus:ring-accent/50 disabled:opacity-50"
                disabled={busyId === data.id}
                onClick={(event) => {
                  event.stopPropagation();
                  void checkProxy(data);
                }}
                aria-label={`Check ${data.label}`}
                title="Check"
              >
                <Shield className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                className="inline-flex h-7 items-center justify-center rounded border border-accent/40 bg-accent/20 px-2 text-[11px] text-accent hover:bg-accent/30 focus:outline-none focus:ring-2 focus:ring-accent/50 disabled:opacity-50"
                disabled={busyId === data.id}
                onClick={(event) => {
                  event.stopPropagation();
                  void createProfile(data);
                }}
                aria-label={`Create profile from ${data.label}`}
                title="Auto profile"
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            </div>
          ) : null,
      },
    ],
    [busyId, checkProxy, createProfile],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div
      className="flex h-full w-full max-w-none flex-col gap-3 p-3 lg:p-5"
      data-testid="proxy-overview"
      data-ui-state={uiStateAttr(
        UI_STATE.proxyOverview,
        loading && UI_STATE.proxyOverviewLoading,
        Boolean(error) && UI_STATE.proxyOverviewError,
        !loading && !error && items.length === 0 && UI_STATE.proxyOverviewEmpty,
        !loading && !error && items.length > 0 && UI_STATE.proxyOverviewList,
      )}
    >
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
          <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading proxy pool...
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-border bg-surface-1 px-6 py-16 text-center">
          <Globe2 className="mb-3 h-8 w-8 text-gray-600" />
          <p className="text-sm text-gray-400">No proxies in inventory yet.</p>
          <p className="mt-1 text-xs text-gray-600">Ingest via API or VCVM admin tooling.</p>
        </div>
      ) : useFallbackCards ? (
        <ProxyCards
          items={items}
          busyId={busyId}
          selectedId={selectedProxyId}
          onToggleDetails={(item) => setSelectedProxyId((current) => current === item.id ? null : item.id)}
          onCheck={checkProxy}
          onCreateProfile={createProfile}
        />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          <div className="relative max-w-sm">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-500" />
            <input
              type="text"
              className="input h-8 pl-8 text-xs"
              value={quickFilter}
              onChange={(event) => setQuickFilter(event.target.value)}
              placeholder="Search proxies..."
              aria-label="Search proxies grid"
            />
          </div>
          <div className="grid min-h-0 flex-1 grid-rows-[minmax(12rem,1fr)_auto] gap-2">
            <CompactDataGrid<ProxyInventoryItem>
              ariaLabel="Proxies grid"
              storageKey="proxies"
              columns={columns}
              rows={items}
              quickFilterText={quickFilter}
              selectedId={selectedProxyId}
              onRowClick={(item) => setSelectedProxyId((current) => current === item.id ? null : item.id)}
              testId="proxy-desktop-grid"
            />
            {selectedProxy ? (
              <ProxyDetail
                item={selectedProxy}
                busy={busyId === selectedProxy.id}
                onClose={() => setSelectedProxyId(null)}
                onCheck={() => void checkProxy(selectedProxy)}
                onCreateProfile={() => void createProfile(selectedProxy)}
              />
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

function ProxyCards({
  items,
  busyId,
  selectedId,
  onToggleDetails,
  onCheck,
  onCreateProfile,
}: {
  items: ProxyInventoryItem[];
  busyId: string | null;
  selectedId: string | null;
  onToggleDetails: (item: ProxyInventoryItem) => void;
  onCheck: (item: ProxyInventoryItem) => void;
  onCreateProfile: (item: ProxyInventoryItem) => void;
}) {
  return (
    <div className="space-y-2 overflow-y-auto">
      {items.map((item) => (
        <div key={item.id} className="rounded-xl border border-border bg-surface-1 px-3 py-2.5">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className="inline-flex min-w-0 items-center gap-1 text-left text-sm font-medium text-gray-100"
                  onClick={() => onToggleDetails(item)}
                  aria-label={`${selectedId === item.id ? "Hide" : "Show"} details for ${item.label}`}
                  aria-expanded={selectedId === item.id}
                >
                  <ChevronDown className={`h-3.5 w-3.5 shrink-0 transition-transform ${selectedId === item.id ? "rotate-180" : ""}`} />
                  <span className="truncate">{item.label}</span>
                </button>
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
                {item.authenticity_score != null ? <span>auth {item.authenticity_score}</span> : null}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              <button
                type="button"
                className="btn btn-secondary inline-flex h-9 items-center gap-1 px-2.5 text-xs"
                disabled={busyId === item.id}
                onClick={() => onCheck(item)}
              >
                <Shield className="h-3.5 w-3.5" />
                Check
              </button>
              <button
                type="button"
                className="btn btn-primary inline-flex h-9 items-center gap-1 px-2.5 text-xs"
                disabled={busyId === item.id}
                onClick={() => onCreateProfile(item)}
              >
                <Plus className="h-3.5 w-3.5" />
                Auto profile
              </button>
            </div>
          </div>
          {selectedId === item.id ? (
            <ProxyDetail
              item={item}
              busy={busyId === item.id}
              onClose={() => onToggleDetails(item)}
              onCheck={() => onCheck(item)}
              onCreateProfile={() => onCreateProfile(item)}
            />
          ) : null}
        </div>
      ))}
    </div>
  );
}

function DetailValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[9px] font-semibold uppercase tracking-wide text-gray-500">{label}</dt>
      <dd className="mt-0.5 truncate text-[11px] text-gray-200" title={value}>{value}</dd>
    </div>
  );
}

function ProxyDetail({
  item,
  busy,
  onClose,
  onCheck,
  onCreateProfile,
}: {
  item: ProxyInventoryItem;
  busy: boolean;
  onClose: () => void;
  onCheck: () => void;
  onCreateProfile: () => void;
}) {
  const reachable = item.reachable == null ? "Unknown" : item.reachable ? "Reachable" : "Unreachable";
  return (
    <section
      className="mt-2 max-h-[clamp(10rem,24dvh,17rem)] overflow-auto rounded-lg border border-sky-500/25 bg-[#10141b] p-3"
      role="region"
      aria-label={`Proxy checker details for ${item.label}`}
    >
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-semibold text-gray-100">{item.label}</div>
          <div className="mt-0.5 text-[10px] text-sky-300">VCVM Proxy-Checker · redacted inventory</div>
        </div>
        <button
          type="button"
          className="inline-flex h-7 w-7 items-center justify-center rounded border border-border text-gray-400 hover:bg-surface-3 hover:text-white"
          onClick={onClose}
          aria-label={`Close proxy details for ${item.label}`}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4 xl:grid-cols-6">
        <DetailValue label="Host" value={item.host_masked || "Unknown"} />
        <DetailValue label="Port" value={item.port == null ? "Unknown" : String(item.port)} />
        <DetailValue label="User" value={item.username_masked || "Not set"} />
        <DetailValue label="Credentials" value={item.has_credentials ? "Configured" : "Not set"} />
        <DetailValue label="State" value={item.check_state} />
        <DetailValue label="Reachability" value={reachable} />
        <DetailValue label="Latency" value={formatLatency(item.latency_ms)} />
        <DetailValue label="Risk" value={formatScore(item.risk_score)} />
        <DetailValue label="Authenticity" value={formatScore(item.authenticity_score)} />
        <DetailValue label="Country" value={item.country_code || "Unknown"} />
        <DetailValue label="Timezone" value={item.timezone_hint || "Unknown"} />
        <DetailValue label="Locale" value={item.locale_hint || "Unknown"} />
        <DetailValue label="Last checked" value={formatDateTime(item.last_checked_at)} />
        <DetailValue label="Created" value={formatDateTime(item.created_at)} />
        <DetailValue label="Updated" value={formatDateTime(item.updated_at)} />
        <DetailValue label="Active" value={item.active ? "Yes" : "No"} />
      </dl>
      {item.warnings.length || item.blockers.length ? (
        <div className="mt-3 flex flex-wrap gap-1.5 text-[10px]">
          {item.warnings.map((warning) => (
            <span key={`warning-${warning}`} className="rounded bg-amber-500/15 px-2 py-1 text-amber-200">{warning}</span>
          ))}
          {item.blockers.map((blocker) => (
            <span key={`blocker-${blocker}`} className="rounded bg-red-500/15 px-2 py-1 text-red-200">{blocker}</span>
          ))}
        </div>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          className="btn btn-secondary h-8 text-xs"
          disabled={busy}
          onClick={onCheck}
          aria-label={`Run VCVM check for ${item.label}`}
        >
          <Shield className="mr-1 h-3.5 w-3.5" /> Check now
        </button>
        <button
          type="button"
          className="btn btn-primary h-8 text-xs"
          disabled={busy}
          onClick={onCreateProfile}
          aria-label={`Create profile from proxy details ${item.label}`}
        >
          <Plus className="mr-1 h-3.5 w-3.5" /> Auto profile
        </button>
      </div>
    </section>
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
