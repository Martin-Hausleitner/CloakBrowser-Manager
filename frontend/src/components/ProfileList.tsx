import { Pin, Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import type { Profile } from "../lib/api";
import { compareOrganizedProfiles, profileOrganizationLabel } from "../lib/profileOrganization";
import { StatusIndicator } from "./StatusIndicator";

interface ProfileListProps {
  profiles: Profile[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNew?: () => void;
  canCreate?: boolean;
  onTogglePin?: (id: string) => void;
  canManage?: boolean;
  compact?: boolean;
}

interface ProfileGroup {
  key: string;
  label: string;
  profiles: Profile[];
}

function groupProfiles(profiles: Profile[]): ProfileGroup[] {
  const groups = new Map<string, ProfileGroup>();
  for (const profile of profiles) {
    const label = profileOrganizationLabel(profile);
    const key = `${profile.project_id || "default"}\u0000${profile.folder_path || ""}`;
    const group = groups.get(key) ?? { key, label, profiles: [] };
    group.profiles.push(profile);
    groups.set(key, group);
  }
  return Array.from(groups.values())
    .map((group) => ({ ...group, profiles: [...group.profiles].sort(compareOrganizedProfiles) }))
    .sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }));
}

export function ProfileList({
  profiles,
  selectedId,
  onSelect,
  onNew,
  canCreate = false,
  onTogglePin,
  canManage = false,
  compact = true,
}: ProfileListProps) {
  const [search, setSearch] = useState("");

  const { filtered, groups } = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    const nextFiltered = profiles.filter((profile) =>
      `${profile.name} ${profileOrganizationLabel(profile)}`.toLowerCase().includes(normalizedSearch),
    );
    return { filtered: nextFiltered, groups: groupProfiles(nextFiltered) };
  }, [profiles, search]);

  const runningCount = profiles.filter((p) => p.status === "running").length;

  return (
    <div className="flex h-full flex-col">
      <div className={`border-b border-border ${compact ? "px-2 py-2" : "p-4"}`}>
        <div className="relative">
          <Search className="absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            placeholder="Search…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="input py-1 pl-7 text-[11px]"
            aria-label="Search profiles"
          />
        </div>
        <div className="mt-1.5 flex items-center justify-between gap-2">
          <span className="text-[10px] text-gray-500">
            {runningCount > 0 ? `${runningCount} running` : `${profiles.length} profiles`}
          </span>
          {canCreate && onNew ? (
            <button
              type="button"
              onClick={onNew}
              className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-gray-400 hover:bg-surface-2 hover:text-gray-200"
              aria-label="New profile"
              title="New profile"
            >
              <Plus className="h-3 w-3" />
              New
            </button>
          ) : null}
        </div>
      </div>

      <div className={`min-h-0 flex-1 overflow-y-auto ${compact ? "p-1" : "p-2"}`}>
        {filtered.length === 0 ? (
          <div className="px-2 py-6 text-center text-[11px] text-gray-500">
            {profiles.length === 0 ? "No profiles yet" : "No matches"}
          </div>
        ) : null}
        {groups.map((group) => (
          <section key={group.key} className="mb-1">
            <div className="truncate px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-gray-600">
              {group.label}
            </div>
            {group.profiles.map((profile) => (
              <div
                key={profile.id}
                className={`group mb-0.5 flex items-center gap-0.5 rounded border-l-2 ${
                  selectedId === profile.id
                    ? "border-accent bg-surface-3"
                    : "border-transparent hover:bg-surface-2"
                }`}
                style={profile.accent_color ? { borderLeftColor: profile.accent_color } : undefined}
              >
                <button
                  type="button"
                  onClick={() => onSelect(profile.id)}
                  className="min-w-0 flex-1 px-1.5 py-1.5 text-left"
                >
                  <div className="flex items-center gap-1.5">
                    <StatusIndicator status={profile.status} />
                    <span className="truncate text-xs font-medium text-gray-100">{profile.name}</span>
                  </div>
                </button>
                {canManage && onTogglePin ? (
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onTogglePin(profile.id);
                    }}
                    className={`mr-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-gray-600 opacity-70 hover:bg-surface-3 hover:opacity-100 ${
                      profile.pinned ? "text-amber-400 opacity-100" : "group-hover:opacity-100"
                    }`}
                    aria-label={`${profile.pinned ? "Unpin" : "Pin"} ${profile.name}`}
                    title={`${profile.pinned ? "Unpin" : "Pin"} ${profile.name}`}
                  >
                    <Pin className={`h-2.5 w-2.5 ${profile.pinned ? "fill-current" : ""}`} />
                  </button>
                ) : profile.pinned ? (
                  <Pin className="mr-1 h-2.5 w-2.5 shrink-0 fill-current text-amber-400" aria-label="Pinned" />
                ) : null}
              </div>
            ))}
          </section>
        ))}
      </div>
    </div>
  );
}
