# Browser-Use Agent Workspace Design

**Status:** Approved architecture, pending written-spec review

**Date:** 2026-07-24

**Repository:** `Martin-Hausleitner/CloakBrowser-Manager` fork only
**Target branch:** `main` after implementation and release validation

## 1. Outcome

CloakBrowser Manager becomes a compact, Browser-Use-inspired agent workspace in which Browser-Use can control the exact same persistent CloakBrowser profile that a human sees through VNC/CDP. The Manager remains the sole authority for profile launch, proxy, fingerprint, viewport, access control, health checks, and retained data. Browser-Use runs in an isolated VCVM sidecar and receives only a short-lived, single-run capability.

The workspace exposes the same browser controls on desktop, tablet, mobile, and fullscreen. It distinguishes persistent browser sessions from temporary task chats and renders typed agent outputs such as actions, screenshots, extracted data, links, status, and errors instead of reducing every result to Markdown.

## 2. Scope and sequencing

This specification is the first implementation increment of the broader CloakBrowser Manager goal. It covers the tightly coupled product slice required for a real Browser-Use workspace:

1. Browser-Use sidecar attached to the same CloakBrowser profile.
2. Anti-stealth, proxy, and fingerprint health gating before automation.
3. First-class projects, temporary/done chats, runs, and typed outputs.
4. Fixed reusable browser-session inventory backed by existing profiles.
5. Compact adaptive desktop/mobile/fullscreen UI using shared controls.
6. Local, VCVM, authorization, failure, and responsive E2E validation.

Existing profile organization, proxy inventory, extension inventory, account/access surfaces, live metrics, and CDP/VNC streaming remain in place. This increment integrates with them without unrelated redesign. Later increments may extend the same adapter contract to Stagehand, Unbrowse, Browser Harness, Antigravity, Claude Code, Codex, and OpenCode.

## 3. Non-goals

- Browser-Use Cloud is not used; it would create a different browser.
- Browser-Use never launches, kills, or owns Chromium.
- The sidecar never mounts profile storage.
- The feature does not promise that any fingerprint is undetectable.
- The UI does not expose proxy credentials, raw cookies, tokens, or extension configuration writes.
- This increment does not add multi-browser tasks or concurrent automation on one profile.
- Browser downloads and user uploads remain disabled in this increment. The only binary artifact is a Manager-ingested screenshot with a fixed private storage contract.

## 4. Chosen architecture

### 4.1 Components

```text
Human or harness
      |
      v
CloakBrowser Manager API
  - authorization
  - profiles and launch
  - proxy/fingerprint health
  - projects/tasks/runs/outputs
  - run and CDP leases
      |
      | short-lived single-run capability
      v
Browser-Use sidecar on VCVM
  - claims one run
  - attaches through authenticated Manager CDP proxy
  - emits sanitized typed outputs
      |
      v
Existing CloakBrowser profile
  - same process
  - same user-data directory
  - same proxy and fingerprint
  - visible in VNC/CDP live view
```

### 4.2 Why a sidecar

Embedding Browser-Use inside FastAPI would couple long LLM runs, dependency churn, memory pressure, cancellation, and crashes to the Manager. A CLI-only integration would make authenticated CDP headers, run lifecycle, observability, and structured output persistence fragile. The VCVM sidecar isolates runtime failures while keeping attachment local and low latency.

### 4.3 Ownership invariant

The Manager is the only component allowed to:

- create, launch, stop, restart, or delete a browser profile;
- select profile storage, proxy, UA, locale, timezone, viewport, or fingerprint arguments;
- issue or revoke a CDP capability;
- decide whether the current health state permits automation.

The sidecar may only attach, execute a bounded task, report events, and disconnect. It must call Browser-Use disconnect/stop semantics that leave the remote browser alive.

## 5. Run lifecycle and authorization

### 5.1 Public API

- `POST /api/task-sessions/{session_id}/runs`
- `GET /api/task-runs/{run_id}`
- `GET /api/task-runs/{run_id}/outputs?after_sequence=...`
- `POST /api/task-runs/{run_id}/cancel`

Run creation accepts:

```json
{
  "harness": "browser-use",
  "task": "Navigate to the target and return the page title",
  "profile_id": "vcvm-mobile-demo",
  "launch_if_stopped": false,
  "allowed_origins": ["https://example.com"],
  "max_steps": 20,
  "timeout_seconds": 300,
  "model_alias": "default"
}
```

Every run names its browser `profile_id`. The profile must exist in the task's immutable `sandbox_id`, and the caller must hold `automate` for both the task sandbox and that profile. The run stores the selected profile independently; it does not silently reassign the task. `launch_if_stopped=true` additionally requires `operate`. Unauthorized resources preserve the existing indistinguishable `404` behavior.

