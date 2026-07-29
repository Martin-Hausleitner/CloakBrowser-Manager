# SUPERSEDED: Antigravity + Unbrowse Single-Harness Plan

This plan was superseded on 2026-07-29 after the user clarified that Browser Harness, Unbrowse, and Stagehand must be simultaneously available as an ordered tool stack rather than a mutually exclusive browser-harness selection. Use `2026-07-29-antigravity-browser-tool-router-plan.md` instead.

# Previous Antigravity + Unbrowse Provider/Harness Composition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Antigravity/Grok Build through ACPX while independently selecting Unbrowse as the browser harness for one Manager-owned CloakBrowser profile.

**Architecture:** Preserve `harness="acpx"` as the execution-worker routing key and `agent="grok-build"` as the AI-provider adapter. Add an optional `browser_harness` run field (`browser-use`, `browser-harness`, `unbrowse`, or `stagehand`) that is persisted, returned to the ACPX worker, and injected into the run-scoped MCP environment. Existing requests without `browser_harness` keep their current `cloakbrowser` MCP behavior. The UI presents provider/model and browser harness as separate compact controls.

**Tech Stack:** FastAPI/Pydantic, SQLite migrations, Python ACPX worker and MCP bridge, React/TypeScript, Vitest, Pytest.

---

### Task 1: Persist and expose the independent browser-harness contract

**Files:**
- Modify: `backend/models.py`
- Modify: `backend/database.py`
- Modify: `backend/main.py`
- Modify: `backend/worker_runtime.py`
- Test: `backend/tests/test_models.py`
- Test: `backend/tests/test_task_runs_api.py`
- Test: `backend/tests/test_browser_use_internal_api.py`

- [ ] **Step 1: Write failing model tests**

Add tests proving this request is valid and normalized without changing worker routing:

```python
run = TaskRunCreate(
    harness="acpx",
    agent="grok-build",
    browser_harness="unbrowse",
    task="Open https://example.com",
    profile_id="profile-1",
)
assert run.harness == "acpx"
assert run.agent == "grok-build"
assert run.browser_harness == "unbrowse"
```

Also prove `browser_harness` is rejected for non-ACPX runs, and reject non-browser values such as `acpx`, `codex`, and `antigravity`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q \
  backend/tests/test_models.py \
  backend/tests/test_task_runs_api.py \
  backend/tests/test_browser_use_internal_api.py
```

Expected: failures because `TaskRunCreate` and task-run responses do not yet define `browser_harness`.

- [ ] **Step 3: Add the minimal typed contract and migration**

Define:

```python
BrowserHarness = Literal["browser-use", "browser-harness", "unbrowse", "stagehand"]
```

Add `browser_harness: BrowserHarness | None = None` to create, public response, worker claim, and capability response models. Extend the validator:

```python
if self.browser_harness is not None and self.harness != "acpx":
    raise ValueError("browser_harness is only valid for acpx harness")
```

Add nullable `browser_harness TEXT` to `task_runs` through the repository's idempotent schema-upgrade path. Thread it through `create_task_run_with_message`, `_task_run_from_row`, `_task_run_response`, `_claim_response`, and capability response construction. Existing rows normalize to `None`.

- [ ] **Step 4: Add API and claim tests**

Create an Antigravity profile and submit:

```json
{
  "harness": "acpx",
  "agent": "grok-build",
  "browser_harness": "unbrowse",
  "task": "Open https://example.com",
  "profile_id": "<profile>",
  "allowed_origins": ["https://example.com"]
}
```

Assert HTTP 201, response persistence, and that `/internal/task-runs/claim?harness=acpx` returns `harness="acpx"`, `agent="grok-build"`, and `browser_harness="unbrowse"`.

- [ ] **Step 5: Run focused tests and commit**

Expected: all focused tests pass. Commit only the Task 1 files.

---

### Task 2: Bind the selected browser harness into the ACPX run safely

**Files:**
- Modify: `scripts/acpx_worker.py`
- Modify: `scripts/cbm_mcp.py`
- Modify: `scripts/test_acpx_worker.py`
- Modify: `scripts/test_cbm_mcp.py`

- [ ] **Step 1: Write failing worker-environment tests**

For a claim with `browser_harness="unbrowse"`, assert the child environment contains:

```python
assert environment["CBM_BROWSER_HARNESS"] == "unbrowse"
```

For legacy claims, assert `CBM_BROWSER_HARNESS` defaults to `browser-harness`, preserving current bounded `cloakbrowser` tools.

- [ ] **Step 2: Verify RED**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest -q scripts/test_acpx_worker.py scripts/test_cbm_mcp.py
```

