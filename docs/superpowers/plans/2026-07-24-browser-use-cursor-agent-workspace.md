# Browser-Use Cursor Agent Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one compact CloakBrowser Manager product in which Cursor CLI submits Browser-Use work to an isolated VCVM sidecar controlling the same persistent anti-stealth browser profile, with projects, temporary/done tasks, typed outputs, and shared desktop/mobile/fullscreen controls.

**Architecture:** The FastAPI Manager owns profiles, authorization, health policy, projects, tasks, runs, outputs, automation leases, and artifacts. A private Browser-Use sidecar claims runs and attaches through a short-lived Manager CDP capability; Cursor CLI is only a scoped public API client. React renders one adaptive workspace and reuses a single mounted viewer plus one control model across desktop, mobile, and fullscreen.

**Tech Stack:** Python 3.11-3.13, FastAPI, SQLite, Browser-Use 0.11.2 pinned sidecar, React 19, TypeScript 5.7, Vite/Vitest, noVNC/CDP, Docker Compose, Cursor Agent CLI 2026.07.20.

---

## File structure

New backend responsibilities live in focused modules instead of further expanding `backend/main.py`:

- `backend/origin_policy.py` — normalize and enforce run navigation origins.
- `backend/run_health.py` — immutable snapshot and deterministic gate.
- `backend/automation_leases.py` — profile lease, claim, capability, expiry.
- `backend/cdp_gateway.py` — lease-aware CDP discovery/socket registry.
- `backend/artifact_store.py` — private screenshot ingestion and retrieval.
- `backend/workspace_maintenance.py` — clock-injected expiry/retention loops.
- `browser_use_sidecar/` — isolated worker, Manager client, Browser-Use adapter.
- `scripts/cbm_http_client.py` — reusable scoped public Manager client.
- `scripts/cbm_cursor_browser_use.py` — stable NDJSON Cursor-facing adapter.
- `.cursor/rules/cloakbrowser-browser-use.mdc` — instruct Cursor to call the adapter.
- `.agents/skills/cloakbrowser-manager-development/references/cursor-browser-use.md` — canonical agent-facing contract.
- `frontend/src/components/workspace/` — projects/tasks, typed timeline, composer, browser pane.
- `frontend/src/components/viewer/` — shared viewer controls and mobile identity editor.

## Task 0: Record the clean baseline

**Files:** No tracked files change.

- [ ] Create `.venv` with Python 3.13 and install backend test requirements.
- [ ] Run `.venv/bin/python -m pytest -q backend/tests`; expected baseline: `399 passed, 1 warning`.
- [ ] Run `cd frontend && npm ci && npm test && npm run build`; expected baseline: `135 passed` and build success.
- [ ] Record the pre-existing npm audit result (`1 low, 4 high`) separately; do not change dependencies here.
- [ ] Verify `.venv/`, `frontend/node_modules/`, and `frontend/dist/` remain ignored and `git diff --check` passes.

## Task 1: Lock the clean baseline and migration contract

**Files:**
- Create: `backend/tests/test_workspace_migration.py`
- Modify: `backend/database.py`
- Test: `backend/tests/test_database.py`

- [ ] **Step 1: Write the failing legacy-migration tests**

```python
def test_workspace_migration_preserves_history_and_snapshots_ownership(tmp_path):
    legacy = create_legacy_database(tmp_path, sandbox_id="alpha", project_id="default")
    db = Database(legacy)
    task = db.get_task_session("task-1")
    assert task["sandbox_id"] == "alpha"
    assert task["project_id"] == "default"
    assert task["profile_id"] == "profile-1"

def test_profile_delete_sets_null_and_preserves_task_history(database):
    task = seeded_task(database)
    database.delete_profile(task["profile_id"])
    assert database.get_task_session(task["id"])["profile_id"] is None
```

- [ ] **Step 2: Verify RED**

Run: `pytest -q backend/tests/test_workspace_migration.py`

Expected: FAIL because the legacy schema has no immutable task sandbox and cascades profile deletion.

- [ ] **Step 3: Implement the idempotent SQLite migration**

Add a `schema_migrations` table and `_migrate_agent_workspace_v1(conn)` that rebuilds `task_sessions` with nullable `profile_id ... ON DELETE SET NULL`, copies `sandbox_id/project_id`, preserves IDs/timestamps/messages/events, and removes the task rewrite from `update_profile()`.

- [ ] **Step 4: Verify GREEN and regression**

Run: `pytest -q backend/tests/test_workspace_migration.py backend/tests/test_database.py backend/tests/test_task_sessions_api.py`

Expected: PASS with the two old move/delete assertions replaced by immutable-history assertions.

