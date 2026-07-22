---
name: cloakbrowser-manager-development
description: Use when continuing, reviewing, testing, deploying, or documenting the CloakBrowser Manager fork, especially its mobile VNC workspace, profile organization, scoped access, harness bridge, VCVM deployment, or release evidence.
metadata:
  category: integration-documentation
  triggers:
    - CloakBrowser Manager
    - mobile VNC
    - VCVM
    - Codex Computer Use
    - profile organization
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
