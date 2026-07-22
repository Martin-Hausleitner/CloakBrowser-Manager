# Profile Health, Proxy And BrowserScan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a redacted, access-controlled profile-health result that measures browser-path reachability, saved-config/runtime fingerprint consistency, optional VCVM proxychecker risk, and conservative BrowserScan authenticity after the first successful launch.

**Architecture:** Keep measurement logic in a dependency-injected `backend/profile_health.py` service, persistence in the existing SQLite module, authorization and task scheduling in FastAPI, and read-only presentation in existing profile surfaces. Launch remains asynchronous with respect to the probe. External sources are optional, fail closed, and are reduced to safe normalized fields before persistence.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, SQLite, Playwright async API, httpx, React 19, TypeScript, Vitest/Testing Library, Tailwind CSS.

**Design source:** `docs/superpowers/specs/2026-07-22-profile-health-proxy-browserscan-design.md`

---

### Task 1: Add normalized health models and persistence

**Files:**
- Modify: `backend/models.py`
- Modify: `backend/database.py`
- Modify: `backend/tests/test_models.py`
- Modify: `backend/tests/test_database.py`

- [ ] **Step 1: Write failing model and migration tests**

Add tests proving the response defaults to explicit unavailable/null values, accepts only the six documented states, creates the additive table on an existing database, upserts one row per profile, and deletes the row with the profile.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv/bin/pytest backend/tests/test_models.py backend/tests/test_database.py -q`

Expected: FAIL because the health models and persistence helpers do not exist.

- [ ] **Step 3: Implement the schema and normalized repository helpers**

Add `ProfileHealthResponse`, component source metadata, state literals, safe warning/blocker lists, table creation, `get_profile_health`, `upsert_profile_health`, and profile-delete cleanup. Keep JSON parsing defensive and return normalized empty containers on corrupt legacy content.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest backend/tests/test_models.py backend/tests/test_database.py -q`

Expected: all focused tests pass.

### Task 2: Implement pure redaction, consistency and classifier functions

**Files:**
- Create: `backend/profile_health.py`
- Create: `backend/tests/test_profile_health.py`

- [ ] **Step 1: Write failing pure-function tests**

Cover:

- IPv4/IPv6 masking without retaining the raw value;
- risk-score clamping and authenticity derivation;
- trusted/blocked proxychecker URLs;
- proxychecker response normalization and safe reason categories;
- matching, mismatching and missing fingerprint signals;
- BrowserScan explicit score extraction;
- challenge, consent, timeout and unsupported markup returning a blocker with `score=None`;
- raw external text never surviving normalization.

- [ ] **Step 2: Run the new test file and verify RED**

Run: `.venv/bin/pytest backend/tests/test_profile_health.py -q`

Expected: FAIL because `profile_health.py` does not exist.

- [ ] **Step 3: Implement the smallest pure functions**

Use bounded regular expressions, enum-like constants, length limits, `ipaddress`, `urllib.parse`, and typed dictionaries/dataclasses. Do not introduce a new dependency.

- [ ] **Step 4: Verify GREEN and scan for accidental raw-data fields**

Run: `.venv/bin/pytest backend/tests/test_profile_health.py -q`

Run: `rg -n "raw_(ip|proxy|html|text|response)|page_content|exception_message" backend/profile_health.py backend/models.py`

Expected: tests pass; no persisted or response field is designed for raw external data.

### Task 3: Implement dependency-injected runtime observations

**Files:**
- Modify: `backend/profile_health.py`
- Modify: `backend/tests/test_profile_health.py`
- Reference: `backend/browser_manager.py`

- [ ] **Step 1: Write failing async observation tests with fakes**

Prove that the service opens temporary pages in the existing context, measures browser-path network reachability, evaluates runtime signals, classifies BrowserScan without clicking anything, closes every page in `finally`, normalizes proxychecker responses, and produces a result when individual optional sources are unavailable.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest backend/tests/test_profile_health.py -q`

Expected: FAIL on missing orchestration methods.

- [ ] **Step 3: Implement bounded adapters and orchestrator**

Implement `ProfileHealthProbe` with injectable clock, HTTP client factory, endpoint URLs, and browser page factory/context. Add per-component timeouts, a fixed public target, and no retries. Aggregate state from component results without manufacturing missing scores.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest backend/tests/test_profile_health.py -q`

