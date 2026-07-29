# Antigravity Browser Tool Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a provider-independent, ordered Browser Harness/Unbrowse/Stagehand tool stack to managed CloakBrowser runs and prove the first Grok ACP vertical slice on VCVM.

**Architecture:** Keep the current worker harness fields for compatibility while adding normalized provider, browser-tool, and routing-policy fields. The existing ACPX worker is the first provider runner. It passes one run-scoped router configuration to `cbm_mcp.py`, which becomes the stable browser-tool facade and enforces ordered fallback without allowing a second browser.

**Tech Stack:** FastAPI/Pydantic, SQLite, Python MCP SDK, ACPX, React/TypeScript, Pytest, Vitest.

---

### Task 1: Normalized provider and browser-tool run contract

**Files:**
- Modify: `backend/models.py`
- Modify: `backend/database.py`
- Modify: `backend/main.py`
- Modify: `backend/worker_runtime.py`
- Test: `backend/tests/test_models.py`
- Test: `backend/tests/test_task_runs_api.py`
- Test: `backend/tests/test_browser_use_internal_api.py`

- [ ] Write failing model tests for `provider`, ordered `browser_tools`, and `routing_policy`.
- [ ] Verify RED with the three focused backend test files.
- [ ] Add exact provider/tool literals and strict nested models with `extra="forbid"`.
- [ ] Reject duplicate tools, empty enabled stacks, unsupported transports, `allow_second_browser=true`, and browser tools on incompatible unmanaged requests.
- [ ] Add nullable `provider_json`, `browser_tools_json`, and `routing_policy_json` columns through the existing idempotent migration path.
- [ ] Normalize legacy `harness=acpx, agent=grok-build` into provider `grok/acp`; normalize standalone legacy browser workers into a one-tool stack.
- [ ] Thread normalized fields through create/read/public response/internal claim/capability response.
- [ ] Assert exact equality of provider/tool/routing fields across create response, `WorkerClaimResponse`, and `WorkerCapabilityResponse`; legacy responses use `null`, `[]`, `null`.
- [ ] Preserve the current stored `harness="antigravity"` compatibility path for legacy `acpx/grok-build` runs without calling it Google Antigravity in normalized fields.
- [ ] Prove legacy requests still return 201 and new requests persist exact tool order.
- [ ] Run focused tests GREEN and self-review.

### Task 2: Run-scoped router environment and fallback state machine

**Files:**
- Modify: `scripts/acpx_worker.py`
- Modify: `scripts/cbm_mcp.py`
- Create: `scripts/browser_tool_router.py`
- Test: `scripts/test_acpx_worker.py`
- Test: `scripts/test_cbm_mcp.py`
- Create: `scripts/test_browser_tool_router.py`

- [ ] Write failing tests proving ACPX injects bounded JSON tool/routing fields and defaults legacy claims safely.
- [ ] Write failing pure-router tests for ordered success, eligible fallback, fail-closed errors, timeouts, and attempt bounds.
- [ ] Verify RED.
- [ ] Add immutable router models and an adapter protocol.
- [ ] Make `cbm_mcp` parse the run-scoped router configuration and expose one stable MCP surface.
- [ ] Preserve the existing Manager capability/origin checks for every adapter invocation.
- [ ] Emit typed routing telemetry without secrets.
- [ ] Persist routing telemetry only as `{tool_id, action_class, duration_ms, result_class, fallback_reason}` through existing task output kinds; never persist raw prompts, headers, cookies, proxy values, capability tokens, or CDP URLs.
- [ ] Run focused tests GREEN and self-review.

### Task 3: Browser Harness adapter and real same-profile fallback

**Files:**
- Create: `scripts/browser_harness_adapter.py`
- Create: `scripts/test_browser_harness_adapter.py`
- Modify: `scripts/browser_tool_router.py`
- Modify: `scripts/cbm_mcp.py`
- Modify: worker provisioning/requirements only if the installed package is not already runtime-visible.

- [ ] Write failing tests for run-scoped `BU_CDP_URL`, origin enforcement, bounded actions, cancellation, and denial of arbitrary Python/shell execution.
- [ ] Verify RED.
- [ ] Reuse the installed `browser-use/browser-harness` package through a bounded adapter attached only to the Manager capability gateway.
- [ ] Prove it never discovers or launches another browser.
- [ ] Run adapter/router tests GREEN and self-review.

### Task 4: Unbrowse and Stagehand adapters

**Files:**
- Create or modify focused adapter modules under `scripts/`.
- Reuse: `scripts/unbrowse_worker.py`, `scripts/stagehand_worker.py`, and their existing helpers.
- Test: corresponding focused worker/adapter tests.

- [ ] Extract reusable same-profile operations from existing standalone workers without changing their legacy behavior.
- [ ] Add Unbrowse adapter tests for route success, route miss, capability propagation, and no independent browser fallback.
- [ ] Add Stagehand adapter tests for ready semantic action, `model_required`, and no independent browser launch.
- [ ] Verify RED, implement minimal adapters, then verify GREEN.

### Task 5: Provider detection and optional OpenAI-compatible proxy

**Files:**
- Modify: backend provider readiness models/routes.
- Modify: `scripts/acpx_worker.py` and/or focused provider modules.
- Test: provider readiness and redaction tests.

- [ ] Add tests for installed `agy`, `grok`, ACPX preflight, and loopback proxy `/v1/models` readiness.
- [ ] Add provider result reason codes: `ready`, `auth_required`, `protocol_unavailable`, `proxy_unavailable`, and `model_unavailable`.
- [ ] Never return proxy credentials or raw model configuration.
- [ ] Keep Grok ACP as the first production E2E runner; expose Antigravity CLI and OpenAI-compatible modes honestly according to readiness.
- [ ] Keep `grok/openai-compatible` invalid for create-run until a bounded OpenAI tool-call loop is implemented and tested in this task; readiness alone must not advertise execution capability.

### Task 6: Compact provider/tool UI

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/workspace/AgentBrowserWorkspace.tsx`
- Test: `frontend/src/components/workspace/AgentBrowserWorkspace.test.tsx`

- [ ] Write failing tests for independent provider and ordered tool controls.
- [ ] Prove changing either does not remount `ProfileViewer`.
- [ ] Replace the incorrect `Antigravity -> Grok Build` label/preset.
- [ ] Add compact readiness badges, reorder/enable controls, and per-item smoke actions behind one collapsible panel.
- [ ] Submit the normalized run contract and preserve legacy UI paths.
- [ ] Run workspace tests and production build GREEN.

### Task 7: Reviews, deployment, and real VCVM E2E

**Files:**
- Create: `docs/reports/VCVM-ANTIGRAVITY-BROWSER-TOOL-ROUTER-E2E-2026-07-29.md`

- [ ] Run spec-compliance review for every task.
- [ ] Run code-quality/security review after spec approval.
- [ ] Run focused tests, full backend suite, frontend suite, lint/type checks, and build sequentially.
- [ ] Deploy through the existing VCVM release path.
- [ ] Run Grok ACP with ordered `Unbrowse -> Stagehand -> Browser Harness` tools against a harmless explicit URL.
- [ ] Prove the same profile/browser is retained across fallback using profile ID, capability/run IDs, browser PID/target evidence, and screenshot.
- [ ] Verify public UI with Agent Browser and capture screenshot, console errors, network errors, and operator-visible status.
- [ ] Commit, push to Martin's fork, and publish immutable report evidence.