### 5.2 Internal sidecar API

- `POST /internal/task-runs/claim` atomically claims the oldest queued run whose profile has no active automation lease. It returns no work as `204`.
- `POST /internal/task-runs/{run_id}/heartbeat` renews the worker claim and profile automation lease.
- `POST /internal/task-runs/{run_id}/capability` issues the run-bound CDP capability once the health gate passes.
- `DELETE /internal/task-runs/{run_id}/capability` explicitly revokes the capability and closes its active CDP sockets.
- `POST /internal/task-runs/{run_id}/outputs` appends one allowlisted typed output with an idempotency key.
- `PUT /internal/task-runs/{run_id}/screenshots/{output_id}` ingests one validated screenshot body.
- `POST /internal/task-runs/{run_id}/complete` and `/fail` transition the run to a terminal state.

The sidecar authenticates with a narrowly scoped service identity stored only in VCVM secret storage. It never receives an administrator token or an all-sandbox agent key. Claiming uses one database transaction: select an eligible queued run, create the profile lease, bind the worker identity, and update the run state. A queued run has `claim_eligible_at = null` while an earlier run or direct client holds the profile lease. The Manager sets `claim_eligible_at` only when the run is first in the per-profile queue and the profile is free; if another atomic acquirer wins before the worker claim, it clears the timestamp again. A claim is renewed every 15 seconds and expires after 45 seconds without a heartbeat. Heartbeat loss revokes the CDP capability, closes active sockets, and fails the run as `worker_lost`; it does not retry after the first browser action.

### 5.3 State machine

```text
queued -> health_check -> running -> succeeded
   |             |          |-> failed
   |             |          |-> cancelled
   |             |          |-> revoked
   |             |          |-> queued (one pre-action worker retry)
   |             |-> blocked_health -> queued
   |-> cancelled
```

Only one run may hold the automation lease for a profile. Contention has one deterministic behavior: later runs remain queued in creation order and may be cancelled while queued. They never receive a CDP capability until the earlier lease ends.

### 5.4 Lease behavior

The Manager issues a cryptographically random 256-bit opaque `cbm_run_...` capability bound to `run_id`, `profile_id`, `automate`, the worker service identity, and the run deadline. Only a SHA-256 digest is stored. Validation uses a constant-time comparison and requires an active run, worker claim, profile lease, and unexpired deadline. The capability is valid only for the bound profile and may be used for Browser-Use's version request plus its CDP WebSocket; replay against another profile or after a terminal transition fails as `404`/`4403` without reaching the upstream CDP port.

The token is sent only in an authorization header. Explicit capability deletion, grant revocation, cancellation, run completion, heartbeat loss, deadline expiry, or service-credential rotation revokes the digest, closes all sockets registered to the capability, and prevents reconnect.

### 5.5 Existing CDP clients and exclusivity

All direct `/api/profiles/{profile_id}/cdp` automation clients, including agent open-links, use the same lease table as task runs:

- `POST /api/profiles/{profile_id}/automation-leases` atomically acquires the profile when no unexpired lease exists. It requires `automate`, binds the actor identity, creates a 256-bit one-time lease token whose digest is stored, and returns `lease_id`, token, and expiry. Contention returns `409 automation_busy`.
- `POST /api/profiles/{profile_id}/automation-leases/{lease_id}/heartbeat` renews the lease every 15 seconds; it expires after 45 seconds without renewal.
- `DELETE /api/profiles/{profile_id}/automation-leases/{lease_id}` releases it and closes registered sockets.

`/cdp/json/version`, `/cdp/json/list`, and the CDP WebSocket require the lease token in `X-CBM-Automation-Lease`; tokens in query strings are rejected. Validation binds profile, lease ID, actor, active grant, and expiry using constant-time digest comparison. The HTTP discovery responses expose only Manager-proxy WebSocket URLs and never upstream ports. WebSocket close, heartbeat loss, grant revocation, or explicit release atomically retires the lease and token. Task-run capabilities are leases owned by the worker service and follow the same validation path.

Manager-owned live-view/screencast observers are exempt only because they expose no caller-controlled CDP commands. This prevents an existing `automate` principal from bypassing Browser-Use run serialization.

### 5.6 Navigation policy

The request field is `allowed_origins`, not a loose domain list. Each entry is a normalized `https://host[:port]` or `http://host[:port]` origin. Hosts are lowercased and IDNA-normalized; default ports are removed. Wildcards, paths, credentials, fragments, and ambiguous suffix matching are rejected. IP literals are rejected unless the exact literal origin is explicitly listed.

