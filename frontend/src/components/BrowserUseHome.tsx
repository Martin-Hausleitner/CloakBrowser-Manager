import { useMemo, useState } from "react";
import { Paperclip, Settings2, ArrowUp, SlidersHorizontal } from "lucide-react";
import type { Profile, ProfileHarness } from "../lib/api";
import { CALLABLE_BROWSER_HARNESSES, HARNESS_OPTIONS, harnessLabel } from "../lib/harnessOptions";

interface BrowserUseHomeProps {
  projects: string[];
  projectId: string;
  harness: ProfileHarness;
  profiles: Profile[];
  task: string;
  canManage: boolean;
  onProjectChange: (projectId: string) => void;
  onHarnessChange: (harness: ProfileHarness) => void;
  onTaskChange: (task: string) => void;
  onOpenSettings: (profileId: string | null) => void;
  onOpenProxies: () => void;
  onOpenProfiles: () => void;
  onOpenAccounts: () => void;
  onCreateProjectProfile: () => void;
  onLaunchSelected: () => void;
  selectedProfile: Profile | null;
}

export function BrowserUseHome({
  projects,
  projectId,
  harness,
  profiles,
  task,
  canManage,
  onProjectChange,
  onHarnessChange,
  onTaskChange,
  onOpenSettings,
  onOpenProxies,
  onOpenProfiles,
  onOpenAccounts,
  onCreateProjectProfile,
  onLaunchSelected,
  selectedProfile,
}: BrowserUseHomeProps) {
  const [settingsOpen, setSettingsOpen] = useState(true);
  const projectProfiles = profiles.filter(
    (profile) => (profile.project_id || "default") === projectId,
  );
  const selectedOption = useMemo(
    () => HARNESS_OPTIONS.find((option) => option.value === harness),
    [harness],
  );

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <label className="sr-only" htmlFor="home-project">
            Project
          </label>
          <select
            id="home-project"
            className="input max-w-[14rem] py-1.5 text-xs"
            value={projectId}
            onChange={(event) => onProjectChange(event.target.value)}
          >
            {projects.map((project) => (
              <option key={project} value={project}>
                {project}
              </option>
            ))}
            {!projects.includes(projectId) ? (
              <option value={projectId}>{projectId}</option>
            ) : null}
          </select>
          <span className="hidden text-xs text-gray-600 sm:inline">/</span>
          <label className="sr-only" htmlFor="home-harness">
            Browser harness
          </label>
          <select
            id="home-harness"
            className="input max-w-[14rem] py-1.5 text-xs"
            value={harness}
            onChange={(event) => onHarnessChange(event.target.value as ProfileHarness)}
            title="Preferred harness metadata; host actions still require Codex Computer Use"
          >
            {HARNESS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {canManage ? (
            <>
              <button type="button" className="btn btn-secondary text-xs" onClick={onOpenProxies}>
                Proxies
              </button>
              <button type="button" className="btn btn-secondary text-xs" onClick={onOpenProfiles}>
                Profiles
              </button>
              <button type="button" className="btn btn-secondary text-xs" onClick={onOpenAccounts}>
                Accounts
              </button>
              <button
                type="button"
                className="btn btn-secondary text-xs"
                onClick={onCreateProjectProfile}
              >
                New in project
              </button>
            </>
          ) : null}
        </div>
      </div>

      <div className="flex flex-1 flex-col items-center justify-center px-4 pb-16">
        <div className="mb-8 text-center">
          <div className="text-2xl font-semibold tracking-tight text-gray-100">Browser Use</div>
          <p className="mt-1 text-xs text-gray-500">
            Compact CloakBrowser workspace · preference: {harnessLabel(harness)}
            {selectedOption ? ` · ${selectedOption.approach}` : ""}
          </p>
        </div>

        <div className="w-full max-w-2xl rounded-2xl border border-border bg-surface-1 p-3 shadow-sm">
          <textarea
            value={task}
            onChange={(event) => onTaskChange(event.target.value)}
            rows={3}
            placeholder="Give the agent a task, e.g. open BrowserScan and report the authenticity score."
            className="w-full resize-none bg-transparent px-2 py-2 text-sm text-gray-100 outline-none placeholder:text-gray-600"
          />
          <div className="mt-2 flex items-center justify-between gap-2 px-1">
            <div className="flex items-center gap-1.5 text-gray-500">
              <button
                type="button"
                className="inline-flex h-9 w-9 items-center justify-center rounded-lg hover:bg-surface-2"
                title="Attachments are not required for this MVP shell"
                aria-label="Attachments"
              >
                <Paperclip className="h-4 w-4" />
              </button>
              <button
                type="button"
                className="inline-flex h-9 w-9 items-center justify-center rounded-lg hover:bg-surface-2"
                title="Open profile settings"
                aria-label="Open profile settings"
                onClick={() => onOpenSettings(selectedProfile?.id ?? projectProfiles[0]?.id ?? null)}
              >
                <Settings2 className="h-4 w-4" />
              </button>
              <button
                type="button"
                className={`inline-flex h-9 w-9 items-center justify-center rounded-lg hover:bg-surface-2 ${
                  settingsOpen ? "bg-surface-2 text-gray-200" : ""
                }`}
                title="Toggle harness & settings panel"
                aria-label="Toggle harness and settings panel"
                aria-pressed={settingsOpen}
                onClick={() => setSettingsOpen((open) => !open)}
              >
                <SlidersHorizontal className="h-4 w-4" />
              </button>
              <select
                className="input py-1 text-xs"
                value={selectedProfile?.id ?? ""}
                onChange={(event) => onOpenSettings(event.target.value || null)}
                aria-label="Run with browser profile"
              >
                <option value="">Choose browser…</option>
                {projectProfiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                    {profile.status === "running" ? " · running" : ""}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-amber-700 text-white hover:bg-amber-600 disabled:opacity-40"
              disabled={!selectedProfile}
              onClick={onLaunchSelected}
              aria-label="Open or launch selected browser"
              title="Open or launch selected browser"
            >
              <ArrowUp className="h-4 w-4" />
            </button>
          </div>
        </div>

        {settingsOpen ? (
          <HarnessSettingsPanel
            harness={harness}
            onHarnessChange={onHarnessChange}
            selectedProfile={selectedProfile}
            onEditProfile={() =>
              onOpenSettings(selectedProfile?.id ?? projectProfiles[0]?.id ?? null)
            }
          />
        ) : null}
      </div>
    </div>
  );
}

function HarnessSettingsPanel({
  harness,
  onHarnessChange,
  selectedProfile,
  onEditProfile,
}: {
  harness: ProfileHarness;
  onHarnessChange: (harness: ProfileHarness) => void;
  selectedProfile: Profile | null;
  onEditProfile: () => void;
}) {
  return (
    <div className="mt-6 w-full max-w-2xl space-y-3">
      <div className="rounded-xl border border-border bg-surface-1/80 p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <div>
            <p className="text-sm font-medium text-gray-100">Callable browser backends</p>
            <p className="text-[11px] text-gray-500">
              One-to-one with Browser Use excerpts · preference only until a verified bridge runs
            </p>
          </div>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          {HARNESS_OPTIONS.filter((option) =>
            CALLABLE_BROWSER_HARNESSES.includes(option.value),
          ).map((option) => {
            const active = option.value === harness;
            return (
              <button
                key={option.value}
                type="button"
                onClick={() => onHarnessChange(option.value)}
                className={`rounded-xl border px-3 py-2.5 text-left transition-colors ${
                  active
                    ? "border-accent/50 bg-accent/10"
                    : "border-border bg-surface-2 hover:bg-surface-3"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-gray-100">{option.label}</span>
                  <span className="rounded-full bg-surface-3 px-1.5 py-0.5 text-[10px] text-gray-400">
                    {option.short}
                  </span>
                </div>
                <p className="mt-1 text-[11px] leading-snug text-gray-500">{option.description}</p>
              </button>
            );
          })}
        </div>
      </div>

      {selectedProfile ? (
        <CompactSettingsCard profile={selectedProfile} onEdit={onEditProfile} />
      ) : (
        <div className="rounded-xl border border-dashed border-border bg-surface-1/50 px-4 py-6 text-center text-xs text-gray-500">
          Choose a profile to edit viewport, proxy, geo, humanize, and more settings.
        </div>
      )}
    </div>
  );
}

function CompactSettingsCard({
  profile,
  onEdit,
}: {
  profile: Profile;
  onEdit: () => void;
}) {
  const rows = [
    ["Viewport", `${profile.screen_width}×${profile.screen_height}`],
    ["Platform", profile.platform],
    ["Timezone", profile.timezone || "auto"],
    ["Locale", profile.locale || "auto"],
    ["Harness", harnessLabel(profile.harness)],
    ["GeoIP", profile.geoip ? "on" : "off"],
    ["Humanize", profile.humanize ? profile.human_preset : "off"],
    ["Clipboard", profile.clipboard_sync ? "sync" : "off"],
    ["Color", profile.color_scheme || "default"],
    ["Search", profile.search_engine || "default"],
    ["Proxy", profile.proxy ? "configured" : "none"],
    ["Headless", profile.headless ? "on" : "off"],
  ] as const;

  return (
    <div className="rounded-xl border border-border bg-surface-1/80 p-4">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <p className="text-sm font-medium text-gray-100">{profile.name}</p>
          <p className="text-[11px] text-gray-500">Expanded settings overview</p>
        </div>
        <button type="button" className="btn btn-secondary text-xs" onClick={onEdit}>
          Edit settings
        </button>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {rows.map(([label, value]) => (
          <div key={label} className="rounded-lg bg-surface-2 px-2.5 py-2">
            <div className="text-[10px] uppercase tracking-wide text-gray-500">{label}</div>
            <div className="truncate text-xs text-gray-200">{value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
