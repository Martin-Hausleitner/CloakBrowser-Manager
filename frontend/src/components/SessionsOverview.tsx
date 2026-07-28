import type { ICellRendererParams, ValueGetterParams } from "ag-grid-community";
import { ChevronDown, ExternalLink, History, Loader2, Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type Profile, type TaskHarnessSession } from "../lib/api";
import { CompactDataGrid } from "./data-grid/CompactDataGrid";

const SESSION_GRID_FALLBACK_QUERY = "(max-width: 767px), (pointer: coarse)";
const SESSION_LIMIT_PER_PROFILE = 8;
export const MAX_SESSION_PROFILE_CALLS = 12;

type SessionRow = {
  id: string;
  sessionId: string;
  profileId: string;
  profileName: string;
  title: string;
  projectId: string;
  workflowState: TaskHarnessSession["workflow_state"];
  status: TaskHarnessSession["status"];
  retentionClass: TaskHarnessSession["retention_class"];
  activityAt: string;
};

interface SessionsOverviewProps {
  profiles: Profile[];
  selectedId: string | null;
  onSelectProfile: (profileId: string) => void;
}

function useSessionsGridFallback() {
  const [fallback, setFallback] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia(SESSION_GRID_FALLBACK_QUERY).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;
    const query = window.matchMedia(SESSION_GRID_FALLBACK_QUERY);
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

function formatActivity(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString().slice(0, 16).replace("T", " ");
}

function sessionTitle(session: TaskHarnessSession) {
  return session.title?.trim() || "Untitled session";
}

function sessionRowsForProfile(profile: Profile, sessions: TaskHarnessSession[]): SessionRow[] {
  return sessions.map((session) => ({
    id: `${profile.id}:${session.id}`,
    sessionId: session.id,
    profileId: profile.id,
    profileName: profile.name,
    title: sessionTitle(session),
    projectId: session.project_id || profile.project_id || "default",
    workflowState: session.workflow_state,
    status: session.status,
    retentionClass: session.retention_class,
    activityAt: session.activity_at,
  }));
}

function sortSessionRows(rows: SessionRow[]) {
  return [...rows].sort((a, b) => b.activityAt.localeCompare(a.activityAt));
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Failed to load sessions";
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

export function SessionsOverview({ profiles, onSelectProfile }: SessionsOverviewProps) {
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [errors, setErrors] = useState<string[]>([]);
  const [quickFilter, setQuickFilter] = useState("");
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const useFallbackCards = useSessionsGridFallback();

  const visibleProfiles = useMemo(
    () => profiles.slice(0, MAX_SESSION_PROFILE_CALLS),
    [profiles],
  );

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    setLoading(true);
    setErrors([]);

    void Promise.allSettled(
      visibleProfiles.map(async (profile) => {
        const sessions = await api.listTaskSessions(profile.id, {
          limit: SESSION_LIMIT_PER_PROFILE,
          signal: controller.signal,
        });
        return sessionRowsForProfile(profile, sessions);
      }),
    ).then((settled) => {
      if (!active || controller.signal.aborted) return;

      const nextRows: SessionRow[] = [];
      const nextErrors: string[] = [];
      settled.forEach((result, index) => {
        const profile = visibleProfiles[index];
        if (result.status === "fulfilled") {
          nextRows.push(...result.value);
        } else if (profile && !isAbortError(result.reason)) {
          nextErrors.push(`${profile.name}: ${errorMessage(result.reason)}`);
        }
      });
      setRows(sortSessionRows(nextRows));
      setErrors(nextErrors);
      setLoading(false);
    });

    return () => {
      active = false;
      controller.abort();
    };
  }, [visibleProfiles]);

  const columns = useMemo(
    () => [
      {
        headerName: "Profile",
        field: "profileName" as const,
        minWidth: 170,
        flex: 1.1,
        cellRenderer: ({ data, value }: ICellRendererParams<SessionRow, string>) =>
          data ? <span className="block truncate font-medium text-gray-100">{value}</span> : null,
      },
      { headerName: "Title", field: "title" as const, minWidth: 210, flex: 1.5 },
      { headerName: "Project", field: "projectId" as const, minWidth: 120, flex: 0.85 },
      { headerName: "Workflow", field: "workflowState" as const, width: 115 },
      { headerName: "Status", field: "status" as const, width: 105 },
      { headerName: "Retention", field: "retentionClass" as const, width: 120 },
      {
        headerName: "Activity",
        valueGetter: ({ data }: ValueGetterParams<SessionRow>) => (data ? formatActivity(data.activityAt) : ""),
        width: 155,
      },
    ],
    [],
  );

  const selectedRow = rows.find((row) => row.id === selectedSessionId) ?? null;

  return (
    <div className="flex h-full w-full max-w-none flex-col gap-3 p-3 lg:p-5" data-testid="sessions-overview">
      <div>
        <h2 className="text-lg font-semibold text-gray-100">Sessions</h2>
        <p className="mt-1 text-sm text-gray-500">
          Task sessions aggregated from visible profiles only. Session metadata stays out of the table.
        </p>
      </div>

      {errors.length > 0 ? (
        <div className="rounded-md border border-red-600/30 bg-red-600/10 px-3 py-2 text-sm text-red-300">
          {errors.join("; ")}
        </div>
      ) : null}

      {loading ? (
        <div className="flex flex-1 items-center justify-center text-sm text-gray-500">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading sessions...
        </div>
      ) : rows.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-border bg-surface-1 px-6 py-16 text-center">
          <History className="mb-3 h-8 w-8 text-gray-600" />
          <p className="text-sm text-gray-400">No task sessions found.</p>
          <p className="mt-1 text-xs text-gray-600">Sessions appear after profile-scoped task activity is stored.</p>
        </div>
      ) : useFallbackCards ? (
        <div className="space-y-2 overflow-y-auto">
          {rows.map((row) => (
            <div
              key={row.id}
              className={`rounded-xl border px-3 py-2.5 ${
                row.id === selectedSessionId ? "border-accent/40 bg-accent/10" : "border-border bg-surface-1"
              }`}
            >
              <button
                type="button"
                className="w-full text-left"
                onClick={() => setSelectedSessionId((current) => current === row.id ? null : row.id)}
                aria-label={`${row.id === selectedSessionId ? "Hide" : "Show"} details for ${row.title}`}
                aria-expanded={row.id === selectedSessionId}
              >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-1 truncate text-sm font-medium text-gray-100">
                    <ChevronDown className={`h-3.5 w-3.5 shrink-0 transition-transform ${row.id === selectedSessionId ? "rotate-180" : ""}`} />
                    {row.title}
                  </div>
                  <div className="mt-0.5 text-xs text-gray-500">
                    {row.profileName} · {row.projectId}
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5 text-[10px] uppercase tracking-wide text-gray-400">
                  <span className="rounded-full bg-surface-3 px-2 py-0.5">{row.workflowState}</span>
                  <span className="rounded-full bg-surface-3 px-2 py-0.5">{row.status}</span>
                </div>
              </div>
              <div className="mt-2 text-[11px] text-gray-600">
                {row.retentionClass} · {formatActivity(row.activityAt)}
              </div>
              </button>
              {row.id === selectedSessionId ? (
                <SessionDetail row={row} onClose={() => setSelectedSessionId(null)} onOpenProfile={() => onSelectProfile(row.profileId)} />
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          <div className="relative max-w-sm">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-500" />
            <input
              type="text"
              className="input h-8 pl-8 text-xs"
              value={quickFilter}
              onChange={(event) => setQuickFilter(event.target.value)}
              placeholder="Search sessions..."
              aria-label="Search sessions grid"
            />
          </div>
          <div className="grid min-h-0 flex-1 grid-rows-[minmax(12rem,1fr)_auto] gap-2">
            <CompactDataGrid<SessionRow>
              ariaLabel="Sessions grid"
              storageKey="sessions"
              columns={columns}
              rows={rows}
              quickFilterText={quickFilter}
              selectedId={selectedSessionId}
              onRowClick={(row) => setSelectedSessionId((current) => current === row.id ? null : row.id)}
              testId="sessions-desktop-grid"
            />
            {selectedRow ? (
              <SessionDetail
                row={selectedRow}
                onClose={() => setSelectedSessionId(null)}
                onOpenProfile={() => onSelectProfile(selectedRow.profileId)}
              />
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

function SessionDetail({ row, onClose, onOpenProfile }: { row: SessionRow; onClose: () => void; onOpenProfile: () => void }) {
  const values = [
    ["Profile", row.profileName],
    ["Project", row.projectId],
    ["Workflow", row.workflowState],
    ["Status", row.status],
    ["Retention", row.retentionClass],
    ["Activity", formatActivity(row.activityAt)],
    ["Session ID", row.sessionId],
    ["Profile ID", row.profileId],
  ] as const;
  return (
    <section
      className="mt-2 max-h-[clamp(9rem,22dvh,15rem)] overflow-auto rounded-lg border border-violet-500/25 bg-[#14111c] p-3"
      role="region"
      aria-label={`Session details for ${row.title}`}
    >
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-semibold text-gray-100">{row.title}</div>
          <div className="mt-0.5 text-[10px] text-violet-300">Typed session fields · sensitive metadata omitted</div>
        </div>
        <button
          type="button"
          className="inline-flex h-7 w-7 items-center justify-center rounded border border-border text-gray-400 hover:bg-surface-3 hover:text-white"
          onClick={onClose}
          aria-label={`Close session details for ${row.title}`}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4 xl:grid-cols-6">
        {values.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-[9px] font-semibold uppercase tracking-wide text-gray-500">{label}</dt>
            <dd className="mt-0.5 truncate text-[11px] text-gray-200" title={value}>{value}</dd>
          </div>
        ))}
      </dl>
      <button
        type="button"
        className="btn btn-primary mt-3 inline-flex h-8 items-center gap-1.5 text-xs"
        onClick={onOpenProfile}
        aria-label={`Open live profile ${row.profileName}`}
      >
        <ExternalLink className="h-3.5 w-3.5" /> Open live profile
      </button>
    </section>
  );
}
