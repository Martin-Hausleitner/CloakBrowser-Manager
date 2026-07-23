---
name: cloakbrowser-manager-development
description: Use when continuing, reviewing, testing, deploying, or documenting the CloakBrowser Manager fork, especially its mobile VNC workspace, profile organization, scoped access, harness bridge, redacted profile health, VCVM deployment, or release evidence.
metadata:
  category: integration-documentation
  triggers:
    - CloakBrowser Manager
    - mobile VNC
    - VCVM
    - Codex Computer Use
    - profile organization
    - profile health
    - BrowserScan
    - proxychecker
    - scoped browser access
---

# Continue CloakBrowser Manager Development

Use this repository-local workflow to continue the fork without losing its security boundaries, mobile constraints, or evidence history.

## Non-negotiable boundaries

- Work in the CloakBrowser Manager fork only: `https://github.com/Martin-Hausleitner/CloakBrowser-Manager`.
- Treat `https://github.com/CloakHQ/CloakBrowser-Manager` as read-only upstream unless the user explicitly changes that scope.
- Never push CloakBrowser work, reports, or generated files to `fintaro-ai/Fintaro-Agent`.
- Keep the Manager and browser runtime on the VCVM. Use the Mac as a client, validation host, and secure tunnel endpoint.
- Never expose administrator tokens, user passwords, proxy credentials, agent keys, cookies, browser content, raw launch arguments, or host paths in logs, reports, screenshots, commits, or chat.
- Preserve unrelated dirty files. Stage explicit paths and review the staged diff before every commit.
- A preferred harness is metadata, not an execution authority. Host-scoped browser actions must continue to require the verified `codex-computer-use` bridge and fail closed otherwise.
- Profile health is an observation, not an undetectability guarantee. Persist only masked IPs, numeric scores, source states and whitelisted warning/blocker codes; never provider HTML, raw responses or exception text.
- Keep the optional proxychecker on a trusted VCVM-local boundary. Manager `/health` must not depend on it, and an unavailable proxychecker must degrade to an explicit source state rather than block profile launch.

## Start every continuation

1. Read the following sources in order:
   - `README.md`, especially **Fork development status**.
   - `docs/GOAL-ACCEPTANCE-MATRIX-2026-07-22.md`.
   - The newest relevant plan in `docs/superpowers/plans/`.
   - The relevant audit or benchmark document linked by the matrix.
2. Inspect `git status`, the active branch, both remotes, and recent commits. Do not discard or rewrite unknown changes.
3. Check that `fork` points to Martin Hausleitner's repository and `origin` points to CloakHQ upstream.
4. Select the first incomplete acceptance-matrix row whose prerequisites are already proven.
5. State the exact claim, verification needed, and stop condition before editing.

If the repository's `bd` issue tool is unavailable, record that limitation in the handoff; do not invent issue state.

## Implement one vertical slice at a time

1. Add a failing test for the requested behavior or security boundary.
2. Make the smallest implementation that passes it; reuse existing types, access checks, redaction helpers, and mobile components.
3. Run focused backend or frontend tests immediately.
4. Run the relevant integrated build/gate before widening scope.
5. Update the acceptance matrix only from fresh evidence.
6. Commit the slice with explicit staging. Do not mix reports, generated build metadata, benchmark artifacts, or unrelated changes.

## Product rules to preserve

### Mobile UI and VNC

- Keep the live browser as the dominant surface.
- Use progressive disclosure: persistent controls stay limited to essential session, fullscreen, tools, chat, and send actions.
- Controls may look compact, but actionable hit areas remain at least 44 by 44 CSS pixels.
- Text inputs remain at least 16 px on iOS to prevent focus zoom.
- Derive keyboard-open layout from `visualViewport`; keep both VNC and composer visible and non-overlapping.
- Keep Fit, Width, Height, Phone fit, viewer zoom, editable framebuffer viewport, fullscreen session switcher, and grid behavior reachable.
- Do not add benchmark controls or diagnostic clutter to the mobile workspace.

### Profiles and access

