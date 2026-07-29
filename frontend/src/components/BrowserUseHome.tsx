import { useState } from "react";
import { ArrowUp, SlidersHorizontal } from "lucide-react";
import type { Profile } from "../lib/api";
import { harnessLabel } from "../lib/harnessOptions";

interface BrowserUseHomeProps {
  projects: string[];
  projectId: string;
  profiles: Profile[];
  task: string;
  onProjectChange: (projectId: string) => void;
  onTaskChange: (task: string) => void;
  onSelectProfile: (profileId: string | null) => void;
  onOpenSettings: (profileId: string | null) => void;
  onLaunchSelected: () => void;
  selectedProfile: Profile | null;
}

export function BrowserUseHome({
  projects,
  projectId,
  profiles,
  task,
  onProjectChange,
  onTaskChange,
  onSelectProfile,
  onOpenSettings,
  onLaunchSelected,
  selectedProfile,
}: BrowserUseHomeProps) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const projectProfiles = profiles.filter(
    (profile) => (profile.project_id || "default") === projectId,
  );

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-1 flex-col items-center justify-center px-4 pb-12">
        <div className="mb-5 text-center">
          <div className="text-2xl font-semibold tracking-tight text-gray-100">Browser Use</div>
          <p className="mt-1 text-xs text-gray-500">Choose a browser and describe the task.</p>
        </div>

        <div className="w-full max-w-2xl rounded-2xl border border-border bg-surface-1 p-3 shadow-sm">
          <textarea
            value={task}
            onChange={(event) => onTaskChange(event.target.value)}
            rows={2}
            placeholder="Give the agent a task, e.g. open BrowserScan and report the authenticity score."
            className="w-full resize-none bg-transparent px-2 py-2 text-sm text-gray-100 outline-none placeholder:text-gray-600"
          />
          <div className="mt-2 flex items-center justify-between gap-2 px-1">
            <div className="flex min-w-0 flex-1 items-center gap-1.5 text-gray-500">
              <label className="sr-only" htmlFor="home-project">Project</label>
              <select
                id="home-project"
                className="input h-9 max-w-[9rem] py-1 text-xs"
                value={projectId}
                onChange={(event) => onProjectChange(event.target.value)}
              >
                {projects.map((project) => (
                  <option key={project} value={project}>{project}</option>
                ))}
                {!projects.includes(projectId) ? <option value={projectId}>{projectId}</option> : null}
              </select>
              <select
                className="input h-9 min-w-0 flex-1 py-1 text-xs"
                value={selectedProfile?.id ?? ""}
                onChange={(event) => onSelectProfile(event.target.value || null)}
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
              <button
                type="button"
                className={`inline-flex h-9 w-9 items-center justify-center rounded-lg hover:bg-surface-2 ${
                  settingsOpen ? "bg-surface-2 text-gray-200" : ""
                }`}
                title="Browser settings"
                aria-label="Browser settings"
                aria-pressed={settingsOpen}
                aria-expanded={settingsOpen}
                onClick={() => setSettingsOpen((open) => !open)}
              >
                <SlidersHorizontal className="h-4 w-4" />
              </button>
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
  selectedProfile,
  onEditProfile,
}: {
  selectedProfile: Profile | null;
  onEditProfile: () => void;
}) {
  return (
    <div className="mt-3 w-full max-w-2xl">
      {selectedProfile ? (
        <CompactSettingsCard profile={selectedProfile} onEdit={onEditProfile} />
      ) : (
        <div className="rounded-lg border border-dashed border-border bg-surface-1/50 px-3 py-2 text-center text-[11px] text-gray-500">
          Choose a browser to inspect its essential settings.
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
  const proxyDisplay = profile.proxy_display ?? profile.proxy;

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-surface-1/80 px-3 py-2">
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium text-gray-100">{profile.name}</p>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-gray-400">
          <span className="rounded bg-surface-2 px-1.5 py-0.5">
            {profile.screen_width}×{profile.screen_height}
          </span>
          <span className="rounded bg-surface-2 px-1.5 py-0.5">
            {harnessLabel(profile.harness)}
          </span>
          <span className={`rounded px-1.5 py-0.5 ${proxyDisplay ? "bg-emerald-950/70 text-emerald-300" : "bg-surface-2"}`}>
            {proxyDisplay ? "Proxy ready" : "No proxy"}
          </span>
          <span className="rounded bg-surface-2 px-1.5 py-0.5">
            {profile.status === "running" ? "Live" : "Stopped"}
          </span>
        </div>
      </div>
      <button
        type="button"
        className="btn btn-secondary h-7 px-2 text-[10px]"
        onClick={onEdit}
        aria-label="Edit profile settings"
      >
        Edit
      </button>
    </div>
  );
}