- [ ] **Step 5: Commit**

```bash
git add backend/database.py backend/tests/test_workspace_migration.py backend/tests/test_database.py backend/tests/test_task_sessions_api.py
git commit -m "feat(tasks): Preserve project-owned task history"
```

## Task 2: Add first-class projects and task lifecycle

**Files:**
- Modify: `backend/models.py`
- Modify: `backend/database.py`
- Modify: `backend/main.py`
- Create: `backend/tests/test_projects_api.py`
- Create: `backend/tests/test_task_lifecycle_api.py`

- [ ] **Step 1: Write failing project and lifecycle API tests**

```python
def test_empty_project_survives_reload(client, operator_headers):
    created = client.post("/api/projects", headers=operator_headers,
                          json={"id": "research", "name": "Research", "sandbox_id": "alpha"})
    assert created.status_code == 201
    assert client.get("/api/projects?sandbox_id=alpha", headers=operator_headers).json()[0]["id"] == "research"

def test_task_can_be_done_archived_unarchived_and_reopened(client, interact_headers):
    task = create_task(client, interact_headers)
    assert patch_task(client, task, {"workflow_state": "done"})["workflow_state"] == "done"
    assert patch_task(client, task, {"archived": True})["archived_at"]
    assert patch_task(client, task, {"archived": False, "workflow_state": "open"})["workflow_state"] == "open"
```

- [ ] **Step 2: Verify RED**

Run: `pytest -q backend/tests/test_projects_api.py backend/tests/test_task_lifecycle_api.py`

Expected: FAIL with missing routes/models.

- [ ] **Step 3: Implement minimal models, persistence, and routes**

Add `ProjectCreate/Update/Response`, `TaskSessionUpdate`, optimistic `row_version`, sandbox-scoped `404`, and `PATCH /api/task-sessions/{id}`. Authorization resolves from immutable task `sandbox_id`.

- [ ] **Step 4: Verify GREEN**

Run: `pytest -q backend/tests/test_projects_api.py backend/tests/test_task_lifecycle_api.py backend/tests/test_access_control.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/database.py backend/main.py backend/tests/test_projects_api.py backend/tests/test_task_lifecycle_api.py
git commit -m "feat(projects): Add projects and temporary task lifecycle"
```

## Task 3: Normalize origins and freeze health decisions

**Files:**
- Create: `backend/origin_policy.py`
- Create: `backend/run_health.py`
- Create: `backend/tests/test_origin_policy.py`
- Create: `backend/tests/test_run_health.py`
- Modify: `backend/profile_health.py`

- [ ] **Step 1: Write failing policy tests**

```python
@pytest.mark.parametrize("value", ["*.example.com", "https://user@example.com", "https://example.com/path", "javascript:alert(1)"])
def test_rejects_ambiguous_origin(value):
    with pytest.raises(ValueError):
        normalize_origin(value)

def test_warning_health_requires_score_and_no_critical_reasons():
    decision = evaluate_health(snapshot(state="warning", score=89, reasons=[]), policy=POLICY)
    assert decision.allowed is True
    assert evaluate_health(snapshot(state="warning", score=89, reasons=["platform_ua_mismatch"]), POLICY).allowed is False
```

- [ ] **Step 2: Verify RED**

Run: `pytest -q backend/tests/test_origin_policy.py backend/tests/test_run_health.py`

Expected: FAIL because modules are absent.

- [ ] **Step 3: Implement pure deterministic modules**

Use `urllib.parse`, IDNA normalization, explicit HTTP(S) origins, versioned `HealthPolicy`, existing states `pending|running|passed|warning|failed|unavailable`, a 10-minute default freshness window, score 70, and non-overridable proxy/measurement failures.

- [ ] **Step 4: Verify GREEN**

Run: `pytest -q backend/tests/test_origin_policy.py backend/tests/test_run_health.py backend/tests/test_profile_health.py backend/tests/test_profile_health_api.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/origin_policy.py backend/run_health.py backend/profile_health.py backend/tests/test_origin_policy.py backend/tests/test_run_health.py
git commit -m "feat(health): Gate runs with immutable identity policy"
```

## Task 4: Add runs, typed outputs, and durable retry markers

**Files:**
- Modify: `backend/models.py`
- Modify: `backend/database.py`
- Modify: `backend/main.py`
- Create: `backend/tests/test_task_runs_api.py`
- Create: `backend/tests/test_task_outputs_api.py`

- [ ] **Step 1: Write failing run/output tests**

