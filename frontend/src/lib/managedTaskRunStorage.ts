import type { TaskRunStatus } from "./api";

const BROWSER_USE_RUN_STORAGE_PREFIX = "cloakbrowser.browser-use.last-run:";

export const ACTIVE_TASK_RUN_STATES = new Set<TaskRunStatus>([
  "queued",
  "health_check",
  "blocked_health",
  "running",
]);

function browserUseRunStorageKey(profileId: string): string {
  return `${BROWSER_USE_RUN_STORAGE_PREFIX}${profileId}`;
}

export function readRememberedBrowserUseRun(profileId: string): string | null {
  try {
    return window.sessionStorage.getItem(browserUseRunStorageKey(profileId));
  } catch {
    return null;
  }
}

export function rememberBrowserUseRun(profileId: string, runId: string): void {
  try {
    window.sessionStorage.setItem(browserUseRunStorageKey(profileId), runId);
  } catch {
    // The run remains usable in memory when storage is disabled or full.
  }
}

export function forgetBrowserUseRun(profileId: string): void {
  try {
    window.sessionStorage.removeItem(browserUseRunStorageKey(profileId));
  } catch {
    // Ignore unavailable storage; there is no local state left to recover.
  }
}