Expected: failing assertions because the run-scoped environment and `RunContext` do not expose a browser harness.

- [ ] **Step 3: Extend run-scoped context without exposing credentials**

Add `browser_harness` to `RunContext`; accept only the four `BrowserHarness` values. Thread the normalized field into the ACPX child environment. Include the selected harness in the mandatory system prompt so the provider cannot silently substitute a different browser surface.

- [ ] **Step 4: Add a fail-closed MCP backend selector**

Keep the existing Playwright-backed controller for `browser-harness` and legacy requests. For `unbrowse`, instantiate a run-scoped Unbrowse controller that connects only through the Manager capability endpoint/gateway and exposes the same bounded `browser_navigate`, `browser_inspect`, `browser_read_text`, `browser_click`, and `browser_fill` MCP tool names. If Unbrowse is missing or its MCP handshake fails, return an explicit `browser_harness_unavailable` error; never fall back to raw Playwright for an explicitly selected Unbrowse run.

- [ ] **Step 5: Test exact routing and no-fallback behavior**

Use fakes to prove:

```python
assert build_controller(context_with_unbrowse).__class__.__name__ == "UnbrowseMcpController"
assert build_controller(context_with_browser_harness).__class__.__name__ == "CbmMcpController"
```

Also prove an Unbrowse startup failure does not call the Playwright connector.

- [ ] **Step 6: Run focused tests and commit**

Expected: worker and MCP tests pass with no leaked capability token or raw CDP URL in output.

---

### Task 3: Separate provider/model and browser harness in the compact UI

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/workspace/AgentBrowserWorkspace.tsx`
- Test: `frontend/src/components/workspace/AgentBrowserWorkspace.test.tsx`

- [ ] **Step 1: Write failing UI tests**

Render an Antigravity profile, select provider `Antigravity · Grok Build`, select browser harness `Unbrowse`, launch, and assert:

```ts
expect(apiMock.createTaskRun).toHaveBeenCalledWith(
  expect.any(String),
  expect.objectContaining({
    harness: "acpx",
    agent: "grok-build",
    browser_harness: "unbrowse",
  }),
)
```

Assert provider and browser-harness controls have separate accessible names and that changing one does not reset the other.

- [ ] **Step 2: Verify RED**

Run:

```bash
cd frontend
npm test -- --run AgentBrowserWorkspace.test.tsx
```

Expected: failure because the API type and UI state do not define `browser_harness`.

- [ ] **Step 3: Implement the compact controls**

Add `BrowserHarness` and `browser_harness` to API types. Replace the single mutually-exclusive Antigravity/Unbrowse choice for managed ACPX sessions with two compact controls:

```text
Provider: Antigravity / Grok Build
Browser:  Unbrowse
```

Keep Browser Use, Unbrowse, and Stagehand as standalone worker modes for compatibility. Only ACPX/provider mode shows the optional browser-harness selector.

- [ ] **Step 4: Run tests, typecheck, and commit**

Run workspace tests followed by `npm run build`. Expected: zero failures and a successful TypeScript/Vite build.

---

### Task 4: Review, deploy, and prove the composed E2E path

**Files:**
- Create: `docs/reports/VCVM-ANTIGRAVITY-UNBROWSE-E2E-2026-07-29.md`

- [ ] **Step 1: Spec-compliance review**

Review the complete diff against this plan. Require explicit proof that provider selection, browser-harness selection, persistence, worker claim, MCP routing, and fail-closed behavior are all covered.

- [ ] **Step 2: Code-quality and security review**

Check migration idempotency, no-fallback security, token redaction, capability scoping, origin enforcement, and backwards compatibility.

- [ ] **Step 3: Run fresh verification sequentially**

Run targeted backend/worker tests, full backend suite, frontend tests, and frontend build. Do not run backend tests concurrently with frontend build.

- [ ] **Step 4: Deploy on VCVM and run real E2E**

Create a temporary profile/session, submit `harness=acpx`, `agent=grok-build`, `browser_harness=unbrowse`, and a harmless explicit URL. Verify claim execution, typed output, screenshot/title evidence, and that the visible browser is the same Manager profile rather than a second browser.

- [ ] **Step 5: Browser verification and report**

Use Agent Browser against `https://vcvm.tail6a40cd.ts.net/`, verify the two independent controls and live run status, capture a screenshot, and document exact pass/fail evidence without secrets.