Expected: all probe tests pass without external network access.

### Task 4: Add access-controlled APIs and first-launch scheduling

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/browser_manager.py` only if a minimal launch result hook is needed
- Modify: `backend/tests/test_api.py`
- Modify: `backend/tests/test_access_control.py`
- Create or Modify: `backend/tests/test_profile_health_api.py`

- [ ] **Step 1: Write failing API and authorization tests**

Cover default read, stored read, viewer/operator/admin permissions, missing/unauthorized 404 equivalence, stopped-profile conflict, duplicate in-flight reuse, redaction, and HTTP 202 scheduling.

- [ ] **Step 2: Write failing launch-scheduling tests**

Prove first successful launch schedules once, a stored result suppresses automatic rerun, duplicate launch does not duplicate the probe, and a probe failure never changes a successful launch response.

- [ ] **Step 3: Run focused backend tests and verify RED**

Run: `.venv/bin/pytest backend/tests/test_profile_health_api.py backend/tests/test_api.py backend/tests/test_access_control.py -q`

Expected: FAIL because the routes and scheduler are absent.

- [ ] **Step 4: Implement scoped routes and in-flight registry**

Reuse existing scoped-profile helpers, store `asyncio.Task` objects by profile id, attach a done callback that removes the exact completed task, and persist only normalized results. Keep launch response timing independent of the probe.

- [ ] **Step 5: Verify GREEN**

Run: `.venv/bin/pytest backend/tests/test_profile_health_api.py backend/tests/test_api.py backend/tests/test_access_control.py -q`

Expected: all focused API and access tests pass.

### Task 5: Add a compact read-only health presentation

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`
- Create: `frontend/src/components/ProfileHealthSummary.tsx`
- Create: `frontend/src/components/ProfileHealthSummary.test.tsx`
- Modify: `frontend/src/components/ProfileList.tsx`
- Modify: `frontend/src/components/ProfileList.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Test: `frontend/src/components/mobile/MobileSplitScreen.test.tsx`

- [ ] **Step 1: Write failing API and rendering tests**

Assert type-safe fetch/run methods, compact status display, explicit measured/derived/unavailable/skipped labels, operator-only rerun, viewer read access, and absence of raw proxy/IP/error strings.

- [ ] **Step 2: Lock the mobile non-clutter rule with a failing or regression test**

Assert the live mobile workspace has no persistent `Run scan`, `Benchmark`, or `Proxy check` control.

- [ ] **Step 3: Run focused frontend tests and verify RED**

Run: `cd frontend && npm test -- --run src/lib/api.test.ts src/components/ProfileHealthSummary.test.tsx src/components/ProfileList.test.tsx src/App.test.tsx src/components/mobile/MobileSplitScreen.test.tsx`

Expected: new API/component tests fail before implementation; the non-clutter regression remains green.

- [ ] **Step 4: Implement compact read-only UI**

Use existing tokens and progressive disclosure. Show one small list status and a details panel in the desktop/profile settings surface. Do not add settings, target URLs, proxy inputs, or external page content.

- [ ] **Step 5: Verify GREEN and build**

Run: `cd frontend && npm test -- --run src/lib/api.test.ts src/components/ProfileHealthSummary.test.tsx src/components/ProfileList.test.tsx src/App.test.tsx src/components/mobile/MobileSplitScreen.test.tsx && npm run build`

Expected: tests and production build pass.

### Task 6: Configure and validate the VCVM-local proxychecker boundary

**Files:**
- Modify: `docker-compose.vcvm.yml`
- Modify: `.env.example` if present
- Modify: `scripts/deploy_vcvm.sh`
- Modify or Create: relevant deployment-surface tests under `scripts/tests/`
- Modify: `docs/VCVM-DEPLOYMENT.md` or the current deployment document

- [ ] **Step 1: Add failing deployment-surface tests**

Assert the Manager accepts an optional proxychecker URL, the Docker service can reach only the explicitly configured host-local endpoint, the Manager remains bound to loopback, and no credential is rendered into public documentation.

- [ ] **Step 2: Implement optional configuration**

Add the minimal host gateway/configuration needed for VCVM deployment. Do not default a public or untrusted URL and do not make the Manager healthcheck depend on proxychecker availability.

- [ ] **Step 3: Verify deployment tests**

Run the focused script/deployment tests identified by `rg -n "deploy_vcvm|docker-compose.vcvm" scripts/tests`.

Expected: all focused tests pass.

### Task 7: Run full local release verification

**Files:**
- Verify all changed files above.

- [ ] **Step 1: Run the complete backend suite**

Run: `.venv/bin/pytest backend/tests -q`

Expected: all backend tests pass.

- [ ] **Step 2: Run the complete frontend suite and production build**

Run: `cd frontend && npm test -- --run && npm run build`

Expected: all frontend tests and build pass.

- [ ] **Step 3: Run the complete script/release suite**

Run the repository's canonical script test command documented by the continuation skill/release checklist.

Expected: all script and release checks pass, including Python 3.11 compilation.

- [ ] **Step 4: Run static diff and secret checks**

Run: `git diff --check`

Run the repository's secret/redaction gate against explicitly changed paths.

Expected: no whitespace, secret, raw proxy, token, or credential findings.

### Task 8: Deploy and prove the live VCVM path

**Files:**
- Update: `docs/GOAL-ACCEPTANCE-MATRIX-2026-07-22.md`
- Update: `README.md`
- Update: `.agents/skills/cloakbrowser-manager-development/SKILL.md` only if the proven workflow adds a durable instruction
- Update: this plan's execution record

- [ ] **Step 1: Verify/start the existing proxychecker on VCVM loopback**

Confirm the correct process owns the selected port and that `/health` returns the expected schema. Do not print `.env`, proxy URLs, or credentials.

- [ ] **Step 2: Deploy Manager on VCVM with the trusted local endpoint**

Use the existing secret-file deployment path and keep the public bind unchanged.

- [ ] **Step 3: Run authenticated browser E2E**

Launch a test profile, prove launch is not blocked by the probe, wait for the stored result, verify masking and source states, rerun manually, refresh, and confirm persistence. Exercise viewer versus operator authorization.

- [ ] **Step 4: Validate the mobile UI and capture safe screenshots**

Use the existing browser gate across required viewports. Confirm no new mobile clutter, no keyboard regression, no raw IP/proxy/error, and no hidden Full View controls.

- [ ] **Step 5: Update documentation from fresh evidence only**

Change the matrix status to `Proven` only if VCVM evidence covers the full slice. Record exact test counts, deployment port, screenshot artifact location, and remaining physical Safari/Tailnet blockers without embedding secrets or local-only URLs.

### Task 9: Commit, push to the fork and verify remote bytes

**Files:**
- Stage only files intentionally changed by this plan.

- [ ] **Step 1: Review status and explicit staged diff**

Run: `git status --short`

Run: `git diff --cached --stat && git diff --cached`

Expected: no generated `tsbuildinfo`, unrelated benchmark/Fintaro artifacts, credentials, or user-owned files are staged.

- [ ] **Step 2: Commit the vertical slice**

Use a focused message such as `feat(health): add redacted profile runtime checks`.

- [ ] **Step 3: Push only the active fork branch**

Push `integrate-pr-47-27-26` to the `fork` remote. Do not push to upstream or Fintaro.

- [ ] **Step 4: Verify the remote SHA and GitHub files**

Confirm `fork/integrate-pr-47-27-26` equals local `HEAD`, and read back the committed README, continuation skill, design, plan, and implementation files from the fork GitHub URLs.

- [ ] **Step 5: Record execution evidence**

Mark completed checkboxes, add commit SHA and fresh test/deployment evidence to this plan, and leave all unproven device/network requirements explicit.
