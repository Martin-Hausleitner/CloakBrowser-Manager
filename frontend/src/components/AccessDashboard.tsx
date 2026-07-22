import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  Bot,
  Copy,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  ShieldCheck,
  UserRound,
  UsersRound,
  X,
} from "lucide-react";
import {
  api,
  type AccessAgent,
  type AccessGrant,
  type AccessGroup,
  type AccessPermission,
  type AccessRole,
  type AccessSandbox,
  type AccessUser,
  type Profile,
} from "../lib/api";
import { hasAccessPermission } from "../lib/accessPermissions";
import { profileOrganizationLabel } from "../lib/profileOrganization";

interface AccessDashboardProps {
  onClose: () => void;
}

interface UserDraft {
  username: string;
  password: string;
  role: AccessRole;
  active: boolean;
  grants: AccessGrant[];
  group_ids: string[];
}

interface AgentDraft {
  display_name: string;
  paperclip_agent_id: string;
  active: boolean;
  grants: AccessGrant[];
}

interface GroupDraft {
  name: string;
  description: string;
  active: boolean;
  member_user_ids: string[];
  grants: AccessGrant[];
}

type AccessTab = "identities" | "groups";

const emptyUserDraft = (): UserDraft => ({
  username: "",
  password: "",
  role: "viewer",
  active: true,
  grants: [],
  group_ids: [],
});

const emptyAgentDraft = (): AgentDraft => ({
  display_name: "",
  paperclip_agent_id: "",
  active: true,
  grants: [],
});

const emptyGroupDraft = (): GroupDraft => ({
  name: "",
  description: "",
  active: true,
  member_user_ids: [],
  grants: [],
});

const permissionLabel: Record<AccessPermission, string> = {
  view: "View",
  interact: "Interact",
  operate: "Operate",
  automate: "Automate",
};

type ControlPermission = Exclude<AccessPermission, "automate">;
const controlPermissions: readonly ControlPermission[] = ["view", "interact", "operate"];
const permissionOrder: Record<AccessPermission, number> = {
  view: 0,
  interact: 1,
  operate: 2,
  automate: 3,
};

const actionButtonClass = "btn-secondary inline-flex min-h-11 items-center gap-1";
const primaryActionButtonClass = "btn-primary inline-flex min-h-11 items-center gap-1";
const compactActionButtonClass = "min-h-11 rounded-md px-2 text-[11px] text-gray-500 underline focus:outline-none focus:ring-2 focus:ring-accent/50";
const iconButtonClass = "inline-flex h-11 w-11 shrink-0 items-center justify-center rounded border border-border bg-surface-2 text-gray-300 transition-colors hover:bg-surface-3 focus:outline-none focus:ring-2 focus:ring-accent/50";

function summarizeGrants(grants: AccessGrant[]) {
  if (!grants.length) return "No sandbox access";
  const grouped = new Map<string, AccessPermission[]>();
  for (const grant of grants) {
    const permissions = grouped.get(grant.sandbox_id) ?? [];
    if (!permissions.includes(grant.permission)) permissions.push(grant.permission);
    grouped.set(grant.sandbox_id, permissions);
  }
  return [...grouped.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([sandboxId, permissions]) => (
      `${sandboxId}: ${permissions
        .sort((left, right) => permissionOrder[left] - permissionOrder[right])
        .map((permission) => permissionLabel[permission])
        .join(" + ")}`
    ))
    .join(" · ");
}

function sortGrants(grants: AccessGrant[]) {
  return [...grants].sort((left, right) => (
    left.sandbox_id.localeCompare(right.sandbox_id)
      || permissionOrder[left.permission] - permissionOrder[right.permission]
  ));
}

function updateControlGrant(
  grants: AccessGrant[],
  sandboxId: string,
  permission: "" | ControlPermission,
) {
  const preserved = grants.filter((grant) => (
    grant.sandbox_id !== sandboxId || grant.permission === "automate"
  ));
  return sortGrants(permission
    ? [...preserved, { sandbox_id: sandboxId, permission }]
    : preserved);
}

function updateAutomationGrant(grants: AccessGrant[], sandboxId: string, enabled: boolean) {
  const preserved = grants.filter((grant) => (
    grant.sandbox_id !== sandboxId || grant.permission !== "automate"
  ));
  return sortGrants(enabled
    ? [...preserved, { sandbox_id: sandboxId, permission: "automate" }]
    : preserved);
}

