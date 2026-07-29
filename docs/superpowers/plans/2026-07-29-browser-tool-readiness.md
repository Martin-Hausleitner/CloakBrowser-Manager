# Browser Tool Readiness and Smoke-Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every registered VCVM browser tool harness—Unbrowse, Stagehand, and Browser Harness—report honest live readiness in normal and full view and provide a one-click, same-profile end-to-end smoke test.

**Architecture:** Reuse the existing authenticated worker preflight table and the existing provider-independent browser router. The ACPX worker probes the exact adapter runtime it will execute, reports redacted readiness to the Manager, and the Manager exposes a public authenticated read endpoint. The UI consumes that endpoint, disables unavailable tools, and starts a single-tool ACPX smoke run through the existing health, capability, origin, and same-browser gates.

**Tech Stack:** FastAPI, Pydantic, SQLite worker-runtime state, Python asyncio adapters, React/TypeScript, Vitest, pytest, ACPX, MCP, Browser Harness, Unbrowse, Stagehand.

---

### Task 1: Adapter Readiness Contract

**Files:**
- Modify: `scripts/unbrowse_router_adapter.py`
- Modify: `scripts/stagehand_router_adapter.py`
- Modify: `scripts/browser_harness_adapter.py`
- Test: `scripts/test_unbrowse_router_adapter.py`
- Test: `scripts/test_stagehand_router_adapter.py`
- Test: `scripts/test_browser_harness_adapter.py`

- [ ] **Step 1: Write failing adapter readiness tests**

Add tests that call a public `preflight()` method and assert a sanitized result shaped as `{"ready": bool, "reason_code": str}`. Cover executable missing, exact successful probe, malformed output, timeout, cancellation propagation, and absence of secrets or command paths in the returned reason.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPATH=. pytest -q scripts/test_unbrowse_router_adapter.py scripts/test_stagehand_router_adapter.py scripts/test_browser_harness_adapter.py -k preflight
```

Expected: FAIL because the public readiness API does not exist.

- [ ] **Step 3: Implement the minimal public preflight methods**

Expose the existing Unbrowse and Stagehand probes without opening a browser or creating a gateway. Add Browser Harness `--version`/runtime probing using bounded subprocess IO, the existing executable resolver, no shell, minimal environment, strict timeout, process-group cleanup, and redacted errors.

- [ ] **Step 4: Run adapter tests and verify GREEN**

Run the command from Step 2 and then:

```bash
python3 -m py_compile scripts/unbrowse_router_adapter.py scripts/stagehand_router_adapter.py scripts/browser_harness_adapter.py
```

Expected: all focused tests pass; compilation exits 0.

### Task 2: Authenticated Worker-to-Manager Readiness

**Files:**
- Modify: `backend/models.py`
- Modify: `backend/worker_runtime.py`
- Modify: `backend/main.py`
- Modify: `scripts/acpx_worker.py`
- Test: `backend/tests/test_browser_use_internal_api.py`
- Test: `backend/tests/test_provider_readiness_api.py`
- Test: `scripts/test_acpx_worker.py`

- [ ] **Step 1: Write failing API and worker tests**

Add tests for authenticated worker reports for the three canonical browser-tool IDs, rejection of unsafe/unknown IDs, freshness/stale handling, redacted public responses, and ACPX worker periodic reporting. The response contract is:

```json
{
  "tools": [
    {"id":"unbrowse","ready":false,"state":"failed","reason_code":"auth_required","checked_at":"..."},
    {"id":"stagehand","ready":true,"state":"ready","reason_code":"ready","checked_at":"..."},
    {"id":"browser-harness","ready":true,"state":"ready","reason_code":"ready","checked_at":"..."}
  ]
}
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPATH=. pytest -q backend/tests/test_browser_use_internal_api.py backend/tests/test_provider_readiness_api.py scripts/test_acpx_worker.py -k 'browser_tool or tool_readiness'
```

Expected: FAIL because report/read endpoints and periodic reporting are absent.

- [ ] **Step 3: Implement the minimal authenticated readiness path**

Reuse `worker_harness_preflights` with `harness="browser-tools"` and `agent=<tool-id>`. Add strict request/response models and endpoints `POST /internal/browser-tools/readiness` and `GET /api/browser-tools/readiness`. The public endpoint must return all three canonical tools, mark missing/stale results honestly, and never expose worker IDs, binary paths, stderr, tokens, or command environment.

- [ ] **Step 4: Wire ACPX worker periodic probes**

Instantiate the same adapter classes used by `cbm_mcp`, call their public `preflight()` methods during the existing preflight cycle, and post only canonical IDs plus `ready`/`reason_code`. Probe failures must not crash the worker or silently mark a tool ready.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the command from Step 2 plus:

```bash
python3 -m py_compile backend/models.py backend/worker_runtime.py backend/main.py scripts/acpx_worker.py
```

Expected: all focused tests pass; compilation exits 0.

### Task 3: Compact UI Readiness and Single-Tool Smoke Tests

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/workspace/ProviderToolControl.tsx`
- Modify: `frontend/src/components/workspace/AgentBrowserWorkspace.tsx`
- Test: `frontend/src/lib/api.test.ts`
- Test: `frontend/src/components/workspace/AgentBrowserWorkspace.test.tsx`

