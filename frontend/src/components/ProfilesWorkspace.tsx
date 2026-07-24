import { Users, Pin } from "lucide-react";
import type { Profile } from "../lib/api";
import { harnessLabel } from "../lib/harnessOptions";
import { StatusIndicator } from "./StatusIndicator";

interface ProfilesWorkspaceProps {
  profiles: Profile[];
  selectedId: string | null;
  onSelect: (profileId: string) => void;
  onEdit: (profileId: string) => void;
  canManage: boolean;
}

export function ProfilesWorkspace({
  profiles,
  selectedId,
  onSelect,
  onEdit,
  canManage,
}: ProfilesWorkspaceProps) {
  const sorted = [...profiles].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  return (
    <div className="mx-auto flex h-full max-w-4xl flex-col gap-4 p-6">
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
      ) : (
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
                      {(profile.project_id || "default") +
                        (profile.folder_path ? ` / ${profile.folder_path}` : "")}{" "}
                      · {harnessLabel(profile.harness)} · {profile.platform}
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
      )}
    </div>
  );
}