- Keep `sandbox_id` as the authorization key. `project_id` and `folder_path` are organization metadata only.
- Preserve deterministic ordering: pinned, project, folder, name, newest creation time, then id.
- Redact proxy, fingerprint, filesystem, and launch-argument details from access summaries.
- Preserve indistinguishable `404` behavior for missing and unauthorized profiles.
- Treat viewer, interaction, operation, and automation permissions as distinct capabilities.

### Harnesses and automation

- Keep Codex Computer Use as the execution boundary for browser-visible host actions.
- Saved preferences for Antigravity, Claude Code, OpenCode, or Browser Use may be displayed and propagated as metadata, but must not bypass bridge verification.
- Keep quick actions typed and capability-checked. Reject unknown commands.
- Server-side task history is persistence and conversation state; it is not proof that an agent executed a browser task.

### Profile health and external observations

- Schedule the automatic probe only after the first successful launch and never await it in the launch response.
- Keep manual reruns behind `operate`; keep stored reads behind `view`; preserve indistinguishable `404` behavior outside scope.
- Mask IPv4 and IPv6 before persistence. Never store or return the raw outbound address.
- Treat saved-versus-runtime fingerprint consistency, proxy risk/authenticity and BrowserScan authenticity as separate measurements. Never substitute one score for another.
- BrowserScan challenge, consent, timeout or unsupported markup returns an unavailable score with a whitelisted blocker. Do not click consent or CAPTCHA controls.
- Do not interpret labels such as `WebDriver` as a detection by themselves; require an explicit negative result.
- Keep health disclosure off the persistent mobile workspace. A compact desktop summary with progressive detail is sufficient.

## Current verified checkpoint

Snapshot from 23 July 2026 (evening) on `integrate-pr-47-27-26`:

- Backend suite **371/371**; frontend suite **133/133**; production build passed.
- Browser-Use desktop shell, compact sidebar, project/harness selectors, proxy overview, and auto geo-aligned profile creation deployed on VCVM.
- Proxy inventory ingest of 11 entries live-proven; Proxy-Checker check returns redacted scores; credentials stay server-side.
- Prior 22–23 July checkpoints still hold for profile health, mobile gate, live diagnostics, and proxychecker health reachability.
- Safari Remote Automation is disabled, so WebKit and physical-iPhone evidence remain external blockers.
- The next vertical slices are batch proxy checks with better geo enrichment and the release handoff pack.

Refresh these numbers and claims after any relevant change; this is a dated handoff, not permanent proof.

## Evidence rules

Use the release checklist in [references/release-checklist.md](references/release-checklist.md).

Classify every claim as one of:

- **Proven**: fresh test or live evidence directly covers the full claim.
- **Implemented; scoped verification passed**: code and focused tests pass, but the required VCVM/device proof is not fresh.
- **Partial**: only part of the requirement is implemented or measured.
- **Blocked externally**: product code is ready, but an external prerequisite such as Safari Remote Automation, Tailnet policy, or physical-device access is unavailable.
- **Missing**: no acceptable implementation or evidence exists.

Never convert Chromium emulation into a Safari claim, a visual canvas-change proxy into encoded FPS, a WebSocket handshake into touch-to-pixel latency, or a persisted benchmark report into live diagnostics.

## Documentation and handoff

Before ending a development session:

1. Update the README status, completed work, current work, open work, roadmap, timeline, and fresh verification counts.
2. Update the goal matrix row-by-row with exact evidence and remaining proof.
3. Mark completed plan checkboxes and retain unresolved tasks.
4. Record changed files, test results, deployment state, browser/screenshots, latency limits, and external blockers.
5. Commit and push only to the fork branch, then verify the remote branch SHA matches local `HEAD`.
6. Do not provide a URL until an independent browser check has loaded it, exercised the relevant flow, and captured a screenshot.

## Stop conditions

Stop and report a blocker only when credentials or external authorization are required, the requested action is destructive, or three safe recovery approaches have failed. Otherwise continue through edit, test, browser validation, documentation, commit, push, and remote verification.

## Required completion report

Return:

1. `Summary`
2. `Changes Made`
3. `Validation Results`
4. `Deployment And Browser Evidence`
5. `Open Gaps`
6. `Fork Branch And Commit`
