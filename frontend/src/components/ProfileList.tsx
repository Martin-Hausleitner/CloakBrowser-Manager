import { ChevronDown, ChevronRight, Pin, Plus, Search, Monitor } from "lucide-react";
import { useMemo, useState } from "react";
import type { Profile } from "../lib/api";
import { compareOrganizedProfiles, profileOrganizationLabel } from "../lib/profileOrganization";
import { StatusIndicator } from "./StatusIndicator";

interface ProfileListProps {
  profiles: Profile[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  canCreate?: boolean;
  onTogglePin?: (id: string) => void;
  onBulkOrganize?: (payload: {
    profile_ids: string[];
    project_id?: string;
    folder_path?: string;
    pinned?: boolean;
  }) => Promise<void> | void;
  canManage?: boolean;
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
  canCreate = true,
  onTogglePin,
  onBulkOrganize,
  canManage = false,
}: ProfileListProps) {
  const [search, setSearch] = useState("");
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => new Set());
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [bulkProject, setBulkProject] = useState("default");
  const [bulkFolder, setBulkFolder] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);

  const { filtered, groups } = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    const nextFiltered = profiles.filter((profile) =>
      `${profile.name} ${profileOrganizationLabel(profile)}`.toLowerCase().includes(normalizedSearch),
    );
    return { filtered: nextFiltered, groups: groupProfiles(nextFiltered) };
  }, [profiles, search]);

  const runningCount = profiles.filter((p) => p.status === "running").length;
  const toggleGroup = (key: string) => {
    setCollapsedGroups((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="p-4 border-b border-border">
        <div className="flex items-center gap-2 mb-3">
          <Monitor className="h-4 w-4 text-accent" />
          <h1 className="text-sm font-semibold tracking-tight">CloakBrowser Manager</h1>
        </div>
        {runningCount > 0 && (
          <div className="text-xs text-gray-500 mb-3">
            {runningCount} running
          </div>
        )}
        {/* Search */}
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-gray-500" />
          <input
            type="text"
            placeholder="Search profiles..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="input pl-8 py-1.5 text-xs"
          />
        </div>
        {canManage && onBulkOrganize ? (
          <div className="mt-3 space-y-2 rounded-md border border-border bg-surface-2 p-2">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
              Bulk organize ({selectedIds.size})
            </div>
            <input
              aria-label="Bulk project"
              className="input py-1.5 text-xs"
              value={bulkProject}
              onChange={(e) => setBulkProject(e.target.value)}
              placeholder="project"
            />
            <input
              aria-label="Bulk folder path"
              className="input py-1.5 text-xs"
              value={bulkFolder}
              onChange={(e) => setBulkFolder(e.target.value)}
              placeholder="folder/path"
            />
            <button
              type="button"
              className="btn btn-secondary w-full text-xs"
              disabled={selectedIds.size === 0 || bulkBusy}
              onClick={async () => {
                setBulkBusy(true);
                try {
                  await onBulkOrganize({
                    profile_ids: Array.from(selectedIds),
                    project_id: bulkProject.trim() || "default",
                    folder_path: bulkFolder.trim(),
                  });
                  setSelectedIds(new Set());
                } finally {
                  setBulkBusy(false);
                }
              }}
            >
              Move selected
            </button>
          </div>
        ) : null}
      </div>

      {/* Profile list */}
      <div className="flex-1 overflow-y-auto p-2">
        {filtered.length === 0 && (
          <div className="text-center text-gray-500 text-xs py-8">
            {profiles.length === 0 ? "No profiles yet" : "No matches"}
          </div>
        )}
        {groups.map((group, groupIndex) => {
          const collapsed = collapsedGroups.has(group.key);
          const countId = `profile-group-count-${groupIndex}`;
          return (
            <section key={group.key} className="mb-2">
              <button
                type="button"
                onClick={() => toggleGroup(group.key)}
                className="flex w-full items-center gap-1 rounded px-1.5 py-1 text-left text-[11px] font-semibold uppercase tracking-wide text-gray-500 hover:bg-surface-2"
                aria-expanded={!collapsed}
                aria-label={`Profile group: ${group.label}`}
                aria-describedby={countId}
              >
                {collapsed ? <ChevronRight className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                <span className="min-w-0 flex-1 truncate">{group.label}</span>
                <span id={countId}>{group.profiles.length}</span>
              </button>
              {!collapsed ? (
                <div className="mt-1">
                  {group.profiles.map((profile) => (
                    <div
                      key={profile.id}
                      className={`mb-1 rounded-md border transition-colors ${
                        selectedId === profile.id
                          ? "bg-surface-3 border-border-hover"
                          : "hover:bg-surface-2 border-transparent"
                      }`}
                      style={{
                        borderLeftWidth: 3,
                        ...(profile.accent_color ? { borderLeftColor: profile.accent_color } : {}),
                      }}
                    >
                      <div className="flex items-start gap-1.5">
                        {canManage && onBulkOrganize ? (
                          <label className="flex items-center pl-2 pt-3">
                            <input
                              type="checkbox"
                              aria-label={`Select ${profile.name}`}
                              checked={selectedIds.has(profile.id)}
                              onChange={(e) => {
                                setSelectedIds((current) => {
                                  const next = new Set(current);
                                  if (e.target.checked) next.add(profile.id);
                                  else next.delete(profile.id);
                                  return next;
                                });
                              }}
                            />
                          </label>
                        ) : null}
                        <button
                          type="button"
                          onClick={() => onSelect(profile.id)}
                          className="min-w-0 flex-1 px-3 py-2.5 text-left"
                        >
                          <div className="flex items-center gap-2">
                            <StatusIndicator status={profile.status} />
                            <span className="truncate text-sm font-medium">{profile.name}</span>
                            {profile.pinned ? (
                              <span className="inline-flex items-center gap-1 rounded bg-surface-4 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-gray-300">
                                <Pin className="h-2.5 w-2.5" />
                                Pinned
                              </span>
                            ) : null}
                          </div>
                          <div className="mt-1 ml-4 flex items-center gap-2">
                            <span className="truncate text-xs text-gray-500">{group.label}</span>
                            <span className="text-xs text-gray-600">·</span>
                            <span className="text-xs text-gray-500 capitalize">{profile.platform}</span>
                            {profile.proxy && (
                              <>
                                <span className="text-xs text-gray-600">·</span>
                                <span className="text-xs text-gray-500">Proxy</span>
                              </>
                            )}
                          </div>
                          {profile.tags.length > 0 && (
                            <div className="ml-4 mt-1.5 flex flex-wrap gap-1">
                              {profile.tags.map((t) => (
                                <span
                                  key={t.tag}
                                  className="rounded-full bg-surface-4 px-1.5 py-0.5 text-[10px] text-gray-400"
                                  style={t.color ? { backgroundColor: `${t.color}20`, color: t.color } : undefined}
                                >
                                  {t.tag}
                                </span>
                              ))}
                            </div>
                          )}
                        </button>
                        {canManage && onTogglePin ? (
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              onTogglePin(profile.id);
                            }}
                            className={`m-1.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded border border-border hover:bg-surface-3 ${
                              profile.pinned ? "text-indigo-200" : "text-gray-500"
                            }`}
                            aria-label={`${profile.pinned ? "Unpin" : "Pin"} ${profile.name}`}
                            title={`${profile.pinned ? "Unpin" : "Pin"} ${profile.name}`}
                          >
                            <Pin className="h-3.5 w-3.5" />
                          </button>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>
              ) : null}
            </section>
          );
        })}
      </div>

      {/* New profile button */}
      {canCreate && (
        <div className="p-3 border-t border-border">
          <button onClick={onNew} className="btn-secondary w-full flex items-center justify-center gap-1.5">
            <Plus className="h-3.5 w-3.5" />
            <span>New Profile</span>
          </button>
        </div>
      )}
    </div>
  );
}