```python
def test_output_idempotency_and_first_action_marker(client, automate_headers):
    run = create_run(client, automate_headers)
    body = {"idempotency_key": "step-1", "kind": "action", "summary": "Navigate", "payload": {"url": "https://example.com"}}
    first = append_internal_output(client, run, body)
    second = append_internal_output(client, run, body)
    assert first["id"] == second["id"]
    assert get_run(client, run)["first_action_sequence"] == first["sequence"]
```

- [ ] **Step 2: Verify RED**

Run: `pytest -q backend/tests/test_task_runs_api.py backend/tests/test_task_outputs_api.py`

Expected: FAIL with missing tables/routes.

- [ ] **Step 3: Implement run/output storage and public APIs**

Store prompt once as a task message; create a run referencing `task_message_id`, explicit same-sandbox `profile_id`, immutable health JSON, normalized origins, deadline, status, retry markers, and ordered allowlisted outputs. Add create/get/cancel/retry-health/override/tail routes.

- [ ] **Step 4: Verify GREEN**

Run: `pytest -q backend/tests/test_task_runs_api.py backend/tests/test_task_outputs_api.py backend/tests/test_task_sessions_api.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/database.py backend/main.py backend/tests/test_task_runs_api.py backend/tests/test_task_outputs_api.py
git commit -m "feat(runs): Persist Browser-Use runs and typed outputs"
```

## Task 5: Serialize automation and harden CDP

**Files:**
- Create: `backend/automation_leases.py`
- Create: `backend/cdp_gateway.py`
- Modify: `backend/main.py`
- Modify: `backend/session_views.py`
- Create: `backend/tests/test_automation_leases.py`
- Create: `backend/tests/test_cdp_automation_leases_api.py`
- Create: `backend/tests/test_cdp_observer.py`

- [ ] **Step 1: Write failing lease and bypass tests**

```python
def test_second_profile_lease_is_busy(service):
    first = service.acquire_direct("profile-1", actor="agent-a")
    with pytest.raises(AutomationBusy):
        service.acquire_direct("profile-1", actor="agent-b")
    assert service.validate(first.token, "profile-1", "agent-a")

def test_cdp_query_token_is_rejected(client, automate_headers):
    assert client.get("/api/profiles/p1/cdp/json/version?token=secret", headers=automate_headers).status_code == 400
```

- [ ] **Step 2: Verify RED**

Run: `pytest -q backend/tests/test_automation_leases.py backend/tests/test_cdp_automation_leases_api.py backend/tests/test_cdp_observer.py`

Expected: FAIL.

- [ ] **Step 3: Implement lease service and observer split**

Use `BEGIN IMMEDIATE`, a partial unique profile index, 32 random bytes, SHA-256 at rest, `hmac.compare_digest`, 15-second heartbeat, 45-second expiry, `X-CBM-Automation-Lease`, Manager-only discovery URLs, and socket revocation. Live observer accepts only fixed screencast actions; interactive input remains on VNC or a leased surface.

- [ ] **Step 4: Verify GREEN and revocation regression**

Run: `pytest -q backend/tests/test_automation_leases.py backend/tests/test_cdp_automation_leases_api.py backend/tests/test_cdp_observer.py backend/tests/test_access_control.py backend/tests/test_session_views.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/automation_leases.py backend/cdp_gateway.py backend/main.py backend/session_views.py backend/tests/test_automation_leases.py backend/tests/test_cdp_automation_leases_api.py backend/tests/test_cdp_observer.py
git commit -m "feat(cdp): Enforce exclusive scoped automation leases"
```

## Task 6: Add private screenshots and retention maintenance

**Files:**
- Create: `backend/artifact_store.py`
- Create: `backend/workspace_maintenance.py`
- Modify: `backend/main.py`
- Create: `backend/tests/test_artifact_store.py`
- Create: `backend/tests/test_workspace_cleanup.py`

- [ ] **Step 1: Write failing traversal, size, and clock tests**

```python
def test_screenshot_store_ignores_caller_paths(store, png_bytes):
    artifact = store.ingest_screenshot(output_id="out-1", body=png_bytes, media_type="image/png", sha256=sha256(png_bytes))
    assert artifact.path.is_relative_to(store.root)
    assert "out-1" not in artifact.path.name

def test_temporary_task_archives_after_seven_days(maintenance, clock):
    clock.advance(days=7, seconds=1)
    maintenance.cleanup_retention_once()
    assert maintenance.db.get_task_session("task-1")["archived_at"]
```

- [ ] **Step 2: Verify RED**

Run: `pytest -q backend/tests/test_artifact_store.py backend/tests/test_workspace_cleanup.py`

Expected: FAIL.

- [ ] **Step 3: Implement minimal safe store and idempotent cleanup**

