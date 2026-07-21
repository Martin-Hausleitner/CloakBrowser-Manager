import { describe, expect, it } from "vitest";
import { hasAccessPermission } from "./accessPermissions";

describe("hasAccessPermission", () => {
  const grants = [
    { sandbox_id: "research", permission: "operate" as const },
    { sandbox_id: "research", permission: "automate" as const },
    { sandbox_id: "view-only", permission: "view" as const },
  ];

  it("evaluates all grants for a sandbox instead of only the first match", () => {
    expect(hasAccessPermission(grants, "research", "view")).toBe(true);
    expect(hasAccessPermission(grants, "research", "interact")).toBe(true);
    expect(hasAccessPermission(grants, "research", "operate")).toBe(true);
    expect(hasAccessPermission(grants, "research", "automate")).toBe(true);
  });

  it("keeps CDP automation independent from interactive control", () => {
    expect(hasAccessPermission(grants, "view-only", "view")).toBe(true);
    expect(hasAccessPermission(grants, "view-only", "interact")).toBe(false);
    expect(hasAccessPermission(grants, "view-only", "automate")).toBe(false);
  });
});
