# Settings-first Browser Use Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all provider, model, harness, ACP/ACPX and browser-tool configuration from the live chat into a dedicated Settings tab while preserving launch behavior and a mounted live viewer.

**Architecture:** Add a first-class `settings` application view and a focused `HarnessSettingsWorkspace`. Lift the runtime configuration (`agent`, ACPX agent and provider routing) into a small shared context so Settings and the agent runner use one source of truth. Reuse the existing readiness and smoke-test APIs; do not add a browser protocol or UI dependency.

**Tech Stack:** React 19, TypeScript, Vite, Tailwind, Vitest, Testing Library.

---

### Task 1: Shared runtime configuration

**Files:**
- Create: `frontend/src/components/workspace/WorkspaceRuntimeConfig.tsx`
- Create: `frontend/src/components/workspace/WorkspaceRuntimeConfig.test.tsx`
- Modify: `frontend/src/components/workspace/ProviderToolControl.tsx`

- [ ] **Step 1: Write failing tests**

Cover these behaviors:

```tsx
it("shares one runtime configuration between settings and runner consumers", () => {
  // Render two consumers under WorkspaceRuntimeConfigProvider.
  // Update agent, ACPX agent and provider routing in the first consumer.
  // Assert the second consumer receives the same values without remounting.
});

it("keeps canonical browser tools in the default provider route", () => {
  expect(DEFAULT_WORKSPACE_RUNTIME_CONFIG.providerRouting.browserTools)
    .toEqual(DEFAULT_BROWSER_TOOLS);
});
```

- [ ] **Step 2: Verify RED**

Run:

```bash
cd frontend
npm test -- --run src/components/workspace/WorkspaceRuntimeConfig.test.tsx
```

Expected: fail because the context/provider does not exist.

- [ ] **Step 3: Implement minimal shared context**

Export:

```ts
export type WorkspaceRuntimeMode = "cli" | "acp" | "acpx";

export interface WorkspaceRuntimeConfig {
  agent: AgentMode;
  acpxAgent: AcpxAgent;
  providerRouting: ProviderRoutingState;
}

export function WorkspaceRuntimeConfigProvider(props: PropsWithChildren): JSX.Element;
export function useWorkspaceRuntimeConfig(): {
  config: WorkspaceRuntimeConfig;
  setConfig: Dispatch<SetStateAction<WorkspaceRuntimeConfig>>;
};
```

Use the current `DEFAULT_PROVIDER_ROUTING` and canonical tool order. Do not persist credentials or readiness data.

- [ ] **Step 4: Verify GREEN**

Run the focused test and `ProviderToolControl.test.tsx` if present. Both must pass.

### Task 2: Dedicated Settings page

**Files:**
- Create: `frontend/src/components/HarnessSettingsWorkspace.tsx`
- Create: `frontend/src/components/HarnessSettingsWorkspace.test.tsx`
- Modify: `frontend/src/lib/uiFlowRegistry.ts`

- [ ] **Step 1: Write failing page tests**

Test that the page:

```tsx
it("renders runtime, harness, provider, model and browser-tool settings", async () => {
  // Assert visible CLI/ACP/ACPX choices.
  // Assert installed managed harnesses and local CLI readiness.
  // Assert ProviderToolControl is visible exactly once.
  // Assert Unbrowse, Stagehand and Browser Harness are visible.
});

it("shows unavailable and auth-required adapters honestly", async () => {
  // Mock readiness responses and assert reason codes are visible and unavailable rows disabled.
});

it("runs one selected harness smoke test with the selected profile", async () => {
  // Assert same profile id, https://example.com origin and allow_second_browser=false.
});
```

- [ ] **Step 2: Verify RED**

Run:

```bash
cd frontend
npm test -- --run src/components/HarnessSettingsWorkspace.test.tsx
```

Expected: fail because the page does not exist.

- [ ] **Step 3: Implement the Settings page**

The page must have three compact sections:

1. `Runtime`: CLI/ACP/ACPX mode, selected managed harness or local CLI, ACPX agent.
2. `Provider`: the existing `ProviderToolControl` including model and routing policy.
3. `Browser tools`: Unbrowse, Stagehand and Browser Harness readiness, refresh and single-tool/harness test actions.

Fetch existing endpoints only:

```ts
api.getOrcaCapabilities();
api.getTaskHarnessPresence(harness, options);
api.getTaskHarnessPreflights("acpx", options);
api.getProviderReadiness(options);
```

Use the selected running profile for tests. Errors stay on this page, never in the chat output.

- [ ] **Step 4: Verify GREEN**

Run the focused Settings test until it passes without console warnings.

### Task 3: App navigation and state ownership

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/components/BrowserUseHome.tsx`
- Modify: `frontend/src/components/BrowserUseHome.test.tsx`

- [ ] **Step 1: Write failing navigation tests**

Add assertions that:

```tsx
it("opens a dedicated Settings workspace from the sidebar", async () => {
  // Click Settings; assert app.desktop.settings and Harness Settings content.
});

