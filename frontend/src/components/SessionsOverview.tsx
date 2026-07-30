import type { ICellRendererParams, ValueGetterParams } from "ag-grid-community";
import { ChevronDown, ExternalLink, History, Loader2, Search, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
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
  rowVersion: number;
};

type StateFilter = "open" | "done" | "archived" | "all";
type RetentionFilter = "all" | TaskHarnessSession["retention_class"];

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

function sessionRowForProfile(profile: Profile, session: TaskHarnessSession): SessionRow {
  return {
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
    rowVersion: session.row_version,
  };
}

function sessionRowsForProfile(profile: Profile, sessions: TaskHarnessSession[]): SessionRow[] {
  return sessions.map((session) => sessionRowForProfile(profile, session));
}

function applyUpdatedSession(row: SessionRow, session: TaskHarnessSession): SessionRow {
  return {
    ...row,
    title: sessionTitle(session),
    projectId: session.project_id || row.projectId,
    workflowState: session.workflow_state,
    status: session.status,
    retentionClass: session.retention_class,
    activityAt: session.activity_at,
    rowVersion: session.row_version,
  };
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
  const [stateFilter, setStateFilter] = useState<StateFilter>("open");
  const [retentionFilter, setRetentionFilter] = useState<RetentionFilter>("all");
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [mutatingSessionId, setMutatingSessionId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
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

  const counts = useMemo(() => ({
    open: rows.filter((row) => row.status === "active" && row.workflowState === "open").length,
    done: rows.filter((row) => row.status === "active" && row.workflowState === "done").length,
    archived: rows.filter((row) => row.status === "archived").length,
    all: rows.length,
  }), [rows]);

  const stateFilteredRows = useMemo(() => rows.filter((row) => {
    const stateMatches = stateFilter === "all"
      || (stateFilter === "archived" && row.status === "archived")
      || (stateFilter === "open" && row.status === "active" && row.workflowState === "open")
      || (stateFilter === "done" && row.status === "active" && row.workflowState === "done");
    const retentionMatches = retentionFilter === "all" || row.retentionClass === retentionFilter;
    return stateMatches && retentionMatches;
  }), [retentionFilter, rows, stateFilter]);

  const fallbackRows = useMemo(() => {
    const query = quickFilter.trim().toLowerCase();
    if (!query) return stateFilteredRows;
    return stateFilteredRows.filter((row) => (
      `${row.title} ${row.profileName} ${row.projectId} ${row.workflowState} ${row.status} ${row.retentionClass}`
        .toLowerCase()
        .includes(query)
    ));
  }, [quickFilter, stateFilteredRows]);

  useEffect(() => {
    if (selectedSessionId && !stateFilteredRows.some((row) => row.id === selectedSessionId)) {
      setSelectedSessionId(null);
    }
  }, [selectedSessionId, stateFilteredRows]);

  const selectedRow = stateFilteredRows.find((row) => row.id === selectedSessionId) ?? null;

  const updateSession = useCallback(async (
    row: SessionRow,
    updates: Omit<Parameters<typeof api.updateTaskSession>[1], "row_version">,
    successMessage: string,
  ) => {
    setMutatingSessionId(row.id);
    setActionError(null);
    try {
      const updated = await api.updateTaskSession(row.sessionId, {
        row_version: row.rowVersion,
        ...updates,
      });
      setRows((current) => sortSessionRows(current.map((item) => (
        item.id === row.id ? applyUpdatedSession(item, updated) : item
      ))));
      setAnnouncement(successMessage);
    } catch (error) {
      const message = errorMessage(error);
      setActionError(
        /conflict|409/i.test(message)
          ? "This chat changed elsewhere. Refresh and try again."
          : message,
      );
    } finally {
      setMutatingSessionId(null);
    }
  }, []);

  return (
    <div className="flex h-full w-full max-w-none flex-col gap-3 p-3 lg:p-5" data-testid="sessions-overview">
      <div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold text-gray-100">Task chats</h2>
          <div className="text-[11px] text-gray-500">
            Open {counts.open} · Done {counts.done} · Archived {counts.archived}
          </div>
        </div>
        <p className="mt-1 text-sm text-gray-500">
          Project chats for visible browser profiles. Browser sessions stay fixed.
        </p>
      </div>

      <div className="sr-only" aria-live="polite">{announcement}</div>

      {errors.length > 0 ? (
        <div className="rounded-md border border-red-600/30 bg-red-600/10 px-3 py-2 text-sm text-red-300">
          {errors.join("; ")}
        </div>
      ) : null}

      {actionError ? (
        <div role="alert" className="rounded-md border border-red-600/30 bg-red-600/10 px-3 py-2 text-sm text-red-300">
          {actionError}
        </div>
      ) : null}

      {!loading && rows.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <div
            className="grid grid-cols-4 rounded-md border border-border bg-surface-1 p-0.5"
            role="tablist"
            aria-label="Task chat state"
          >
            {(["open", "done", "archived", "all"] as const).map((value) => (
              <button
                key={value}
                type="button"
                role="tab"
                className={`min-h-11 rounded px-2 text-[11px] font-medium capitalize ${
                  stateFilter === value ? "bg-[#2e2b5f] text-white" : "text-gray-400 hover:bg-surface-3 hover:text-white"
                }`}
                onClick={() => setStateFilter(value)}
                aria-label={`${value.charAt(0).toUpperCase()}${value.slice(1)} task chats`}
                aria-selected={stateFilter === value}
              >
                {value} {counts[value]}
              </button>
            ))}
          </div>
          <div className="relative min-w-[12rem] flex-1 sm:max-w-sm">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-500" />
            <input
              type="text"
              className="input h-11 w-full pl-8 text-xs"
              value={quickFilter}
              onChange={(event) => setQuickFilter(event.target.value)}
              placeholder="Search task chats..."
              aria-label="Search task chats"
            />
          </div>
          <label className="flex min-h-11 items-center gap-2 rounded-md border border-border bg-surface-1 px-2 text-[11px] text-gray-500">
            <span>Retention</span>
            <select
              className="bg-transparent text-gray-200 outline-none"
              value={retentionFilter}
              onChange={(event) => setRetentionFilter(event.target.value as RetentionFilter)}
              aria-label="Filter task chats by retention"
            >
              <option value="all">Any retention</option>
              <option value="temporary">Temporary</option>
              <option value="project">Project</option>
              <option value="legacy">Legacy</option>
            </select>
          </label>
        </div>
      ) : null}

      {loading ? (
        <div className="flex flex-1 items-center justify-center text-sm text-gray-500">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading sessions...
        </div>
      ) : rows.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-border bg-surface-1 px-6 py-16 text-center">
          <History className="mb-3 h-8 w-8 text-gray-600" />
          <p className="text-sm text-gray-400">No task chats found.</p>
          <p className="mt-1 text-xs text-gray-600">Task chats appear after profile-scoped agent activity is stored.</p>
        </div>
      ) : stateFilteredRows.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-border bg-surface-1 px-6 py-16 text-center">
          <History className="mb-3 h-8 w-8 text-gray-600" />
          <p className="text-sm text-gray-400">No {stateFilter} task chats.</p>
          <p className="mt-1 text-xs text-gray-600">Choose another state or retention filter.</p>
        </div>
      ) : useFallbackCards ? (
        <div className="space-y-2 overflow-y-auto">
          {fallbackRows.map((row) => (
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
                <SessionDetail
                  row={row}
                  busy={mutatingSessionId === row.id}
                  onClose={() => setSelectedSessionId(null)}
                  onOpenProfile={() => onSelectProfile(row.profileId)}
                  onUpdate={(updates, message) => updateSession(row, updates, message)}
                />
              ) : null}
            </div>
          ))}
          {fallbackRows.length === 0 ? (
            <div className="rounded-md border border-dashed border-border px-3 py-8 text-center text-xs text-gray-500">
              No task chats match this search.
            </div>
          ) : null}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          <div className="grid min-h-0 flex-1 grid-rows-[minmax(12rem,1fr)_auto] gap-2">
            <CompactDataGrid<SessionRow>
              ariaLabel="Sessions grid"
              storageKey="sessions"
              columns={columns}
              rows={stateFilteredRows}
              quickFilterText={quickFilter}
              selectedId={selectedSessionId}
              onRowClick={(row) => setSelectedSessionId((current) => current === row.id ? null : row.id)}
              testId="sessions-desktop-grid"
            />
            {selectedRow ? (
              <SessionDetail
                row={selectedRow}
                busy={mutatingSessionId === selectedRow.id}
                onClose={() => setSelectedSessionId(null)}
                onOpenProfile={() => onSelectProfile(selectedRow.profileId)}
                onUpdate={(updates, message) => updateSession(selectedRow, updates, message)}
              />
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

function SessionDetail({
  row,
  busy,
  onClose,
  onOpenProfile,
  onUpdate,
}: {
  row: SessionRow;
  busy: boolean;
  onClose: () => void;
  onOpenProfile: () => void;
  onUpdate: (
    updates: Omit<Parameters<typeof api.updateTaskSession>[1], "row_version">,
    message: string,
  ) => Promise<void>;
}) {
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
      aria-label={`Task chat details for ${row.title}`}
      aria-busy={busy}
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
      <div className="mt-3 flex flex-wrap gap-2">
        {row.status === "active" ? (
          <button
            type="button"
            className="btn btn-secondary min-h-11 text-xs"
            disabled={busy}
            onClick={() => void onUpdate(
              { workflow_state: row.workflowState === "open" ? "done" : "open" },
              row.workflowState === "open" ? `Marked ${row.title} done` : `Reopened ${row.title}`,
            )}
            aria-label={row.workflowState === "open" ? `Mark ${row.title} done` : `Reopen ${row.title}`}
          >
            {busy ? "Saving..." : row.workflowState === "open" ? "Mark done" : "Reopen"}
          </button>
        ) : null}
        <button
          type="button"
          className="btn btn-secondary min-h-11 text-xs"
          disabled={busy}
          onClick={() => void onUpdate(
            { archived: row.status !== "archived" },
            row.status === "archived" ? `Restored ${row.title}` : `Archived ${row.title}`,
          )}
          aria-label={row.status === "archived" ? `Restore ${row.title}` : `Archive ${row.title}`}
        >
          {busy ? "Saving..." : row.status === "archived" ? "Restore" : "Archive"}
        </button>
        {row.retentionClass !== "legacy" ? (
          <button
            type="button"
            className="btn btn-secondary min-h-11 text-xs"
            disabled={busy}
            onClick={() => void onUpdate(
              { retention_class: row.retentionClass === "temporary" ? "project" : "temporary" },
              row.retentionClass === "temporary" ? `Kept ${row.title} in project` : `Made ${row.title} temporary`,
            )}
            aria-label={row.retentionClass === "temporary" ? `Keep ${row.title} in project` : `Make ${row.title} temporary`}
          >
            {busy ? "Saving..." : row.retentionClass === "temporary" ? "Keep in project" : "Make temporary"}
          </button>
        ) : null}
        <button
          type="button"
          className="btn btn-primary inline-flex min-h-11 items-center gap-1.5 text-xs"
          onClick={onOpenProfile}
          aria-label={`Open live profile ${row.profileName}`}
        >
          <ExternalLink className="h-3.5 w-3.5" /> Open live profile
        </button>
      </div>
    </section>
  );
}
