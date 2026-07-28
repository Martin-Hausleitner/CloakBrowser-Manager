import type { ICellRendererParams, ValueGetterParams } from "ag-grid-community";
import { ExternalLink, KeyRound, Search, Shield, Link2, CircleDot, UserRound } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { Profile } from "../lib/api";
import { harnessLabel } from "../lib/harnessOptions";
import { CompactDataGrid } from "./data-grid/CompactDataGrid";

const ACCOUNT_GRID_FALLBACK_QUERY = "(max-width: 767px), (pointer: coarse)";

export type AccountSessionState = "active" | "idle" | "unknown";
export type AccountAuthState = "unknown" | "signed_in" | "needs_2fa" | "inactive";
export type SyncProviderState = "off" | "planned" | "linked";

export type AccountRow = {
  id: string;
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

/** Derive redacted account rows from profiles - never invent credentials or cookies. */
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
      id: profile.id,
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
          ? "No login/2FA label yet - tag profile notes (signed-in, needs-2fa) without storing credentials."
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

function accountStateLabel(value: AccountSessionState | AccountAuthState | SyncProviderState) {
  return value.replace(/_/g, " ");
}

function StatusBadge({ value, className }: { value: string; className: string }) {
  return <span className={`rounded-full px-2 py-0.5 text-[10px] uppercase tracking-wide ${className}`}>{value}</span>;
}

function useAccountsGridFallback() {
  const [fallback, setFallback] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia(ACCOUNT_GRID_FALLBACK_QUERY).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;
    const query = window.matchMedia(ACCOUNT_GRID_FALLBACK_QUERY);
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

export function AccountsOverview({ profiles, selectedId, onSelect }: AccountsOverviewProps) {
  const [quickFilter, setQuickFilter] = useState("");
  const [needs2faOnly, setNeeds2faOnly] = useState(false);
  const useFallbackCards = useAccountsGridFallback();
  const rows = useMemo(() => deriveAccountRows(profiles), [profiles]);
  const gridRows = useMemo(
    () => (needs2faOnly ? rows.filter((row) => row.auth === "needs_2fa") : rows),
    [needs2faOnly, rows],
  );

  const columns = useMemo(
    () => [
      {
        headerName: "Profile",
        field: "name" as const,
        minWidth: 170,
        flex: 1.2,
        cellRenderer: ({ data, value }: ICellRendererParams<AccountRow, string>) =>
          data ? (
            <button
              type="button"
              className="min-w-0 text-left font-medium text-gray-100 hover:text-white"
              onClick={(event) => {
                event.stopPropagation();
                onSelect(data.profileId);
              }}
              aria-label={`Select ${data.name}`}
            >
              <span className="block truncate">{value}</span>
            </button>
          ) : null,
      },
      { headerName: "Project", field: "projectId" as const, minWidth: 110, flex: 0.8 },
      {
        headerName: "Harness",
        valueGetter: ({ data }: ValueGetterParams<AccountRow>) => harnessLabel(data?.harness),
        width: 130,
      },
      {
        headerName: "Session",
        field: "session" as const,
        width: 115,
        cellRenderer: ({ value }: ICellRendererParams<AccountRow, AccountSessionState>) =>
          value ? <StatusBadge value={accountStateLabel(value)} className={sessionBadge[value]} /> : null,
      },
      {
        headerName: "Auth",
        field: "auth" as const,
        width: 120,
        cellRenderer: ({ value }: ICellRendererParams<AccountRow, AccountAuthState>) =>
          value ? <StatusBadge value={accountStateLabel(value)} className={authBadge[value]} /> : null,
      },
      {
        headerName: "Bitwarden",
        field: "bitwarden" as const,
        width: 120,
        cellRenderer: ({ value }: ICellRendererParams<AccountRow, SyncProviderState>) =>
          value ? <StatusBadge value={accountStateLabel(value)} className={syncBadge[value]} /> : null,
      },
      {
        headerName: "Keypad",
        field: "keypad" as const,
        width: 105,
        cellRenderer: ({ value }: ICellRendererParams<AccountRow, SyncProviderState>) =>
          value ? <StatusBadge value={accountStateLabel(value)} className={syncBadge[value]} /> : null,
      },
      { headerName: "Notes", field: "notes" as const, minWidth: 240, flex: 1.6 },
      {
        headerName: "Actions",
        sortable: false,
        filter: false,
        width: 96,
        cellRenderer: ({ data }: ICellRendererParams<AccountRow>) =>
          data ? (
            <button
              type="button"
              className="inline-flex h-7 items-center justify-center rounded border border-border bg-surface-3 px-2 text-[11px] text-gray-200 hover:bg-surface-4 focus:outline-none focus:ring-2 focus:ring-accent/50"
              onClick={(event) => {
                event.stopPropagation();
                onSelect(data.profileId);
              }}
              aria-label={`Open ${data.name}`}
              title="Open"
            >
              <ExternalLink className="h-3.5 w-3.5" />
            </button>
          ) : null,
      },
    ],
    [onSelect],
  );

  return (
    <div className="flex h-full w-full max-w-none flex-col gap-3 p-3 lg:p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-100">Accounts &amp; 2FA</h2>
          <p className="mt-1 text-sm text-gray-500">
            Logged-in state, two-factor officers, and future Bitwarden / Keypad sync - redacted,
            no passwords or cookies in Manager UI.
          </p>
        </div>
      </div>

      <div className="grid gap-2 sm:grid-cols-3">
        <div className="rounded-xl border border-border bg-surface-1 px-3 py-3">
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <UserRound className="h-3.5 w-3.5" /> Profiles
          </div>
          <div className="mt-1 text-lg font-semibold text-gray-100" aria-label="Profiles count">
            {rows.length}
          </div>
        </div>
        <div className="rounded-xl border border-border bg-surface-1 px-3 py-3">
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <CircleDot className="h-3.5 w-3.5" /> Live sessions
          </div>
          <div className="mt-1 text-lg font-semibold text-gray-100" aria-label="Live sessions count">
            {rows.filter((row) => row.session === "active").length}
          </div>
        </div>
        <div className="rounded-xl border border-border bg-surface-1 px-3 py-3">
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <Shield className="h-3.5 w-3.5" /> Needs 2FA review
          </div>
          <div className="mt-1 text-lg font-semibold text-gray-100" aria-label="Needs 2FA review count">
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
      ) : useFallbackCards ? (
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
                      {accountStateLabel(row.auth)}
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
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-0 flex-1 sm:max-w-sm">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-500" />
              <input
                type="text"
                className="input h-8 pl-8 text-xs"
                value={quickFilter}
                onChange={(event) => setQuickFilter(event.target.value)}
                placeholder="Search accounts..."
                aria-label="Search accounts grid"
              />
            </div>
            <button
              type="button"
              className={`btn h-8 text-xs ${needs2faOnly ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setNeeds2faOnly((value) => !value)}
              aria-label={needs2faOnly ? "Show all accounts" : "Show needs 2FA accounts"}
            >
              Needs 2FA
            </button>
          </div>
          <CompactDataGrid<AccountRow>
            ariaLabel="Accounts and 2FA grid"
            storageKey="accounts"
            columns={columns}
            rows={gridRows}
            quickFilterText={quickFilter}
            selectedId={selectedId}
            onRowClick={(row) => onSelect(row.profileId)}
            testId="accounts-desktop-grid"
          />
        </div>
      )}
    </div>
  );
}