function toggleId(ids: string[], id: string, checked: boolean) {
  if (checked) return ids.includes(id) ? ids : [...ids, id];
  return ids.filter((currentId) => currentId !== id);
}

function effectiveCapabilityLabel(grants: AccessGrant[], sandboxId: string) {
  const labels: string[] = [];
  if (hasAccessPermission(grants, sandboxId, "operate")) labels.push("Operate");
  else if (hasAccessPermission(grants, sandboxId, "interact")) labels.push("Interact");
  else if (hasAccessPermission(grants, sandboxId, "view")) labels.push("View");
  if (hasAccessPermission(grants, sandboxId, "automate")) labels.push("CDP automation");
  return labels.join(" + ");
}

function groupNamesForUser(user: AccessUser, groups: AccessGroup[]) {
  const groupIds = user.group_ids ?? [];
  return groups
    .filter((group) => groupIds.includes(group.id))
    .sort((left, right) => left.name.localeCompare(right.name))
    .map((group) => group.name);
}

function memberNames(group: AccessGroup, users: AccessUser[]) {
  return users
    .filter((user) => group.member_user_ids.includes(user.id))
    .sort((left, right) => left.username.localeCompare(right.username))
    .map((user) => user.username);
}

function EffectiveAccessPreview({
  label,
  profiles,
  grants,
  administrator = false,
}: {
  label: string;
  profiles: Profile[];
  grants: AccessGrant[];
  administrator?: boolean;
}) {
  const visibleProfiles = profiles.filter((profile) => (
    administrator || hasAccessPermission(grants, profile.sandbox_id, "view")
  ));

  return (
    <details className="mt-2 text-xs text-gray-500">
      <summary className="min-h-11 cursor-pointer py-3 text-gray-400">
        Effective access · {visibleProfiles.length} browser{visibleProfiles.length === 1 ? "" : "s"}
      </summary>
      <div className="rounded-md border border-border bg-surface-0 p-2" aria-label={`Effective browser access for ${label}`}>
        {visibleProfiles.length ? (
          <ul className="space-y-1.5">
            {visibleProfiles.map((profile) => (
              <li key={profile.id} className="flex min-w-0 items-start justify-between gap-2">
                <span className="min-w-0">
                  <span className="block truncate text-gray-300">{profile.name}</span>
                  <span className="block truncate font-mono text-[10px] text-gray-600">
                    {profile.sandbox_id} · {profileOrganizationLabel(profile)}
                  </span>
                </span>
                <span className="shrink-0 text-right text-[10px] text-gray-500">
                  {administrator ? "Administrator" : effectiveCapabilityLabel(grants, profile.sandbox_id)}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p>No browser profiles are visible with these grants.</p>
        )}
      </div>
    </details>
  );
}

function GrantEditor({
  sandboxes,
  grants,
  onChange,
}: {
  sandboxes: AccessSandbox[];
  grants: AccessGrant[];
  onChange: (grants: AccessGrant[]) => void;
}) {
  const allSandboxes = useMemo(() => {
    const known = new Map(sandboxes.map((sandbox) => [sandbox.sandbox_id, sandbox]));
    for (const grant of grants) {
      if (!known.has(grant.sandbox_id)) {
        known.set(grant.sandbox_id, {
          sandbox_id: grant.sandbox_id,
          profile_count: 0,
          project_ids: [],
          folder_paths: [],
          profile_names: [],
        });
      }
    }
    return [...known.values()].sort((left, right) => left.sandbox_id.localeCompare(right.sandbox_id));
  }, [grants, sandboxes]);

  return (
    <fieldset className="min-w-0 rounded-md border border-border bg-surface-1 p-3">
      <legend className="px-1 text-xs font-medium text-gray-300">Sandbox access</legend>
      {allSandboxes.length ? (
        <div className="space-y-2">
          {allSandboxes.map((sandbox) => {
            const controlPermission = controlPermissions
              .slice()
              .reverse()
              .find((permission) => grants.some((grant) => (
                grant.sandbox_id === sandbox.sandbox_id && grant.permission === permission
              ))) ?? "";
            const automationEnabled = grants.some((grant) => (
              grant.sandbox_id === sandbox.sandbox_id && grant.permission === "automate"
            ));
            return (
              <div key={sandbox.sandbox_id} className="grid min-w-0 grid-cols-[minmax(0,1fr)_8rem] items-center gap-x-3 gap-y-1 text-sm sm:grid-cols-[minmax(0,1fr)_8rem_8rem]">
                <div className="min-w-0">
                  <p className="truncate font-mono text-xs text-gray-300">
                    {sandbox.sandbox_id}
                    <span className="ml-1 font-sans text-gray-500">({sandbox.profile_count} browser{sandbox.profile_count === 1 ? "" : "s"})</span>
                  </p>
                  {(sandbox.project_ids?.length || sandbox.folder_paths?.length) ? (
                    <p className="mt-0.5 truncate text-[10px] text-gray-500" title={(sandbox.profile_names ?? []).join(", ")}>
                      {(sandbox.project_ids ?? []).length ? `Projects: ${sandbox.project_ids.join(", ")}` : ""}
                      {(sandbox.project_ids ?? []).length && (sandbox.folder_paths ?? []).length ? " · " : ""}
                      {(sandbox.folder_paths ?? []).length ? `Folders: ${sandbox.folder_paths.join(", ")}` : ""}
                    </p>
                  ) : null}
                </div>
                <select
                  className="input h-11 w-32 shrink-0 py-1 text-xs"
                  value={controlPermission}
                  aria-label={`Browser control for ${sandbox.sandbox_id}`}
                  onChange={(event) => onChange(updateControlGrant(
                    grants,
                    sandbox.sandbox_id,
                    event.target.value as "" | ControlPermission,
                  ))}
                >
                  <option value="">No access</option>
                  {controlPermissions.map((value) => (
                    <option key={value} value={value}>{permissionLabel[value]}</option>
                  ))}
                </select>
                <label className="col-start-2 flex min-h-11 items-center gap-2 text-xs text-gray-400 sm:col-start-3">
                  <input
                    type="checkbox"
                    checked={automationEnabled}
                    aria-label={`CDP automation for ${sandbox.sandbox_id}`}
                    onChange={(event) => onChange(updateAutomationGrant(
                      grants,
                      sandbox.sandbox_id,
                      event.target.checked,
                    ))}
                  />
                  Automate
                </label>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-xs text-gray-500">Create a browser profile and assign it a sandbox first.</p>
      )}
      <p className="mt-2 text-[11px] leading-4 text-gray-500">
        Control tiers are inherited: Interact includes View, and Operate includes Interact. CDP automation is independent and can be combined with Operate.
      </p>
    </fieldset>
  );
}

function MembershipEditor({
  users,
  selectedUserIds,
  onChange,
}: {
  users: AccessUser[];
  selectedUserIds: string[];
  onChange: (userIds: string[]) => void;
}) {
  return (
    <fieldset className="rounded-md border border-border bg-surface-1 p-3">
      <legend className="px-1 text-xs font-medium text-gray-300">Members</legend>
      {users.length ? (
        <div className="grid gap-1 sm:grid-cols-2">
          {users.map((user) => (
            <label key={user.id} className="flex min-h-11 min-w-0 items-center gap-2 rounded border border-border bg-surface-2 px-2 text-sm text-gray-300">
              <input
                type="checkbox"
                checked={selectedUserIds.includes(user.id)}
                aria-label={`Group member ${user.username}`}
                onChange={(event) => onChange(toggleId(selectedUserIds, user.id, event.target.checked))}
              />
              <span className="min-w-0 truncate">{user.username}</span>
            </label>
          ))}
        </div>
      ) : (
        <p className="text-xs text-gray-500">Add people before assigning group membership.</p>
      )}
    </fieldset>
  );
}

export function AccessDashboard({ onClose }: AccessDashboardProps) {
  const [activeTab, setActiveTab] = useState<AccessTab>("identities");
  const [users, setUsers] = useState<AccessUser[]>([]);
  const [agents, setAgents] = useState<AccessAgent[]>([]);
  const [groups, setGroups] = useState<AccessGroup[]>([]);
  const [sandboxes, setSandboxes] = useState<AccessSandbox[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [editingUserId, setEditingUserId] = useState<string | null>(null);
  const [editingAgentId, setEditingAgentId] = useState<string | null>(null);
  const [editingGroupId, setEditingGroupId] = useState<string | null>(null);
  const [showUserForm, setShowUserForm] = useState(false);
  const [showAgentForm, setShowAgentForm] = useState(false);
  const [showGroupForm, setShowGroupForm] = useState(false);
  const [userDraft, setUserDraft] = useState<UserDraft>(emptyUserDraft);
  const [agentDraft, setAgentDraft] = useState<AgentDraft>(emptyAgentDraft);
  const [groupDraft, setGroupDraft] = useState<GroupDraft>(emptyGroupDraft);
  const [revealedKey, setRevealedKey] = useState<{ name: string; key: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [nextUsers, nextAgents, nextGroups, nextSandboxes, nextProfiles] = await Promise.all([
        api.listAccessUsers(),
        api.listAccessAgents(),
        api.listAccessGroups(),
        api.listAccessSandboxes(),
        api.listProfiles(),
      ]);
      setUsers(nextUsers);
      setAgents(nextAgents);
      setGroups(nextGroups);
      setSandboxes(nextSandboxes);
      setProfiles(nextProfiles);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load access controls");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const saveUser = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      if (editingUserId) {
        const data: {
          role: AccessRole;
          active: boolean;
          grants: AccessGrant[];
          group_ids: string[];
          password?: string;
        } = {
          role: userDraft.role,
          active: userDraft.active,
          grants: userDraft.grants,
          group_ids: userDraft.group_ids,
        };
        if (userDraft.password) data.password = userDraft.password;
        await api.updateAccessUser(editingUserId, data);
      } else {
        await api.createAccessUser({
          username: userDraft.username.trim(),
          password: userDraft.password,
          role: userDraft.role,
          grants: userDraft.grants,
          group_ids: userDraft.group_ids,
        });
      }
      setEditingUserId(null);
      setShowUserForm(false);
      setUserDraft(emptyUserDraft());
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save user");
    } finally {
      setSaving(false);
    }
  };

  const saveAgent = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      if (editingAgentId) {
        await api.updateAccessAgent(editingAgentId, {
          display_name: agentDraft.display_name.trim(),
          paperclip_agent_id: agentDraft.paperclip_agent_id.trim() || null,
          active: agentDraft.active,
          grants: agentDraft.grants,
        });
      } else {
        const created = await api.createAccessAgent({
          display_name: agentDraft.display_name.trim(),
          paperclip_agent_id: agentDraft.paperclip_agent_id.trim() || null,
          grants: agentDraft.grants,
        });
        setRevealedKey({ name: created.display_name, key: created.api_key });
        setCopied(false);
      }
      setEditingAgentId(null);
      setShowAgentForm(false);
      setAgentDraft(emptyAgentDraft());
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save Paperclip agent");
    } finally {
      setSaving(false);
    }
  };

  const saveGroup = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      const data = {
        name: groupDraft.name.trim(),
        description: groupDraft.description.trim() || null,
        active: groupDraft.active,
        member_user_ids: groupDraft.member_user_ids,
        grants: groupDraft.grants,
      };
      if (editingGroupId) {
        await api.updateAccessGroup(editingGroupId, data);
      } else {
        await api.createAccessGroup(data);
      }
      setEditingGroupId(null);
      setShowGroupForm(false);
      setGroupDraft(emptyGroupDraft());
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save group");
    } finally {
      setSaving(false);
    }
  };

  const rotateAgentKey = async (agent: AccessAgent) => {
    setSaving(true);
    try {
      const rotated = await api.rotateAccessAgentKey(agent.id);
      setRevealedKey({ name: rotated.display_name, key: rotated.api_key });
      setCopied(false);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not rotate agent key");
    } finally {
      setSaving(false);
    }
  };

  const copyKey = async () => {
    if (!revealedKey) return;
    try {
      await navigator.clipboard.writeText(revealedKey.key);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Copy the key from the field before closing this message.");
    }
  };

  return (
    <main className="mx-auto min-w-0 w-full max-w-5xl p-3 sm:p-6" aria-label="Browser access controls">
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <div className="mt-0.5 rounded-lg bg-accent/10 p-2 text-accent">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-gray-100">Browser access controls</h2>
            <p className="mt-1 max-w-2xl text-sm text-gray-500">
              Assign direct and group-based sandbox permissions for people and Paperclip agents.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" className={actionButtonClass} onClick={() => void refresh()} disabled={loading}>
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <button type="button" className={actionButtonClass} onClick={onClose}>
            <X className="h-3.5 w-3.5" />
            Close
          </button>
        </div>
      </header>

      <div className="mb-4 grid grid-cols-2 gap-1 rounded-md border border-border bg-surface-1 p-1" role="tablist" aria-label="Access sections">
        {(["identities", "groups"] as const).map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={activeTab === tab}
            className={`min-h-11 rounded px-3 text-sm font-medium ${activeTab === tab ? "bg-surface-3 text-gray-100" : "text-gray-400"}`}
            onClick={() => setActiveTab(tab)}
          >
            {tab === "identities" ? "Identities" : "Groups"}
          </button>
        ))}
      </div>

      {error && (
        <div role="alert" className="mb-4 rounded-md border border-red-600/30 bg-red-600/15 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      {revealedKey && (
        <section className="mb-4 rounded-md border border-amber-500/30 bg-amber-500/10 p-3" aria-label="New Paperclip agent key notice">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="font-medium text-amber-100">Copy the new key for {revealedKey.name} now</p>
              <p className="mt-1 text-xs text-amber-100/70">It is shown once and is not stored in this dashboard.</p>
            </div>
            <button type="button" className={actionButtonClass} onClick={() => setRevealedKey(null)}>
              <X className="h-3.5 w-3.5" />
              Hide key
            </button>
          </div>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <input className="input min-h-11 min-w-0 font-mono text-xs" value={revealedKey.key} readOnly aria-label="New Paperclip agent key" />
            <button type="button" className={`${primaryActionButtonClass} self-start sm:shrink-0`} onClick={() => void copyKey()}>
              <Copy className="h-3.5 w-3.5" />
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        </section>
      )}

      {activeTab === "identities" ? (
        <section className="min-w-0 space-y-4" role="tabpanel" aria-label="Identities">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <UserRound className="h-4 w-4 text-accent" />
              <h3 className="font-semibold text-gray-100">People</h3>
            </div>
            <button
              type="button"
              className={actionButtonClass}
              onClick={() => {
                setEditingUserId(null);
                setUserDraft(emptyUserDraft());
                setShowUserForm(true);
              }}
            >
              <Plus className="h-3.5 w-3.5" />
              Add person
            </button>
          </div>

          <div className="space-y-2" aria-label="Configured people">
            {loading ? <p className="text-sm text-gray-500">Loading people...</p> : null}
            {!loading && users.length === 0 ? <p className="text-sm text-gray-500">No named users yet.</p> : null}
            {users.map((user) => {
              const names = groupNamesForUser(user, groups);
              return (
                <article key={user.id} className="rounded-md border border-border bg-surface-1 px-3 py-2.5">
                  <div className="flex items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                        <span className="truncate text-sm font-medium">{user.username}</span>
                        <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[10px] uppercase text-gray-400">{user.role}</span>
                        <span className={`rounded px-1.5 py-0.5 text-[10px] ${user.active ? "bg-emerald-500/10 text-emerald-300" : "bg-amber-500/10 text-amber-300"}`}>
                          {user.active ? "active" : "disabled"}
                        </span>
                      </div>
                      {names.length ? (
                        <div className="mt-1 flex min-w-0 flex-wrap gap-1" aria-label={`Groups for ${user.username}`}>
                          {names.map((name) => (
                            <span key={name} className="max-w-full truncate rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">{name}</span>
                          ))}
                        </div>
                      ) : null}
                      <p className="mt-1 truncate text-xs text-gray-500">Direct: {user.role === "admin" ? "All sandboxes (administrator)" : summarizeGrants(user.grants)}</p>
                      <EffectiveAccessPreview
                        label={user.username}
                        profiles={profiles}
                        grants={user.effective_grants ?? user.grants}
                        administrator={user.role === "admin"}
                      />
                    </div>
                    <button
                      type="button"
                      className={iconButtonClass}
                      aria-label={`Edit ${user.username}`}
                      onClick={() => {
                        setEditingUserId(user.id);
                        setUserDraft({
                          username: user.username,
                          password: "",
                          role: user.role,
                          active: user.active,
                          grants: user.grants,
                          group_ids: user.group_ids ?? [],
                        });
                        setShowUserForm(true);
                      }}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </article>
              );
            })}
          </div>

          {showUserForm ? (
            <form onSubmit={saveUser} className="space-y-3 rounded-md border border-border bg-surface-0 p-3">
              <div className="flex items-center justify-between gap-2">
                <h4 className="text-sm font-medium">{editingUserId ? "Edit person" : "Add person"}</h4>
                <button type="button" className={compactActionButtonClass} onClick={() => {
                  setEditingUserId(null);
                  setShowUserForm(false);
                  setUserDraft(emptyUserDraft());
                }}>Cancel</button>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <label>
                  <span className="label">Username</span>
                  <input className="input min-h-11" aria-label="Username" value={userDraft.username} disabled={Boolean(editingUserId)} required minLength={1} maxLength={80} onChange={(event) => setUserDraft((current) => ({ ...current, username: event.target.value }))} />
                </label>
                <label>
                  <span className="label">{editingUserId ? "New password (optional)" : "Password"}</span>
                  <input className="input min-h-11" aria-label="Password" type="password" value={userDraft.password} required={!editingUserId} minLength={12} autoComplete="new-password" onChange={(event) => setUserDraft((current) => ({ ...current, password: event.target.value }))} />
                </label>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <label>
                  <span className="label">Role</span>
                  <select className="input h-11" aria-label="Role" value={userDraft.role} onChange={(event) => setUserDraft((current) => ({ ...current, role: event.target.value as AccessRole }))}>
                    <option value="viewer">Viewer</option>
                    <option value="operator">Operator</option>
                    <option value="admin">Administrator</option>
                  </select>
                </label>
                {editingUserId ? (
                  <label className="flex min-h-11 items-end gap-2 pb-2 text-sm text-gray-300">
                    <input type="checkbox" checked={userDraft.active} onChange={(event) => setUserDraft((current) => ({ ...current, active: event.target.checked }))} />
                    Sign-in active
                  </label>
                ) : null}
              </div>
              {groups.length ? (
                <fieldset className="rounded-md border border-border bg-surface-1 p-3">
                  <legend className="px-1 text-xs font-medium text-gray-300">Groups</legend>
                  <div className="grid gap-1 sm:grid-cols-2">
                    {groups.map((group) => (
                      <label key={group.id} className="flex min-h-11 min-w-0 items-center gap-2 rounded border border-border bg-surface-2 px-2 text-sm text-gray-300">
                        <input
                          type="checkbox"
                          checked={userDraft.group_ids.includes(group.id)}
                          aria-label={`User group ${group.name}`}
                          onChange={(event) => setUserDraft((current) => ({
                            ...current,
                            group_ids: toggleId(current.group_ids, group.id, event.target.checked),
                          }))}
                        />
                        <span className="min-w-0 truncate">{group.name}</span>
                      </label>
                    ))}
                  </div>
                </fieldset>
              ) : null}
              <GrantEditor sandboxes={sandboxes} grants={userDraft.grants} onChange={(grants) => setUserDraft((current) => ({ ...current, grants }))} />
              <button type="submit" disabled={saving || !userDraft.username.trim() || (!editingUserId && userDraft.password.length < 12)} className={`${primaryActionButtonClass} disabled:opacity-50`}>
                <Plus className="h-3.5 w-3.5" />
                {saving ? "Saving..." : editingUserId ? "Save person" : "Create person"}
              </button>
            </form>
          ) : null}

          <div className="flex flex-wrap items-center justify-between gap-2 pt-2">
            <div className="flex items-center gap-2">
              <Bot className="h-4 w-4 text-accent" />
              <h3 className="font-semibold text-gray-100">Paperclip agents</h3>
            </div>
            <button
              type="button"
              className={actionButtonClass}
              onClick={() => {
                setEditingAgentId(null);
                setAgentDraft(emptyAgentDraft());
                setShowAgentForm(true);
              }}
            >
              <KeyRound className="h-3.5 w-3.5" />
              Create agent key
            </button>
          </div>

          <div className="space-y-2" aria-label="Configured Paperclip agents">
            {loading ? <p className="text-sm text-gray-500">Loading agents...</p> : null}
            {!loading && agents.length === 0 ? <p className="text-sm text-gray-500">No Paperclip agents yet.</p> : null}
            {agents.map((agent) => (
              <article key={agent.id} className="rounded-md border border-border bg-surface-1 px-3 py-2.5">
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                      <span className="truncate text-sm font-medium">{agent.display_name}</span>
                      <span className={`rounded px-1.5 py-0.5 text-[10px] ${agent.active ? "bg-emerald-500/10 text-emerald-300" : "bg-amber-500/10 text-amber-300"}`}>
                        {agent.active ? "active" : "disabled"}
                      </span>
                    </div>
                    {agent.paperclip_agent_id && <p className="mt-0.5 truncate font-mono text-[11px] text-gray-500">{agent.paperclip_agent_id}</p>}
                    <p className="mt-1 truncate text-xs text-gray-500">{summarizeGrants(agent.grants)}</p>
                    <EffectiveAccessPreview
                      label={agent.display_name}
                      profiles={profiles}
                      grants={agent.grants}
                    />
                  </div>
                  <div className="flex gap-1">
                    <button type="button" className={iconButtonClass} aria-label={`Rotate key for ${agent.display_name}`} title="Rotate key" disabled={saving} onClick={() => void rotateAgentKey(agent)}>
                      <RefreshCw className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      className={iconButtonClass}
                      aria-label={`Edit ${agent.display_name}`}
                      onClick={() => {
                        setEditingAgentId(agent.id);
                        setAgentDraft({
                          display_name: agent.display_name,
                          paperclip_agent_id: agent.paperclip_agent_id ?? "",
                          active: agent.active,
                          grants: agent.grants,
                        });
                        setShowAgentForm(true);
                      }}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              </article>
            ))}
          </div>

          {showAgentForm ? (
            <form onSubmit={saveAgent} className="space-y-3 rounded-md border border-border bg-surface-0 p-3">
              <div className="flex items-center justify-between gap-2">
                <h4 className="text-sm font-medium">{editingAgentId ? "Edit Paperclip agent" : "Add Paperclip agent"}</h4>
                <button type="button" className={compactActionButtonClass} onClick={() => {
                  setEditingAgentId(null);
                  setShowAgentForm(false);
                  setAgentDraft(emptyAgentDraft());
                }}>Cancel</button>
              </div>
              <label>
                <span className="label">Display name</span>
                <input className="input min-h-11" aria-label="Display name" value={agentDraft.display_name} required maxLength={120} onChange={(event) => setAgentDraft((current) => ({ ...current, display_name: event.target.value }))} />
              </label>
              <label>
                <span className="label">Paperclip agent ID (optional)</span>
                <input className="input min-h-11 font-mono" aria-label="Paperclip agent ID (optional)" value={agentDraft.paperclip_agent_id} maxLength={160} placeholder="paperclip-agent-research" onChange={(event) => setAgentDraft((current) => ({ ...current, paperclip_agent_id: event.target.value }))} />
              </label>
              {editingAgentId ? (
                <label className="flex min-h-11 items-center gap-2 text-sm text-gray-300">
                  <input type="checkbox" checked={agentDraft.active} onChange={(event) => setAgentDraft((current) => ({ ...current, active: event.target.checked }))} />
                  Agent key active
                </label>
              ) : null}
              <GrantEditor sandboxes={sandboxes} grants={agentDraft.grants} onChange={(grants) => setAgentDraft((current) => ({ ...current, grants }))} />
              <button type="submit" disabled={saving || !agentDraft.display_name.trim()} className={`${primaryActionButtonClass} disabled:opacity-50`}>
                <KeyRound className="h-3.5 w-3.5" />
                {saving ? "Saving..." : editingAgentId ? "Save agent" : "Create key"}
              </button>
            </form>
          ) : null}
        </section>
      ) : (
        <section className="min-w-0 space-y-4" role="tabpanel" aria-label="Groups">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <UsersRound className="h-4 w-4 text-accent" />
              <h3 className="font-semibold text-gray-100">Groups</h3>
            </div>
            <button
              type="button"
              className={actionButtonClass}
              onClick={() => {
                setEditingGroupId(null);
                setGroupDraft(emptyGroupDraft());
                setShowGroupForm(true);
              }}
            >
              <Plus className="h-3.5 w-3.5" />
              Add group
            </button>
          </div>

          <div className="space-y-2" aria-label="Configured groups">
            {loading ? <p className="text-sm text-gray-500">Loading groups...</p> : null}
            {!loading && groups.length === 0 ? <p className="text-sm text-gray-500">No access groups yet.</p> : null}
            {groups.map((group) => {
              const names = memberNames(group, users);
              return (
                <article key={group.id} className="rounded-md border border-border bg-surface-1 px-3 py-2.5">
                  <div className="flex items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                        <span className="truncate text-sm font-medium">{group.name}</span>
                        <span className={`rounded px-1.5 py-0.5 text-[10px] ${group.active ? "bg-emerald-500/10 text-emerald-300" : "bg-amber-500/10 text-amber-300"}`}>
                          {group.active ? "active" : "disabled"}
                        </span>
                        <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[10px] text-gray-400">
                          {group.member_user_ids.length} member{group.member_user_ids.length === 1 ? "" : "s"}
                        </span>
                      </div>
                      {group.description ? <p className="mt-1 line-clamp-2 text-xs text-gray-500">{group.description}</p> : null}
                      <p className="mt-1 truncate text-xs text-gray-500">Members: {names.length ? names.join(", ") : "None"}</p>
                      <p className="mt-1 truncate text-xs text-gray-500">{summarizeGrants(group.grants)}</p>
                    </div>
                    <button
                      type="button"
                      className={iconButtonClass}
                      aria-label={`Edit group ${group.name}`}
                      onClick={() => {
                        setEditingGroupId(group.id);
                        setGroupDraft({
                          name: group.name,
                          description: group.description ?? "",
                          active: group.active,
                          member_user_ids: group.member_user_ids,
                          grants: group.grants,
                        });
                        setShowGroupForm(true);
                      }}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </article>
              );
            })}
          </div>

          {showGroupForm ? (
            <form onSubmit={saveGroup} className="space-y-3 rounded-md border border-border bg-surface-0 p-3">
              <div className="flex items-center justify-between gap-2">
                <h4 className="text-sm font-medium">{editingGroupId ? "Edit group" : "Add group"}</h4>
                <button type="button" className={compactActionButtonClass} onClick={() => {
                  setEditingGroupId(null);
                  setShowGroupForm(false);
                  setGroupDraft(emptyGroupDraft());
                }}>Cancel</button>
              </div>
              <label>
                <span className="label">Group name</span>
                <input className="input min-h-11" aria-label="Group name" value={groupDraft.name} required minLength={1} maxLength={120} onChange={(event) => setGroupDraft((current) => ({ ...current, name: event.target.value }))} />
              </label>
              <label>
                <span className="label">Short description</span>
                <textarea className="input min-h-11" aria-label="Short description" value={groupDraft.description} maxLength={500} rows={2} onChange={(event) => setGroupDraft((current) => ({ ...current, description: event.target.value }))} />
              </label>
              <label className="flex min-h-11 items-center gap-2 text-sm text-gray-300">
                <input type="checkbox" checked={groupDraft.active} aria-label="Group active" onChange={(event) => setGroupDraft((current) => ({ ...current, active: event.target.checked }))} />
                Group active
              </label>
              <MembershipEditor
                users={users}
                selectedUserIds={groupDraft.member_user_ids}
                onChange={(member_user_ids) => setGroupDraft((current) => ({ ...current, member_user_ids }))}
              />
              <GrantEditor sandboxes={sandboxes} grants={groupDraft.grants} onChange={(grants) => setGroupDraft((current) => ({ ...current, grants }))} />
              <button type="submit" disabled={saving || !groupDraft.name.trim()} className={`${primaryActionButtonClass} disabled:opacity-50`}>
                <Plus className="h-3.5 w-3.5" />
                {saving ? "Saving..." : editingGroupId ? "Save group" : "Create group"}
              </button>
            </form>
          ) : null}
        </section>
      )}
    </main>
  );
}