Validate PNG/JPEG, 5 MiB, 4096², digest, `0700` root, `0600` files, opaque names, `O_NOFOLLOW|O_EXCL`, atomic rename, authorized retrieval, seven-day artifact expiry, seven-day inactivity archive, and 30-day purge using an injected clock.

- [ ] **Step 4: Verify GREEN**

Run: `pytest -q backend/tests/test_artifact_store.py backend/tests/test_workspace_cleanup.py backend/tests/test_task_outputs_api.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/artifact_store.py backend/workspace_maintenance.py backend/main.py backend/tests/test_artifact_store.py backend/tests/test_workspace_cleanup.py
git commit -m "feat(outputs): Store screenshots and expire temporary tasks"
```

## Task 7: Protect the internal worker API

**Files:**
- Modify: `backend/access_control.py`
- Modify: `backend/main.py`
- Create: `backend/tests/test_worker_auth.py`
- Create: `backend/tests/test_browser_use_internal_api.py`

- [ ] **Step 1: Write failing worker-auth tests**

```python
def test_internal_routes_reject_admin_and_agent_tokens(client, admin_headers, agent_headers):
    assert client.post("/internal/task-runs/claim", headers=admin_headers).status_code == 401
    assert client.post("/internal/task-runs/claim", headers=agent_headers).status_code == 401

def test_worker_claim_returns_no_capability(client, worker_headers):
    payload = client.post("/internal/task-runs/claim", headers=worker_headers).json()
    assert "capability" not in payload
```

- [ ] **Step 2: Verify RED**

Run: `pytest -q backend/tests/test_worker_auth.py backend/tests/test_browser_use_internal_api.py`

Expected: FAIL because `/internal/*` is not protected.

- [ ] **Step 3: Implement worker identity and internal contracts**

Protect `/internal/*` in middleware, hash worker keys, bind claims, implement heartbeat/capability/output/screenshot/complete/fail/revoke, and revoke claims/sockets on rotation.

- [ ] **Step 4: Verify GREEN**

Run: `pytest -q backend/tests/test_worker_auth.py backend/tests/test_browser_use_internal_api.py backend/tests/test_access_control.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/access_control.py backend/main.py backend/tests/test_worker_auth.py backend/tests/test_browser_use_internal_api.py
git commit -m "feat(worker): Add scoped Browser-Use worker API"
```

## Task 8: Implement the Browser-Use VCVM sidecar

**Files:**
- Create: `browser_use_sidecar/models.py`
- Create: `browser_use_sidecar/manager_client.py`
- Create: `browser_use_sidecar/origin_guard.py`
- Create: `browser_use_sidecar/browser_use_adapter.py`
- Create: `browser_use_sidecar/worker.py`
- Create: `browser_use_sidecar/requirements.lock`
- Create: `browser_use_sidecar/Dockerfile`
- Create: `browser_use_sidecar/tests/`

- [ ] **Step 1: Write failing adapter/worker contract tests**

```python
async def test_adapter_attaches_and_never_kills_browser(fake_browser, capability):
    adapter = BrowserUseAdapter(browser_factory=fake_browser.factory)
    await adapter.run(capability, task="Read title", max_steps=3)
    assert fake_browser.created_contexts == 0
    assert fake_browser.kill_calls == 0
    assert fake_browser.disconnect_calls == 1
```

- [ ] **Step 2: Verify RED**

Run: `pytest -q browser_use_sidecar/tests`

Expected: FAIL because the package is absent.

- [ ] **Step 3: Implement the minimal pinned worker**

Pin Browser-Use `0.11.2`; disable telemetry; claim/heartbeat; request capability only after health; attach with Manager CDP URL and header, `keep_alive=True`, no user data/proxy/UA override; emit status/action/observation/screenshot/data/summary; disconnect in `finally`; never launch or kill Chromium.

- [ ] **Step 4: Verify GREEN and dependency contract**

Run: `pytest -q browser_use_sidecar/tests && python -m compileall -q browser_use_sidecar`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add browser_use_sidecar
git commit -m "feat(browser-use): Add isolated VCVM worker"
```

## Task 9: Add the Cursor CLI public harness

**Files:**
- Create: `scripts/cbm_http_client.py`
- Create: `scripts/cbm_cursor_browser_use.py`
- Create: `scripts/test_cbm_cursor_browser_use.py`
- Modify: `scripts/cbm_agent_ctl.py`
- Create: `.cursor/rules/cloakbrowser-browser-use.mdc`
- Modify: `.agents/skills/cloakbrowser-manager-development/SKILL.md`
- Create: `.agents/skills/cloakbrowser-manager-development/references/cursor-browser-use.md`

- [ ] **Step 1: Write failing public-client and timeout tests**

```python
def test_cursor_harness_never_calls_internal_routes(fake_transport):
    run = CursorPublicHarnessAdapter(fake_transport).submit(PROJECT, TASK, PROFILE, "Read title")
    assert run.id == "run-1"
    assert all(not request.url.path.startswith("/internal/") for request in fake_transport.requests)

