import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  Copy,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  ShieldCheck,
  UserRound,
  X,
} from "lucide-react";
import {
  api,
  type AccessAgent,
  type AccessGrant,
  type AccessPermission,
  type AccessRole,
  type AccessSandbox,
  type AccessUser,
} from "../lib/api";

interface AccessDashboardProps {
  onClose: () => void;
}

interface UserDraft {
  username: string;
  password: string;
  role: AccessRole;
  active: boolean;
  grants: AccessGrant[];
}

interface AgentDraft {
  display_name: string;
  paperclip_agent_id: string;
  active: boolean;
  grants: AccessGrant[];
}

const emptyUserDraft = (): UserDraft => ({
  username: "",
  password: "",
  role: "viewer",
  active: true,
  grants: [],
});

const emptyAgentDraft = (): AgentDraft => ({
  display_name: "",
  paperclip_agent_id: "",
  active: true,
  grants: [],
});

const permissionLabel: Record<AccessPermission, string> = {
  view: "View",
  interact: "Interact",
  operate: "Operate",
  automate: "Automate",
};

function summarizeGrants(grants: AccessGrant[]) {
  return grants.length
    ? grants.map((grant) => `${grant.sandbox_id}: ${permissionLabel[grant.permission]}`).join(" · ")
    : "No sandbox access";
}

function updateGrant(
  grants: AccessGrant[],
  sandboxId: string,
  permission: "" | AccessPermission,
) {
  const withoutSandbox = grants.filter((grant) => grant.sandbox_id !== sandboxId);
  return permission ? [...withoutSandbox, { sandbox_id: sandboxId, permission }] : withoutSandbox;
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
        known.set(grant.sandbox_id, { sandbox_id: grant.sandbox_id, profile_count: 0 });
      }
    }
    return [...known.values()].sort((left, right) => left.sandbox_id.localeCompare(right.sandbox_id));
  }, [grants, sandboxes]);

  return (
    <fieldset className="rounded-md border border-border bg-surface-1 p-3">
      <legend className="px-1 text-xs font-medium text-gray-300">Sandbox access</legend>
      {allSandboxes.length ? (
        <div className="space-y-2">
          {allSandboxes.map((sandbox) => {
            const permission = grants.find((grant) => grant.sandbox_id === sandbox.sandbox_id)?.permission ?? "";
            return (
              <label key={sandbox.sandbox_id} className="flex items-center gap-3 text-sm">
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-gray-300">
                  {sandbox.sandbox_id}
                  <span className="ml-1 font-sans text-gray-500">({sandbox.profile_count} browser{sandbox.profile_count === 1 ? "" : "s"})</span>
                </span>
                <select
                  className="input h-8 w-32 py-1 text-xs"
                  value={permission}
                  aria-label={`Permission for ${sandbox.sandbox_id}`}
                  onChange={(event) => onChange(updateGrant(
                    grants,
                    sandbox.sandbox_id,
                    event.target.value as "" | AccessPermission,
                  ))}
                >
                  <option value="">No access</option>
                  {(Object.keys(permissionLabel) as AccessPermission[]).map((value) => (
                    <option key={value} value={value}>{permissionLabel[value]}</option>
                  ))}
                </select>
              </label>
            );
          })}
        </div>
      ) : (
        <p className="text-xs text-gray-500">Create a browser profile and assign it a sandbox first.</p>
      )}
      <p className="mt-2 text-[11px] leading-4 text-gray-500">
        View shows the live browser. Interact permits VNC and clipboard input. Operate can launch or stop it. Automate permits CDP.
      </p>
    </fieldset>
  );
}

