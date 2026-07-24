# Profile Organization And Harness UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the already-started profile organization feature so projects, nested folders, pins, colors and preferred harnesses behave consistently in desktop and mobile UI without weakening the Codex Computer Use execution boundary.

**Architecture:** Keep authorization scoped to `sandbox_id`; treat `project_id` and `folder_path` as organization metadata only. Move label and ordering rules into one frontend helper, reuse it in desktop and mobile surfaces, and keep execution provider selection separate from the saved preferred-harness label. The verified `codex-computer-use` bridge remains the only host allowed to run typed browser actions.

**Tech Stack:** React 19, TypeScript, Vitest/Testing Library, FastAPI/Pydantic, SQLite, Tailwind CSS.

**Execution record (22 July 2026):** Tasks 1-5 were completed in commits `2cf7e57`, `ce3ead7`, `e93331b`, and `be0465f`. The affected backend slice passed 160 tests, the affected frontend slice passed 92 tests, and the production build passed. A subsequent fresh full local run passed 286 backend tests and 126 frontend tests plus the production build. The live VCVM release rerun remains tracked in the acceptance matrix; it is not part of this completed implementation slice.

---

### Task 1: Centralize profile organization labels and ordering

**Files:**
- Create: `frontend/src/lib/profileOrganization.ts`
- Create: `frontend/src/lib/profileOrganization.test.ts`
- Modify: `frontend/src/components/ProfileList.tsx`
- Test: `frontend/src/components/ProfileList.test.tsx`

- [x] **Step 1: Write the failing helper tests**

```ts
import { describe, expect, it } from "vitest";
import { compareOrganizedProfiles, profileOrganizationLabel } from "./profileOrganization";

describe("profile organization", () => {
  it("formats project and nested folder without changing the access sandbox", () => {
    expect(profileOrganizationLabel({ project_id: "commerce", folder_path: "buyers/us" }))
      .toBe("commerce / buyers/us");
  });

  it("sorts pinned profiles before project, folder, name, timestamp and id", () => {
    const rows = [
      { id: "z", name: "Zulu", pinned: false, project_id: "beta", folder_path: "", created_at: "2026-07-22T00:00:00Z" },
      { id: "a", name: "Alpha", pinned: true, project_id: "alpha", folder_path: "qa", created_at: "2026-07-22T00:00:00Z" },
    ];
    expect(rows.sort(compareOrganizedProfiles).map((row) => row.id)).toEqual(["a", "z"]);
  });
});
```

- [x] **Step 2: Run the helper test and verify RED**

Run: `cd frontend && npm test -- --run src/lib/profileOrganization.test.ts`

Expected: FAIL because `profileOrganization.ts` does not exist.

- [x] **Step 3: Implement the shared helper**

```ts
type OrganizedProfile = {
  id: string;
  name: string;
  pinned: boolean;
  project_id: string;
  folder_path: string;
  created_at: string;
};

export function profileOrganizationLabel(profile: Pick<OrganizedProfile, "project_id" | "folder_path">) {
  const project = profile.project_id || "default";
  return profile.folder_path ? `${project} / ${profile.folder_path}` : project;
}

export function compareOrganizedProfiles(a: OrganizedProfile, b: OrganizedProfile) {
  if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
  return a.project_id.localeCompare(b.project_id)
    || a.folder_path.localeCompare(b.folder_path)
    || a.name.localeCompare(b.name, undefined, { sensitivity: "base" })
    || b.created_at.localeCompare(a.created_at)
    || a.id.localeCompare(b.id);
}
```

- [x] **Step 4: Reuse the helper and make group headers unambiguous**

Import the helper in `ProfileList.tsx`, remove the local duplicate implementations, sort with `compareOrganizedProfiles`, and label each collapsible header as `Profile group: <organization label>` so tests and assistive technology can distinguish it from a profile row.

- [x] **Step 5: Verify the focused desktop tests are GREEN**

Run: `cd frontend && npm test -- --run src/lib/profileOrganization.test.ts src/components/ProfileList.test.tsx`

Expected: all tests pass.

### Task 2: Complete mobile profile and preferred-harness behavior

**Files:**
- Modify: `frontend/src/components/mobile/MobileSplitScreen.tsx`
- Modify: `frontend/src/components/mobile/MobileSplitScreen.test.tsx`
- Modify: `frontend/src/styles/globals.css`
- Test: `frontend/src/components/mobile/MobileSplitScreen.test.tsx`

- [x] **Step 1: Preserve the existing RED evidence**

Run: `cd frontend && npm test -- --run src/components/mobile/MobileSplitScreen.test.tsx`

Expected current result: six failing tests covering organization labels, pinned ordering, accents and preferred-harness metadata.

- [x] **Step 2: Add deterministic display helpers in the component**

```ts
const harnessNames: Record<ProfileHarness, string> = {
  codex: "Codex",
  antigravity: "Antigravity",
  "claude-code": "Claude Code",
  opencode: "OpenCode",
  "browser-use": "Browser Use",
};

const selectedHarnessName = selected ? harnessNames[selected.harness] : "Codex";
const selectedHarnessStatus = codexHostReady
  ? `${selectedHarnessName} · via Codex Computer Use`
  : `${selectedHarnessName} · saved only`;
```

Use `profileOrganizationLabel` and `compareOrganizedProfiles` for the selector, tool grid and fullscreen sessions. Render pin state and `--profile-accent` without exposing access or proxy secrets.