- [ ] **Step 1: Write failing UI tests**

Add tests proving normal and full view show exact readiness for all three tools, unavailable tools are disabled, and each row has a compact `Test` button. Clicking `Test Browser Harness` must refresh health, create an ACPX run with the selected ready provider, enable only `browser-harness`, preserve canonical tool order, set `allow_second_browser=false`, use `https://example.com` as the sole allowed origin, and reuse the selected running profile.

- [ ] **Step 2: Run focused Vitest and verify RED**

Run:

```bash
npm --prefix frontend test -- --run src/lib/api.test.ts src/components/workspace/AgentBrowserWorkspace.test.tsx -t 'browser tool readiness|single-tool smoke'
```

Expected: FAIL because readiness is not fetched/passed and tool rows have no test action.

- [ ] **Step 3: Implement API types and compact controls**

Add `BrowserToolReadiness` types and `getBrowserToolReadiness()`. Pass readiness into both `ProviderToolControl` instances. Add a 24px compact test action per row with accessible names, honest busy/result labels, and no duplicate visible controls.

- [ ] **Step 4: Implement single-tool smoke execution**

Reuse the existing profile-health wait loop and task-session/run APIs. Use the current ready ACP provider/agent binding, exact selected model alias, canonical three-tool array with only the chosen tool enabled, and the existing managed-output timeline. Cancel a stale run if profile selection changes before attachment.

- [ ] **Step 5: Run UI tests and production build**

Run:

```bash
npm --prefix frontend test -- --run src/lib/api.test.ts src/components/workspace/AgentBrowserWorkspace.test.tsx
npm --prefix frontend run build
```

Expected: all tests pass and production build exits 0.

### Task 4: Integrated Quality Gates and VCVM Proof

**Files:**
- Modify only if a failing regression proves a required fix.

- [ ] **Step 1: Run backend and frontend full suites**

```bash
python3 -m pytest -q
npm --prefix frontend test -- --run
npm --prefix frontend run build
git diff --check
```

Expected: all tests pass; only already-known non-failing warnings may remain.

- [ ] **Step 2: Run spec and code-quality reviews**

Dispatch one spec reviewer and, only after approval, one code-quality reviewer. Fix and re-review every P0/P1/P2 finding.

- [ ] **Step 3: Deploy and verify public behavior**

Rebuild the VCVM Compose service, restart `cloakbrowser-acpx.service`, wait for healthy state, open `https://vcvm.tail6a40cd.ts.net/` with Agent Browser, verify all three readiness states in normal and full view, run Browser Harness and Stagehand smoke tests, confirm Unbrowse fails closed when unauthenticated, and capture a connected screenshot with no new console/page errors.

- [ ] **Step 4: Commit and push**

Use the required smart commit workflow with message:

```text
feat(harnesses): add live browser tool readiness
```

