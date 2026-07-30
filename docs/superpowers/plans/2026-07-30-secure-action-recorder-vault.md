# Secure Action Recorder + ACPX CI/CD Implementation Plan

> Execute in the isolated VCVM worktree `feature/secure-action-recorder-vault`. Preserve unrelated work and push only to Martin's fork.

**Goal:** deliver an opt-in MV3 browser action recorder, reference-only vault integration, deterministic flow/prompt compilation, ACPX CI/CD gates, bounded watchdog, and fresh cross-platform evidence.

**Architecture:** the extension captures and redacts locally, the Manager validates/persists bounded artifacts, vault providers are used only through opaque references, and adapters replay canonical flows under Manager leases. CI/CD and the watchdog fail closed and emit redacted receipts.

## Task 1 — Baseline and contracts

**Files:** design spec, active-plan links, issue/ticket receipts.

1. Run existing extension verifier and targeted backend/ACPX tests.
2. Record exact base commit and dirty-worktree state.
3. Add this design and plan without replacing the authoritative master plan.
4. Run `git diff --check` and link checks.

## Task 2 — Recorder normalization and redaction, TDD

**Files:** `extensions/cloak-profile-sync/lib/recorder.js`, recorder tests, manifest/service worker.

1. Write failing tests for navigation, click, safe input, selectors, ordering, prompt determinism, TTL, and session reset.
2. Write failing negative tests for password, cookie, bearer/JWT, OTP/TOTP, passkey, payment, hidden, clipboard, and proxy-secret fixtures.
3. Implement the minimal pure normalization/redaction module.
4. Integrate content script and service worker with explicit start/stop and bounded storage.
5. Verify all tests and existing extension compatibility verifier.

## Task 3 — Compact extension controls

**Files:** popup HTML/CSS/JS and tests/verifier.

1. Add a compact record/stop control, high-contrast live state, event count, expiry, clear, and deterministic export.
2. Keep profile/proxy operations intact and avoid exposing raw token/password values in new UI state.
3. Validate keyboard navigation, labels, and narrow popup layout.

## Task 4 — Manager recorder and reference-only vault API, TDD

**Files:** backend models/routes/storage/tests.

1. Add strict schemas for events and bounded batches; reject extra keys and secret-like content.
2. Bind ingestion to run/profile lease, origin policy, nonce, TTL, and sequence.
3. Persist canonical redacted flow artifacts and hashes.
4. Add provider metadata and `health/authorize_use/revoke_use/receipt` operations for Vaultwarden, Infisical, and optional gopass.
5. Prove there is no agent-facing reveal operation and raw values are rejected.

## Task 5 — Prompt compiler and harness adapters, TDD

**Files:** scripts/compiler and adapter tests.

1. Write deterministic canonical JSON and prompt hash tests.
2. Emit typed assertions, handoffs, origin policies, viewport requirements, and opaque secret references.
3. Add adapter inputs for Browser Use first; preserve Stagehand/Unbrowse routing without coupling the product model to either.
4. Validate with the existing Grok OpenAI-compatible CLI proxy and ACP/ACPX resource envelope.

## Task 6 — ACPX CI/CD workflow

**Files:** workflow definitions, scripts, CI tests, docs.

1. Define stages for extension, backend, security, compiler, ACPX preflight, Browser Use smoke, packaging, deploy, browser verification, and rollback.
2. Pin schemas/runtime versions and artifact checksums.
3. Ensure missing ACPX/Browser Use/VM dependencies classify as `adapter_unavailable`, not success.
4. Emit one redacted machine-readable receipt per stage.

## Task 7 — Bounded watchdog

**Files:** watchdog script/service template/tests.

1. Test healthy, stale, crash, backoff, restart cap, and blocked states.
2. Implement health-only monitoring of the recorder bridge/worker.
3. Assert logs cannot contain environment dumps, request bodies, headers, or secret values.
4. Verify service templates and a controlled crash/recovery on VCVM staging.

## Task 8 — Browser Use E2E on VCVM

1. Package/load the unpacked extension into a Manager-owned disposable demo profile.
2. Navigate only to an allowed deterministic test origin.
3. Start recording visibly, perform safe navigation/click/input, stop, and export.
4. Replay through Browser Use under the same Manager resource contract.
5. Correlate commit, extension checksum, profile, run, lease, actions, prompt hash, outputs, screenshot, and terminal state.

## Task 9 — Second VM validation

1. Confirm authorized SSH/Tailscale reachability and OS/runtime.
2. Transfer only the packaged artifact and safe test fixtures.
3. Run package/static/bridge checks; run browser E2E only if the runtime is compatible.
4. Record latency and an honest blocker for any missing dependency.

## Task 10 — macOS Codex Computer Use validation

1. Load the version-matched Orca computer-use guide and validate runtime capabilities.
2. Use a dedicated safe browser profile, never the user's normal Chrome profile.
3. Visibly load/test start/stop/export using computer-use and capture screenshots.
4. If the required client/runtime is missing, record exact commands/errors and do not substitute a different automation path as proof.

## Task 11 — NotebookLM and source-grounded review

1. Build a source packet from official Chrome MV3, Browser Use, ACPX, Vaultwarden, Infisical, and gopass documentation.
2. Add only public sources to a NotebookLM notebook; never upload configs, tokens, browser state, or private logs.
3. Ask for architecture risks, redaction gaps, CI/CD failure modes, and cross-platform acceptance gaps.
4. Follow up until the answer covers all four; record a redacted synthesis and sources.

## Task 12 — Release gate and landing

1. Run targeted tests, broader affected suites, lint/typecheck/build, extension verifier, secret scan, and `git diff --check`.
2. Perform independent code/security review and resolve material findings.
3. Deploy staging, run Agent Browser/Browser Use verification, and capture fresh screenshots.
4. Commit with the repository smart-commit workflow and push only to Martin's fork.
5. Verify remote commit bytes/SHA and report immutable links, URL, test credentials only if they are non-secret disposable demo credentials, and explicit blockers.

