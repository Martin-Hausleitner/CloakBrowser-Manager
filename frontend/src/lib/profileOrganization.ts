import type { Profile } from "./api";

const collator = new Intl.Collator(undefined, { sensitivity: "base" });

export function profileOrganizationLabel(profile: Profile) {
  const project = profile.project_id || "default";
  return profile.folder_path ? `${project} / ${profile.folder_path}` : project;
}

export function compareOrganizedProfiles(a: Profile, b: Profile) {
  if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;

  const project = collator.compare(a.project_id || "default", b.project_id || "default");
  if (project !== 0) return project;

  const folder = collator.compare(a.folder_path || "", b.folder_path || "");
  if (folder !== 0) return folder;

  const name = collator.compare(a.name, b.name);
  if (name !== 0) return name;

  const createdAt = collator.compare(a.created_at, b.created_at);
  if (createdAt !== 0) return createdAt;

  return collator.compare(a.id, b.id);
}