def test_idle_timeout_cancels_once_and_redacts_token(fake_transport, capsys):
    result = run_cli(["run", "--idle-timeout", "1"], transport=fake_transport)
    assert result == 124
    assert fake_transport.cancel_count == 1
    assert "cbm_agent_" not in capsys.readouterr().err
```

- [ ] **Step 2: Verify RED**

Run: `pytest -q scripts/test_cbm_cursor_browser_use.py scripts/test_cbm_agent_ctl.py`

Expected: FAIL.

- [ ] **Step 3: Implement stable NDJSON commands**

Implement `projects`, `tasks`, `run`, `tail`, `cancel`; validate `CBM_BASE_URL`; read `CBM_AGENT_KEY` without printing it; use public routes only; ignore unknown input fields; emit `{type,run_id,sequence,status,kind,summary,payload}`. Exit `0` success, `1` terminal Manager failure, `64` usage, `69` transport/unreachable, `77` missing scoped key, `124` timeout, `130` SIGINT, and `143` SIGTERM. Emit a local waiting heartbeat every five seconds; require the first observable Cursor record within five seconds; on timeout/signal cancel once.

- [ ] **Step 4: Verify GREEN and real Cursor failure contract**

Run: `pytest -q scripts/test_cbm_cursor_browser_use.py scripts/test_cbm_agent_ctl.py`

Run: `python scripts/cbm_cursor_browser_use.py --help`

Expected: tests PASS and help exits 0. Record the current `cursor-agent -p --mode ask` hang as a bounded external acceptance case, not an infinite process.

- [ ] **Step 5: Commit**

```bash
git add scripts/cbm_http_client.py scripts/cbm_cursor_browser_use.py scripts/test_cbm_cursor_browser_use.py scripts/cbm_agent_ctl.py .cursor/rules/cloakbrowser-browser-use.mdc .agents/skills/cloakbrowser-manager-development
git commit -m "feat(cursor): Add scoped Browser-Use harness adapter"
```

## Task 10: Extend the frontend API and workspace state

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`
- Create: `frontend/src/hooks/useAgentWorkspace.ts`
- Create: `frontend/src/hooks/useAgentWorkspace.test.ts`

- [ ] **Step 1: Write failing typed-client tests**

```typescript
it('creates a run with explicit project task and profile', async () => {
  await api.createTaskRun('task-1', { harness: 'browser-use', profile_id: 'p1', task: 'Read title', allowed_origins: ['https://example.com'] })
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/task-sessions/task-1/runs'), expect.objectContaining({ method: 'POST' }))
})
```

- [ ] **Step 2: Verify RED**

Run: `cd frontend && npm test -- src/lib/api.test.ts src/hooks/useAgentWorkspace.test.ts`

Expected: FAIL.

- [ ] **Step 3: Implement API types and one server-state hook**

Add projects, task lifecycle, runs, outputs, cancellation, health retry/override, screenshot URL, cursors, polling cleanup, and stable selection. Do not add a new state dependency.

- [ ] **Step 4: Verify GREEN**

Run: `cd frontend && npm test -- src/lib/api.test.ts src/hooks/useAgentWorkspace.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/api.test.ts frontend/src/hooks/useAgentWorkspace.ts frontend/src/hooks/useAgentWorkspace.test.ts
git commit -m "feat(ui): Add agent workspace API state"
```

## Task 11: Build projects, tasks, typed outputs, and fixed sessions UI

**Files:**
- Create: `frontend/src/components/workspace/WorkspaceSidebar.tsx`
- Create: `frontend/src/components/workspace/AgentOutputTimeline.tsx`
- Create: `frontend/src/components/workspace/TaskComposer.tsx`
- Create: `frontend/src/components/workspace/AgentWorkspace.tsx`
- Create: matching `*.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles/globals.css`

- [ ] **Step 1: Write failing behavior tests**

```typescript
it('keeps browser sessions visible while tasks scroll', () => {
  render(<WorkspaceSidebar projects={projects} tasks={manyTasks} profiles={profiles} />)
  expect(screen.getByRole('region', { name: /browser sessions/i })).toBeVisible()
})

it('renders typed data and an unknown safe fallback', () => {
  render(<AgentOutputTimeline outputs={[dataOutput, unknownOutput]} />)
  expect(screen.getByRole('table')).toBeVisible()
  expect(screen.getByText(/details/i)).toBeVisible()
})
```