The sidecar enforces the list before every top-level navigation and after redirects. New pages and popups start paused and are closed if their first committed origin is not allowed. A disallowed redirect fails the current action as `navigation_blocked`. Browser subresources may load only when initiated by an allowed top-level page; this policy is a navigation boundary rather than a general network sandbox. The Browser-Use `allowed_domains` setting receives the same normalized host set as defense in depth. Empty `allowed_origins` means unrestricted navigation and is allowed only for callers with `operate`; `automate`-only callers must provide at least one origin.

## 6. Anti-stealth and health gate

Browser-Use attaches only after the Manager has a fresh terminal health result and all temporary health-check pages are closed. The default freshness window is 10 minutes and is configurable from 1 to 60 minutes.

A run copies an immutable redacted health snapshot into its own record at authorization time. It never references the mutable latest-profile row. The snapshot contains:

- proxy reachability and masked exit-IP fingerprint;
- measured versus inferred authenticity score;
- platform, UA, locale, timezone, screen, viewport, and touch consistency;
- BrowserScan summary and structured mismatch reasons;
- measurement timestamp and source freshness.

The existing health states remain authoritative: `pending` and `running` wait; `failed` and `unavailable` block; `passed` may proceed; `warning` may proceed only if all numeric and critical-reason rules pass. The gate additionally requires a fresh result, no measurement error, a successful reachability result for a configured proxy, measured authenticity of at least 70, and none of these critical reason codes: `proxy_unreachable`, `proxy_exit_mismatch`, `platform_ua_mismatch`, or `mobile_identity_inconsistent`. All thresholds, mappings, and reason codes are versioned in the copied snapshot.

`POST /api/task-runs/{run_id}/retry-health` reruns the profile probe and returns `blocked_health -> queued` only after a passing result. `POST /api/task-runs/{run_id}/health-override` requires `operate`, a non-empty reason, and a fresh completed measurement; it cannot override `proxy_unreachable` or a measurement error. A successful override records actor, reason, failed rules, policy version, and immutable snapshot, then returns the run to `queued`. The UI presents this as an explicit warning action, never an automatic fallback.

Phone Fit must configure a coherent mobile identity rather than only a narrow framebuffer. The Manager applies the selected mobile viewport policy, touch/mobile device metrics, UA/platform policy, and screen dimensions as one operation, then schedules a new health measurement.

## 7. Data model

### 7.1 Persistent browser sessions

Existing `profiles` remain the sole persistent browser-session inventory. The UI labels them “Browser sessions”. Completing, archiving, or deleting a task never stops or deletes a browser session.

### 7.2 Projects

Add first-class projects keyed by `(sandbox_id, id)` with name, accent color, description, default retention, archived timestamp, creator, and timestamps. Existing profile `project_id` strings are migrated into project records so empty projects can subsequently exist.

### 7.3 Task sessions

Extend existing task sessions with:

- immutable `sandbox_id` copied from the authorized profile at creation/migration;
- `project_id`;
- `workflow_state: open | done`;
- `done_at`;
- `archived_at`;
- `retention_class: temporary | project | legacy`;
- `expires_at`;
- optimistic `row_version`.

`done` is a user workflow state. `archived` is a visibility overlay. Reopening a task preserves its messages, runs, and outputs. Legacy rows receive no automatic expiry.

Task, message, run-history, output, and screenshot authorization resolves from the task's immutable `sandbox_id`, not from the current profile row. Migration rebuilds `task_sessions` so `profile_id` is nullable and snapshots both `sandbox_id` and `project_id` from the existing profile before replacing `ON DELETE CASCADE` with `ON DELETE SET NULL`. Cross-sandbox resources continue to return indistinguishable `404`. Moving a profile later never moves historical task ownership.

### 7.4 Runs

Each task run records harness, profile, status, bounded execution options, an immutable copied health snapshot, lease timestamps, terminal error code, creator, and timestamps. Run records never store raw prompts sent to external model providers beyond the task message already authorized for persistence.

### 7.5 Typed outputs

Outputs are ordered, idempotent records with a discriminated kind:

- `status`;
- `action`;
- `observation`;
- `screenshot`;
- `extracted_data`;
- `link`;
- `metric`;
- `error`;
- `approval`;
- `summary`.

Each output contains a safe summary, allowlisted JSON payload, sequence, actor, timestamps, and an optional opaque screenshot reference. Binary data is not stored in SQLite. Unknown future kinds render through a safe expandable fallback.

