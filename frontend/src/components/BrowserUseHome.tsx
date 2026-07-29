import { ArrowUp } from "lucide-react";
import type { Profile } from "../lib/api";

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
  onLaunchSelected,
  selectedProfile,
}: BrowserUseHomeProps) {
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

      </div>
    </div>
  );
}