- [ ] **Step 2: Verify RED**

Run: `cd frontend && npm test -- src/components/workspace`

Expected: FAIL.

- [ ] **Step 3: Implement the compact adaptive workspace**

Use project list, Open/Done groups, bounded fixed profile sessions, typed status/action/screenshot/data/link/error cards, collapsed completed steps, composer, cancel, health warning/override, semantic landmarks, `aria-current`, and 44px coarse-pointer hit areas.

- [ ] **Step 4: Verify GREEN**

Run: `cd frontend && npm test -- src/components/workspace src/App.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/workspace frontend/src/App.tsx frontend/src/styles/globals.css
git commit -m "feat(ui): Add compact Browser-Use agent workspace"
```

## Task 12: Share viewer controls and coherent Phone Fit identity

**Files:**
- Create: `frontend/src/components/viewer/ViewerControls.tsx`
- Create: `frontend/src/components/viewer/ViewportEditor.tsx`
- Create: `frontend/src/components/viewer/FullscreenBrowserDialog.tsx`
- Create: `frontend/src/hooks/useViewerPreferences.ts`
- Modify: `frontend/src/components/mobile/MobileSplitScreen.tsx`
- Modify: `frontend/src/components/ProfileViewer.tsx`
- Modify: `backend/browser_manager.py`
- Modify: `backend/models.py`
- Add matching backend/frontend tests.

- [ ] **Step 1: Write failing parity and identity tests**

```typescript
it.each(['desktop', 'mobile', 'fullscreen'])('%s exposes view viewport and sessions', (surface) => {
  renderSurface(surface)
  expect(screen.getByRole('button', { name: /view/i })).toBeVisible()
  expect(screen.getByRole('button', { name: /viewport/i })).toBeVisible()
  expect(screen.getByRole('button', { name: /sessions/i })).toBeVisible()
})
```

```python
def test_phone_fit_applies_coherent_mobile_identity(manager):
    launch = manager.build_launch(profile(phone_fit=True))
    assert launch.mobile is True and launch.has_touch is True
    assert launch.viewport == launch.screen
    assert "Mobile" in launch.user_agent
```

- [ ] **Step 2: Verify RED**

Run: `cd frontend && npm test -- src/components/viewer src/components/mobile/MobileSplitScreen.test.tsx`

Run: `pytest -q backend/tests/test_browser_manager.py backend/tests/test_models.py`

Expected: FAIL.

- [ ] **Step 3: Extract shared controls and implement mobile identity policy**

Keep one mounted viewer; use enum panels; persist local fit/zoom by profile; persist framebuffer/mobile identity server-side; apply touch, device metrics, platform/UA/screen/viewport together and schedule health. Preserve `visualViewport`, 16px input, safe areas, inert/focus restoration, shortcut typing guards, and VNC input.

- [ ] **Step 4: Verify GREEN and build**

Run: `cd frontend && npm test -- src/components/viewer src/components/mobile/MobileSplitScreen.test.tsx src/components/ProfileViewer.test.tsx && npm run build`

Run: `pytest -q backend/tests/test_browser_manager.py backend/tests/test_models.py backend/tests/test_profile_health.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/viewer frontend/src/hooks/useViewerPreferences.ts frontend/src/components/mobile/MobileSplitScreen.tsx frontend/src/components/ProfileViewer.tsx backend/browser_manager.py backend/models.py backend/tests frontend/src
git commit -m "feat(viewer): Share controls and apply mobile identity"
```

## Task 13: Deploy the private VCVM sidecar

**Files:**
- Modify: `docker-compose.vcvm.yml`
- Modify: `scripts/deploy_vcvm.sh`
- Modify: `scripts/test_vcvm_deployment.py`
- Modify: `docs/VCVM-DEPLOYMENT.md`

- [ ] **Step 1: Write failing deployment-shape tests**

```python
def test_browser_use_sidecar_is_private_and_has_no_profile_mount(compose):
    service = compose["services"]["browser-use-worker"]
    assert "ports" not in service
    assert all("/data" not in volume for volume in service.get("volumes", []))
    assert service["environment"]["ANONYMIZED_TELEMETRY"] == "false"
```

- [ ] **Step 2: Verify RED**

Run: `pytest -q scripts/test_vcvm_deployment.py`

Expected: FAIL.

- [ ] **Step 3: Add pinned private service and secret checks**