Screenshots are written only by the Manager after it validates `image/png` or `image/jpeg`, a maximum size of 5 MiB, decoded dimensions no larger than 4096 by 4096, and a matching SHA-256 supplied with the output. Storage lives under a configured private `CBM_ARTIFACT_ROOT` using Manager-generated opaque directory and file names; sidecar paths and filenames are ignored. Writes use a new file with `O_NOFOLLOW`, reject symlinks, and atomically rename after validation. Retrieval requires `view` for the output's sandbox, sends a fixed safe content disposition, and returns indistinguishable `404` across sandboxes. No general file, download, or upload endpoint exists in this increment.

Passwords, cookies, authorization headers, proxy credentials, raw clipboard contents, unredacted DOM dumps, and model tokens are rejected or removed before persistence.

### 7.6 Retention and deletion

- `temporary` tasks auto-archive after 7 days without a message, run, output, or explicit user update and are purged 30 days after archival.
- `project` tasks do not expire automatically.
- `legacy` tasks receive no expiry during migration.
- Reopening a non-purged temporary task clears `archived_at`, updates activity time, and schedules the next inactivity archive.
- Screenshot binaries are removed 7 days after task archival; their output records remain with `artifact_expired=true` until task purge.
- A daily idempotent cleanup job deletes expired rows and screenshot bytes in one recoverable sequence and records only IDs/counts in the audit log.
- Projects are archived, not deleted, in this increment.
- Profile deletion sets historical task/run `profile_id` references to null and preserves project-owned history. Every subsequent run request supplies a new `profile_id` in the same immutable task sandbox and passes normal `automate` authorization; prior runs retain their original profile identifier as redacted audit metadata.

All cleanup tests use an injected clock. Failed screenshot deletion leaves the metadata queued for retry and never removes the only reference before the byte deletion succeeds.

## 8. Adaptive workspace UI

### 8.1 Desktop structure

```text
Projects and tasks | Agent output and composer | Browser session and live view
```

The left rail keeps projects at the top, open/done tasks in the middle, and reusable browser sessions in a bounded persistent region at the bottom. The center pane renders task status, typed outputs, and the composer. The browser pane contains the single mounted viewer plus contextual controls.

The browser must not remount when the user changes zoom, opens a control panel, resizes panes, or enters fullscreen.

### 8.2 Shared browser controls

Desktop, tablet, mobile, and fullscreen reuse the same state model and components for:

- fit, width, height, and reset;
- visual zoom and pane ratio;
- Phone Fit and Mobile/Tablet/Desktop viewport presets;
- editable width and height with restart disclosure;
- session switching;
- reconnect;
- screenshot, copy, and paste when the host capability exists;
- live connection and latency status.

Persistent profile mutations remain distinct from local viewer preferences.

### 8.3 Responsive behavior

- `<600px`: browser-dominant stacked mobile layout; navigation and tools use full-height sheets.
- `600-899px`: tablet split with overlay navigation and a Task/Browser focus switch.
- `900-1199px`: compact desktop with persistent rail, agent pane, and browser pane.
- `>=1200px`: full three-pane workspace; contextual tools may be pinned only when sufficient browser width remains.

Pointer type affects hit-area density, not layout selection.

### 8.4 Mobile and iOS constraints

- interactive hit areas are at least 44 px on coarse pointers;
- compact icons may appear smaller while retaining their hit area;
- composer input text is at least 16 px to prevent Safari focus zoom;
- safe-area insets and `visualViewport` drive keyboard avoidance;
- primary actions remain in the reachable lower region;
- gestures always have visible button alternatives;
- fullscreen is a labelled modal with focus containment, Escape close, inert background, and focus restoration;
- `prefers-reduced-motion`, visible focus, and WCAG AA contrast are preserved.

### 8.5 Progressive disclosure

Always visible: active project/task/profile, run state, browser connection, output timeline, composer, browser view, and start/stop state.

One action away: zoom, fit, pane ratio, viewport, session switch, screenshot/copy/paste, live metrics, and collapsed action details.

Administrative: persistent identity, proxy, extensions, access, and sandbox configuration. These remain outside the main task flow.

## 9. Error handling and recovery

- Sidecar unavailable: a run must be claimed within 60 seconds of continuous claim eligibility, measured from `claim_eligible_at`, or fails with `worker_unavailable`. Time spent queued behind an active profile lease never counts. The timeout is configurable from 30 to 300 seconds and is enforced by the Manager clock, not the worker.
- Health timeout: run becomes `blocked_health`; no CDP capability is issued.
- CDP disconnect: one reconnect is allowed only when the lease remains valid and no ambiguous browser action was in flight.
- Sidecar crash: the first accepted `action` output atomically sets durable `first_action_at` and `first_action_sequence`. Heartbeat loss before that marker revokes capability and lease, increments `retry_count`, and returns the run to `queued` once. Heartbeat loss after the marker, or a second loss, fails as `worker_lost`. Requeue cleanup is one transaction and resets worker, claim, capability digest, lease, heartbeat, and `queued_at` while preserving prior status outputs.
- Model timeout/rate limit/max steps: run terminates with a stable error code; the browser remains running.
- Cancellation/revocation: sidecar disconnects, capability is invalidated, and browser remains visible and running.
- Unknown output: safe fallback card; no raw HTML rendering.
- Screenshot validation failure: output is rejected without exposing server paths.

