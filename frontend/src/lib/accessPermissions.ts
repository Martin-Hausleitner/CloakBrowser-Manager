import type { AccessGrant, AccessPermission } from "./api";

const inheritedPermissions: Record<AccessPermission, readonly AccessPermission[]> = {
  view: ["view", "interact", "operate", "automate"],
  interact: ["interact", "operate"],
  operate: ["operate"],
  automate: ["automate"],
};

export function hasAccessPermission(
  grants: readonly AccessGrant[],
  sandboxId: string,
  permission: AccessPermission,
) {
  const accepted = inheritedPermissions[permission];
  return grants.some((grant) => (
    grant.sandbox_id === sandboxId && accepted.includes(grant.permission)
  ));
}
