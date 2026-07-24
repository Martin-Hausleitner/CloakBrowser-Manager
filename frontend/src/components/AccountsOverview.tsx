import { KeyRound, Shield, Link2, CircleDot, UserRound } from "lucide-react";
import type { Profile } from "../lib/api";
import { harnessLabel } from "../lib/harnessOptions";

export type AccountSessionState = "active" | "idle" | "unknown";
export type AccountAuthState = "unknown" | "signed_in" | "needs_2fa" | "inactive";
export type SyncProviderState = "off" | "planned" | "linked";

export type AccountRow = {
  profileId: string;
  name: string;
  projectId: string;
  harness: string;
  session: AccountSessionState;
  auth: AccountAuthState;
  bitwarden: SyncProviderState;
  keypad: SyncProviderState;
  notes: string;
};

/** Derive redacted account rows from profiles — never invent credentials or cookies. */
export function deriveAccountRows(profiles: Profile[]): AccountRow[] {
  return profiles.map((profile) => {
    const tags = (profile.tags ?? []).map((entry) => entry.tag.toLowerCase());
    const notes = (profile.notes ?? "").toLowerCase();
    const hint = `${tags.join(" ")} ${notes}`;

    let auth: AccountAuthState = "unknown";
    if (/\b(needs[-_ ]?2fa|2fa[-_ ]?pending|mfa[-_ ]?pending)\b/.test(hint)) {
      auth = "needs_2fa";
    } else if (/\b(signed[-_ ]?in|logged[-_ ]?in|active[-_ ]?account)\b/.test(hint)) {
      auth = "signed_in";
    } else if (/\b(inactive|signed[-_ ]?out|logged[-_ ]?out)\b/.test(hint)) {
      auth = "inactive";
    }

    const session: AccountSessionState =
      profile.status === "running" ? "active" : profile.status === "stopped" ? "idle" : "unknown";

    return {
      profileId: profile.id,
      name: profile.name,
      projectId: profile.project_id || "default",
      harness: profile.harness,
      session,
      auth,
      bitwarden: "planned",
      keypad: "planned",
      notes:
        auth === "unknown"
          ? "No login/2FA label yet — tag profile notes (signed-in, needs-2fa) without storing credentials."
          : "Derived from profile tags/notes only; credentials stay out of Manager UI.",
    };
  });
}

const sessionBadge: Record<AccountSessionState, string> = {
  active: "bg-emerald-500/15 text-emerald-300",
  idle: "bg-surface-3 text-gray-400",
  unknown: "bg-surface-3 text-gray-500",
};

const authBadge: Record<AccountAuthState, string> = {
  signed_in: "bg-sky-500/15 text-sky-300",
  needs_2fa: "bg-amber-500/15 text-amber-300",
  inactive: "bg-surface-3 text-gray-500",
  unknown: "bg-surface-3 text-gray-500",
};

const syncBadge: Record<SyncProviderState, string> = {
  linked: "bg-emerald-500/15 text-emerald-300",
  planned: "bg-violet-500/15 text-violet-300",
  off: "bg-surface-3 text-gray-500",
};

interface AccountsOverviewProps {
  profiles: Profile[];
  selectedId: string | null;
  onSelect: (profileId: string) => void;
}

export function AccountsOverview({ profiles, selectedId, onSelect }: AccountsOverviewProps) {
  const rows = deriveAccountRows(profiles);

  return (
    <div className="mx-auto flex h-full max-w-4xl flex-col gap-4 p-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-100">Accounts &amp; 2FA</h2>
          <p className="mt-1 text-sm text-gray-500">
            Logged-in state, two-factor officers, and future Bitwarden / Keypad sync — redacted,
            no passwords or cookies in Manager UI.
          </p>
        </div>
      </div>

      <div className="grid gap-2 sm:grid-cols-3">
        <div className="rounded-xl border border-border bg-surface-1 px-3 py-3">
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <UserRound className="h-3.5 w-3.5" /> Profiles
          </div>
          <div className="mt-1 text-lg font-semibold text-gray-100">{rows.length}</div>
        </div>
        <div className="rounded-xl border border-border bg-surface-1 px-3 py-3">
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <CircleDot className="h-3.5 w-3.5" /> Live sessions
          </div>
          <div className="mt-1 text-lg font-semibold text-gray-100">
            {rows.filter((row) => row.session === "active").length}
          </div>
        </div>
        <div className="rounded-xl border border-border bg-surface-1 px-3 py-3">
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <Shield className="h-3.5 w-3.5" /> Needs 2FA review
          </div>
          <div className="mt-1 text-lg font-semibold text-gray-100">
            {rows.filter((row) => row.auth === "needs_2fa").length}
          </div>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-border bg-surface-1 px-6 py-16 text-center">
          <KeyRound className="mb-3 h-8 w-8 text-gray-600" />
          <p className="text-sm text-gray-400">No profiles yet.</p>
          <p className="mt-1 text-xs text-gray-600">
            Create a profile, then label auth state in notes/tags without storing credentials.
          </p>
        </div>
      ) : (
        <div className="space-y-2 overflow-y-auto">
          {rows.map((row) => {
            const selected = row.profileId === selectedId;
            return (
              <button
                key={row.profileId}
                type="button"
                onClick={() => onSelect(row.profileId)}
                className={`w-full rounded-xl border px-4 py-3 text-left transition-colors ${
                  selected
                    ? "border-accent/40 bg-accent/10"
                    : "border-border bg-surface-1 hover:bg-surface-2"
                }`}
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-gray-100">{row.name}</div>
                    <div className="mt-0.5 text-xs text-gray-500">
                      {row.projectId} · {harnessLabel(row.harness)}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] uppercase tracking-wide ${sessionBadge[row.session]}`}
                    >
                      session {row.session}
                    </span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] uppercase tracking-wide ${authBadge[row.auth]}`}
                    >
                      {row.auth.replace("_", " ")}
                    </span>
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                  <span
                    className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 ${syncBadge[row.bitwarden]}`}
                  >
                    <Link2 className="h-3 w-3" /> Bitwarden · {row.bitwarden}
                  </span>
                  <span
                    className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 ${syncBadge[row.keypad]}`}
                  >
                    <KeyRound className="h-3 w-3" /> Keypad · {row.keypad}
                  </span>
                </div>
                <p className="mt-2 text-[11px] text-gray-600">{row.notes}</p>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