## 10. Deployment

The VCVM compose stack gains a pinned Browser-Use sidecar image and versioned configuration. `ANONYMIZED_TELEMETRY=false` is mandatory. Model-provider secrets remain in VCVM secret storage and are never returned to the Manager UI. The sidecar and Manager communicate over a private container network; only the Manager is exposed through the existing Tailnet path.

Browser-Use is pinned to a tested version and guarded by adapter contract tests. The CloakBrowser binary license remains authoritative; no hosted third-party browser service or external resale is introduced.

## 11. Acceptance gates

### 11.1 Automated

1. Database migration preserves profiles, legacy tasks, messages, events, IDs, and timestamps.
2. Projects can exist without profiles and survive reload.
3. Tasks move through open, done, archived, unarchived, and reopened states.
4. Completing a task does not alter the associated browser session.
5. Run creation enforces `automate`; launch additionally enforces `operate`.
6. Cross-sandbox reads, runs, outputs, and screenshots return indistinguishable `404`.
7. One profile accepts only one automation lease.
8. Revocation closes active CDP and prevents reconnect.
9. Output retries with the same idempotency key create one record.
10. Typed outputs render on desktop and mobile after reload.
11. Viewer controls do not remount the live viewer.
12. Fullscreen View, Viewport, Sessions, Phone Fit, zoom, and exit remain available.
13. Mobile keyboard, focus, touch targets, and safe-area behavior pass browser tests.
14. Logs, API responses, task metadata, and screenshot metadata contain no bearer, proxy, cookie, or model secrets.
15. Existing direct automation CDP clients cannot bypass an active profile lease; live-view observers remain functional.
16. Navigation policy rejects wildcard/suffix confusion, credentialed URLs, disallowed redirects, popups, IP literals, IDNA ambiguity, and cross-profile capability replay.
17. Later health probes cannot mutate the immutable snapshot that authorized an earlier run.
18. Health pass, deterministic block, retry, permitted override, and non-overridable failures follow the versioned policy.
19. Clock-controlled cleanup preserves project/legacy tasks, expires temporary tasks, removes screenshot bytes safely, and leaves no orphaned metadata.
20. Direct-CDP lease acquire, discovery, heartbeat, release, expiry, actor binding, query-token rejection, and contention are deterministic and cannot bypass run leases.
21. Health gating uses the persisted `pending | running | passed | warning | failed | unavailable` states and the documented numeric/reason rules.
22. Profile deletion preserves task history under immutable task-sandbox authorization, while a new run succeeds only with an explicitly selected same-sandbox profile.
23. Worker claim timeout and the single pre-action retry use an injected clock and durable first-action marker; post-action loss never retries.
24. A second run may wait behind an active profile lease for longer than the worker timeout without failing; the timeout begins only when that run becomes continuously claim-eligible.

### 11.2 Real VCVM E2E

1. Start or reuse a proxy-backed CloakBrowser profile.
2. Record the profile process, user-data directory fingerprint, CDP target, masked proxy exit, UA/platform, timezone, locale, screen, viewport, and health result.
3. Submit a Browser-Use task through the Manager.
4. Verify the same browser visibly navigates in VNC and CDP live view.
5. Verify no second browser process or profile directory appears.
6. Confirm status, action, screenshot, extracted-data, and summary outputs persist and render after reload.
7. Cancel a second task and verify the browser remains running.
8. Revoke the run capability and verify the WebSocket closes and cannot reconnect.
9. Repeat UI checks at 390, 768, 1024, and 1440 px, including fullscreen and an open mobile keyboard.
10. Capture versioned JSON evidence and screenshots; record exact commit, container digests, test commands, timings, and known gaps.

## 12. Definition of done

The feature is done only when Browser-Use controls the same existing CloakBrowser profile on the VCVM, the action is visible live, anti-stealth health gates are enforced, typed outputs persist, projects/tasks/browser sessions behave independently, shared browser controls work across desktop/mobile/fullscreen, authorization and failure paths pass, and the complete evidence is committed and pushed to the fork's `main` branch.