Use an immutable image digest, private network, worker Docker secret, provider secret references, no host port, no profile/data mount, healthcheck, bounded memory/CPU, and deployment readiness checks.

- [ ] **Step 4: Verify GREEN**

Run: `pytest -q scripts/test_vcvm_deployment.py && docker compose -f docker-compose.vcvm.yml config -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.vcvm.yml scripts/deploy_vcvm.sh scripts/test_vcvm_deployment.py docs/VCVM-DEPLOYMENT.md
git commit -m "build(vcvm): Deploy private Browser-Use worker"
```

## Task 14: Replace unsafe Skyvern PR #2 without importing AGPL

**Files:**
- Create: `docs/SKYVERN-SIDECAR.md`
- Modify: `frontend/src/lib/harnessOptions.ts`
- Modify: `frontend/src/lib/harnessOptions.test.ts`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write failing disabled-capability test**

```typescript
it('labels Skyvern as an external sidecar requiring license approval', () => {
  const skyvern = harnessOptions.find((item) => item.id === 'skyvern')!
  expect(skyvern.execution).toBe('external-disabled')
  expect(skyvern.reason).toMatch(/AGPL|license/i)
})
```

- [ ] **Step 2: Verify RED**

Run: `cd frontend && npm test -- src/lib/harnessOptions.test.ts`

Expected: FAIL because no safe Skyvern descriptor exists.

- [ ] **Step 3: Document the safe replacement boundary**

Add a disabled external descriptor and document a separately versioned AGPL sidecar, scoped agent key, trusted Manager URL, loopback auth relay, server-owned artifacts, pinned image/source offer/SBOM gate. Do not import Skyvern or expose `/api/harnesses/skyvern/*`.

- [ ] **Step 4: Verify GREEN and close the unsafe PR only after replacement is on main**

Run: `cd frontend && npm test -- src/lib/harnessOptions.test.ts`

Expected: PASS. After the final feature PR merges, close fork PR #2 as superseded with the replacement commit link; do not merge its commits.

- [ ] **Step 5: Commit**

```bash
git add docs/SKYVERN-SIDECAR.md frontend/src/lib/harnessOptions.ts frontend/src/lib/harnessOptions.test.ts CHANGELOG.md
git commit -m "docs(skyvern): Define safe external sidecar boundary"
```

## Task 15: Full local and VCVM acceptance

**Files:**
- Create: `scripts/browser_use_e2e_gate.py`
- Create: `scripts/test_browser_use_e2e_gate.py`
- Modify: `scripts/release_acceptance_gate.py`
- Modify: `scripts/test_release_acceptance_gate.py`
- Modify: `frontend/package.json`
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/agent-workspace.spec.ts`
- Create: `docs/BROWSER-USE-CURSOR-E2E-REPORT-2026-07-24.md`
- Modify: `README.md`

- [ ] **Step 1: Write failing acceptance-runner tests**

```python
def test_gate_requires_same_process_profile_and_typed_outputs(fake_evidence):
    fake_evidence.browser_process_after = "different"
    assert BrowserUseGate(fake_evidence).evaluate().passed is False