it("keeps Browser Use home free of an inline settings popover", () => {
  // Assert no Browser settings toggle and no CompactSettingsCard.
});
```

- [ ] **Step 2: Verify RED**

Run the two focused test files and confirm the expected failures.

- [ ] **Step 3: Implement navigation**

- Add `settings` to the `View` union.
- Add one Settings sidebar item after Sessions.
- Wrap `AppContent` in `WorkspaceRuntimeConfigProvider`.
- Render `HarnessSettingsWorkspace` for `view === "settings"`.
- Remove the inline settings toggle/card from `BrowserUseHome`; keep task, project, profile and launch only.
- Ensure top-bar launch/stream actions do not duplicate inside Settings.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
cd frontend
npm test -- --run src/App.test.tsx src/components/BrowserUseHome.test.tsx
```

### Task 4: Compact chat and remove duplicated controls

**Files:**
- Modify: `frontend/src/components/workspace/AgentBrowserWorkspace.tsx`
- Modify: `frontend/src/components/workspace/AgentBrowserWorkspace.test.tsx`

- [ ] **Step 1: Invert the existing UI tests**

Replace tests that open `workspace-settings`, provider popovers and full-view Provider panels with assertions that:

```tsx
expect(screen.queryByTestId("provider-tool-control")).toBeNull();
expect(screen.queryByTestId("workspace-settings")).toBeNull();
expect(screen.queryByRole("button", { name: "Open harness menu" })).toBeNull();
expect(screen.getByTestId("workspace-active-route")).toHaveTextContent(/Grok|ACP|CLI/);
```

Keep all launch payload assertions for `provider`, `model_alias`, `browser_tools` and `routing_policy`.

- [ ] **Step 2: Verify RED**

Run the relevant `AgentBrowserWorkspace` tests and confirm they fail against the old clustered UI.

- [ ] **Step 3: Implement the compact header**

- Consume runtime configuration from `useWorkspaceRuntimeConfig()`.
- Remove `settingsOpen`, the inline profile/harness settings row and both inline/full-view `ProviderToolControl` render sites.
- Remove the harness popup from the chat.
- Keep a two-row header:
  - row 1: profile name, status, Settings navigation action;
  - row 2: `CLI | ACP | ACPX`, truncated active route summary, stop when active.
- Keep session details collapsed.
- Do not remount `ProfileViewer` when switching runtime modes.
- Preserve existing launch serialization and same-profile/no-second-browser rules.

- [ ] **Step 4: Verify GREEN**

Run the full `AgentBrowserWorkspace.test.tsx` file. Fix behavior, not tests, if launch payloads change.

### Task 5: Mobile affordance without chat clutter

**Files:**
- Modify: `frontend/src/components/mobile/MobileSplitScreen.tsx`
- Modify: `frontend/src/components/mobile/MobileSplitScreen.test.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Write failing compact-mobile test**

Assert that mobile exposes one compact Settings action, the existing chat remains collapsed by default, and provider/model matrices never render inside the mobile chat/fullscreen browser.

- [ ] **Step 2: Verify RED**

Run the focused mobile test.

- [ ] **Step 3: Implement mobile Settings navigation**

Add only a compact navigation affordance to the existing mobile dock/menu. Render `HarnessSettingsWorkspace` as a single-column `h-dvh` page with a back action. Do not add detailed controls to `MobileSplitScreen` itself.

- [ ] **Step 4: Verify GREEN**

Run all `MobileSplitScreen` tests and confirm keyboard/fullscreen viewport tests still pass.

### Task 6: Full verification and visual gate

**Files:**
- Modify only files required by failures found above.

- [ ] **Step 1: Run targeted tests**

```bash
cd frontend
npm test -- --run \
  src/App.test.tsx \
  src/components/BrowserUseHome.test.tsx \
  src/components/HarnessSettingsWorkspace.test.tsx \
  src/components/workspace/WorkspaceRuntimeConfig.test.tsx \
  src/components/workspace/AgentBrowserWorkspace.test.tsx \
  src/components/mobile/MobileSplitScreen.test.tsx
```

- [ ] **Step 2: Run complete frontend verification**

```bash
cd frontend
npm test -- --run
npm run build
```

- [ ] **Step 3: Deploy on VCVM**

Rebuild the existing VCVM container from this worktree without changing public routing or credentials. Confirm health before browser validation.

- [ ] **Step 4: Public browser E2E**

Validate `https://vcvm.tail6a40cd.ts.net/` at 1440x900 and 390x844:

- Settings appears exactly once in navigation.
- Provider/model/tools appear only in Settings.
- Chat header is two compact rows.
- Settings values affect the next ACP/ACPX launch payload.
- Full view has no duplicated Provider panel.
- CDP/VNC stays in the same page and the viewer does not remount during runtime-mode changes.
- No new page or console errors.

Save screenshots for desktop chat, desktop Settings and mobile Settings.

- [ ] **Step 5: Review, commit and push**

Run a spec review and code-quality review. Commit only after both pass, using the repository smart-commit workflow, then verify the pushed SHA and public deployment.

## Plan self-review

- Spec coverage: all ten design acceptance criteria map to Tasks 2–6.
- No new dependency or browser-control protocol is introduced.
- Provider launch payload tests are retained as regression gates.
- Desktop, full-view and mobile surfaces are all covered.
- There are no TODO/TBD placeholders.