export function AccessDashboard({ onClose }: AccessDashboardProps) {
  const [users, setUsers] = useState<AccessUser[]>([]);
  const [agents, setAgents] = useState<AccessAgent[]>([]);
  const [sandboxes, setSandboxes] = useState<AccessSandbox[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [editingUserId, setEditingUserId] = useState<string | null>(null);
  const [editingAgentId, setEditingAgentId] = useState<string | null>(null);
  const [userDraft, setUserDraft] = useState<UserDraft>(emptyUserDraft);
  const [agentDraft, setAgentDraft] = useState<AgentDraft>(emptyAgentDraft);
  const [revealedKey, setRevealedKey] = useState<{ name: string; key: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [nextUsers, nextAgents, nextSandboxes] = await Promise.all([
        api.listAccessUsers(),
        api.listAccessAgents(),
        api.listAccessSandboxes(),
      ]);
      setUsers(nextUsers);
      setAgents(nextAgents);
      setSandboxes(nextSandboxes);
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
          password?: string;
        } = {
          role: userDraft.role,
          active: userDraft.active,
          grants: userDraft.grants,
        };
        if (userDraft.password) data.password = userDraft.password;
        await api.updateAccessUser(editingUserId, data);
      } else {
        await api.createAccessUser({
          username: userDraft.username.trim(),
          password: userDraft.password,
          role: userDraft.role,
          grants: userDraft.grants,
        });
      }
      setEditingUserId(null);
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
      setAgentDraft(emptyAgentDraft());
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save Paperclip agent");
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
    <main className="mx-auto w-full max-w-6xl p-4 sm:p-6" aria-label="Browser access controls">
      <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <div className="mt-0.5 rounded-lg bg-accent/10 p-2 text-accent">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-gray-100">Browser access controls</h2>
            <p className="mt-1 max-w-2xl text-sm text-gray-500">
              Give people and Paperclip agents only the browser sandboxes they need. The server enforces these permissions for the dashboard, VNC and CDP.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" className="btn-secondary inline-flex items-center gap-1.5" onClick={() => void refresh()} disabled={loading}>
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <button type="button" className="btn-secondary inline-flex items-center gap-1.5" onClick={onClose}>
            <X className="h-3.5 w-3.5" />
            Close
          </button>
        </div>
      </header>

      {error && (
        <div role="alert" className="mb-4 rounded-md border border-red-600/30 bg-red-600/15 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      {revealedKey && (
        <section className="mb-5 rounded-md border border-amber-500/30 bg-amber-500/10 p-4" aria-label="New Paperclip agent key notice">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="font-medium text-amber-100">Copy the new key for {revealedKey.name} now</p>
              <p className="mt-1 text-xs text-amber-100/70">It is shown once and is not stored in this dashboard.</p>
            </div>
            <button type="button" className="btn-secondary inline-flex items-center gap-1.5" onClick={() => setRevealedKey(null)}>
              <X className="h-3.5 w-3.5" />
              Hide key
            </button>
          </div>
          <div className="mt-3 flex gap-2">
            <input className="input font-mono text-xs" value={revealedKey.key} readOnly aria-label="New Paperclip agent key" />
            <button type="button" className="btn-primary inline-flex shrink-0 items-center gap-1.5" onClick={() => void copyKey()}>
              <Copy className="h-3.5 w-3.5" />
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        </section>
      )}

      <div className="grid gap-5 xl:grid-cols-2">
        <section className="rounded-lg border border-border bg-surface-0 p-4">
          <div className="mb-4 flex items-center gap-2">
            <UserRound className="h-4 w-4 text-accent" />
            <div>
              <h3 className="font-semibold text-gray-100">People</h3>
              <p className="text-xs text-gray-500">Named dashboard sign-ins with individual sandbox access.</p>
            </div>
          </div>

          <div className="mb-5 space-y-2" aria-label="Configured people">
            {loading ? <p className="text-sm text-gray-500">Loading people…</p> : null}
            {!loading && users.length === 0 ? <p className="text-sm text-gray-500">No named users yet.</p> : null}
            {users.map((user) => (
              <article key={user.id} className="rounded-md border border-border bg-surface-1 px-3 py-2.5">
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">{user.username}</span>
                      <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[10px] uppercase text-gray-400">{user.role}</span>
                      {!user.active && <span className="text-[10px] text-amber-300">disabled</span>}
                    </div>
                    <p className="mt-1 truncate text-xs text-gray-500">{user.role === "admin" ? "All sandboxes (administrator)" : summarizeGrants(user.grants)}</p>
                  </div>
                  <button
                    type="button"
                    className="mobile-icon-button h-9 w-9"
                    aria-label={`Edit ${user.username}`}
                    onClick={() => {
                      setEditingUserId(user.id);
                      setUserDraft({
                        username: user.username,
                        password: "",
                        role: user.role,
                        active: user.active,
                        grants: user.grants,
                      });
                    }}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                </div>
              </article>
            ))}
          </div>

          <form onSubmit={saveUser} className="space-y-3 border-t border-border pt-4">
            <div className="flex items-center justify-between gap-2">
              <h4 className="text-sm font-medium">{editingUserId ? "Edit person" : "Add person"}</h4>
              {editingUserId && (
                <button type="button" className="text-xs text-gray-500 underline" onClick={() => {
                  setEditingUserId(null);
                  setUserDraft(emptyUserDraft());
                }}>Cancel edit</button>
              )}
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label>
                <span className="label">Username</span>
                <input className="input" value={userDraft.username} disabled={Boolean(editingUserId)} required minLength={1} maxLength={80} onChange={(event) => setUserDraft((current) => ({ ...current, username: event.target.value }))} />
              </label>
              <label>
                <span className="label">{editingUserId ? "New password (optional)" : "Password"}</span>
                <input className="input" type="password" value={userDraft.password} required={!editingUserId} minLength={12} autoComplete="new-password" onChange={(event) => setUserDraft((current) => ({ ...current, password: event.target.value }))} />
              </label>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label>
                <span className="label">Role</span>
                <select className="input" value={userDraft.role} onChange={(event) => setUserDraft((current) => ({ ...current, role: event.target.value as AccessRole }))}>
                  <option value="viewer">Viewer</option>
                  <option value="operator">Operator</option>
                  <option value="admin">Administrator</option>
                </select>
              </label>
              {editingUserId ? (
                <label className="flex items-end gap-2 pb-2 text-sm text-gray-300">
                  <input type="checkbox" checked={userDraft.active} onChange={(event) => setUserDraft((current) => ({ ...current, active: event.target.checked }))} />
                  Sign-in active
                </label>
              ) : null}
            </div>
            <GrantEditor sandboxes={sandboxes} grants={userDraft.grants} onChange={(grants) => setUserDraft((current) => ({ ...current, grants }))} />
            <button type="submit" disabled={saving || !userDraft.username.trim() || (!editingUserId && userDraft.password.length < 12)} className="btn-primary inline-flex items-center gap-1.5 disabled:opacity-50">
              <Plus className="h-3.5 w-3.5" />
              {saving ? "Saving…" : editingUserId ? "Save person" : "Add person"}
            </button>
          </form>
        </section>

        <section className="rounded-lg border border-border bg-surface-0 p-4">
          <div className="mb-4 flex items-center gap-2">
            <KeyRound className="h-4 w-4 text-accent" />
            <div>
              <h3 className="font-semibold text-gray-100">Paperclip agents</h3>
              <p className="text-xs text-gray-500">Opaque keys that can only access their assigned sandboxes.</p>
            </div>
          </div>

          <div className="mb-5 space-y-2" aria-label="Configured Paperclip agents">
            {loading ? <p className="text-sm text-gray-500">Loading agents…</p> : null}
            {!loading && agents.length === 0 ? <p className="text-sm text-gray-500">No Paperclip agents yet.</p> : null}
            {agents.map((agent) => (
              <article key={agent.id} className="rounded-md border border-border bg-surface-1 px-3 py-2.5">
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">{agent.display_name}</span>
                      {!agent.active && <span className="text-[10px] text-amber-300">disabled</span>}
                    </div>
                    {agent.paperclip_agent_id && <p className="mt-0.5 truncate font-mono text-[11px] text-gray-500">{agent.paperclip_agent_id}</p>}
                    <p className="mt-1 truncate text-xs text-gray-500">{summarizeGrants(agent.grants)}</p>
                  </div>
                  <div className="flex gap-1">
                    <button type="button" className="mobile-icon-button h-9 w-9" aria-label={`Rotate key for ${agent.display_name}`} title="Rotate key" disabled={saving} onClick={() => void rotateAgentKey(agent)}>
                      <RefreshCw className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      className="mobile-icon-button h-9 w-9"
                      aria-label={`Edit ${agent.display_name}`}
                      onClick={() => {
                        setEditingAgentId(agent.id);
                        setAgentDraft({
                          display_name: agent.display_name,
                          paperclip_agent_id: agent.paperclip_agent_id ?? "",
                          active: agent.active,
                          grants: agent.grants,
                        });
                      }}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              </article>
            ))}
          </div>

          <form onSubmit={saveAgent} className="space-y-3 border-t border-border pt-4">
            <div className="flex items-center justify-between gap-2">
              <h4 className="text-sm font-medium">{editingAgentId ? "Edit Paperclip agent" : "Add Paperclip agent"}</h4>
              {editingAgentId && (
                <button type="button" className="text-xs text-gray-500 underline" onClick={() => {
                  setEditingAgentId(null);
                  setAgentDraft(emptyAgentDraft());
                }}>Cancel edit</button>
              )}
            </div>
            <label>
              <span className="label">Display name</span>
              <input className="input" value={agentDraft.display_name} required maxLength={120} onChange={(event) => setAgentDraft((current) => ({ ...current, display_name: event.target.value }))} />
            </label>
            <label>
              <span className="label">Paperclip agent ID (optional)</span>
              <input className="input font-mono" value={agentDraft.paperclip_agent_id} maxLength={160} placeholder="paperclip-agent-research" onChange={(event) => setAgentDraft((current) => ({ ...current, paperclip_agent_id: event.target.value }))} />
            </label>
            {editingAgentId ? (
              <label className="flex items-center gap-2 text-sm text-gray-300">
                <input type="checkbox" checked={agentDraft.active} onChange={(event) => setAgentDraft((current) => ({ ...current, active: event.target.checked }))} />
                Agent key active
              </label>
            ) : null}
            <GrantEditor sandboxes={sandboxes} grants={agentDraft.grants} onChange={(grants) => setAgentDraft((current) => ({ ...current, grants }))} />
            <button type="submit" disabled={saving || !agentDraft.display_name.trim()} className="btn-primary inline-flex items-center gap-1.5 disabled:opacity-50">
              <KeyRound className="h-3.5 w-3.5" />
              {saving ? "Saving…" : editingAgentId ? "Save agent" : "Create agent key"}
            </button>
          </form>
        </section>
      </div>
    </main>
  );
}