```

- [ ] **Step 2: Verify RED**

Run: `pytest -q scripts/test_browser_use_e2e_gate.py scripts/test_release_acceptance_gate.py`

Expected: FAIL.

- [ ] **Step 3: Implement evidence capture and budgets**

Capture commit/image digests, same browser PID/user-data fingerprint/CDP target, masked proxy exit, UA/platform/timezone/locale/screen/touch, health snapshot, VNC-visible navigation, typed outputs, cancellation/revocation, lease contention, 390/768/1024/1440 screenshots, iOS keyboard layout, Cursor CLI success and forced-hang cancellation, FPS/RTT/touch-to-pixel, and secret scan. Add `@playwright/test` and run the adaptive workspace against Chromium and WebKit. Emit JSON plus a generated report without credentials.

- [ ] **Step 4: Run the complete fresh verification**

Run locally:

```bash
pytest -q backend/tests browser_use_sidecar/tests scripts/test_cbm_cursor_browser_use.py scripts/test_browser_use_e2e_gate.py scripts/test_release_acceptance_gate.py scripts/test_vcvm_deployment.py
cd frontend && npm test && npm run build
cd frontend && npx playwright test e2e/agent-workspace.spec.ts --project=chromium --project=webkit
```

Run on VCVM after deployment:

```bash
python scripts/browser_use_e2e_gate.py --base-url http://127.0.0.1:18115 --profile vcvm-mobile-demo --cursor-cli cursor-agent --output artifacts/browser-use-e2e.json
```

Expected: all automated checks pass; E2E JSON reports the same browser identity, visible navigation, typed outputs, bounded Cursor hang, and zero secret findings.

- [ ] **Step 5: Commit evidence**

```bash
git add scripts/browser_use_e2e_gate.py scripts/test_browser_use_e2e_gate.py scripts/release_acceptance_gate.py scripts/test_release_acceptance_gate.py frontend/package.json frontend/playwright.config.ts frontend/e2e/agent-workspace.spec.ts docs/BROWSER-USE-CURSOR-E2E-REPORT-2026-07-24.md README.md
git commit -m "test(e2e): Verify Cursor-driven Browser-Use workspace"
```

## Mandatory plan-review amendments before Task 16

These checks are part of the named tasks above and may not be omitted:

- **Tasks 1-2 migration:** test creation of project rows from every legacy `(sandbox_id, project_id)`, project creator/default-retention/archive/timestamps, task `done_at`, `retention_class`, `expires_at`, activity, row version conflicts, immutable sandbox ownership, and legacy/project retention.
- **Tasks 4-5 lease lifecycle:** test FIFO eligibility, `claim_eligible_at=null` during contention longer than 60 seconds, continuous eligible timeout, another acquirer clearing eligibility, queued cancellation, atomic lease/capability/worker cleanup, exactly one pre-action retry, durable first-action marker, and no post-action retry.
- **Task 5 CDP contract:** test lease acquire/heartbeat/release/expiry, actor and live-grant binding, `/json/version`, `/json/list`, WebSocket authorization, query-token rejection, Manager-only URLs, and immediate socket closure.
- **Task 6 deletion failure:** inject screenshot-byte deletion failure and prove metadata remains retryable until byte deletion succeeds.
- **Task 7 internal contract:** test exact methods/routes: claim `POST`, heartbeat `POST`, capability `POST`, capability revoke `DELETE`, output `POST`, screenshot `PUT`, complete `POST`, and fail `POST`; every wrong worker/actor is an indistinguishable `404`.
- **Task 8 origin enforcement:** test allowed redirect, blocked redirect, paused popup/new page, first committed origin, IP literal, IDNA ambiguity, normalized Browser-Use `allowed_domains`, and empty-origin authorization requiring `operate`.
- **Task 9 Cursor failures:** separate red-green tests for connect, poll, idle-output, total-run, SIGINT, SIGTERM, and terminal Manager failure; every timeout/signal cancels exactly once and redacts key/token/port/internal-route data.
- **Task 12 viewer parity:** cover desktop, tablet, mobile, and fullscreen; all shared controls; restart disclosure; capability-gated screenshot/copy/paste; and a mount counter proving no viewer remount across zoom, pane, panel, resize, and fullscreen transitions.
- **Tasks 13-16 release security:** generate SBOMs for Manager and sidecar images, run dependency/license/vulnerability gates, assert the Browser-Use MIT pin, keep Skyvern AGPL external/disabled, and run fixture-based leak tests over logs, API/task/output/screenshot metadata for bearer keys, cookies, proxy credentials, direct ports, model secrets, and filesystem paths.

## Task 16: Review, merge, deploy, and verify fork main

**Files:**
- Review all changed files from Tasks 1-15.
- Update: `CHANGELOG.md` and release evidence only if fresh verification supports the claims.

- [ ] **Step 1: Run spec-compliance and code-quality reviews**

Dispatch separate read-only reviewers. Fix every finding and re-review until both approve.

- [ ] **Step 2: Run final secret, diff, and test gates**

```bash
git diff --check fork/main...HEAD
git grep -nE 'ghp_|cbm_agent_[A-Za-z0-9_-]{12,}|AUTH_TOKEN=|CURSOR_API_KEY=' -- ':!*.example' ':!docs/*REPORT*'
pytest -q backend/tests browser_use_sidecar/tests scripts/test_*.py
cd frontend && npm test && npm run build
```

Expected: no diff errors, no real secret matches, zero test/build failures.

- [ ] **Step 3: Push and merge only to the fork**

Push `feature/browser-use-agent-workspace` to `fork`, create a fork PR against `main`, require green verification, then squash-merge. Never push or merge to CloakHQ `origin` or Fintaro.

- [ ] **Step 4: Close superseded branches and PRs**

After the replacement is present on fork `main`, close PR #2 as superseded, verify no other open fork PRs/releases remain, and leave remote branches until their commits are confirmed reachable from `main` or intentionally obsolete.

- [ ] **Step 5: Redeploy and remote-read back**

Deploy the exact fork-main commit to VCVM, rerun the release gate, verify Manager and sidecar health, verify the public URL through the browser, compare remote blob hashes for the report/README, and record the final main SHA.
