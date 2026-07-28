import type { ICellRendererParams, ValueGetterParams } from "ag-grid-community";
import { Pin, Search, Settings, Users } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { Profile } from "../lib/api";
import { harnessLabel } from "../lib/harnessOptions";
import { StatusIndicator } from "./StatusIndicator";
import { CompactDataGrid } from "./data-grid/CompactDataGrid";
import { redactProxyLabel } from "./data-grid/redaction";

const PROFILE_GRID_FALLBACK_QUERY = "(max-width: 767px), (pointer: coarse)";

interface ProfilesWorkspaceProps {
  profiles: Profile[];
  selectedId: string | null;
  onSelect: (profileId: string) => void;
  onEdit: (profileId: string) => void;
  canManage: boolean;
}

function sortProfiles(profiles: Profile[]) {
  return [...profiles].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}

function profileProjectFolder(profile: Profile) {
  return (profile.project_id || "default") + (profile.folder_path ? ` / ${profile.folder_path}` : "");
}

function formatUpdated(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString().slice(0, 16).replace("T", " ");
}

function useProfilesWorkspaceFallback() {
  const [fallback, setFallback] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia(PROFILE_GRID_FALLBACK_QUERY).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;
    const query = window.matchMedia(PROFILE_GRID_FALLBACK_QUERY);
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

export function ProfilesWorkspace({
  profiles,
  selectedId,
  onSelect,
  onEdit,
  canManage,
}: ProfilesWorkspaceProps) {
  const [quickFilter, setQuickFilter] = useState("");
  const useFallbackCards = useProfilesWorkspaceFallback();
  const sorted = useMemo(() => sortProfiles(profiles), [profiles]);

  const columns = useMemo(
    () => [
      {
        headerName: "Status",
        field: "status" as const,
        width: 110,
        cellRenderer: ({ data }: ICellRendererParams<Profile>) =>
          data ? (
            <span className="inline-flex items-center gap-2 text-xs capitalize text-gray-300">
              <StatusIndicator status={data.status} size="sm" />
              {data.status}
            </span>
          ) : null,
      },
      {
        headerName: "Name",
        field: "name" as const,
        minWidth: 190,
        flex: 1.35,
        cellRenderer: ({ data, value }: ICellRendererParams<Profile, string>) =>
          data ? (
            <button
              type="button"
              className="flex min-w-0 items-center gap-2 text-left"
              onClick={(event) => {
                event.stopPropagation();
                onSelect(data.id);
              }}
              aria-label={`Select ${data.name}`}
            >
              <span className="truncate font-medium text-gray-100">{value}</span>
              {data.pinned ? <Pin className="h-3 w-3 shrink-0 text-amber-400" aria-label="Pinned" /> : null}
            </button>
          ) : null,
      },
      {
        headerName: "Project/Folder",
        valueGetter: ({ data }: ValueGetterParams<Profile>) => (data ? profileProjectFolder(data) : ""),
        minWidth: 170,
        flex: 1.1,
      },
      {
        headerName: "Harness",
        valueGetter: ({ data }: ValueGetterParams<Profile>) => harnessLabel(data?.harness),
        width: 140,
      },
      { headerName: "Platform", field: "platform" as const, width: 120 },
      {
        headerName: "Viewport",
        valueGetter: ({ data }: ValueGetterParams<Profile>) => (data ? `${data.screen_width} x ${data.screen_height}` : ""),
        width: 120,
      },
      {
        headerName: "Proxy",
        valueGetter: ({ data }: ValueGetterParams<Profile>) => redactProxyLabel(data?.proxy),
        minWidth: 150,
        flex: 1,
      },
      {
        headerName: "Updated",
        valueGetter: ({ data }: ValueGetterParams<Profile>) => (data ? formatUpdated(data.updated_at) : ""),
        width: 150,
      },
      {
        headerName: "Actions",
        sortable: false,
        filter: false,
        width: 100,
        cellRenderer: ({ data }: ICellRendererParams<Profile>) =>
          canManage && data ? (
            <button
              type="button"
              className="inline-flex h-7 items-center justify-center rounded border border-border bg-surface-3 px-2 text-[11px] text-gray-200 hover:bg-surface-4 focus:outline-none focus:ring-2 focus:ring-accent/50"
              onClick={(event) => {
                event.stopPropagation();
                onEdit(data.id);
              }}
              aria-label={`Settings for ${data.name}`}
              title="Settings"
            >
              <Settings className="h-3.5 w-3.5" />
            </button>
          ) : null,
      },
    ],
    [canManage, onEdit, onSelect],
  );

  return (
    <div className="mx-auto flex h-full w-full max-w-6xl flex-col gap-4 p-6">
      <div>
        <h2 className="text-lg font-semibold text-gray-100">Profiles</h2>
        <p className="mt-1 text-sm text-gray-500">
          Browser profiles with preferred harness metadata. Execution still requires the verified
          Codex Computer Use bridge.
        </p>
      </div>

      {sorted.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-border bg-surface-1 px-6 py-16 text-center">
          <Users className="mb-3 h-8 w-8 text-gray-600" />
          <p className="text-sm text-gray-400">No profiles yet.</p>
        </div>
      ) : useFallbackCards ? (
        <div className="space-y-2 overflow-y-auto">
          {sorted.map((profile) => {
            const selected = profile.id === selectedId;
            return (
              <div
                key={profile.id}
                className={`rounded-xl border px-4 py-3 ${
                  selected ? "border-accent/40 bg-accent/10" : "border-border bg-surface-1"
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <button
                    type="button"
                    className="min-w-0 flex-1 text-left"
                    onClick={() => onSelect(profile.id)}
                  >
                    <div className="flex items-center gap-2">
                      <StatusIndicator status={profile.status} size="sm" />
                      <span className="truncate text-sm font-medium text-gray-100">
                        {profile.name}
                      </span>
                      {profile.pinned ? <Pin className="h-3 w-3 text-amber-400" /> : null}
                    </div>
                    <div className="mt-1 text-xs text-gray-500">
                      {profileProjectFolder(profile)} · {harnessLabel(profile.harness)} · {profile.platform}
                    </div>
                  </button>
                  <div className="flex items-center gap-2">
                    <span className="rounded-full bg-surface-3 px-2 py-0.5 text-[10px] uppercase tracking-wide text-gray-400">
                      {profile.status}
                    </span>
                    {canManage ? (
                      <button
                        type="button"
                        className="btn btn-secondary text-xs"
                        onClick={() => onEdit(profile.id)}
                      >
                        Settings
                      </button>
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })}
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
              placeholder="Search profiles..."
              aria-label="Search profiles grid"
            />
          </div>
          <CompactDataGrid<Profile>
            ariaLabel="Profiles grid"
            storageKey="profiles"
            columns={columns}
            rows={sorted}
            quickFilterText={quickFilter}
            selectedId={selectedId}
            onRowClick={(profile) => onSelect(profile.id)}
            testId="profiles-desktop-grid"
          />
        </div>
      )}
    </div>
  );
}
