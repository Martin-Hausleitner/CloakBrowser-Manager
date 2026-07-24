import type { Profile } from "./api";

function compareText(a: string, b: string) {
  if (a === b) return 0;
  return a < b ? -1 : 1;
}

export function profileOrganizationLabel(profile: Profile) {
  const project = profile.project_id || "default";
  return profile.folder_path ? `${project} / ${profile.folder_path}` : project;
}

export function compareOrganizedProfiles(a: Profile, b: Profile) {
  if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;

  const project = compareText(a.project_id || "default", b.project_id || "default");
  if (project !== 0) return project;

  const folder = compareText(a.folder_path || "", b.folder_path || "");
  if (folder !== 0) return folder;

  const name = compareText(a.name, b.name);
  if (name !== 0) return name;

  const createdAt = compareText(b.created_at, a.created_at);
  if (createdAt !== 0) return createdAt;

  return compareText(a.id, b.id);
}