- [x] **Step 3: Keep execution and preference metadata separate**

Extend the task request metadata with:

```ts
metadata: {
  runner: "codex-computer-use",
  selected_runner: "codex-computer-use",
  selected_profile_id: selected.id,
  selected_profile_name: selected.name,
  preferred_harness: selected.harness,
  execution_provider: "codex-computer-use",
  execution_bridge: "codex-computer-use",
  execution: "host",
  browser_visible: true,
}
```

Do not allow `preferred_harness` to bypass `isCodexVerifiedHost()` or enable host-scoped commands on the server-history fallback.

- [x] **Step 4: Verify the mobile tests are GREEN**

Run: `cd frontend && npm test -- --run src/components/mobile/MobileSplitScreen.test.tsx src/lib/taskHarness.test.ts`

Expected: all mobile and harness boundary tests pass.

### Task 3: Align profile form validation with the backend contract

**Files:**
- Modify: `frontend/src/components/ProfileForm.tsx`
- Modify: `frontend/src/App.test.tsx`

- [x] **Step 1: Add failing form validation assertions**

Extend the existing organization roundtrip test to assert that `Project` has `pattern="[A-Za-z0-9][A-Za-z0-9._-]*"` and `maxLength=80`, and `Folder` has `maxLength=240`, no leading/trailing slash guidance and a browser validation failure for `/unsafe`.

- [x] **Step 2: Run the form test and verify RED**

Run: `cd frontend && npm test -- --run src/App.test.tsx`

Expected: FAIL because the organization inputs do not yet expose the backend constraints.

- [x] **Step 3: Add matching client constraints**

```tsx
<input id="profile-project" pattern="[A-Za-z0-9][A-Za-z0-9._-]*" maxLength={80} required />
<input id="profile-folder" maxLength={240} pattern="(?:[A-Za-z0-9][A-Za-z0-9._ -]*)(?:/[A-Za-z0-9][A-Za-z0-9._ -]*)*|^$" />
```

Keep server-side Pydantic validation authoritative and retain generic API error handling as a fallback.

- [x] **Step 4: Verify form, API and production build**

Run: `cd frontend && npm test -- --run src/App.test.tsx src/lib/api.test.ts && npm run build`

Expected: tests and build pass.

### Task 4: Add project/folder context to access grants without changing authorization semantics

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/tests/test_access_control.py`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/AccessDashboard.tsx`
- Modify: `frontend/src/components/AccessDashboard.test.tsx`

- [x] **Step 1: Write a failing API test for access scope summaries**

Create profiles in one sandbox across two projects/folders and assert `/api/access/sandboxes` returns redacted organization summaries such as `project_ids`, `folder_paths` and profile names/counts without proxy, fingerprint or user-data fields.

- [x] **Step 2: Run the backend test and verify RED**

Run: `.venv/bin/pytest backend/tests/test_access_control.py -q`

Expected: FAIL because the sandbox response currently contains only its identifier and counts.

- [x] **Step 3: Return a redacted organization summary**

Build the summary from profiles already visible to the administrator, deduplicate/sort project and folder strings, and never include proxy credentials, launch arguments, user-data paths or fingerprint seeds.

- [x] **Step 4: Write a failing dashboard test**

Assert each grant row and effective-access preview names the associated project/folder context while grants continue to use `sandbox_id` as their stored and enforced key.

- [x] **Step 5: Render the organization context and verify GREEN**

Run: `cd frontend && npm test -- --run src/components/AccessDashboard.test.tsx && npm run build`

Expected: tests and build pass with no horizontal mobile overflow.

### Task 5: Integrated verification and commit

**Files:**
- Verify all files changed above.

- [x] **Step 1: Run backend regression tests**

Run: `.venv/bin/pytest backend/tests/test_models.py backend/tests/test_database.py backend/tests/test_api.py backend/tests/test_access_control.py -q`

Expected: all tests pass.

- [x] **Step 2: Run frontend regression tests and build**

Run: `cd frontend && npm test -- --run src/App.test.tsx src/components/ProfileList.test.tsx src/components/mobile/MobileSplitScreen.test.tsx src/components/AccessDashboard.test.tsx src/lib/profileOrganization.test.ts src/lib/api.test.ts src/lib/taskHarness.test.ts && npm run build`

Expected: all tests pass and Vite production build succeeds.

- [x] **Step 3: Check the diff**

Run: `git diff --check`

Expected: no whitespace errors.

- [x] **Step 4: Commit the completed slice**

```bash
git add backend/models.py backend/database.py backend/main.py backend/tests/test_models.py backend/tests/test_database.py backend/tests/test_api.py backend/tests/test_access_control.py frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/lib/api.ts frontend/src/lib/api.test.ts frontend/src/lib/profileOrganization.ts frontend/src/lib/profileOrganization.test.ts frontend/src/components/ProfileForm.tsx frontend/src/components/ProfileList.tsx frontend/src/components/ProfileList.test.tsx frontend/src/components/mobile/MobileSplitScreen.tsx frontend/src/components/mobile/MobileSplitScreen.test.tsx frontend/src/components/AccessDashboard.tsx frontend/src/components/AccessDashboard.test.tsx frontend/src/styles/globals.css docs/superpowers/plans/2026-07-22-profile-organization-harness-ui.md
git commit -m "feat: organize profiles and surface harness preferences"
```
